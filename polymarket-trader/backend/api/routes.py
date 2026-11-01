"""REST API 路由定义。

所有 /api/* 端点在此注册，对应 docs/api-contract.md 中的契约。
核心服务（store, client, leaderboard, executor, monitor）由 main.py 注入。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_settings, reload_settings, save_runtime_config, MonitorConfig
from backend.core.analytics import AnalyticsEngine
from backend.core.backtest import BacktestEngine
from backend.core.notifier import EventType
from backend.models.schemas import (
    AddWalletRequest,
    BacktestCompareRequest,
    BacktestParams,
    BacktestResult,
    BacktestRunRequest,
    CreatePipelineRequest,
    DashboardStats,
    DailyPerformance,
    ExecutionRecord,
    ExecutorCredentialsRequest,
    ExecutorModeRequest,
    ImportLeaderboardRequest,
    LeaderboardEntry,
    MarketPerformance,
    NotificationConfigRequest,
    NotificationTestRequest,
    PerformanceSummary,
    PortfolioAllocation,
    SimulateTradeRequest,
    StrategyPerformance,
    StrategyPipeline,
    TradeEvent,
    TradeSide,
    UpdateConfigRequest,
    UpdatePipelineRequest,
    VirtualPortfolioSummary,
    WalletAllocation,
    WalletPerformance,
    WalletWatch,
    WalletProfileRequest,
)
from backend.store.state import StateStore, generate_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# ── 服务依赖注入（由 main.py 设置） ─────────────────────────

_store: StateStore | None = None
_leaderboard_client: Any = None  # LeaderboardClient
_executor_manager: Any = None    # OrderExecutorManager
_wallet_monitor: Any = None      # WalletMonitor
_ws_client: Any = None           # PolymarketWSClient
_notifier: Any = None            # NotifierManager
_polymarket_client: Any = None   # PolymarketClient（持仓查询用）


def get_store() -> StateStore:
    if _store is None:
        raise RuntimeError("StateStore 未初始化")
    return _store


def set_store(store: StateStore) -> None:
    global _store
    _store = store


def set_polymarket_client(client: Any) -> None:
    global _polymarket_client
    _polymarket_client = client


def get_polymarket_client() -> Any:
    if _polymarket_client is None:
        raise RuntimeError("PolymarketClient 未初始化")
    return _polymarket_client


def set_leaderboard_client(client: Any) -> None:
    global _leaderboard_client
    _leaderboard_client = client


def set_executor_manager(mgr: Any) -> None:
    global _executor_manager
    _executor_manager = mgr


def set_wallet_monitor(monitor: Any) -> None:
    global _wallet_monitor
    _wallet_monitor = monitor


def set_ws_client(client: Any) -> None:
    global _ws_client
    _ws_client = client


def set_notifier(notifier: Any) -> None:
    """注入通知管理器（由 main.py 调用）。"""
    global _notifier
    _notifier = notifier


def get_notifier() -> Any:
    """获取通知管理器（未初始化时返回 None）。"""
    return _notifier


# ── 钱包管理 ────────────────────────────────────────────────

@router.get("/wallets", response_model=list[WalletWatch])
async def list_wallets() -> list[WalletWatch]:
    """获取所有监控钱包列表。"""
    wallets = await get_store().get_wallets()
    for wallet in wallets:
        profile = await get_store().get_config('wallet_profile:' + wallet.address.lower())
        if profile:
            for field in ('username', 'specialties', 'notes'):
                setattr(wallet, field, profile.get(field, getattr(wallet, field)))
    return wallets


@router.put('/wallets/{address}/profile', response_model=WalletWatch)
async def update_wallet_profile(address: str, req: WalletProfileRequest):
    wallet = await get_store().get_wallet(address)
    if wallet is None:
        raise HTTPException(404, '钱包不存在')
    wallet.alias = req.alias
    await get_store().add_wallet(wallet)
    await get_store().set_config('wallet_profile:' + address.lower(), req.model_dump())
    for field in ('username', 'specialties', 'notes'):
        setattr(wallet, field, getattr(req, field))
    return wallet


@router.post('/wallets/{address}/profile/refresh')
async def refresh_wallet_profile(address: str):
    if await get_store().get_wallet(address) is None:
        raise HTTPException(404, '钱包不存在')
    try:
        client = get_polymarket_client()
        response = await client._request_with_retry('GET', f'{client.GAMMA_API}/public-profile', params={'address': address})
        data = response.json()
        username = str(data.get('name') or data.get('pseudonym') or '')
        if not username:
            raise ValueError('no public name')
    except Exception:
        raise HTTPException(502, '未能获取公开用户名，原有信息已保留，可手动填写')
    key = 'wallet_profile:' + address.lower()
    profile = await get_store().get_config(key) or {}
    profile.update(username=username, username_source='polymarket', refreshed_at=datetime.now(timezone.utc).isoformat())
    await get_store().set_config(key, profile)
    return profile


@router.post("/wallets", response_model=WalletWatch)
async def add_wallet(req: AddWalletRequest) -> WalletWatch:
    """添加监控钱包。"""
    wallet = WalletWatch(address=req.address, alias=req.alias)
    result = await get_store().add_wallet(wallet)
    # 动态添加到监控器
    if _wallet_monitor is not None:
        await _wallet_monitor.add_wallet(req.address, req.alias)
    return result


@router.delete("/wallets/{address}")
async def delete_wallet(address: str) -> dict[str, bool]:
    """删除监控钱包。"""
    ok = await get_store().delete_wallet(address)
    if not ok:
        raise HTTPException(status_code=404, detail="钱包不存在")
    # 从监控器移除
    if _wallet_monitor is not None:
        await _wallet_monitor.remove_wallet(address)
    return {"ok": True}


@router.post("/wallets/{address}/toggle", response_model=WalletWatch)
async def toggle_wallet(address: str) -> WalletWatch:
    """切换钱包监控启用状态。"""
    wallet = await get_store().toggle_wallet(address)
    if wallet is None:
        raise HTTPException(status_code=404, detail="钱包不存在")
    if _wallet_monitor is not None:
        if wallet.enabled:
            await _wallet_monitor.add_wallet(wallet.address, wallet.alias)
        else:
            await _wallet_monitor.remove_wallet(wallet.address)
    return wallet


# ── 排行榜 ──────────────────────────────────────────────────

@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    window: str = Query(default="week", pattern="^(day|week|month|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[LeaderboardEntry]:
    """获取 Polymarket 排行榜。"""
    if _leaderboard_client is not None:
        return await _leaderboard_client.fetch_leaderboard(window=window, limit=limit)
    return []


@router.post("/wallets/import-leaderboard")
async def import_leaderboard(req: ImportLeaderboardRequest) -> dict[str, Any]:
    """从排行榜批量导入钱包。"""
    store = get_store()
    if any(not re.fullmatch(r'0x[0-9a-fA-F]{40}', address) for address in req.wallets):
        raise HTTPException(422, '排行榜钱包地址无效')
    added = 0
    for i, addr in enumerate(req.wallets):
        alias = req.aliases[i] if i < len(req.aliases) else ""
        wallet = WalletWatch(address=addr, alias=alias)
        await store.add_wallet(wallet)
        if i < len(req.usernames) and req.usernames[i]:
            key = 'wallet_profile:' + addr.lower()
            profile = await store.get_config(key) or {}
            profile['username'] = req.usernames[i]
            await store.set_config(key, profile)
        if _wallet_monitor is not None:
            await _wallet_monitor.add_wallet(addr, alias)
        added += 1
    return {"imported": added}


# ── 策略流水线 ──────────────────────────────────────────────

@router.get("/pipelines", response_model=list[StrategyPipeline])
async def list_pipelines() -> list[StrategyPipeline]:
    """获取所有策略流水线。"""
    return await get_store().get_pipelines()


@router.post("/pipelines", response_model=StrategyPipeline)
async def create_pipeline(req: CreatePipelineRequest) -> StrategyPipeline:
    """创建策略流水线。"""
    pipeline = StrategyPipeline(id=generate_id("pl_"), name=req.name)
    return await get_store().save_pipeline(pipeline)


@router.put("/pipelines/{pipeline_id}", response_model=StrategyPipeline)
async def update_pipeline(
    pipeline_id: str, req: UpdatePipelineRequest
) -> StrategyPipeline:
    """更新策略流水线。"""
    store = get_store()
    pipeline = await store.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="流水线不存在")
    if req.strategies is not None:
        pipeline.strategies = req.strategies
    if req.enabled is not None:
        pipeline.enabled = req.enabled
    if req.target_wallets is not None:
        pipeline.target_wallets = req.target_wallets
    return await store.save_pipeline(pipeline)


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str) -> dict[str, bool]:
    """删除策略流水线。"""
    ok = await get_store().delete_pipeline(pipeline_id)
    if not ok:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return {"ok": True}


# ── 策略类型 ────────────────────────────────────────────────

@router.get("/strategies/available")
async def list_available_strategies() -> list[dict[str, Any]]:
    """获取所有可用策略类型及其参数 schema。

    从 STRATEGY_REGISTRY 动态生成，包含策略类型、类别、参数 schema。
    """
    from backend.strategies.base import STRATEGY_REGISTRY

    # 策略类型中文名
    name_map = {
        "MIRROR": "镜像跟单",
        "PROPORTIONAL": "比例跟单",
        "FIXED_AMOUNT": "固定金额",
        "PRICE_OFFSET": "价格偏移",
        "DELAYED": "延迟执行",
        "MARKET_FILTER": "市场过滤",
        "SIZE_FILTER": "数量过滤",
        "REVERSE": "反向跟单",
        "POSITION_CAP": "仓位上限",
        "STOP_LOSS": "止损",
    }
    # 策略描述
    desc_map = {
        "MIRROR": "完全复制源交易的方向和数量",
        "PROPORTIONAL": "按比例系数缩放交易数量",
        "FIXED_AMOUNT": "使用固定 USDC 金额下单",
        "PRICE_OFFSET": "在源价格基础上偏移",
        "DELAYED": "延迟一定时间后执行",
        "MARKET_FILTER": "按市场条件过滤信号",
        "SIZE_FILTER": "按交易数量过滤",
        "REVERSE": "反转交易方向",
        "POSITION_CAP": "限制单市场最大仓位",
        "STOP_LOSS": "达到亏损阈值时平仓",
    }

    result: list[dict[str, Any]] = []
    for strategy_type, strategy_cls in STRATEGY_REGISTRY.items():
        type_val = strategy_type.value
        result.append({
            "type": type_val,
            "category": strategy_cls.category.value if hasattr(strategy_cls, "category") else "",
            "name": name_map.get(type_val, type_val),
            "description": desc_map.get(type_val, ""),
            "params_schema": getattr(strategy_cls, "param_schema", {}),
        })
    return result


# ── 交易与执行 ──────────────────────────────────────────────

@router.get("/trades", response_model=list[TradeEvent])
async def list_trades(
    wallet: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TradeEvent]:
    """获取交易事件列表。"""
    return await get_store().get_trades(wallet=wallet, limit=limit)


@router.get("/trades/stats")
async def get_trades_stats(
    wallet: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    """交易流买卖方向统计（供交易流面板顶部统计卡使用）。

    Returns:
        {total, buy_count, sell_count, buy_amount, sell_amount,
         avg_buy_amount, avg_sell_amount, max_amount}
    金额口径：成交金额 = 价格 × 数量（USDC）。
    """
    trades = await get_store().get_trades(wallet=wallet, limit=limit)
    buy = [t for t in trades if str(t.side).upper().endswith("BUY")]
    sell = [t for t in trades if str(t.side).upper().endswith("SELL")]

    def _amount(t: TradeEvent) -> float:
        return float(t.price) * float(t.size)

    buy_amount = sum(_amount(t) for t in buy)
    sell_amount = sum(_amount(t) for t in sell)
    amounts = [_amount(t) for t in trades]

    return {
        "total": len(trades),
        "buy_count": len(buy),
        "sell_count": len(sell),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "avg_buy_amount": round(buy_amount / len(buy), 2) if buy else 0.0,
        "avg_sell_amount": round(sell_amount / len(sell), 2) if sell else 0.0,
        "max_amount": round(max(amounts), 2) if amounts else 0.0,
    }


@router.get("/executions", response_model=list[ExecutionRecord])
async def list_executions(
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ExecutionRecord]:
    """获取执行记录列表。"""
    return await get_store().get_executions(limit=limit)


# ── 仪表盘统计 ──────────────────────────────────────────────

@router.get("/stats", response_model=DashboardStats)
async def get_stats() -> DashboardStats:
    """获取仪表盘统计数据。"""
    store = get_store()
    wallets = await store.get_wallets()
    trades = await store.get_trades(limit=500)
    executions = await store.get_executions(limit=500)
    signals = await store.get_signals(limit=500)

    # 今日数据
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = sum(1 for t in trades if t.timestamp.strftime("%Y-%m-%d") == today)
    today_executions = sum(1 for e in executions if e.timestamp.strftime("%Y-%m-%d") == today)
    total_pnl = sum(e.pnl or 0 for e in executions if e.pnl is not None)

    return DashboardStats(
        watched_wallets=len(wallets),
        today_trades=today_trades,
        today_signals=sum(1 for s in signals if s.created_at.strftime("%Y-%m-%d") == today),
        today_executions=today_executions,
        total_pnl=total_pnl,
    )


# ── 配置管理 ────────────────────────────────────────────────

@router.get("/config")
async def get_config() -> dict[str, Any]:
    """获取当前配置（脱敏，不返回私钥）。"""
    settings = get_settings()
    return {
        "proxy": settings.proxy.model_dump(),
        "monitor": settings.monitor.model_dump(),
        "risk": settings.risk.model_dump(),
        "executor": {"mode": settings.executor.mode},
        "server": settings.server.model_dump(),
        "polymarket": {
            "api_key": "***" if settings.polymarket.api_key else "",
            "wallet_address": settings.polymarket.wallet_address,
            "has_credentials": bool(settings.polymarket.api_key),
            "signature_type": settings.polymarket.signature_type,
            "funder": settings.polymarket.funder,
        },
    }


@router.put("/config")
async def update_config(req: UpdateConfigRequest) -> dict[str, bool]:
    """更新配置。"""
    monitor_data = get_settings().monitor.model_dump()
    for key in ("interval", "min_amount", "max_amount"):
        value = getattr(req, "monitor_" + key)
        if value is not None:
            monitor_data[key] = value
    if monitor_data["max_amount"] and monitor_data["min_amount"] > monitor_data["max_amount"]:
        raise HTTPException(status_code=422, detail="监测金额下限不能大于上限")
    changes = {"monitor": MonitorConfig(**monitor_data).model_dump()}
    if req.risk is not None:
        changes["risk"] = req.risk.model_dump()
    if req.proxy is not None:
        changes["proxy"] = req.proxy.model_dump()
    try:
        save_runtime_config(changes)
    except OSError:
        raise HTTPException(status_code=500, detail="配置文件保存失败，请检查文件写入权限")
    if req.risk is not None:
        for key, value in req.risk.model_dump().items():
            os.environ["RISK_" + key.upper()] = str(value)
    if req.monitor_interval is not None:
        os.environ["MONITOR_INTERVAL"] = str(req.monitor_interval)
    if req.monitor_min_amount is not None:
        os.environ["MONITOR_MIN_AMOUNT"] = str(req.monitor_min_amount)
    if req.monitor_max_amount is not None:
        os.environ["MONITOR_MAX_AMOUNT"] = str(req.monitor_max_amount)
    if req.proxy is not None:
        os.environ["PROXY_HTTP"] = req.proxy.http
        os.environ["PROXY_HTTPS"] = req.proxy.https
    reload_settings()
    # 让已运行的组件立即读到新配置（settings 单例已重建，
    # 但组件持有的是旧对象引用，需显式替换）
    new_settings = get_settings()
    _sync_settings_to_running_components(new_settings)
    if req.proxy is not None:
        if _executor_manager is not None:
            _executor_manager.live_connection.invalidate()
            _executor_manager._live.reset()
        import httpx
        for service in (_polymarket_client, _leaderboard_client):
            if service is not None:
                previous = service._client
                service._client = httpx.AsyncClient(
                    timeout=service.TIMEOUT,
                    proxy=new_settings.proxy.http or new_settings.proxy.https or None,
                )
                service.settings = new_settings
                await previous.aclose()
        if _ws_client is not None:
            await _ws_client.disconnect()
            _ws_client._proxy = new_settings.proxy.http or new_settings.proxy.https or None
            await _ws_client.connect()
    return {"ok": True}


def _sync_settings_to_running_components(settings: Any) -> None:
    """把刷新后的 settings 同步给已初始化的组件，使配置改动即时生效。

    目前涉及：钱包监控器（金额范围/轮询间隔）、执行器管理器（风控/组合）。
    """
    monitor = _wallet_monitor
    if monitor is not None:
        try:
            monitor.settings = settings
            for address in monitor.get_watched_wallets():
                state = monitor.get_wallet_state(address)
                if state is not None:
                    state.interval = settings.monitor.interval
        except Exception:
            pass
    mgr = _executor_manager
    if mgr is not None:
        if hasattr(mgr, 'accounts'):
            mgr.accounts.hydrate(settings)
            for adapter in mgr.accounts.adapters.values():
                if hasattr(adapter, 'engine'):
                    engine = adapter.engine
                    proxy_changed = engine.settings.proxy != settings.proxy
                    engine.settings.proxy = settings.proxy.model_copy(deep=True)
                    engine.settings.executor = settings.executor.model_copy(deep=True)
                    engine.settings.risk = settings.risk.model_copy(deep=True)
                    if proxy_changed:
                        engine.reset()
        try:
            mgr.settings = settings
            mgr.risk.settings = settings
            # 子执行器同样持有旧 settings 引用，需要一并替换
            for sub in ("_paper", "_live"):
                sub_obj = getattr(mgr, sub, None)
                if sub_obj is not None:
                    try:
                        sub_obj.settings = settings
                    except Exception:
                        pass
        except Exception:
            pass

# ── 执行器 ──────────────────────────────────────────────────

@router.get('/executor/status')
async def executor_status():
    if _executor_manager is None:
        raise HTTPException(503, '执行器未初始化')
    return await _executor_manager.live_connection.snapshot()


@router.post('/executor/check')
async def check_executor_connection():
    if _executor_manager is None:
        raise HTTPException(503, '执行器未初始化')
    return await _executor_manager.live_connection.check()


@router.post("/executor/mode")
async def set_executor_mode(req: ExecutorModeRequest):
    if _executor_manager is None:
        raise HTTPException(503, '执行器未初始化')
    mgr = _executor_manager
    generation = mgr.live_connection.generation
    if req.mode == 'live':
        if not mgr.live_connection.configuration()['complete']:
            raise HTTPException(400, '请先完整配置实盘凭证和资金钱包，再检测连接')
        result = await mgr.live_connection.check()
        if not result['ready']:
            raise HTTPException(400, '实盘前置检查未通过，请查看连接状态中的具体检查项')
    async with mgr._configuration_lock:
        if req.mode == 'live' and generation != mgr.live_connection.generation:
            raise HTTPException(409, '检测期间配置已改变或已停止实盘，请重新检测并启动')
        if req.mode == 'live' and not (await mgr.live_connection.snapshot())['ready']:
            raise HTTPException(409, '实盘状态已改变，请重新检查待核对订单和账户状态')
        if req.mode == 'paper':
            mgr.live_connection.invalidate()
        os.environ['EXECUTOR_MODE'] = req.mode
        reload_settings()
        _sync_settings_to_running_components(get_settings())
        mgr.set_mode(req.mode)
    return {'mode': req.mode, 'status': await mgr.live_connection.snapshot()}


@router.post("/executor/credentials")
async def set_executor_credentials(req: ExecutorCredentialsRequest) -> dict[str, bool]:
    """设置执行器凭据。"""
    mgr = _executor_manager
    if mgr is None:
        raise HTTPException(503, '执行器未初始化')
    async with mgr._configuration_lock:
        if mgr.mode.value == 'LIVE':
            raise HTTPException(409, '请先切回模拟模式再修改凭证')
        if hasattr(mgr, 'accounts'):
            from backend.accounts.service import SECRET_FIELDS
            fields = req.model_dump(exclude_none=True)
            profile = mgr.accounts.profiles[mgr.accounts.active_id]
            profile = profile.model_copy(update={k: fields[k] for k in ('signature_type', 'funder', 'wallet_address') if k in fields and k != 'wallet_address'})
            if fields.get('wallet_address') is not None:
                profile.address = fields['wallet_address']
            try:
                await mgr.accounts.save(type(profile).model_validate(profile.model_dump()), {k: v for k, v in fields.items() if k in SECRET_FIELDS})
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return {'ok': True}
        for field, value in req.model_dump(exclude_none=True).items():
            if value != '' or field in ('funder', 'wallet_address'):
                os.environ['POLYMARKET_' + field.upper()] = str(value)
        reload_settings()
        _sync_settings_to_running_components(get_settings())
        mgr._live.reset()
        mgr.live_connection.invalidate()
    return {'ok': True}


@router.get('/executor/preflight')
async def executor_preflight():
    try:
        if hasattr(_executor_manager, 'accounts'):
            return (await _executor_manager.accounts.adapter().check_connection()).model_dump()
        return await _executor_manager._live.preflight()
    except Exception:
        raise HTTPException(400, '实盘只读检查失败：请检查 SDK、凭证、网络、签名类型与 Funder')


@router.post('/executions/{signal_id}/reconcile', response_model=ExecutionRecord)
async def reconcile_live_execution(signal_id: str):
    record = await get_store().get_execution(signal_id)
    if record is None or record.mode.value != 'LIVE':
        raise HTTPException(404, '实盘执行记录不存在')
    if record.status.value != 'PENDING':
        return record
    try:
        engine = getattr(_executor_manager, 'account_coordinator', _executor_manager._live)
        result = await engine.reconcile(record)
        from backend.main import _current_app
        if _current_app is not None:
            await _current_app.state.desk.recover()
        return result
    except ValueError:
        raise HTTPException(409, '缺少订单号，请在 Polymarket 核对，系统不会自动重发')
    except Exception:
        raise HTTPException(502, '暂时无法核对订单，保持待核对状态')


@router.post('/executions/{signal_id}/cancel', response_model=ExecutionRecord)
async def cancel_live_execution(signal_id: str):
    record = await get_store().get_execution(signal_id)
    if record is None or record.mode.value != 'LIVE' or record.status.value != 'PENDING':
        raise HTTPException(409, '仅可对待核对的实盘订单请求撤单')
    try:
        engine = getattr(_executor_manager, 'account_coordinator', _executor_manager._live)
        result = await engine.cancel(record)
        from backend.main import _current_app
        if _current_app is not None:
            await _current_app.state.desk.recover()
        return result
    except ValueError:
        raise HTTPException(409, '缺少订单号，请先到交易所核对')
    except Exception:
        raise HTTPException(502, '撤单结果待核对，请刷新订单状态，不要假定已取消')


# ── WebSocket 状态 ──────────────────────────────────────────

from pydantic import BaseModel as _BaseModel


class _WSSubscribeRequest(_BaseModel):
    """WS 订阅请求体。"""
    token_ids: list[str]


@router.get("/ws/status")
async def get_ws_status() -> dict[str, Any]:
    """获取 WebSocket Market Channel 客户端状态。

    Returns:
        {connected, subscribed_markets, price_cache_size, uptime}
    """
    if _ws_client is None:
        return {
            "connected": False,
            "subscribed_markets": [],
            "price_cache_size": 0,
            "uptime": 0.0,
            "enabled": False,
        }
    status = _ws_client.get_status()
    status["enabled"] = True
    return status


@router.post("/ws/subscribe")
async def ws_subscribe(req: _WSSubscribeRequest) -> dict[str, Any]:
    """手动订阅额外市场 token_id。

    Body: {"token_ids": ["TOKEN_ID1", "TOKEN_ID2", ...]}
    """
    if _ws_client is None:
        raise HTTPException(status_code=503, detail="WebSocket 客户端未启用")
    if not req.token_ids:
        raise HTTPException(status_code=400, detail="token_ids 不能为空")
    await _ws_client.subscribe(req.token_ids)
    return {
        "ok": True,
        "subscribed": len(req.token_ids),
        "total_subscribed": len(_ws_client._subscribed_assets),
    }


@router.get("/ws/price/{token_id}")
async def get_ws_price(token_id: str) -> dict[str, Any]:
    """获取缓存的实时价格。

    Returns:
        {mid, best_bid, best_ask, last_trade_price, timestamp} 或空对象
    """
    if _ws_client is None:
        raise HTTPException(status_code=503, detail="WebSocket 客户端未启用")
    price = _ws_client.get_cached_price(token_id)
    if price is None:
        return {"token_id": token_id, "cached": False}
    return {"token_id": token_id, "cached": True, **price}


# ── 通知推送 ────────────────────────────────────────────────

#: 通知配置在 DB config 表中的键
NOTIFICATION_CONFIG_KEY = "notification_config"


def _mask_token(token: str) -> str:
    """脱敏 Telegram Bot Token：显示前 4 后 4。"""
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def _mask_secret(secret: str) -> str:
    """完全隐藏密码。"""
    if not secret:
        return ""
    return "********"


def _notification_config_payload(cfg: Any) -> dict[str, Any]:
    """把通知配置序列化为脱敏后的字典。"""
    return {
        "telegram_enabled": bool(getattr(cfg, "telegram_enabled", False)),
        "telegram_token": _mask_token(getattr(cfg, "telegram_token", "") or ""),
        "telegram_token_set": bool(getattr(cfg, "telegram_token", "")),
        "telegram_chat_ids": list(getattr(cfg, "telegram_chat_ids", []) or []),
        "email_enabled": bool(getattr(cfg, "email_enabled", False)),
        "email_host": getattr(cfg, "email_host", "") or "",
        "email_port": int(getattr(cfg, "email_port", 587) or 587),
        "email_user": getattr(cfg, "email_user", "") or "",
        "email_password": _mask_secret(getattr(cfg, "email_password", "") or ""),
        "email_password_set": bool(getattr(cfg, "email_password", "")),
        "email_from": getattr(cfg, "email_from", "") or "",
        "email_to": list(getattr(cfg, "email_to", []) or []),
        "email_use_ssl": bool(getattr(cfg, "email_use_ssl", False)),
        "min_notify_amount": float(getattr(cfg, "min_notify_amount", 0.0) or 0.0),
        "notify_events": list(getattr(cfg, "notify_events", []) or []),
        "notify_wallets": list(getattr(cfg, "notify_wallets", []) or []),
        "cooldown_seconds": float(getattr(cfg, "cooldown_seconds", 0.0) or 0.0),
        "daily_summary_hour": int(getattr(cfg, "daily_summary_hour", 8) or 0),
        "daily_summary_minute": int(getattr(cfg, "daily_summary_minute", 0) or 0),
    }


def _current_notification_config() -> Any:
    """获取当前生效的通知配置（Settings → NotificationConfig）。"""
    settings = get_settings()
    return settings.notification


@router.get("/notifications/config")
async def get_notification_config() -> dict[str, Any]:
    """获取通知配置（脱敏）。

    Token 显示为前 4 后 4，密码完全隐藏。
    """
    return _notification_config_payload(_current_notification_config())


@router.put("/notifications/config")
async def update_notification_config(req: NotificationConfigRequest) -> dict[str, Any]:
    """更新通知配置。

    写入 DB config 表并热更新 NotifierManager（无需重启服务）。
    """
    from backend.config import NotificationConfig

    current = _current_notification_config()
    data = current.model_dump()

    updates = req.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if value is None:
            continue
        # 敏感字段：前端不回显，空字符串表示「保持原值」
        if key in ("telegram_token", "email_password") and value == "":
            continue
        data[key] = value

    try:
        new_cfg = NotificationConfig(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"通知配置无效: {e}")

    # 1. 写入 DB config 表（持久化）
    try:
        await get_store().set_config(
            NOTIFICATION_CONFIG_KEY,
            new_cfg.model_dump(mode="json"),
        )
    except Exception as e:
        logger.exception("保存通知配置到 DB 失败")
        raise HTTPException(status_code=500, detail=f"保存通知配置失败: {e}")

    # 2. 同步到环境变量（保持与现有 /api/config 行为一致）
    _notification_config_to_env(new_cfg)

    # 3. 热更新全局 Settings 与 NotifierManager
    try:
        settings = reload_settings()
    except Exception:
        logger.exception("热加载通知配置失败")
        settings = get_settings()
    # 环境变量已写入，此处再显式赋值确保与 new_cfg 完全一致
    settings.notification = new_cfg

    notifier = get_notifier()
    if notifier is not None:
        try:
            notifier.update_config(new_cfg)
        except Exception:
            logger.exception("热更新通知管理器失败")

    return {"ok": True, "config": _notification_config_payload(new_cfg)}


def _notification_config_to_env(cfg: Any) -> None:
    """把通知配置写回环境变量（与 YAML/环境变量优先级模型保持一致）。"""
    env_values = {
        "TELEGRAM_ENABLED": "true" if cfg.telegram_enabled else "false",
        "TELEGRAM_TOKEN": cfg.telegram_token,
        "TELEGRAM_CHAT_IDS": ",".join(cfg.telegram_chat_ids),
        "EMAIL_ENABLED": "true" if cfg.email_enabled else "false",
        "EMAIL_HOST": cfg.email_host,
        "EMAIL_PORT": str(cfg.email_port),
        "EMAIL_USER": cfg.email_user,
        "EMAIL_PASSWORD": cfg.email_password,
        "EMAIL_FROM": cfg.email_from,
        "EMAIL_TO": ",".join(cfg.email_to),
        "EMAIL_USE_SSL": "true" if cfg.email_use_ssl else "false",
        "NOTIFY_MIN_AMOUNT": str(cfg.min_notify_amount),
        "NOTIFY_EVENTS": ",".join(cfg.notify_events),
        "NOTIFY_WALLETS": ",".join(cfg.notify_wallets),
        "NOTIFY_COOLDOWN": str(cfg.cooldown_seconds),
    }
    for key, value in env_values.items():
        os.environ[key] = value


@router.post("/notifications/test")
async def test_notification(req: NotificationTestRequest) -> dict[str, Any]:
    """发送测试通知。

    Body: {"channel": "telegram" | "email"}

    Returns:
        {ok: bool, channel: str, message: str}
    """
    notifier = get_notifier()
    if notifier is None:
        raise HTTPException(status_code=503, detail="通知管理器未初始化")

    channel = req.channel
    available = notifier.get_channels()
    if channel not in available:
        return {
            "ok": False,
            "channel": channel,
            "message": f"{channel} 渠道未启用或配置不完整（当前可用渠道: {', '.join(available) or '无'}）",
        }

    title = "🔔 测试通知"
    message = (
        "这是一条来自 Polymarket Trader 的测试通知。\n"
        "如果你收到了它，说明通知通道工作正常。"
    )
    data = {
        "测试": True,
        "时间": datetime.now(timezone.utc).isoformat(),
    }

    try:
        results = await notifier.send_now(
            EventType.TEST_EVENT, title, message, data, channels=[channel]
        )
    except Exception as e:
        logger.exception("测试通知异常")
        return {"ok": False, "channel": channel, "message": f"发送异常: {e}"}

    ok = bool(results.get(channel))
    detail = "发送成功" if ok else "发送失败，请检查配置与网络"
    return {"ok": ok, "channel": channel, "message": detail}


@router.get("/notifications/log")
async def get_notification_logs(
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """获取最近的通知发送记录。

    每项: {id, created_at, event_type, channel, status, error, payload}
    """
    return await get_store().get_notification_logs(limit=limit)


# ── 策略回测（N2 任务区段） ─────────────────────────────────

@router.get("/backtest/strategies")
async def list_backtest_strategies() -> list[dict[str, Any]]:
    """获取可用于回测的策略类型及其参数 schema。

    复用 STRATEGY_REGISTRY，结构与 /strategies/available 一致。
    """
    from backend.core.backtest import build_strategy_catalog

    return build_strategy_catalog()


@router.post("/backtest/run", response_model=BacktestResult)
async def run_backtest(req: BacktestRunRequest) -> BacktestResult:
    """对单个钱包的历史交易运行策略回测。

    Body: {wallet, pipeline_id | pipeline_config, params}
    """
    pipeline_config = await _resolve_backtest_pipeline(
        req.pipeline_id, req.pipeline_config
    )
    engine = BacktestEngine(store=get_store())
    try:
        result = await engine.run_backtest(req.wallet, pipeline_config, req.params)
    finally:
        await engine.aclose()
    return result


@router.post("/backtest/compare")
async def compare_backtests(req: BacktestCompareRequest) -> dict[str, Any]:
    """多钱包对比回测。

    用同一套策略流水线对多个钱包分别回测，返回对比结果。
    Body: {wallets: [...], pipeline_id | pipeline_config, params}
    """
    if not req.wallets:
        raise HTTPException(status_code=400, detail="wallets 不能为空")

    pipeline_config = await _resolve_backtest_pipeline(
        req.pipeline_id, req.pipeline_config
    )
    engine = BacktestEngine(store=get_store())
    results: list[BacktestResult] = []
    try:
        for wallet in req.wallets:
            result = await engine.run_backtest(wallet, pipeline_config, req.params)
            results.append(result)
    finally:
        await engine.aclose()

    # 排名：按超额收益降序
    ranked = sorted(results, key=lambda r: r.metrics.excess_pnl, reverse=True)
    return {
        "pipeline_id": pipeline_config.id,
        "pipeline_name": pipeline_config.name,
        "count": len(results),
        "results": [r.model_dump() for r in ranked],
        "summary": {
            "best_wallet": ranked[0].wallet if ranked else "",
            "best_excess_pnl": ranked[0].metrics.excess_pnl if ranked else 0.0,
            "avg_excess_pnl": (
                sum(r.metrics.excess_pnl for r in results) / len(results)
                if results else 0.0
            ),
            "total_pnl": sum(r.metrics.total_pnl for r in results),
            "avg_pass_rate": (
                sum(r.metrics.pass_rate for r in results) / len(results)
                if results else 0.0
            ),
            "avg_win_rate": (
                sum(r.metrics.win_rate for r in results) / len(results)
                if results else 0.0
            ),
        },
    }


async def _resolve_backtest_pipeline(
    pipeline_id: str | None,
    pipeline_config: StrategyPipeline | None,
) -> StrategyPipeline:
    """解析回测用的流水线配置。

    优先级：内联 ``pipeline_config`` > 数据库中的 ``pipeline_id`` > 默认镜像跟单。
    """
    if pipeline_config is not None:
        return pipeline_config
    if pipeline_id:
        pipeline = await get_store().get_pipeline(pipeline_id)
        if pipeline is None:
            raise HTTPException(status_code=404, detail="流水线不存在")
        return pipeline
    # 默认：镜像跟单基线（等价于「直接照抄源交易」）
    from backend.models.schemas import StrategyCategory, StrategyConfig, StrategyType

    return StrategyPipeline(
        id="pl_default_mirror",
        name="默认镜像跟单",
        strategies=[
            StrategyConfig(
                id="st_default_mirror",
                type=StrategyType.MIRROR,
                category=StrategyCategory.EXECUTION,
                name="镜像跟单",
                description="完全复制源交易的方向和数量（回测默认基线）",
                enabled=True,
                params={},
                display_order=0,
            )
        ],
        enabled=True,
        target_wallets=[],
    )


# ── 绩效分析（N3 任务区段） ────────────────────────────────

#: ``days`` 查询参数：0 表示全部历史
_ANALYTICS_DAYS_QUERY = Query(default=7, ge=0, le=3650)


def _analytics_engine() -> AnalyticsEngine:
    """构造分析引擎；StateStore 未就绪时降级为空数据集（返回空结果而非 500）。"""
    try:
        return AnalyticsEngine(store=get_store())
    except RuntimeError:
        # lifespan 未执行（如裸 TestClient）或 store 尚未初始化
        return AnalyticsEngine(store=None)


@router.get("/analytics/summary", response_model=PerformanceSummary)
async def get_analytics_summary(
    days: int = _ANALYTICS_DAYS_QUERY,
) -> PerformanceSummary:
    """获取跟单绩效总览。

    Query: ``days`` 统计周期天数（0 = 全部历史）。

    聚合 signals / executions / trades 三张表，输出信号数、执行数、
    成交率、总盈亏、胜率、平均持仓时长、下单延迟分布与执行质量统计。
    """
    return await _analytics_engine().get_performance_summary(days=days)


@router.get("/analytics/wallets", response_model=list[WalletPerformance])
async def get_analytics_wallets(
    days: int = _ANALYTICS_DAYS_QUERY,
) -> list[WalletPerformance]:
    """获取按钱包分组的绩效统计。

    每个被监控钱包的信号数、执行数、成交率、盈亏、胜率与贡献占比。
    """
    return await _analytics_engine().get_wallet_performance(days=days)


@router.get("/analytics/strategies", response_model=list[StrategyPerformance])
async def get_analytics_strategies(
    days: int = _ANALYTICS_DAYS_QUERY,
) -> list[StrategyPerformance]:
    """获取按策略分组的绩效统计（策略归因）。

    每个策略实例的触发次数、通过率、贡献盈亏，以及它在流水线中的位置。
    """
    return await _analytics_engine().get_strategy_performance(days=days)


@router.get("/analytics/markets", response_model=list[MarketPerformance])
async def get_analytics_markets(
    days: int = _ANALYTICS_DAYS_QUERY,
) -> list[MarketPerformance]:
    """获取按市场分组的绩效统计。

    返回盈利 Top10（bucket=profit）、亏损 Top10（bucket=loss），
    以及无盈亏的市场（bucket=flat）。
    """
    return await _analytics_engine().get_market_performance(days=days)


@router.get("/analytics/daily", response_model=list[DailyPerformance])
async def get_analytics_daily(
    days: int = Query(default=30, ge=0, le=3650),
) -> list[DailyPerformance]:
    """获取每日绩效时间序列。

    返回按日期升序排列的每日盈亏、信号数、执行数、成交数与累计盈亏，
    直接用于前端柱状图渲染。
    """
    return await _analytics_engine().get_daily_performance(days=days)


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """健康检查端点（供 Docker HEALTHCHECK / 监控探活使用）。"""
    return {"status": "ok"}


# ── 组合跟单（多钱包分散） ───────────────────────────────────

# 组合配置在 config 表中的存储键
_PORTFOLIO_CONFIG_KEY = "portfolio_allocation"


def _executor_mgr() -> Any:
    """获取执行器管理器（未初始化时抛 503，让前端降级处理）。"""
    if _executor_manager is None:
        raise HTTPException(status_code=503, detail="执行器尚未初始化")
    return _executor_manager


@router.get("/portfolio", response_model=PortfolioAllocation)
async def get_portfolio() -> PortfolioAllocation:
    """获取组合跟单配置（权重列表 + 联动风控上限）。

    未配置过时返回默认配置（空权重列表，等价于未启用组合跟单）。
    """
    mgr = _executor_mgr()
    alloc = getattr(mgr, "allocator", None)
    if alloc is not None:
        return alloc.allocation
    return PortfolioAllocation()


@router.put("/portfolio", response_model=PortfolioAllocation)
async def update_portfolio(allocation: PortfolioAllocation) -> PortfolioAllocation:
    """保存组合跟单配置，立即生效（影响后续新信号的下单量分配）。"""
    mgr = _executor_mgr()
    mgr.set_portfolio_allocation(allocation)

    # 持久化到 config 表，重启后自动恢复
    try:
        await get_store().set_config(
            _PORTFOLIO_CONFIG_KEY, allocation.model_dump(mode="json")
        )
    except Exception:
        logger.warning("持久化组合配置失败（不影响本次生效）", exc_info=True)

    logger.info(
        "组合跟单配置已更新: %d 个钱包, 总敞口上限 %.2f",
        len(allocation.allocations), allocation.max_total_exposure,
    )
    return allocation


@router.get("/portfolio/exposure")
async def get_portfolio_exposure() -> dict[str, Any]:
    """获取组合当前敞口快照（总量、上限、使用率、各钱包明细）。"""
    mgr = _executor_mgr()
    alloc = getattr(mgr, "allocator", None)
    if alloc is None:
        return {
            "total_exposure": 0.0,
            "total_limit": 0.0,
            "utilization": 0.0,
            "wallets": [],
        }
    return alloc.exposure_snapshot()


@router.get("/portfolio/weights")
async def get_portfolio_weights() -> dict[str, float]:
    """获取归一化后的权重表（{wallet: share}，share 之和为 1）。"""
    mgr = _executor_mgr()
    alloc = getattr(mgr, "allocator", None)
    if alloc is None:
        return {}
    return alloc.normalized_weights()


@router.post("/portfolio/check")
async def check_portfolio_allocation(
    wallet: str = Query(..., description="被跟单钱包地址"),
    amount: float = Query(..., gt=0, description="目标金额 (USDC)"),
) -> dict[str, Any]:
    """试算一笔信号的分配结果（不实际下单），供前端预览。"""
    mgr = _executor_mgr()
    alloc = getattr(mgr, "allocator", None)
    if alloc is None:
        return {
            "allowed": True, "wallet": wallet, "weight": 1.0,
            "allocated_amount": amount, "reason": "未配置组合权重，全额执行",
        }
    result = alloc.allocate(
        wallet=wallet,
        target_amount=amount,
        global_max_amount=get_settings().risk.max_trade_amount,
    )
    return result.model_dump()


@router.get("/portfolio/positions", response_model=VirtualPortfolioSummary)
async def get_virtual_positions(
    wallet: str | None = Query(None, description="按钱包过滤；留空返回全部"),
) -> VirtualPortfolioSummary:
    """获取虚拟持仓盯市快照（模拟资金账户）。

    按 CLOB 真实价格实时盯市：
    - floating_pnl：持有期浮动盈亏（现价 vs 均价）
    - realized_pnl：已平仓部分按真实价结算的盈亏
    """
    mgr = _executor_mgr()
    tracker = getattr(mgr, "positions", None)
    if tracker is None:
        raise HTTPException(status_code=503, detail="虚拟持仓账本未初始化")
    return await tracker.snapshot(wallet=wallet)


@router.post("/portfolio/positions/reset")
async def reset_virtual_positions(
    wallet: str | None = Query(None, description="按钱包清仓；留空清空全部"),
) -> dict[str, Any]:
    """清空虚拟持仓（重置模拟账户，不影响历史执行记录）。"""
    mgr = _executor_mgr()
    store = getattr(mgr, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="状态存储未初始化")
    removed = await store.clear_virtual_positions(wallet=wallet)
    return {"removed": removed, "wallet": wallet or "*"}


# ── 目标钱包真实持仓（Polymarket 链上持仓） ───────────────────

@router.get("/wallets/positions")
async def get_wallet_positions(
    wallet: str | None = Query(None, description="钱包地址；留空则返回所有监控钱包"),
    status: str = Query("OPEN", description="持仓状态 OPEN/REDEEMABLE/CLOSED"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """获取目标钱包在 Polymarket 的真实持仓（链上数据，非模拟）。

    与 /portfolio/positions（虚拟账户盯市）互补：
    - 本端点：被跟单钱包的真实持仓，回答「他现在买了什么」
    - /portfolio/positions：我们自己的模拟盘持仓与盈亏
    """
    client = get_polymarket_client()
    store = get_store()

    targets: list[str] = []
    if wallet:
        targets = [wallet]
    else:
        targets = [w.address for w in await store.get_wallets() if w.enabled]

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for addr in targets:
        try:
            raw = await client.get_wallet_positions(user=addr, status=status, limit=limit)
            for p in raw:
                p["_wallet"] = addr
            results.extend(raw)
        except Exception as e:  # 单个钱包失败不影响其他钱包
            errors.append(f"{addr}: {e}")

    # 归一化字段，方便前端统一渲染
    positions = [_normalize_wallet_position(p) for p in results]
    positions.sort(key=lambda p: abs(p.get("current_value") or 0), reverse=True)

    total_value = sum(p.get("current_value") or 0 for p in positions)
    total_pnl = sum(p.get("cash_pnl") or 0 for p in positions)

    return {
        "wallet": wallet or "*",
        "status": status,
        "count": len(positions),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "positions": positions,
        "errors": errors,
    }


def _normalize_wallet_position(raw: dict[str, Any]) -> dict[str, Any]:
    """把 Polymarket positions 原始字段归一化为前端友好结构。"""
    def _num(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    return {
        "wallet": raw.get("_wallet", ""),
        "market_title": raw.get("title") or raw.get("market_title") or "",
        "market_slug": raw.get("slug") or raw.get("market_slug") or "",
        "condition_id": raw.get("conditionId") or raw.get("condition_id") or "",
        "outcome": raw.get("outcome") or "",
        "size": _num(raw.get("size")),
        "avg_price": _num(raw.get("avgPrice") or raw.get("avg_price")),
        "cur_price": _num(raw.get("curPrice") or raw.get("cur_price")),
        "current_value": _num(raw.get("currentValue") or raw.get("current_value")),
        "initial_value": _num(raw.get("initialValue") or raw.get("initial_value")),
        "cash_pnl": _num(raw.get("cashPnl") or raw.get("cash_pnl")),
        "percent_pnl": _num(raw.get("percentPnl") or raw.get("percent_pnl")),
        "redeemable": bool(raw.get("redeemable", False)),
        "end_date": raw.get("endDate") or raw.get("end_date") or "",
    }


# ── 链路自检（模拟交易注入） ──────────────────────────────────

@router.post("/simulate/trade", response_model=TradeEvent)
async def simulate_trade(req: SimulateTradeRequest) -> TradeEvent:
    """注入一笔模拟交易，走完整跟单链路（仅 paper 模式可用）。

    用于在不等待真实交易的情况下验证配置是否正确：
    模拟交易 → 策略管道 → 风控 → 组合分配 → 执行器 → 落库 → 前端推送。

    安全约束：executor.mode 必须为 paper，避免在实盘模式下误触发真实下单。
    """
    settings = get_settings()
    if settings.executor.mode != "paper":
        raise HTTPException(
            status_code=403,
            detail="模拟注入仅在 paper（模拟）模式下可用，请先在设置中切回 paper",
        )

    monitor = _wallet_monitor
    if monitor is None:
        raise HTTPException(status_code=503, detail="钱包监控尚未初始化")

    # 未指定钱包时取第一个已监控钱包
    wallet = req.wallet
    if not wallet:
        watched = monitor.get_watched_wallets()
        if not watched:
            raise HTTPException(status_code=400, detail="尚未添加任何监控钱包")
        wallet = watched[0]

    trade = TradeEvent(
        wallet=wallet,
        condition_id="0xsim" + uuid.uuid4().hex[:8],
        token_id="sim_token_" + uuid.uuid4().hex[:8],
        market_slug=req.market_slug,
        market_title=req.market_title,
        side=TradeSide(req.side),
        price=req.price,
        size=req.size,
        timestamp=datetime.now(timezone.utc),
        tx_hash="0xsim" + uuid.uuid4().hex[:16],
    )

    if not monitor.in_amount_range(trade):
        raise HTTPException(status_code=422, detail="模拟交易金额不在监测范围内，请调整金额或监测范围")

    # 落库 + 触发与真实捕获完全相同的回调链路
    try:
        await get_store().save_trade(trade)
    except Exception:
        logger.warning("保存模拟交易失败（不影响链路执行）", exc_info=True)

    await monitor._notify_callbacks(trade)

    logger.info(
        "注入模拟交易: 钱包 %s %s %.2f @ %.4f (%.2f USDC)",
        wallet, req.side, req.size, req.price, req.size * req.price,
    )
    return trade


@router.get("/simulate/status")
async def simulate_status() -> dict[str, Any]:
    """链路自检状态：是否可注入 + 前置条件检查结果。

    供前端自检面板展示「还差哪一步」，避免用户盲目点按钮。
    """
    settings = get_settings()
    store = get_store()

    wallets: list[WalletWatch] = []
    pipelines: list[StrategyPipeline] = []
    try:
        wallets = await store.get_wallets()
        pipelines = await store.get_pipelines()
    except Exception:
        logger.debug("读取自检状态失败", exc_info=True)

    enabled_pipelines = [p for p in pipelines if p.enabled]
    ready = bool(wallets) and bool(enabled_pipelines)
    mode_ok = settings.executor.mode == "paper"

    checks = [
        {
            "name": "监控钱包",
            "ok": bool(wallets),
            "detail": f"{len(wallets)} 个已添加" if wallets else "尚未添加钱包",
        },
        {
            "name": "启用中的策略管道",
            "ok": bool(enabled_pipelines),
            "detail": (
                f"{len(enabled_pipelines)}/{len(pipelines)} 条启用"
                if pipelines else "尚未创建策略管道"
            ),
        },
        {
            "name": "模拟模式",
            "ok": mode_ok,
            "detail": f"当前 {settings.executor.mode} 模式",
        },
    ]

    return {
        "ready": ready and mode_ok,
        "mode": settings.executor.mode,
        "wallets": len(wallets),
        "pipelines": len(pipelines),
        "enabled_pipelines": len(enabled_pipelines),
        "max_trade_amount": settings.risk.max_trade_amount,
        "checks": checks,
    }
