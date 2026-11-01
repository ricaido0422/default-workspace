from fastapi import APIRouter, HTTPException, Request
from backend.core.trading_desk import DeskPolicy, ManualOrder

router = APIRouter(prefix='/api/desk', tags=['trading-desk'])


def desk(request):
    return request.app.state.desk


@router.get('/state')
async def state(request: Request):
    d = desk(request)
    return {'policy': (await d.policy()).model_dump(), 'summary': await d.account_snapshot(), 'sync': dict(request.app.state.market_sync.status), 'queue': await d.queue(),
            'exit_worker_error': await d.store.get_config('desk_exit_error')}


@router.put('/policy')
async def policy(request: Request, body: DeskPolicy):
    return await desk(request).set_policy(body)


@router.get('/platform-trades')
async def feed(request: Request):
    try:
        return {'trades': await desk(request).platform_feed(), 'scope': '平台最近最多100笔公开成交，实时推送并以 REST 补拉，断线期间不保证无遗漏；自动跟单仅作用于已配置监控钱包。'}
    except Exception:
        raise HTTPException(502, '平台成交暂不可用，请检查网络或代理')


@router.get('/source-matrix')
async def matrix(request: Request):
    return await desk(request).source_matrix()


@router.get('/book/{token_id}')
async def book(request: Request, token_id: str):
    if not token_id.isdigit() or len(token_id) > 100:
        raise HTTPException(422, 'Token ID 必须为数字')
    try:
        d = desk(request)
        data = await d.market_data.book(token_id) if d.market_data else await d.client.get_orderbook(token_id)
        asks = sorted(float(x['price']) for x in data.get('asks', []) if float(x['size']) > 0)
        bids = sorted((float(x['price']) for x in data.get('bids', []) if float(x['size']) > 0), reverse=True)
        return {'best_ask': asks[0] if asks else None, 'best_bid': bids[0] if bids else None,
                'condition_id': data.get('market', ''), 'min_order_size': data.get('min_order_size'),
                'tick_size': data.get('tick_size'), 'timestamp': data.get('timestamp')}
    except Exception:
        raise HTTPException(502, '读取盘口失败')


@router.post('/preview')
async def preview(request: Request, body: ManualOrder):
    try:
        d = desk(request)
        signal = await d.manual_signal(body)
        quote = await d.quote(signal)
        return {**quote, 'mode': d.manager.mode.value, 'cash_estimate': quote['price'] * body.size,
                'fee_estimate': quote['price'] * body.size * (await d.policy()).fee_bps / 10000}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(502, '无法预览当前盘口，请检查标的或网络')


