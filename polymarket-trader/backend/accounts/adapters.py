"""独立接入实现；不包含跟单倍数、选市场或仓位策略。"""
import asyncio
import json
from datetime import timedelta
from backend.accounts.contracts import *
from backend.core.live_clob import LiveExecutor
from backend.models.schemas import ExecutionRecord, ExecutionMode, ExecutionStatus, FollowSignal


class HmacAdapter:
    capabilities = Capabilities(description='EVM 私钥签名 + CLOB HMAC，可后台执行')

    def __init__(self, service, profile):
        self.service, self.profile = service, profile
        settings = service.manager.settings.model_copy(deep=True)
        c = settings.polymarket
        values = service.vault.get(profile.id)
        for key in ('private_key', 'api_key', 'api_secret', 'api_passphrase'):
            setattr(c, key, values.get(key, ''))
        c.wallet_address, c.signature_type, c.funder = profile.address, profile.signature_type, profile.funder
        self.engine = LiveExecutor(settings, service.store, service.manager.client)
        self.engine.submission_guard = service.manager.check_submission

    async def check_connection(self):
        try:
            data = await self.engine.preflight()
            return ConnectionStatus(account_id=self.profile.id, capabilities=self.capabilities,
                                    **{k: data[k] for k in ('ready', 'authenticated', 'balance_usdc', 'allowance_ready')})
        except Exception:
            return ConnectionStatus(account_id=self.profile.id, capabilities=self.capabilities,
                                    error='认证失败：请检查账户凭证、签名类型、Funder、余额授权及网络')

    async def read_account(self):
        state = await self.check_connection()
        if not state.authenticated:
            raise ValueError(state.error)
        address = self.profile.funder or self.profile.address
        if not address and self.engine.settings.polymarket.private_key:
            from eth_account import Account
            address = Account.from_key(self.engine.settings.polymarket.private_key).address
        # 使用严格请求，不能把网络错误伪装成空持仓；分页直到完成。
        rows, offset = [], 0
        while True:
            response = await self.service.manager.client._request_with_retry('GET',
                'https://data-api.polymarket.com/positions', params={'user': address, 'limit': 100, 'offset': offset, 'sizeThreshold': 0})
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError('持仓响应格式错误')
            rows.extend(page)
            if len(page) < 100:
                break
            offset += 100
            if offset >= 10000:
                raise ValueError('持仓分页超过上限，不能声称完整同步')
        return AccountSnapshot(account_id=self.profile.id, address=address,
                               balance_usdc=state.balance_usdc, positions=rows)

    async def signal(self, intent_id):
        async with self.service.store._db.execute('SELECT signal FROM order_intents WHERE id=? AND account_id=?',
                                                  (intent_id, self.profile.id)) as cur:
            row = await cur.fetchone()
        if not row:
            raise ValueError('未找到该账户的持久化订单意图')
        return FollowSignal.model_validate_json(row[0])

    @staticmethod
    def submission(record):
        return Submission(account_id=record.account_id, intent_id=record.signal_id, order_id=record.order_id or '',
                          state=record.status.value, error=record.error or '', fill_size=record.fill_size, fill_price=record.fill_price)

    async def submit_order(self, intent):
        signal = await self.signal(intent.id)
        self.engine.armed = self.service.manager.mode.value == 'LIVE' and self.service.active_id == self.profile.id
        record = await self.engine.execute(signal)
        return self.submission(record)

    async def get_receipt(self, submission):
        record = await self.service.store.get_execution(submission.intent_id)
        if not record or record.account_id != self.profile.id:
            raise ValueError('订单账户不匹配')
        record = await self.engine.reconcile(record)
        return Receipt(**self.submission(record).model_dump())

    async def cancel_order(self, submission):
        record = await self.service.store.get_execution(submission.intent_id)
        if not record or record.account_id != self.profile.id:
            raise ValueError('订单账户不匹配')
        record = await self.engine.cancel(record)
        return Receipt(**self.submission(record).model_dump())

    async def sync_pnl(self):
        snapshot = await self.read_account()
        return PnlSnapshot(account_id=self.profile.id, positions=snapshot.positions, complete=snapshot.complete,
                           source='Polymarket Data API: cashPnl / realizedPnl / currentValue')


