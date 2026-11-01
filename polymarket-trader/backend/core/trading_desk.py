"""交易台：审核队列、盘口校验、持仓台账和交易质量统计。"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict
from backend.models.schemas import FollowSignal, MarketRef, TradeEvent, TradeSide, ExecutionMode


def now():
    return datetime.now(timezone.utc)


class DeskPolicy(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    workflow: Literal['observe', 'review', 'auto'] = 'review'
    max_age_seconds: int = Field(default=60, ge=5, le=3600)
    max_price_deviation: float = Field(default=.02, ge=0, le=.5)
    max_spread: float = Field(default=.05, gt=0, le=1)
    min_depth_usdc: float = Field(default=20, ge=0, le=1000000)
    max_event_exposure: float = Field(default=200, gt=0, le=1000000)
    max_daily_loss: float = Field(default=50, gt=0, le=1000000)
    max_drawdown: float = Field(default=100, gt=0, le=1000000)
    fee_bps: float = Field(default=20, ge=0, le=1000)
    exits_enabled: bool = False
    follow_specialties_only: bool = False
    stop_loss_pct: float = Field(default=.10, gt=0, le=1)
    take_profit_pct: float = Field(default=.20, gt=0, le=10)


class ManualOrder(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    token_id: str = Field(pattern=r'^\d+$', min_length=1, max_length=100)
    condition_id: str = Field(default='', max_length=100)
    title: str = Field(default='', max_length=300)
    sector: str = Field(default='未分类', max_length=40)
    side: TradeSide
    price: float = Field(gt=0, lt=1)
    size: float = Field(gt=0, le=1000000)
    client_order_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,80}$')


def infer_sector(title):
    text = title.lower()
    groups = {
        '加密货币': ('bitcoin', 'ethereum', 'crypto', 'btc', ' eth ', 'solana'),
        '体育': (' vs ', ' vs.', 'nba', 'nfl', 'tennis', 'open:', 'spread:', 'o/u', 'world cup', 'premier league'),
        '政治': ('election', 'president', 'trump', 'senate', 'democrat', 'republican', 'congress'),
        '经济': ('fed ', 'inflation', 'gdp', 'interest rate', 'recession', 'cpi'),
    }
    return next((sector for sector, words in groups.items() if any(w in text for w in words)), '未分类')


class TradingDesk:
    def __init__(self, store, manager, client):
        self.store, self.manager, self.client = store, manager, client
        self.lock = asyncio.Lock()
        self.metadata = {}
        self.feed_cache = (0, [])
        self.market_data = None

    @property
    def db(self):
        return self.store._db

    async def policy(self):
        return DeskPolicy(**(await self.store.get_config('desk_policy') or {}))

    async def set_policy(self, policy):
        await self.store.set_config('desk_policy', policy.model_dump())
        return policy

    async def queue(self, limit=200):
        async with self.db.execute('SELECT * FROM desk_queue ORDER BY created_at DESC LIMIT ?', (limit,)) as cur:
            rows = await cur.fetchall()
        policy = await self.policy()
        result = []
        for row in rows:
            signal = json.loads(row['signal'])
            age = (now()-datetime.fromisoformat(signal['source_trade']['timestamp'])).total_seconds()
            result.append({**dict(row), 'signal': signal,
                           'expired': row['status'] == 'waiting' and age > policy.max_age_seconds})
        return result

    async def get_item(self, signal_id):
        async with self.db.execute('SELECT * FROM desk_queue WHERE signal_id=?', (signal_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            raise ValueError('审核记录不存在')
        return dict(row)

    async def mark(self, signal_id, status, reason=''):
        await self.db.execute('UPDATE desk_queue SET status=?, reason=? WHERE signal_id=?', (status, reason, signal_id))
        await self.db.commit()

    async def enrich(self, signal):
        market = signal.market
        if market.token_id.startswith('sim_token_'):
            return
        key = market.condition_id or market.slug or market.token_id
        if key not in self.metadata:
            try:
                data = await self.client.get_market_by_condition_id(market.condition_id) if market.condition_id else {}
                events = data.get('events') or []
                event = events[0] if events else {}
                self.metadata[key] = (str(event.get('id') or event.get('slug') or market.condition_id or market.token_id), data.get('question') or market.title)
            except Exception:
                self.metadata[key] = (market.condition_id or market.token_id, market.title)
        event_key, title = self.metadata[key]
        market.event_key = market.event_key or event_key
        market.title = market.title or title
        if market.sector == '未分类':
            market.sector = infer_sector(title)

    async def manual_signal(self, request):
        market = MarketRef(token_id=request.token_id, condition_id=request.condition_id,
                           title=request.title, sector=request.sector)
        # 每个委托由服务端重新解析 token 对应的真实市场，不能通过假 condition 绕过事件限额。
        if not request.condition_id:
            book = await self.client.get_orderbook(request.token_id)
            market.condition_id = str(book.get('market') or '')
        trade = TradeEvent(wallet='manual', condition_id=market.condition_id, token_id=request.token_id,
                           market_title=request.title, side=request.side, price=request.price, size=request.size)
        signal = FollowSignal(source_trade=trade, pipeline_id='manual', market=market, side=request.side,
                              price=request.price, size=request.size, signal_id='manual-' + request.client_order_id)
        await self.enrich(signal)
        return signal

    async def manual(self, request):
        return await self.submit(await self.manual_signal(request), force_review=True)

    async def submit(self, signal, force_review=False, execution_workflow=None):
        async with self.lock:
            await self.enrich(signal)
            mode = self.manager.mode.value
            if mode == 'LIVE' and hasattr(self.manager, 'accounts'):
                if signal.expected_mode is not None and signal.account_id != self.manager.accounts.active_id:
                    raise ValueError('跟单规则绑定的钱包未选中，请切回该账户')
                signal.account_id = self.manager.accounts.active_id
            if signal.expected_mode is not None and signal.expected_mode.value != mode:
                raise ValueError('规则绑定账户与当前模式不符')
            policy = await self.policy()
            workflow = 'observe' if policy.workflow=='observe' else execution_workflow or policy.workflow
            status = 'waiting' if force_review or workflow != 'observe' else 'observed'
            cur = await self.db.execute('INSERT OR IGNORE INTO desk_queue VALUES (?,?,?,?,?,?)',
                (signal.signal_id, mode, status, signal.model_dump_json(), '', now().isoformat()))
            await self.db.commit()
            if cur.rowcount == 0:
                previous = await self.get_item(signal.signal_id)
                old = FollowSignal.model_validate_json(previous['signal'])
                if (old.market.token_id, old.side, old.price, old.size, previous['mode']) != (signal.market.token_id, signal.side, signal.price, signal.size, mode):
                    raise ValueError('同一委托标识不能修改方向、数量、价格或账户模式')
                return previous
            await self.store.save_signal(signal)
            if not force_review and workflow == 'auto':
                await self._approve(signal.signal_id, automatic=True)
            return await self.get_item(signal.signal_id)

    async def reject(self, signal_id):
        async with self.lock:
            row = await self.get_item(signal_id)
            if row['status'] != 'waiting':
                raise ValueError('此信号已处理，不能重复操作')
            await self.mark(signal_id, 'rejected', '用户拒绝')
        return {'ok': True}

    async def approve(self, signal_id):
        async with self.lock:
            return await self._approve(signal_id)

    async def quote(self, signal):
        policy = await self.policy()
        age = (now() - signal.source_trade.timestamp).total_seconds()
        if age > policy.max_age_seconds or age < -10:
            raise ValueError('信号过期，请根据最新行情重新创建委托')
        if not (math.isfinite(signal.price) and math.isfinite(signal.size) and 0 < signal.price < 1 and signal.size > 0):
            raise ValueError('价格或数量无效')
        if self.manager.mode.value == 'PAPER' and signal.market.token_id.startswith('sim_token_'):
            return {'price': signal.price, 'best': signal.price, 'spread': 0, 'depth_usdc': 1000000, 'simulated': True}
        book = await self.market_data.book(signal.market.token_id) if self.market_data else await self.client.get_orderbook(signal.market.token_id)
        if not book or book.get('error'):
            raise ValueError('无法取得有效盘口，已阻止下单')
        if book.get('timestamp'):
            stamp = float(book['timestamp'])
            stamp = stamp / 1000 if stamp > 1e12 else stamp
            if now().timestamp() - stamp > policy.max_age_seconds or stamp - now().timestamp() > 10:
                raise ValueError('订单簿时间过期或异常，已阻止下单')
        actual_condition = str(book.get('market') or '')
        if actual_condition:
            if signal.market.condition_id and signal.market.condition_id != actual_condition:
                raise ValueError('Token 与市场不匹配，请重新选择交易标的')
            signal.market.condition_id = actual_condition
        def levels(name):
            return [(float(x['price']), float(x['size'])) for x in book.get(name, [])
                    if 0 < float(x['price']) < 1 and float(x['size']) > 0
                    and math.isfinite(float(x['size']))]
        asks, bids = sorted(levels('asks')), sorted(levels('bids'), reverse=True)
        if not asks or not bids:
            raise ValueError('买卖盘口缺失，无法执行')
        spread = asks[0][0] - bids[0][0]
        if spread < 0 or spread > policy.max_spread:
            raise ValueError('买卖价差超过限制或盘口异常')
        buy = signal.side == TradeSide.BUY
        side_levels = asks if buy else bids
        best = side_levels[0][0]
        adverse = best - signal.source_trade.price if buy else signal.source_trade.price - best
        if adverse > policy.max_price_deviation + 1e-9:
            raise ValueError('相比源交易的不利价格偏离过大，已跳过追价')
        eligible = [(p, q) for p, q in side_levels if (p <= signal.price + 1e-9 if buy else p >= signal.price - 1e-9)]
        depth = sum(p*q for p, q in eligible)
        if depth < policy.min_depth_usdc:
            raise ValueError('限价内盘口深度不足')
        remaining, cash = signal.size, 0.0
        for price, quantity in eligible:
            take = min(quantity, remaining)
            cash += price * take
            remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            raise ValueError('限价内数量不足，FOK 无法全部成交')
        minimum = float(book.get('min_order_size') or 0)
        if signal.size < minimum:
            raise ValueError(f'数量低于市场最小委托量 {minimum:g}')
        return {'price': cash / signal.size, 'best': best, 'spread': spread, 'depth_usdc': depth, 'simulated': False,
                'min_order_size': minimum, 'price_source': book.get('_source', 'polymarket_clob_rest')}

    async def _approve(self, signal_id, automatic=False):
        row = await self.get_item(signal_id)
        if row['status'] != 'waiting':
            raise ValueError('此信号已处理，不能重复下单')
        if row['mode'] != self.manager.mode.value:
            raise ValueError('执行模式已变更，请拒绝旧信号并重新创建委托')
        signal = FollowSignal.model_validate_json(row['signal'])
        if row['mode'] == 'LIVE' and hasattr(self.manager, 'accounts') and signal.account_id != self.manager.accounts.active_id:
            raise ValueError('审核信号属于另一个钱包，请切回原账户')
        try:
            if signal.pipeline_id.startswith('copy_'):
                from backend.core.copy_rules import get_rules, rule_quantity
                rule = next((r for r in await get_rules(self.store) if r.id == signal.pipeline_id), None)
                if not rule or not rule.enabled or rule.mode != row['mode']:
                    raise ValueError('跟单规则已暂停或账户模式不符')
                if signal.side == TradeSide.SELL and signal.size > await rule_quantity(self, rule, signal.market.token_id) + 1e-8:
                    raise ValueError('本规则可卖份额不足')
            quote = await self.quote(signal)
            await self.enrich(signal)
            if row['mode'] != self.manager.mode.value:
                raise ValueError('执行模式已变更，禁止跨模式执行')
            state = await self.summary(mode=row['mode'])
            policy = await self.policy()
            strategy_auto = False
            if signal.pipeline_id.startswith('smart_'):
                if not hasattr(self, 'smart'): raise ValueError('智能策略服务未启动')
                strategy_auto = await self.smart.authorize(signal, automatic)
            if automatic and (policy.workflow == 'observe' or not (strategy_auto or policy.workflow == 'auto')):
                await self.mark(signal_id, 'waiting', '自动执行已暂停，转为待审核')
                return {'status': 'WAITING', 'error': '自动执行已暂停'}
            if signal.side == TradeSide.BUY:
                if row['mode'] == 'PAPER':
                    required = signal.price * signal.size * (1 + policy.fee_bps/10000)
                    if required > state['cash_balance'] + 1e-8:
                        raise ValueError(f'虚拟资金不足：可用 {state["cash_balance"]:.2f} USDC，含估算费用需 {required:.2f}')
                    async with self.db.execute('SELECT 1 FROM paper_settlements WHERE token_id=?', (signal.market.token_id,)) as cur:
                        if await cur.fetchone():
                            raise ValueError('该市场已按平台最终结果结算，不能重新买入')
                if policy.follow_specialties_only and signal.pipeline_id != 'manual':
                    profile = await self.store.get_config('wallet_profile:' + signal.source_trade.wallet.lower()) or {}
                    if signal.market.sector not in profile.get('specialties', []):
                        raise ValueError('该交易板块未列入此钱包的擅长板块，已跳过买入')
                if state['daily_net_pnl_estimate'] <= -policy.max_daily_loss:
                    raise ValueError('已触发每日亏损熔断，停止新增买入；允许卖出减仓')
                if state['drawdown'] >= policy.max_drawdown:
                    raise ValueError('已触发已实现收益回撤熔断，停止新增买入')
                exposure = sum(p['cost'] for p in state['positions'] if p['event_key'] == signal.market.event_key)
                if exposure + signal.price * signal.size > policy.max_event_exposure:
                    raise ValueError('同一事件合计投入超过上限（包含多个来源钱包）')
            else:
                if self.manager.mode.value == 'LIVE':
                    if hasattr(self.manager, 'accounts'):
                        snapshot = await self.manager.accounts.adapter().read_account()
                        available = sum(float(p.get('size', 0)) for p in snapshot.positions if str(p.get('asset', p.get('token_id', ''))) == signal.market.token_id)
                    else:
                        available = await self.manager._live.token_balance(signal.market.token_id)
                else:
                    available = sum(p['quantity'] for p in state['positions'] if p['token_id'] == signal.market.token_id)
                if signal.size > available + 1e-8:
                    raise ValueError(f'可卖份额不足，当前可卖 {available:.4f}')
            # 与全局风控使用相同的原始委托量，避免组合缩放后的记账不一致。
            signal.quoted_price = quote['price']
            signal.quote_source = quote.get('price_source', 'paper_selfcheck' if quote.get('simulated') else '')
            signal.min_order_size = quote.get('min_order_size', 0)
            signal.expected_mode = ExecutionMode(row['mode'])
            self.manager.settings.executor.max_signal_age_seconds = policy.max_age_seconds
            if row['mode'] == 'PAPER' and self.manager.mode.value == 'PAPER':
                self.manager.risk._positions = dict(state['cost_by_condition'])
                self.manager.risk._daily_count = state['daily_trade_count']
                self.manager.risk._daily_date = now().date().isoformat()
                self.manager.allocator._exposure = dict(state['exposure_by_wallet'])
            if (now()-signal.source_trade.timestamp).total_seconds() > policy.max_age_seconds:
                raise ValueError('信号在检查期间过期，请重新创建委托')
            await self.store.save_signal(signal)
            await self.db.execute('UPDATE desk_queue SET signal=? WHERE signal_id=?', (signal.model_dump_json(), signal_id))
            await self.mark(signal_id, 'executing', '已提交执行，不可重复审批')
            record = await self.manager.execute(signal)
            await self.capture(signal, record, policy)
            await self.mark(signal_id, record.status.value.lower(), record.error)
            try:
                from backend.api.events import get_event_bus, Channels
                await get_event_bus().publish_nowait(Channels.EXECUTION_UPDATE, record)
            except Exception:
                pass  # 推送故障不能把已执行订单重新变为可提交状态。
            return record.model_dump(mode='json')
        except ValueError as exc:
            await self.mark(signal_id, 'blocked', str(exc))
            return {'status': 'BLOCKED', 'error': str(exc)}
        except Exception:
            # 已开始执行的信号不能恢复为 waiting，以免重复提交。
            current = await self.get_item(signal_id)
            if current['status'] == 'waiting':
                await self.mark(signal_id, 'blocked', '检查失败，请确认网络和行情后重新创建信号')
            raise

    async def capture(self, signal, record, policy=None):
        if record.status.value not in ('FILLED', 'PARTIAL') or record.fill_price is None or not record.fill_size:
            return
        policy = policy or await self.policy()
        same_account = not hasattr(self.manager, 'accounts') or record.account_id == self.manager.accounts.active_id
        before = await self.summary() if signal.side == TradeSide.SELL and record.mode == self.manager.mode and same_account else None
        async with self.db.execute('SELECT size FROM desk_fills WHERE signal_id=?', (signal.signal_id,)) as old_cur:
            old_fill = await old_cur.fetchone()
        delta = max(0, record.fill_size - (old_fill['size'] if old_fill else 0))
        cursor = await self.db.execute('''INSERT INTO desk_fills VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(signal_id) DO UPDATE SET price=excluded.price,size=excluded.size
            WHERE excluded.size>=desk_fills.size''',
            (signal.signal_id, record.mode.value, signal.model_dump_json(), record.fill_price,
             record.fill_size, policy.fee_bps, record.timestamp.isoformat()))
        await self.db.commit()
        if cursor.rowcount and delta and before and signal.side == TradeSide.SELL:
            position = next((p for p in before['positions'] if p['token_id'] == signal.market.token_id), None)
            released = min(delta, position['quantity']) * position['avg_entry'] if position else 0
            if record.mode.value == 'LIVE':
                state = await self.store.get_config(self.manager.risk_key) or {}
                positions = state.get('positions', {})
                key = signal.market.condition_id
                positions[key] = max(0, positions.get(key, 0)-released)
                remaining = released
                exposure = state.get('portfolio_exposure', {})
                for wallet in exposure:
                    amount = min(exposure[wallet], remaining)
                    exposure[wallet] -= amount
                    remaining -= amount
                state.update(positions=positions, portfolio_exposure=exposure)
                await self.store.set_config(self.manager.risk_key, state)
            else:
                self.manager.risk.update_position(signal.market.condition_id, -released)
                for wallet, amount in self.manager.allocator.wallet_exposure().items():
                    take = min(amount, released)
                    self.manager.allocator.release_exposure(wallet, take)
                    released -= take

    async def recover(self):
        await self.sync_settled_legacy_positions()
        # 崩溃后只补记已确认的成交，不重新发送任何委托。
        async with self.db.execute('SELECT q.signal_id,q.signal FROM desk_queue q WHERE EXISTS (SELECT 1 FROM executions e WHERE e.signal_id=q.signal_id)') as cur:
            recovery_rows = await cur.fetchall()
        for row in recovery_rows:
            record = await self.store.get_execution(row['signal_id'])
            if record:
                await self.capture(FollowSignal.model_validate_json(row['signal']), record)
                await self.mark(row['signal_id'], record.status.value.lower(), record.error)

    async def summary(self, mode=None):
        mode = mode or self.manager.mode.value
        async with self.db.execute('SELECT * FROM desk_fills WHERE mode=? ORDER BY created_at, rowid', (mode,)) as cur:
            rows = await cur.fetchall()
        lots, stats = defaultdict(list), {}
        gross = fees = benchmark = daily = equity = peak = drawdown = 0.0
        unmatched_sells = 0
        cash_delta = 0.0
        daily_trade_count = 0
        journal = []
        for raw in rows:
            r = dict(raw)
            s = FollowSignal.model_validate_json(r['signal'])
            if mode == 'LIVE' and hasattr(self.manager, 'accounts') and s.account_id != self.manager.accounts.active_id:
                continue
            token, price, size = s.market.token_id, r['price'], r['size']
            fee = price * size * r['fee_bps'] / 10000
            settlement = s.pipeline_id == 'paper-settlement'
            cash_delta += (-price*size if s.side == TradeSide.BUY else price*size)-fee
            if r['created_at'][:10] == now().date().isoformat() and not settlement:
                daily_trade_count += 1
            fees += fee
            realized = reference = 0.0
            if s.side == TradeSide.BUY:
                lots[token].append({'quantity': size, 'entry': price, 'source_entry': s.source_trade.price,
                    'wallet': s.source_trade.wallet, 'sector': s.market.sector, 'event_key': s.market.event_key or s.market.condition_id,
                    'condition_id': s.market.condition_id, 'title': s.market.title, 'opened_at': r['created_at'], 'fee_bps': r['fee_bps']})
            else:
                remaining = size
                for lot in lots[token]:
                    take = min(lot['quantity'], remaining)
                    if take <= 0:
                        continue
                    pnl = (price - lot['entry']) * take
                    base = (s.source_trade.price - lot['source_entry']) * take
                    realized += pnl
                    reference += base
                    key = (lot['wallet'], lot['sector'])
                    stat = stats.setdefault(key, {'wallet': key[0], 'sector': key[1], 'closed_lots': 0, 'wins': 0, 'gross_pnl': 0., 'benchmark_pnl': 0., 'hold_seconds': 0.})
                    stat['closed_lots'] += 1
                    stat['wins'] += int(pnl > 0)
                    stat['gross_pnl'] += pnl
                    stat['benchmark_pnl'] += base
                    stat['fee_estimate'] = stat.get('fee_estimate', 0) + (lot['entry'] * lot.get('fee_bps', 0) + price * r['fee_bps']) * take / 10000
                    stat['hold_seconds'] += max(0, (datetime.fromisoformat(r['created_at']) - datetime.fromisoformat(lot['opened_at'])).total_seconds())
                    lot['quantity'] -= take
                    remaining -= take
                if remaining > 1e-8:
                    unmatched_sells += 1
            gross += realized
            benchmark += reference
            equity += realized - fee
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
            if r['created_at'][:10] == now().date().isoformat():
                daily += realized-fee
            journal.append({'signal_id': s.signal_id, 'mode': mode, 'wallet': s.source_trade.wallet,
                'title': s.market.title, 'token_id': token, 'side': s.side.value, 'price': price, 'size': size,
                'source_price': s.source_trade.price, 'fee_estimate': fee, 'realized_gross_pnl': realized,
                'kind': 'settlement' if settlement else 'trade', 'price_source': s.quote_source,
                'adverse_slippage': (price-s.source_trade.price) * (1 if s.side == TradeSide.BUY else -1), 'timestamp': r['created_at']})
        positions = []
        cost_by_condition = defaultdict(float)
        exposure_by_wallet = defaultdict(float)
        for token, token_lots in lots.items():
            active = [lot for lot in token_lots if lot['quantity'] > 1e-8]
            if not active:
                continue
            quantity = sum(lot['quantity'] for lot in active)
            cost = sum(lot['quantity'] * lot['entry'] for lot in active)
            p = active[0]
            for lot in active:
                cost_by_condition[lot['condition_id']] += lot['quantity'] * lot['entry']
                exposure_by_wallet[lot['wallet']] += lot['quantity'] * lot['entry']
            positions.append({**p, 'token_id': token, 'quantity': quantity, 'cost': cost, 'avg_entry': cost/quantity})
        for stat in stats.values():
            stat['net_pnl_estimate'] = stat['gross_pnl'] - stat.get('fee_estimate', 0)
            stat['win_rate'] = stat['wins'] / stat['closed_lots']
            stat['avg_hold_seconds'] = stat['hold_seconds'] / stat['closed_lots']
        initial = await self.store.get_config('paper_initial_balance')
        initial = 10000.0 if initial is None else float(initial)
        return {'mode': mode, 'gross_realized_pnl': gross, 'fee_estimate': fees,
            'initial_balance': initial if mode == 'PAPER' else None,
            'cash_balance': initial + cash_delta if mode == 'PAPER' else None,
            'cost_by_condition': dict(cost_by_condition), 'exposure_by_wallet': dict(exposure_by_wallet),
            'daily_trade_count': daily_trade_count,
            'net_pnl_estimate': gross-fees, 'benchmark_pnl': benchmark, 'daily_net_pnl_estimate': daily,
            'drawdown': drawdown, 'positions': positions, 'wallet_sectors': list(stats.values()),
            'journal': journal[-100:][::-1], 'unmatched_sells': unmatched_sells,
            'scope': '本交易台成交及平台最终结果确认的模拟结算；FIFO 配对，费用按设置费率估算；不含交易台外操作。浮动收益另按真实买盘估值，日期按UTC。'}

    async def set_paper_capital(self, amount):
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError('虚拟初始资金必须为正数')
        async with self.lock:
            async with self.db.execute("SELECT 1 FROM desk_fills WHERE mode='PAPER' LIMIT 1") as cur:
                if await cur.fetchone():
                    raise ValueError('已有模拟成交，不能修改初始资金以覆盖历史盈亏')
            await self.store.set_config('paper_initial_balance', amount)
        return {'initial_balance': amount}

    async def current_bid(self, token):
        if self.market_data:
            return (await self.market_data.mark(token))['price']
        return await self.client.get_best_price(token, side='SELL')

    async def account_snapshot(self, mode=None):
        state = await self.summary(mode=mode)
        marked, value, floating, missing = [], 0.0, 0.0, 0
        semaphore = asyncio.Semaphore(8)
        async def fetch_mark(p):
            async with semaphore:
                try:
                    async def get_quote():
                        return await self.market_data.mark(p['token_id']) if self.market_data else {
                            'price': await self.current_bid(p['token_id']), 'source': 'polymarket_clob_rest', 'checked_at': now().isoformat()}
                    return await asyncio.wait_for(get_quote(), 4)
                except Exception:
                    return {'price': None, 'source': 'unavailable'}
        quotes = await asyncio.gather(*(fetch_mark(p) for p in state['positions']))
        for p, quote in zip(state['positions'], quotes):
            price = quote['price']
            if price is None:
                missing += 1
            else:
                value += price * p['quantity']
                floating += (price-p['avg_entry']) * p['quantity']
            marked.append({**p, 'mark_price': price, 'mark_source': quote.get('source'),
                'floating_pnl': (price-p['avg_entry'])*p['quantity'] if price is not None else None})
        state.update(positions=marked, marks_missing=missing, market_value=None if missing else value,
                     floating_pnl=None if missing else floating,
                     equity=None if missing or state['cash_balance'] is None else state['cash_balance']+value)
        if state['mode'] == 'LIVE':
            try:
                if hasattr(self.manager, 'accounts'):
                    account = await self.manager.accounts.adapter().read_account()
                    state['cash_balance'] = account.balance_usdc
                    state['platform_positions'] = account.positions
                    state['account_id'] = account.account_id
                    state['platform_observed_at'] = account.observed_at.isoformat()
                else:
                    state['cash_balance'] = (await self.manager._live.preflight())['balance_usdc']
                state['equity'] = None if missing else state['cash_balance']+value
            except Exception:
                state['balance_error'] = '实盘余额暂不可用，请检查凭证与网络'
        async with self.db.execute('SELECT * FROM paper_settlements ORDER BY created_at DESC LIMIT 100') as cur:
            rows = await cur.fetchall()
        state['paper_settlements'] = [{k: r[k] for k in ('token_id', 'condition_id', 'price', 'quantity', 'source', 'created_at')} for r in rows] if state['mode'] == 'PAPER' else []
        return state

    async def settle_paper(self, resolution):
        """只改变 PAPER 台账，最终结果再次查询确认后按平台支付比例入账。"""
        if resolution.get('final') is not True or resolution.get('source') not in ('polymarket_clob_final_winner', 'polymarket_gamma_resolved'):
            return 0
        payouts = resolution.get('payouts', {})
        if not payouts or any(not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in payouts.values()):
            return 0
        count = 0
        async with self.lock:
            state = await self.summary(mode='PAPER')
            for p in state['positions']:
                if p['condition_id'] != resolution.get('condition_id') or p['token_id'] not in payouts:
                    continue
                price = float(payouts[p['token_id']])
                stamp = now().isoformat()
                trade = TradeEvent(wallet=p['wallet'], condition_id=p['condition_id'], token_id=p['token_id'],
                    market_title=p['title'], side=TradeSide.SELL, price=price, size=p['quantity'])
                signal_id = 'settlement-' + p['token_id']
                signal = FollowSignal(source_trade=trade, pipeline_id='paper-settlement',
                    market=MarketRef(condition_id=p['condition_id'], token_id=p['token_id'], title=p['title'],
                                     sector=p['sector'], event_key=p['event_key']),
                    side=TradeSide.SELL, price=price, size=p['quantity'], signal_id=signal_id,
                    expected_mode=ExecutionMode.PAPER, quote_source=resolution['source'])
                cursor = await self.db.execute('INSERT OR IGNORE INTO paper_settlements VALUES (?,?,?,?,?,?,?,?,?)',
                    (signal_id, p['token_id'], p['condition_id'], price, p['quantity'], resolution['source'],
                     json.dumps(resolution), signal.model_dump_json(), stamp))
                await self.db.commit()
                if cursor.rowcount:
                    count += 1
                    if self.manager.mode.value == 'PAPER':
                        self.manager.risk.update_position(p['condition_id'], -p['cost'])
                    from backend.api.events import get_event_bus
                    await get_event_bus().publish_nowait('settlement_update', {'signal_id': signal_id, 'token_id': p['token_id'], 'price': price, 'quantity': p['quantity']})
        await self.sync_settled_legacy_positions()
        return count

    async def sync_settled_legacy_positions(self):
        from backend.core.paper_positions import VirtualPositionTracker
        tracker = VirtualPositionTracker(self.store, self.client)
        async with self.db.execute('SELECT token_id,price FROM paper_settlements') as cur:
            rows = await cur.fetchall()
        for r in rows:
            pos = await self.store.get_virtual_position('desk-account', r['token_id'])
            if pos and pos.net_qty > 1e-8:
                await tracker.apply_fill('desk-account', r['token_id'], TradeSide.SELL, r['price'], pos.net_qty)

    async def source_matrix(self):
        trades = sorted(await self.store.get_trades(limit=10000), key=lambda t: t.timestamp)
        inventory, rows = defaultdict(list), {}
        for trade in trades:
            sector = infer_sector(trade.market_title)
            key = (trade.wallet, sector)
            row = rows.setdefault(key, {'wallet': trade.wallet, 'sector': sector, 'samples': 0,
                'matched_closes': 0, 'wins': 0, 'realized_sample_pnl': 0., 'missing_basis_sells': 0, 'volume': 0.})
            row['samples'] += 1
            row['volume'] += trade.price * trade.size
            token = (trade.wallet, trade.token_id)
            if trade.side == TradeSide.BUY:
                inventory[token].append([trade.size, trade.price, trade.timestamp])
            else:
                remaining, pnl = trade.size, 0.
                matched = 0.
                weighted_hold = 0.
                for lot in inventory[token]:
                    take = min(remaining, lot[0])
                    pnl += (trade.price-lot[1])*take
                    remaining -= take
                    lot[0] -= take
                    matched += take
                    weighted_hold += max(0, (trade.timestamp-lot[2]).total_seconds())*take
                if matched > 0:
                    row['matched_closes'] += 1
                    row['wins'] += int(pnl > 0)
                    row['realized_sample_pnl'] += pnl
                    row['hold_seconds'] = row.get('hold_seconds', 0) + weighted_hold/matched
                    row['avg_hold_seconds'] = row['hold_seconds']/row['matched_closes']
                    row['peak'] = max(row.get('peak', 0), row['realized_sample_pnl'])
                    row['max_drawdown'] = max(row.get('max_drawdown', 0), row['peak']-row['realized_sample_pnl'])
                if remaining > 1e-8:
                    row['missing_basis_sells'] += 1
        return {'rows': list(rows.values()), 'scope': '最近最多10000笔已监测成交；板块为标题关键词推断；只配对样本内买卖，不含历史成本、赎回及钱包全量收益，不能当作盈利认证。'}

    async def platform_feed(self):
        if self.market_data:
            return await self.market_data.recent()
        if now().timestamp() - self.feed_cache[0] < 3:
            return self.feed_cache[1]
        response = await self.client._request_with_retry('GET', 'https://data-api.polymarket.com/trades', params={'limit': 100})
        data = response.json()
        items = data if isinstance(data, list) else data.get('data', [])
        trades = []
        for raw in items or []:
            trade = self.client._parse_trade(raw, str(raw.get('proxyWallet') or raw.get('proxy_wallet') or ''))
            if trade:
                trades.append(trade.model_dump(mode='json'))
        self.feed_cache = (now().timestamp(), trades)
        return trades

    async def evaluate_exits(self):
        policy = await self.policy()
        if not policy.exits_enabled or policy.workflow == 'observe':
            return
        state = await self.summary()
        pending = [r for r in await self.queue(limit=1000000) if r['mode'] == self.manager.mode.value
                   and r['status'] in ('waiting', 'executing', 'pending') and not r['expired']]
        for p in state['positions']:
            if any(r['signal']['market']['token_id'] == p['token_id'] for r in pending):
                continue
            price = await self.current_bid(p['token_id'])
            if not price or price <= 0 or price >= 1:
                continue
            change = price / p['avg_entry'] - 1
            if change > -policy.stop_loss_pct and change < policy.take_profit_pct:
                continue
            account_id = self.manager.accounts.active_id if hasattr(self.manager, 'accounts') else 'legacy'
            cooldown_key = (f'desk_exit:LIVE:{account_id}:{p["token_id"]}' if self.manager.mode.value == 'LIVE'
                            else f'desk_exit:PAPER:{p["token_id"]}')
            last = await self.store.get_config(cooldown_key) or 0
            if now().timestamp() - last < 60:
                continue
            await self.store.set_config(cooldown_key, now().timestamp())
            quantity = min(p['quantity'], self.manager.settings.risk.max_trade_amount / price)
            # 当前 bid 为最低可接受卖价，提交前仍需复核盘口深度与持仓。
            trade = TradeEvent(wallet=p['wallet'], condition_id=p['condition_id'], token_id=p['token_id'],
                market_title=p['title'], side=TradeSide.SELL, price=price, size=quantity)
            signal = FollowSignal(source_trade=trade, pipeline_id='risk-exit',
                market=MarketRef(condition_id=p['condition_id'], token_id=p['token_id'], title=p['title'], sector=p['sector'], event_key=p['event_key']),
                side=TradeSide.SELL, price=price, size=quantity, signal_id='exit-' + uuid.uuid4().hex)
            await self.submit(signal)

    async def exit_worker(self):
        while True:
            await asyncio.sleep(5)
            try:
                await self.evaluate_exits()
                await self.store.set_config('desk_exit_error', None)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.store.set_config('desk_exit_error', '止盈止损检查暂时失败，请检查网络和盘口；未自动重试下单')
                continue