@router.post('/orders')
async def manual(request: Request, body: ManualOrder):
    try:
        return await desk(request).manual(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(502, '创建委托失败，请先检查交易台队列，勿重复提交')


@router.post('/queue/{signal_id}/approve')
async def approve(request: Request, signal_id: str):
    try:
        return await desk(request).approve(signal_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception:
        raise HTTPException(502, '审批结果待核对，请刷新队列与执行记录，不要重复审批')


@router.post('/queue/{signal_id}/reject')
async def reject(request: Request, signal_id: str):
    try:
        return await desk(request).reject(signal_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get('/account-positions')
async def account_positions(request: Request):
    d = desk(request)
    if d.manager.mode.value != 'LIVE':
        return {'positions': (await d.summary())['positions'], 'scope': '交易台模拟持仓'}
    c = d.manager.settings.polymarket
    try:
        from eth_account import Account
        address = c.funder or Account.from_key(c.private_key).address
        positions = await d.client.get_wallet_positions(user=address, status='OPEN', limit=500)
        return {'positions': positions, 'scope': '交易所账户公开持仓快照，最多500条；实际可卖份额提交前另行读取余额。'}
    except Exception:
        raise HTTPException(502, '账户持仓获取失败，请检查凭证与网络')


from pydantic import BaseModel, Field
class PaperCapital(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)

@router.post('/paper-capital')
async def paper_capital(request: Request, body: PaperCapital):
    try:
        return await desk(request).set_paper_capital(body.amount)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


from backend.core.copy_rules import CopyRule, get_rules
from backend.models.schemas import WalletWatch

@router.get('/copy-rules')
async def copy_rules(request: Request):
    d = desk(request)
    rules = await get_rules(d.store)
    return {'rules': [r.model_dump() for r in rules], 'mode': d.manager.mode.value,
            'account_id': d.manager.accounts.active_id if hasattr(d.manager, 'accounts') else 'legacy',
            'workflow': (await d.policy()).workflow,
            'scope': '规则绑定 PAPER/LIVE；同模式共享资金与全局风控。高级策略仍独立运行，请避免同钱包重复跟单。'}


@router.put('/copy-rules/{rule_id}')
async def save_copy_rule(request: Request, rule_id: str, body: CopyRule):
    d = desk(request)
    if body.id != rule_id:
        raise HTTPException(422, '规则标识不匹配')
    async with d.lock:
        rules = await get_rules(d.store)
        old = next((r for r in rules if r.id == rule_id), None)
        if body.mode == 'LIVE' and hasattr(d.manager, 'accounts'):
            if not old:
                body.account_id = d.manager.accounts.active_id
            elif old.account_id != body.account_id:
                raise HTTPException(409, '已有规则不能更换交易账户，请新增规则')
        if old and (old.wallet.lower() != body.wallet.lower() or old.mode != body.mode):
            raise HTTPException(409, '已有规则不能更换钱包或账户模式，请新建规则')
        if not old and len(rules) >= 100:
            raise HTTPException(409, '最多保存100条快捷规则')
        if not old and any(r.wallet.lower() == body.wallet.lower() and r.mode == body.mode and r.account_id == body.account_id for r in rules):
            raise HTTPException(409, '该钱包在此模式已有规则，请编辑已有规则')
        if not old and body.mode != d.manager.mode.value:
            raise HTTPException(409, '请先在交易台切换到目标账户模式再创建规则')
        # 编辑/暂停立即作废旧审核单，避免旧金额或旧过滤条件继续被审批。
        if old and old != body:
            for row in await d.queue(limit=1000000):
                if row['status'] == 'waiting' and row['signal']['pipeline_id'] == rule_id:
                    await d.mark(row['signal_id'], 'rejected', '跟单规则已修改或暂停，旧委托失效')
        if not old or (body.enabled and not old.enabled):
            from datetime import datetime, timezone
            await d.store.set_config('copy_rule_activation:' + rule_id, datetime.now(timezone.utc).isoformat())
        await d.store.set_config('copy_rules', [r.model_dump() for r in rules if r.id != rule_id] + [body.model_dump()])
    # 规则已绑定的地址加入监控；保留现有备注、公开用户名和人工板块标签。
    wallet = next((w for w in await d.store.get_wallets() if w.address.lower() == body.wallet.lower()), None)
    if wallet is None:
        wallet = WalletWatch(address=body.wallet)
        await d.store.add_wallet(wallet)
    if body.enabled and not wallet.enabled:
        await d.store.toggle_wallet(wallet.address)
    if body.enabled:
        await request.app.state.monitor.add_wallet(wallet.address, wallet.alias)
    return body.model_dump()


@router.get('/discovery')
async def discovery(request: Request, window: str = 'week'):
    if window not in ('day', 'week', 'month', 'all'):
        raise HTTPException(422, '时间范围无效')
    d = desk(request)
    wallets = await d.store.get_wallets()
    profiles = {}
    for w in wallets:
        profiles[w.address.lower()] = {**w.model_dump(mode='json'),
            **(await d.store.get_config('wallet_profile:' + w.address.lower()) or {})}
    leaders = await request.app.state.leaderboard_client.fetch_leaderboard(window=window, limit=50)
    rows = []
    for entry in leaders:
        row = entry.model_dump()
        p = profiles.pop(entry.wallet.lower(), {})
        rows.append({**row, 'alias': p.get('alias', ''), 'username': p.get('username') or entry.username,
            'specialties': p.get('specialties', []), 'trade_count': p.get('trade_count', 0),
            'watched': bool(p), 'origin': 'official_leaderboard'})
    rows.extend({'wallet': p['address'], 'alias': p.get('alias', ''), 'username': p.get('username', ''),
        'specialties': p.get('specialties', []), 'trade_count': p.get('trade_count', 0),
        'pnl': None, 'volume': None, 'watched': True, 'origin': 'watched'} for p in profiles.values())
    return {'rows': rows, 'window': window, 'leaderboard_available': bool(leaders),
        'scope': '官方榜单区间盈亏与本地观测成交分别展示；板块是钱包人工标签，不是官方板块排名。缺少完整成本与样本时不推断胜率。'}
