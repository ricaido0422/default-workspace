"""本机受保护 API：账户资料永远不返回密钥。"""
import json
from datetime import timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr
from backend.accounts.contracts import AccountProfile, utc

router = APIRouter(prefix='/api/accounts', tags=['accounts'])


def service():
    from backend.api.routes import _executor_manager
    if _executor_manager is None or not hasattr(_executor_manager, 'accounts'):
        raise HTTPException(503, '账户服务未初始化')
    return _executor_manager.accounts


class SaveAccount(BaseModel):
    profile: AccountProfile
    secrets: dict[str, SecretStr] = Field(default_factory=dict)


@router.get('')
async def accounts():
    return service().public()


@router.get('/events')
async def events():
    s = service()
    async with s.store._db.execute('SELECT account_id,intent_id,state,error,created_at FROM account_events WHERE account_id=? ORDER BY id DESC LIMIT 30', (s.active_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.put('')
async def save_account(body: SaveAccount):
    s = service()
    async with s.manager._configuration_lock:
        try:
            await s.save(body.profile, {k: v.get_secret_value() for k,v in body.secrets.items()})
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception:
            raise HTTPException(500, '凭证加密保存失败，请检查当前 Windows 用户与数据目录')
    return s.public()


@router.post('/{account_id}/select')
async def select_account(account_id: str):
    s = service()
    async with s.manager._configuration_lock, s.manager._execution_lock:
        try:
            await s.select(account_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    return s.public()


@router.post('/{account_id}/check')
async def check_account(account_id: str):
    s = service()
    if account_id not in s.profiles:
        raise HTTPException(404, '账户不存在')
    return await s.adapter(account_id).check_connection()


@router.post('/{account_id}/derive-credentials')
async def derive_credentials(account_id: str):
    import asyncio
    s = service()
    async with s.manager._configuration_lock:
        if s.manager.mode.value == 'LIVE' or account_id != s.active_id or s.profiles[account_id].kind != 'magic_hmac':
            raise HTTPException(409, '请先停止实盘并选中 Magic HMAC 账户')
        try:
            from eth_account import Account
            from py_clob_client.client import ClobClient
            from backend.accounts.clob_signing import configure_transport
            configure_transport(s.manager.settings)
            values = s.vault.get(account_id)
            p = s.profiles[account_id]
            address = Account.from_key(values.get('private_key', '')).address
            if p.address and p.address.lower() != address.lower():
                raise ValueError('签名地址不一致')
            sdk = ClobClient('https://clob.polymarket.com', key=values['private_key'], chain_id=137,
                             signature_type=p.signature_type, funder=p.funder or None)
            credentials = await asyncio.to_thread(sdk.create_or_derive_api_creds)
            if not credentials or not all((credentials.api_key, credentials.api_secret, credentials.api_passphrase)):
                raise ValueError('返回凭证不完整')
            await s.save(p.model_copy(update={'address': address}), dict(api_key=credentials.api_key,
                api_secret=credentials.api_secret, api_passphrase=credentials.api_passphrase, private_key=values['private_key']))
            return {'ok': True}
        except Exception:
            raise HTTPException(502, '生成 HMAC 凭证失败，请检查已保存私钥、账户地址与网络；不会返回密钥内容')


@router.get('/{account_id}/snapshot')
async def snapshot(account_id: str):
    s = service()
    if account_id not in s.profiles:
        raise HTTPException(404, '账户不存在')
    try:
        result = await s.adapter(account_id).read_account()
        await s.store._db.execute('INSERT INTO account_snapshots VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at',
            (account_id, result.model_dump_json(), utc().isoformat()))
        await s.store._db.commit()
        return result
    except Exception:
        raise HTTPException(502, '账户余额/持仓同步失败，请检查连接；未以空持仓覆盖旧数据')


@router.post('/{account_id}/browser/challenge')
async def challenge(account_id: str):
    s = service()
    if account_id != s.active_id or s.profiles[account_id].kind != 'okx':
        raise HTTPException(409, '请先选中 OKX 插件账户')
    async with s.manager._configuration_lock:
        return {'job_id': await s.manager.browser_signing.challenge(account_id)}


@router.post('/{account_id}/browser/heartbeat')
async def heartbeat(account_id: str):
    s = service()
    adapter = s.adapter(account_id) if account_id == s.active_id else None
    if adapter is None or s.profiles[account_id].kind != 'okx' or not hasattr(adapter, 'online_until') or adapter.online_until < utc():
        raise HTTPException(409, '插件会话未签名验证或已过期，请重新连接')
    adapter.online_until = utc()+timedelta(seconds=35)
    return {'ok': True}


@router.get('/browser/jobs')
async def jobs():
    s = service()
    async with s.store._db.execute("SELECT * FROM wallet_jobs WHERE account_id=? AND state='WAITING' AND expires_at>? ORDER BY created_at", (s.active_id, utc().isoformat())) as cur:
        rows = await cur.fetchall()
    return [dict(id=r['id'], kind=r['kind'], intent_id=r['intent_id'], expires_at=r['expires_at'], typed_data=json.loads(r['payload'])['typed_data']) for r in rows]


class SignatureBody(BaseModel):
    signature: str = Field(pattern=r'^0x[0-9a-fA-F]{130}$')


@router.post('/browser/jobs/{job_id}/signature')
async def sign_job(job_id: str, body: SignatureBody):
    try:
        return await service().manager.browser_signing.complete(job_id, body.signature)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception:
        raise HTTPException(502, '签名通道请求失败；若已提交订单请核对回执，系统不会自动重发')


@router.post('/browser/jobs/{job_id}/reject')
async def reject_job(job_id: str):
    s = service()
    async with s.manager.browser_signing.lock:
        async with s.store._db.execute("SELECT * FROM wallet_jobs WHERE id=? AND account_id=? AND state='WAITING'", (job_id, s.active_id)) as cur:
            r = await cur.fetchone()
        if not r:
            raise HTTPException(409, '签名任务已处理')
        await s.store._db.execute("UPDATE wallet_jobs SET state='REJECTED' WHERE id=?", (job_id,))
        if r['kind'] == 'order':
            from backend.accounts.contracts import Submission
            await s.manager.account_coordinator.apply(Submission(account_id=s.active_id, intent_id=r['intent_id'], state='FAILED', error='用户拒绝插件签名，订单未发送'))
        await s.store._db.commit()
    return {'ok': True}
