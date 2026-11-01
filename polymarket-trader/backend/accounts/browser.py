"""OKX 浏览器签名任务。服务端构建并校验原始数据，仅接受签名字符串。"""
import asyncio
import json
from datetime import timedelta
from backend.accounts.contracts import utc, Submission


class BrowserSigning:
    def __init__(self, service, coordinator):
        self.service, self.coordinator = service, coordinator
        self.lock = asyncio.Lock()

    async def challenge(self, account_id):
        adapter = self.service.adapter(account_id)
        if not adapter.capabilities.interactive_signing or not adapter.profile.address:
            raise ValueError('请先保存 OKX EOA 地址')
        timestamp = str(int(utc().timestamp()))
        typed = {'domain': {'name': 'ClobAuthDomain', 'version': '1', 'chainId': 137},
            'types': {'EIP712Domain': [{'name':'name','type':'string'}, {'name':'version','type':'string'}, {'name':'chainId','type':'uint256'}],
                'ClobAuth': [{'name':'address','type':'address'}, {'name':'timestamp','type':'string'}, {'name':'nonce','type':'uint256'}, {'name':'message','type':'string'}]},
            'primaryType': 'ClobAuth', 'message': {'address': adapter.profile.address, 'timestamp': timestamp,
                'nonce': '0', 'message': 'This message attests that I control the given wallet'}}
        return await adapter.job('auth', 'auth', {'typed_data': typed}, utc()+timedelta(seconds=90))

    async def complete(self, job_id, signature):
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        async with self.lock, self.service.manager._configuration_lock:
            db = self.service.store._db
            async with db.execute('SELECT * FROM wallet_jobs WHERE id=?', (job_id,)) as cur:
                row = await cur.fetchone()
            if not row or row['state'] != 'WAITING' or row['account_id'] != self.service.active_id:
                raise ValueError('签名任务不存在、已处理或钱包已切换')
            from datetime import datetime
            if datetime.fromisoformat(row['expires_at']) < utc():
                raise ValueError('签名任务已过期')
            p = self.service.profiles[row['account_id']]
            payload = json.loads(row['payload'])
            try:
                address = Account.recover_message(encode_typed_data(full_message=payload['typed_data']), signature=signature)
            except Exception:
                raise ValueError('钱包签名无效')
            if address.lower() != p.address.lower():
                raise ValueError('签名钱包与配置账户不一致')
            if row['kind'] == 'order' and self.service.manager.mode.value != 'LIVE':
                raise ValueError('实盘已停止，签名不会提交')
            adapter = self.service.adapter(p.id)
            if row['kind']=='order':
                await self.service.manager.check_submission(await adapter.signal(row['intent_id']))
            claimed = await db.execute("UPDATE wallet_jobs SET state='SENDING' WHERE id=? AND state='WAITING'", (job_id,))
            if claimed.rowcount != 1:
                raise ValueError('签名任务状态已改变，订单未发送')
            await db.commit()
            if row['kind'] == 'auth':
                import httpx
                headers = {'POLY_ADDRESS': p.address, 'POLY_SIGNATURE': signature,
                    'POLY_TIMESTAMP': payload['typed_data']['message']['timestamp'], 'POLY_NONCE': '0'}
                proxy = self.service.manager.settings.proxy
                async with httpx.AsyncClient(timeout=15, proxy=proxy.http or proxy.https or None, follow_redirects=False) as client:
                    r = await client.get('https://clob.polymarket.com/auth/derive-api-key', headers=headers)
                    if r.status_code == 404:
                        r = await client.post('https://clob.polymarket.com/auth/api-key', headers=headers)
                    r.raise_for_status()
                    data = r.json()
                if not all(data.get(k) for k in ('apiKey', 'secret', 'passphrase')):
                    raise ValueError('平台未返回完整 API 凭证，请重新连接')
                values = self.service.vault.get(p.id)
                values.update(api_key=data['apiKey'], api_secret=data['secret'], api_passphrase=data['passphrase'])
                await asyncio.to_thread(self.service.vault.put, p.id, values)
                adapter.engine.reset()
                self.service.hydrate(self.service.manager.settings)
                adapter.online_until = utc()+timedelta(seconds=35)
                self.service.manager.live_connection.invalidate()
            else:
                record = await self.service.store.get_execution(row['intent_id'])
                if not record or record.account_id != p.id:
                    raise ValueError('未找到原账户订单')
                if row['kind'] == 'cancel':
                    record = await adapter.engine.cancel(record)
                    await self.coordinator.apply(adapter.submission(record))
                else:
                    from backend.accounts.clob_signing import SignedOrder
                    from py_clob_client.clob_types import OrderType
                    signed = SignedOrder(payload['typed_data'], signature)
                    record.order_id = payload['order_hash']
                    record.error = '签名已验证，提交结果待核对；禁止重复发送'
                    await self.service.store.update_execution(record)
                    sub = Submission(account_id=p.id, intent_id=record.signal_id, order_id=record.order_id, state='PENDING', error=record.error)
                    await self.coordinator.apply(sub)
                    # 从这里开始任何超时都不重试 POST；用已持久化订单哈希查询回执。
                    try:
                        response = await asyncio.to_thread(adapter.engine._clob_client.post_order, signed, OrderType.FOK)
                        if response.get('orderID'):
                            sub.order_id = response['orderID']
                        if response.get('success') is False:
                            sub.state, sub.error = 'FAILED', '交易所拒绝订单，请检查资金、授权、价格与流动性'
                        await self.coordinator.apply(sub)
                    except Exception:
                        await self.coordinator.apply(sub)
            await db.execute("UPDATE wallet_jobs SET state='DONE' WHERE id=?", (job_id,))
            await db.commit()
            return {'ok': True}
