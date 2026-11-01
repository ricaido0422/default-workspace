"""实盘只读就绪检测。成功表示前置检查通过，不声称真实订单已经成交。"""
import asyncio
from datetime import datetime, timezone


class LiveConnection:
    def __init__(self, manager):
        self.manager = manager
        self.result = None
        self.generation = 0
        self.lock = asyncio.Lock()

    def invalidate(self):
        self.generation += 1
        self.result = None

    def configuration(self):
        if hasattr(self.manager, 'accounts') and self.manager.accounts.profiles:
            s = self.manager.accounts
            p = s.profiles[s.active_id]
            flags = s.vault.flags(p.id)
            required = ('oauth_token',) if p.kind == 'magic_oauth' else ('api_key', 'api_secret', 'api_passphrase') if p.kind == 'okx' else ('api_key', 'api_secret', 'api_passphrase', 'private_key')
            return {'fields': flags, 'complete': all(flags.get(k) for k in required) and (bool(p.relay_url and p.address) if p.kind == 'magic_oauth' else True),
                    'signature_type': p.signature_type, 'funder_configured': bool(p.funder),
                    'account_id': p.id, 'name': p.name, 'kind': p.kind,
                    'capabilities': s.adapter().capabilities.model_dump()}
        c = self.manager.settings.polymarket
        fields = {'api_key': bool(c.api_key), 'api_secret': bool(c.api_secret),
                  'api_passphrase': bool(c.api_passphrase), 'private_key': bool(c.private_key)}
        complete = all(fields.values()) and (c.signature_type == 0 or bool(c.funder))
        return {'fields': fields, 'complete': complete, 'signature_type': c.signature_type,
                'funder_configured': bool(c.funder)}

    async def snapshot(self):
        pending = 0
        if self.manager.store:
            account_id = self.manager.accounts.active_id if hasattr(self.manager, 'accounts') else 'legacy'
            async with self.manager.store._db.execute("SELECT COUNT(DISTINCT signal_id) FROM executions WHERE mode='LIVE' AND status='PENDING' AND account_id=?", (account_id,)) as cur:
                pending = (await cur.fetchone())[0]
        config = self.configuration()
        r = dict(self.result or {'checked_at': None, 'ready': False, 'checks': [], 'balance_usdc': None})
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(r['checked_at'])).total_seconds() if r['checked_at'] else None
        r.update(mode=self.manager.mode.value, configuration=config, pending_orders=pending,
                 stale=age is None or age > 60, age_seconds=age,
                 ready=bool(r['ready'] and age is not None and age <= 60 and config['complete'] and not pending))
        r['state'] = ('unconfigured' if not config['complete'] else 'pending_orders' if pending else
                      'unchecked' if not r['checked_at'] else 'stale' if r['stale'] else 'ready' if r['ready'] else 'blocked')
        r['scope'] = '只读检查通过不代表已完成真实成交；每笔委托仍校验最新盘口、余额、授权与限额。'
        return r

    async def check(self):
        async with self.lock:
            generation = self.generation
            checks = []
            config = self.configuration()
            if hasattr(self.manager, 'accounts'):
                adapter = self.manager.accounts.adapter()
                result = await adapter.check_connection()
                checks = [
                    {'key': 'configuration', 'label': '账户配置', 'ok': config['complete'], 'message': config['name']},
                    {'key': 'authentication', 'label': '鉴权与签名通道', 'ok': result.authenticated and (not adapter.capabilities.interactive_signing or result.ready), 'message': result.error or adapter.capabilities.description},
                    {'key': 'balance', 'label': '可用余额', 'ok': (result.balance_usdc or 0) > 0, 'message': str(result.balance_usdc) if result.balance_usdc is not None else '未能读取'},
                    {'key': 'allowance', 'label': '交易授权', 'ok': result.allowance_ready, 'message': '已授权' if result.allowance_ready else '请检查官方平台授权'},
                ]
                if generation == self.generation:
                    self.result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': checks,
                        'balance_usdc': result.balance_usdc, 'ready': result.ready and config['complete']}
                return await self.snapshot()
            checks.append({'key': 'configuration', 'label': '凭证与签名配置',
                'ok': config['complete'], 'message': '字段已填写，签名匹配将在认证时验证' if config['complete'] else '请补齐 API 三项凭证、私钥；代理钱包还需 Funder'})
            balance = None
            try:
                # 使用公开 CLOB 服务检查实际网络；不产生资金操作。
                response = await asyncio.wait_for(self.manager._live.client._request_with_retry('GET', 'https://clob.polymarket.com/'), 12)
                network = response.status_code == 200 and response.text.strip().strip('"').upper() == 'OK'
                checks.append({'key': 'network', 'label': 'Polymarket CLOB 网络', 'ok': network,
                    'message': '服务已连通' if network else '服务响应异常，请检查网络或代理'})
            except Exception:
                network = False
                checks.append({'key': 'network', 'label': 'Polymarket CLOB 网络', 'ok': False, 'message': '无法连通，请检查网络或代理'})
            if config['complete'] and network:
                try:
                    result = await self.manager._live.preflight()
                    balance = result['balance_usdc']
                    checks.extend([
                        {'key': 'authentication', 'label': '账户认证与签名', 'ok': result['authenticated'], 'message': '已读取受认证的账户余额'},
                        {'key': 'balance', 'label': '可用抵押品余额', 'ok': balance > 0, 'message': f'{balance:.6f} USDC'},
                        {'key': 'allowance', 'label': '交易合约授权', 'ok': result['allowance_ready'], 'message': '检测到正授权额度；单笔仍由交易所校验' if result['allowance_ready'] else '尚未授权，请在官方平台完成授权'},
                    ])
                except Exception as exc:
                    # 不向日志/界面回传 SDK 异常内容，避免泄露认证请求中的秘密。
                    checks.append({'key': 'authentication', 'label': '账户认证与签名', 'ok': False,
                        'message': '认证请求超时，请检查网络或代理' if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else '认证或签名检查失败，请核对凭证、签名类型与 Funder'})
            if generation != self.generation:
                return await self.snapshot()
            self.result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': checks,
                'balance_usdc': balance, 'ready': len(checks) == 5 and all(c['ok'] for c in checks)}
            return await self.snapshot()

    async def worker(self):
        first = True
        while True:
            try:
                if self.manager.mode.value == 'LIVE' or first and self.configuration()['complete']:
                    await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.invalidate()
            first = False
            await asyncio.sleep(30)