class OAuthAdapter:
    """兼容桥接口，绝不猜测平台 OAuth 私有端点。"""
    capabilities = Capabilities(description='OAuth 中继后台执行；需配置兼容桥并通过协议校验')

    def __init__(self, service, profile):
        self.service, self.profile = service, profile

    async def request(self, method, path, body=None):
        import httpx
        token = self.service.vault.get(self.profile.id).get('oauth_token', '')
        if not self.profile.relay_url or not token:
            raise ValueError('请配置中继地址（含端口）与 OAuth 会话凭证')
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            r = await client.request(method, self.profile.relay_url + path,
                                    headers={'Authorization': 'Bearer ' + token}, json=body)
            r.raise_for_status()
            data = r.json()
        return data

    async def account(self):
        data = await self.request('GET', '/v1/account')
        if (data.get('protocol') != 'polymarket-account-bridge/1' or data.get('chain_id') != 137
                or not self.profile.address or str(data.get('address', '')).lower() != self.profile.address.lower()):
            raise ValueError('中继协议、网络或账户地址不匹配')
        return data

    async def check_connection(self):
        try:
            data = await self.account()
            balance = float(data['balance_usdc'])
            import math
            if not math.isfinite(balance) or balance < 0:
                raise ValueError('余额无效')
            auth, allowance = data.get('authenticated') is True, data.get('allowance_ready') is True
            return ConnectionStatus(account_id=self.profile.id, ready=auth and allowance and balance > 0,
                authenticated=auth, allowance_ready=allowance, balance_usdc=balance, capabilities=self.capabilities)
        except Exception:
            return ConnectionStatus(account_id=self.profile.id, capabilities=self.capabilities,
                error='中继未就绪：检查 URL/端口、会话有效性及 bridge/1 协议与账户匹配')

    async def read_account(self):
        d = await self.account()
        if d.get('authenticated') is not True or not isinstance(d.get('positions'), list):
            raise ValueError('中继未返回有效持仓')
        return AccountSnapshot(account_id=self.profile.id, address=self.profile.address,
                               balance_usdc=d['balance_usdc'], positions=d['positions'])

    def validated(self, data, intent_id):
        r = Receipt.model_validate(data)
        if r.account_id != self.profile.id or r.intent_id != intent_id:
            raise ValueError('中继回执账户或意图不匹配')
        if r.state in ('FILLED', 'PARTIAL') and (not r.order_id or not r.fill_size or not r.fill_price or not 0 < r.fill_price < 1):
            raise ValueError('中继缺少有效成交证明')
        return r

    async def submit_order(self, intent):
        await self.account()
        signal = await HmacAdapter.signal(self, intent.id)
        await self.service.manager.check_submission(signal)
        return self.validated(await self.request('POST', '/v1/orders', intent.model_dump(mode='json')), intent.id)

    async def get_receipt(self, submission):
        from urllib.parse import quote
        return self.validated(await self.request('GET', '/v1/orders/' + quote(submission.intent_id, safe='')), submission.intent_id)

    async def cancel_order(self, submission):
        from urllib.parse import quote
        return self.validated(await self.request('DELETE', '/v1/orders/' + quote(submission.intent_id, safe='')), submission.intent_id)

    async def sync_pnl(self):
        s = await self.read_account()
        return PnlSnapshot(account_id=self.profile.id, positions=s.positions, complete=s.complete, source='OAuth bridge/1')


class AddressSigner:
    """只用于 L2 认证头与未签名订单构建，不持有也不模拟私钥。"""
    def __init__(self, address):
        self.value = address
    def address(self):
        return self.value


