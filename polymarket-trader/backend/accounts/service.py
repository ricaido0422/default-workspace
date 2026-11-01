"""账户配置与凭证生命周期。策略层不读取登录方式或密钥。"""
import asyncio
from pathlib import Path
from backend.accounts.contracts import AccountProfile
from backend.accounts.vault import CredentialVault

SECRET_FIELDS = {'private_key', 'api_key', 'api_secret', 'api_passphrase', 'oauth_token'}


class AccountService:
    def __init__(self, manager):
        self.manager = manager
        self.store = manager.store
        self.vault = CredentialVault(Path(self.store.db_path).resolve().with_suffix('.credentials'))
        self.profiles = {}
        self.active_id = 'legacy'
        self.adapters = {}

    async def initialize(self):
        async with self.store._db.execute('SELECT payload FROM account_profiles') as cursor:
            for row in await cursor.fetchall():
                p = AccountProfile.model_validate_json(row[0])
                self.profiles[p.id] = p
        if not self.profiles:
            c = self.manager.settings.polymarket
            p = AccountProfile(id='legacy', name='默认实盘账户', kind='magic_hmac',
                               address=c.wallet_address or '', signature_type=c.signature_type, funder=c.funder or '')
            secrets = {k: getattr(c, k, '') for k in SECRET_FIELDS if k != 'oauth_token'}
            await self.save(p, secrets)
        saved = await self.store.get_config('active_account')
        self.active_id = saved if isinstance(saved, str) and saved in self.profiles else next(iter(self.profiles))
        self.hydrate(self.manager.settings)

    def hydrate(self, settings):
        if not self.profiles:
            return
        p = self.profiles[self.active_id]
        values = self.vault.get(p.id)
        for key in SECRET_FIELDS - {'oauth_token'}:
            setattr(settings.polymarket, key, values.get(key, ''))
        settings.polymarket.wallet_address = p.address
        settings.polymarket.funder = p.funder
        settings.polymarket.signature_type = p.signature_type

    async def save(self, profile, secrets):
        if self.manager.mode.value == 'LIVE':
            raise ValueError('请先停止实盘再修改账户')
        if profile.kind == 'okx' and (profile.signature_type != 0 or profile.funder and profile.funder.lower() != profile.address.lower()):
            raise ValueError('OKX 当前通道仅接受自托管 EOA，签名类型应为 0')
        if set(secrets) - SECRET_FIELDS:
            raise ValueError('包含未支持的凭证字段')
        if profile.kind == 'okx' and secrets.get('private_key'):
            raise ValueError('OKX 插件通道禁止接收私钥')
        if profile.kind != 'magic_oauth' and secrets.get('oauth_token'):
            raise ValueError('当前通道不接受 OAuth token')
        values = self.vault.get(profile.id)
        old = self.profiles.get(profile.id)
        if old and (old.kind != profile.kind or old.address.lower() != profile.address.lower() or old.relay_url != profile.relay_url):
            values = {}  # 换地址/通道不得继承另一账户的凭证。
        values.update({k: v for k, v in secrets.items() if v})
        await asyncio.to_thread(self.vault.put, profile.id, values)
        await self.store._db.execute('INSERT INTO account_profiles VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                                     (profile.id, profile.model_dump_json()))
        await self.store._db.commit()
        self.profiles[profile.id] = profile
        await self.store._db.execute("UPDATE wallet_jobs SET state='INVALIDATED' WHERE account_id=? AND state='WAITING'", (profile.id,))
        await self.store._db.commit()
        self.adapters.pop(profile.id, None)
        self.manager.live_connection.invalidate()
        if profile.id == self.active_id:
            self.hydrate(self.manager.settings)
            self.manager._live.reset()

    async def select(self, account_id):
        if self.manager.mode.value == 'LIVE':
            raise ValueError('请先停止实盘再切换钱包')
        if account_id not in self.profiles:
            raise ValueError('账户不存在')
        # 未解决的委托和审核仍属于原账户，不随选中项迁移。
        self.active_id = account_id
        await self.store._db.execute("UPDATE wallet_jobs SET state='INVALIDATED' WHERE state='WAITING'")
        await self.store._db.commit()
        await self.store.set_config('active_account', account_id)
        self.hydrate(self.manager.settings)
        self.manager._live.reset()
        self.manager.live_connection.invalidate()
        from backend.core.order_executor import RiskManager
        self.manager._risk = RiskManager(self.manager.settings)
        self.manager._allocator._exposure = {}

    def public(self):
        return {'active_id': self.active_id, 'accounts': [dict(p.model_dump(),
                    credentials=self.vault.flags(p.id), capabilities=self.adapter(p.id).capabilities.model_dump())
                    for p in self.profiles.values()]}

    def adapter(self, account_id=None):
        account_id = account_id or self.active_id
        if account_id not in self.adapters:
            from backend.accounts.adapters import HmacAdapter, OAuthAdapter, OKXAdapter
            factories = {'magic_hmac': HmacAdapter, 'magic_oauth': OAuthAdapter, 'okx': OKXAdapter}
            p = self.profiles[account_id]
            self.adapters[account_id] = factories[p.kind](self, p)
        return self.adapters[account_id]
