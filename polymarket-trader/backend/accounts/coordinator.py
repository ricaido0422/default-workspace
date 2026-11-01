"""唯一执行入口：持久化意图、通道分发、回执退避核对；未知提交绝不重发。"""
import asyncio
import json
from datetime import timedelta
from backend.accounts.contracts import OrderIntent, Submission, utc
from backend.models.schemas import ExecutionRecord, ExecutionMode, ExecutionStatus


class AccountCoordinator:
    def __init__(self, service):
        self.service = service
        self.lock = asyncio.Lock()
        self.last_snapshot = 0

    async def event(self, account_id, intent_id, state, error=''):
        await self.service.store._db.execute('INSERT INTO account_events(account_id,intent_id,state,error,created_at) VALUES (?,?,?,?,?)',
            (account_id, intent_id, state, error, utc().isoformat()))
        await self.service.store._db.commit()

    async def apply(self, submission):
        record = ExecutionRecord(signal_id=submission.intent_id, account_id=submission.account_id,
            mode=ExecutionMode.LIVE, status=ExecutionStatus.PENDING if submission.state in ('AWAITING_SIGNATURE', 'SUBMITTING') else ExecutionStatus(submission.state),
            order_id=submission.order_id or '', fill_price=submission.fill_price,
            fill_size=submission.fill_size, error=submission.error)
        old = await self.service.store.get_execution(record.signal_id)
        changed = old is None or (old.status, old.order_id, old.fill_price, old.fill_size, old.error) != (record.status, record.order_id, record.fill_price, record.fill_size, record.error)
        if old:
            await self.service.store.update_execution(record)
        else:
            await self.service.store.save_execution(record)
        await self.service.store._db.execute('UPDATE order_intents SET submission=?,state=?,error=?,next_check=? WHERE id=? AND account_id=?',
            (submission.model_dump_json(), submission.state, submission.error, utc().timestamp()+5, submission.intent_id, submission.account_id))
        if changed:
            await self.event(submission.account_id, submission.intent_id, submission.state, submission.error)
        else:
            await self.service.store._db.commit()
        return record

    async def execute(self, signal):
        async with self.lock:
            if signal.account_id != self.service.active_id:
                raise ValueError('信号绑定钱包与当前账户不同，请切回原账户或重新创建信号')
            old = await self.service.store.get_execution(signal.signal_id)
            if old:
                return old
            intent = OrderIntent(id=signal.signal_id, account_id=signal.account_id,
                token_id=signal.market.token_id, condition_id=signal.market.condition_id,
                side=signal.side.value, limit_price=signal.price, quantity=signal.size,
                expires_at=signal.source_trade.timestamp + timedelta(seconds=self.service.manager.settings.executor.max_signal_age_seconds))
            await self.service.store._db.execute('INSERT INTO order_intents(id,account_id,intent,signal,state,created_at) VALUES (?,?,?,?,?,?)',
                (intent.id, intent.account_id, intent.model_dump_json(), signal.model_dump_json(), 'SUBMITTING', utc().isoformat()))
            await self.service.store._db.commit()
            pending = Submission(account_id=intent.account_id, intent_id=intent.id, state='PENDING', error='提交结果待确认，禁止重复提交')
            # HMAC 引擎自行保存提交前记录；其他通道也必须有崩溃恢复标记。
            adapter = self.service.adapter()
            try:
                status = await adapter.check_connection()
                if not status.ready or intent.expires_at <= utc() or self.service.manager.mode.value != 'LIVE':
                    pending.state = 'FAILED'
                    pending.error = status.error or '连接未就绪、信号过期或实盘已停止，未提交'
                else:
                    pending = await adapter.submit_order(intent)
            except Exception:
                # 请求可能已经抵达交易所，保守保持待核对；不输出可能含 token 的异常。
                pending.error = '提交通道异常，结果待核对；不会自动重发，请检查账户连接及交易所订单'
            return await self.apply(pending)

    async def reconcile(self, record):
        adapter = self.service.adapter(record.account_id)
        previous = (record.status.value, record.fill_size, record.fill_price)
        submission = Submission(account_id=record.account_id, intent_id=record.signal_id,
            order_id=record.order_id or '', state=record.status.value, error=record.error or '')
        receipt = await adapter.get_receipt(submission)
        result = await self.apply(receipt)
        if (receipt.state, receipt.fill_size, receipt.fill_price) != previous:
            from backend.api.events import get_event_bus, Channels
            bus = get_event_bus()
            if bus:
                await bus.publish_nowait(Channels.EXECUTION_UPDATE, result)
        return result

    async def cancel(self, record):
        submission = Submission(account_id=record.account_id, intent_id=record.signal_id,
            order_id=record.order_id or '', state=record.status.value)
        return await self.apply(await self.service.adapter(record.account_id).cancel_order(submission))

    async def tick(self):
        db = self.service.store._db
        async with db.execute("SELECT * FROM order_intents WHERE state='AWAITING_SIGNATURE' OR (state IN ('SUBMITTING','PENDING') AND next_check<=?) ORDER BY next_check LIMIT 30", (utc().timestamp(),)) as cur:
            rows = await cur.fetchall()
        for row in rows:
            intent = OrderIntent.model_validate_json(row['intent'])
            try:
                if row['state'] == 'AWAITING_SIGNATURE':
                    await db.execute('UPDATE order_intents SET next_check=? WHERE id=?', (utc().timestamp()+5, intent.id))
                    if intent.expires_at <= utc() or self.service.manager.mode.value != 'LIVE' or intent.account_id != self.service.active_id:
                        async with db.execute("SELECT 1 FROM wallet_jobs WHERE intent_id=? AND kind='order' AND state='SENDING'", (intent.id,)) as c:
                            if await c.fetchone():
                                continue
                        await db.execute("UPDATE wallet_jobs SET state='EXPIRED' WHERE intent_id=? AND state='WAITING'", (intent.id,))
                        await self.apply(Submission(account_id=intent.account_id, intent_id=intent.id, state='FAILED', error='签名等待已过期、已停止实盘或已切换钱包，未发送'))
                    continue
                record = await self.service.store.get_execution(intent.id)
                if record:
                    await self.reconcile(record)
                else:
                    await self.apply(Submission(account_id=intent.account_id, intent_id=intent.id, state='PENDING', error='进程在提交阶段中断，请核对交易所；禁止自动重发'))
                snapshot = await self.service.adapter(intent.account_id).read_account()
                await db.execute('INSERT INTO account_snapshots VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at',
                    (intent.account_id, snapshot.model_dump_json(), utc().isoformat()))
            except Exception:
                attempts = row['attempts']+1
                error = '回执或持仓同步暂时失败，后台退避重试；请核对交易所订单，禁止重复下单'
                await db.execute('UPDATE order_intents SET attempts=?,next_check=?,error=? WHERE id=?',
                    (attempts, utc().timestamp()+min(120, 5*2**min(attempts, 5)), error, intent.id))
                if attempts in (1, 3, 10):
                    await self.event(intent.account_id, intent.id, 'SYNC_ERROR', error)
                record = await self.service.store.get_execution(intent.id)
                if record and record.status == ExecutionStatus.PENDING:
                    record.error = error
                    await self.service.store.update_execution(record)
        if utc().timestamp() - self.last_snapshot >= 30:
            self.last_snapshot = utc().timestamp()
            # 即使成交立即确认，也同步平台实际持仓；模拟模式不接触未配置凭证。
            if self.service.manager.mode.value == 'LIVE':
                try:
                    snapshot = await self.service.adapter().read_account()
                    await db.execute('INSERT INTO account_snapshots VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at',
                        (snapshot.account_id, snapshot.model_dump_json(), utc().isoformat()))
                except Exception:
                    await self.event(self.service.active_id, '', 'SYNC_ERROR', '平台持仓同步失败，保留上次快照；不能视为空持仓')
        await db.commit()

    async def worker(self, desk):
        while True:
            try:
                await self.tick()
                await desk.recover()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 不记录 SDK 或中继异常原文，以免认证信息泄露。
                import logging
                logging.getLogger(__name__).warning('账户回执同步失败，将在下一周期继续')
            await asyncio.sleep(5)