class OKXAdapter(HmacAdapter):
    capabilities = Capabilities(interactive_signing=True, unattended=False,
        description='OKX 插件逐笔弹窗签名；浏览器与插件必须在线，仅半自动')

    def __init__(self, service, profile):
        super().__init__(service, profile)
        self.online_until = utc() - timedelta(seconds=1)
        self.engine._init_clob_client = self.init_client

    def init_client(self):
        from backend.accounts.clob_signing import CurrentClobClient as ClobClient
        from backend.accounts.clob_signing import configure_transport
        configure_transport(self.service.manager.settings)
        from py_clob_client.clob_types import ApiCreds
        v = self.service.vault.get(self.profile.id)
        if not self.profile.address or not all(v.get(k) for k in ('api_key', 'api_secret', 'api_passphrase')):
            raise ValueError('请先连接插件并授权生成 CLOB 凭证')
        sdk = ClobClient('https://clob.polymarket.com', chain_id=137,
                         creds=ApiCreds(v['api_key'], v['api_secret'], v['api_passphrase']))
        sdk.signer = AddressSigner(self.profile.address)
        from types import SimpleNamespace
        sdk.builder = SimpleNamespace(sig_type=0, funder=self.profile.address)
        sdk.mode = 2
        self.engine._clob_client = sdk

    async def check_connection(self):
        status = await super().check_connection()
        if self.online_until < utc():
            status.ready = False
            status.error = 'OKX 插件未在线验证；请在浏览器连接并保持页面打开'
        return status

    async def submit_order(self, intent):
        if self.online_until < utc():
            raise ValueError('插件离线，不能请求签名')
        from backend.accounts.clob_signing import build_typed_order, order_hash
        if self.engine._clob_client is None:
            await asyncio.to_thread(self.init_client)
        sdk = self.engine._clob_client
        book = await asyncio.to_thread(sdk.get_book_context, intent.token_id)
        if intent.side == 'SELL' and await self.engine.token_balance(intent.token_id) + 1e-8 < intent.quantity:
            raise ValueError('实盘可卖份额不足')
        typed = build_typed_order(self.profile.address, '', 0, intent.token_id, intent.side,
                                  intent.limit_price, intent.quantity, book)
        payload = {'typed_data': typed, 'order_hash': order_hash(typed)}
        await self.job(intent.id, 'order', payload, intent.expires_at)
        return Submission(account_id=self.profile.id, intent_id=intent.id, state='AWAITING_SIGNATURE',
                          job_id=intent.id, error='等待 OKX 插件逐笔签名确认')

    async def job(self, intent_id, kind, payload, expires):
        import uuid
        job_id = intent_id if kind == 'order' else str(uuid.uuid4())
        await self.service.store._db.execute('INSERT INTO wallet_jobs VALUES (?,?,?,?,?,?,?,?,?)',
            (job_id, self.profile.id, intent_id, kind, json.dumps(payload), 'WAITING', '', expires.isoformat(), utc().isoformat()))
        await self.service.store._db.commit()
        return job_id

    async def cancel_order(self, submission):
        # 本地授权签名，不伪装为交易所要求的撤单 EIP712。
        import uuid
        typed = {'types': {'EIP712Domain': [{'name': 'name', 'type': 'string'}, {'name': 'chainId', 'type': 'uint256'}],
            'CancelAuthorization': [{'name': 'orderId', 'type': 'string'}, {'name': 'nonce', 'type': 'string'}]},
            'primaryType': 'CancelAuthorization', 'domain': {'name': 'Local Polymarket Trader Cancel', 'chainId': 137},
            'message': {'orderId': submission.order_id, 'nonce': str(uuid.uuid4())}}
        await self.job(submission.intent_id, 'cancel', {'typed_data': typed}, utc() + timedelta(seconds=90))
        return Receipt(**submission.model_dump(exclude={'state', 'error'}), state='PENDING', error='等待插件确认撤单授权')
