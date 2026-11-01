"""FastAPI 应用入口。

初始化所有核心服务，通过事件总线串联完整数据流：
  monitor.on_new_trade
    → EventBus 推送 trade_update
    → pipeline.process(trade) → signal
      → EventBus 推送 signal_generated
      → executor.execute(signal) → record
        → EventBus 推送 execution_update
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from urllib.parse import urlsplit
import ipaddress

from backend.api.events import Channels, get_event_bus, set_event_bus, EventBus
from backend.api.routes import (
    router as api_router,
    set_store,
    set_leaderboard_client,
    set_executor_manager,
    set_notifier,
    set_wallet_monitor,
    set_ws_client,
    set_polymarket_client,
)
from backend.api.ws import router as ws_router, manager as ws_manager, register_ws_with_eventbus
from backend.config import get_settings
from backend.core.leaderboard import LeaderboardClient
from backend.core.notifier import EventType, NotifierManager
from backend.core.order_executor import OrderExecutorManager
from backend.core.polymarket_client import PolymarketClient
from backend.core.wallet_monitor import WalletMonitor
from backend.core.ws_client import PolymarketWSClient
from backend.models.schemas import PortfolioAllocation
from backend.store.state import StateStore
from backend.core.trading_desk import TradingDesk
from backend.api.desk_routes import router as desk_router

# 导入策略模块以触发 @register_strategy 注册
import backend.strategies  # noqa: F401

logger = logging.getLogger(__name__)


async def _on_new_trade(trade: Any, ws_price: dict | None = None) -> None:
    """WalletMonitor 新交易回调。

    数据流：
    1. 推送 trade_update 到 EventBus
    2. 对每个启用的 pipeline 执行策略链 → 生成 signal
    3. 推送 signal_generated 到 EventBus
    4. 执行 signal → execution record
    5. 推送 execution_update 到 EventBus

    Args:
        trade: 新交易事件
        ws_price: WS 实时缓存价格（如果有），含 mid/best_bid/best_ask 等
    """
    bus = get_event_bus()

    # 1. 推送交易事件
    await bus.publish_nowait(Channels.TRADE_UPDATE, trade)

    # 2-5. 策略处理 + 执行（需要 app 上下文）
    app = _current_app
    if app is None:
        return

    store: StateStore = app.state.store
    executor_mgr: OrderExecutorManager = app.state.executor_manager
    ctx = app.state.strategy_context

    # 快捷跟单规则与高级策略共用最终执行通道。
    from backend.core.copy_rules import get_rules, build_signal
    for rule in await get_rules(store):
        try:
            signal = await build_signal(app.state.desk, rule, trade)
            if signal:
                await bus.publish_nowait(Channels.SIGNAL_GENERATED, signal)
                await app.state.desk.submit(signal)
        except Exception:
            logger.exception('快捷跟单规则 %s 处理失败', rule.id)

    # 获取所有启用的 pipeline
    try:
        pipelines = await store.get_pipelines()
    except Exception:
        logger.exception("获取 pipelines 失败")
        return

    for pipeline_config in pipelines:
        if not pipeline_config.enabled:
            continue
        # 检查目标钱包
        if pipeline_config.target_wallets and trade.wallet not in pipeline_config.target_wallets:
            continue

        try:
            from backend.strategies.pipeline import StrategyPipelineRunner
            runner = StrategyPipelineRunner.from_config(pipeline_config)
            signal = await runner.process(trade, ctx)
        except Exception:
            logger.exception("策略管道 %s 处理失败", pipeline_config.id)
            continue

        if signal is None:
            continue  # 被过滤

        # 3. 推送信号
        await bus.publish_nowait(Channels.SIGNAL_GENERATED, signal)

        # 保存信号到 DB
        try:
            await store.save_signal(signal)
        except Exception:
            logger.debug("保存信号失败", exc_info=True)

        # 4. 执行
        try:
            await app.state.desk.submit(signal)
        except Exception:
            logger.exception("执行信号 %s 失败", signal.signal_id)
            continue

        # 执行结果由交易台在实际审批或自动执行完成后统一推送。


# 全局 app 引用（供回调访问）
_current_app: FastAPI | None = None


# ── 通知事件处理 ────────────────────────────────────────────

def _fmt_amount(value: float | None) -> str:
    """格式化金额。"""
    if value is None:
        return "-"
    return f"{value:,.2f}"


async def _on_trade_update(trade: Any) -> None:
    """EventBus trade_update → 大额交易通知。

    只在金额达到 ``min_notify_amount`` 阈值时才通知（过滤规则在
    NotifierManager 中统一执行）。
    """
    notifier: NotifierManager | None = getattr(_current_app.state, "notifier", None)
    if notifier is None:
        return

    try:
        amount = float(getattr(trade, "price", 0)) * float(getattr(trade, "size", 0))
        side = getattr(getattr(trade, "side", None), "value", str(getattr(trade, "side", "")))
        title = f"{getattr(trade, 'market_title', '') or getattr(trade, 'market_slug', '') or '未知市场'}"
        message = (
            f"钱包 `{getattr(trade, 'wallet', '')}` 以 {side} 方向买入\n"
            f"价格 {getattr(trade, 'price', 0):.4f} | 数量 {getattr(trade, 'size', 0):,.2f} "
            f"| 金额 {amount:,.2f} USDC"
        )
        data = {
            "wallet": getattr(trade, "wallet", ""),
            "market": title,
            "side": side,
            "price": getattr(trade, "price", 0),
            "size": getattr(trade, "size", 0),
            "amount": amount,
            "tx_hash": getattr(trade, "tx_hash", ""),
        }
        await notifier.send(EventType.TRADE_DETECTED, title, message, data)
    except Exception:
        logger.debug("交易通知失败", exc_info=True)


async def _on_signal_generated(signal: Any) -> None:
    """EventBus signal_generated → 跟单信号通知。"""
    notifier: NotifierManager | None = getattr(_current_app.state, "notifier", None)
    if notifier is None:
        return

    try:
        market = getattr(signal, "market", None)
        market_title = getattr(market, "title", "") or getattr(market, "slug", "") or "未知市场"
        side = getattr(getattr(signal, "side", None), "value", str(getattr(signal, "side", "")))
        price = float(getattr(signal, "price", 0))
        size = float(getattr(signal, "size", 0))
        amount = price * size
        source_wallet = getattr(getattr(signal, "source_trade", None), "wallet", "")
        title = f"跟单信号 · {side} · {market_title}"
        message = (
            f"流水线 `{getattr(signal, 'pipeline_id', '')}` 生成信号\n"
            f"价格 {price:.4f} | 数量 {size:,.2f} | 金额 {amount:,.2f} USDC\n"
            f"源钱包 {source_wallet or '-'}"
        )
        data = {
            "wallet": source_wallet,
            "market": market_title,
            "side": side,
            "price": price,
            "size": size,
            "amount": amount,
            "pipeline_id": getattr(signal, "pipeline_id", ""),
            "signal_id": getattr(signal, "signal_id", ""),
        }
        await notifier.send(EventType.SIGNAL_GENERATED, title, message, data)
    except Exception:
        logger.debug("信号通知失败", exc_info=True)


async def _on_execution_update(record: Any) -> None:
    """EventBus execution_update → 成交 / 失败 / 风控拦截通知。

    风控拦截的判定：状态为 FAILED 且 error 以「风控拒绝」开头。
    """
    notifier: NotifierManager | None = getattr(_current_app.state, "notifier", None)
    if notifier is None:
        return

    try:
        status = getattr(getattr(record, "status", None), "value", str(getattr(record, "status", "")))
        error = getattr(record, "error", "") or ""
        fill_price = getattr(record, "fill_price", None)
        fill_size = getattr(record, "fill_size", None)
        pnl = getattr(record, "pnl", None)

        if status == "FILLED":
            amount = (fill_price or 0) * (fill_size or 0)
            title = "✅ 跟单成交"
            message = (
                f"订单 `{getattr(record, 'order_id', '') or '-'}` 已成交\n"
                f"成交价 {_fmt_amount(fill_price)} | 数量 {_fmt_amount(fill_size)} "
                f"| 金额 {_fmt_amount(amount)} USDC\n"
                f"盈亏 {_fmt_amount(pnl)} USDC"
            )
            data = {
                "signal_id": getattr(record, "signal_id", ""),
                "order_id": getattr(record, "order_id", ""),
                "price": fill_price,
                "size": fill_size,
                "amount": amount,
                "pnl": pnl,
                "mode": getattr(getattr(record, "mode", None), "value", ""),
                "status": status,
            }
            await notifier.send(EventType.EXECUTION_FILLED, title, message, data)
            return

        # 失败 / 风控拦截
        is_risk = error.startswith("风控拒绝") or "风控" in error[:6]
        event_type = EventType.RISK_BLOCKED if is_risk else EventType.EXECUTION_FAILED
        title = "🛡 风控拦截" if is_risk else "❌ 跟单失败"
        message = (
            f"信号 `{getattr(record, 'signal_id', '') or '-'}` 执行未成功\n"
            f"原因：{error or '未知错误'}"
        )
        data = {
            "signal_id": getattr(record, "signal_id", ""),
            "order_id": getattr(record, "order_id", ""),
            "status": status,
            "error": error,
            "mode": getattr(getattr(record, "mode", None), "value", ""),
        }
        await notifier.send(event_type, title, message, data)
    except Exception:
        logger.debug("执行通知失败", exc_info=True)


async def _daily_summary_worker(store: StateStore, notifier: NotifierManager) -> None:
    """每日汇总任务（cron 风格）。

    每天在固定时间（UTC 小时/分钟，由配置决定）发送一次汇总：
    今日交易数、信号数、成交数、盈亏。
    """
    from datetime import datetime, timedelta, timezone

    while True:
        try:
            cfg = notifier.config
            hour = int(getattr(cfg, "daily_summary_hour", 8) or 0)
            minute = int(getattr(cfg, "daily_summary_minute", 0) or 0)

            now = datetime.now(timezone.utc)
            # 计算下一个触发时刻（今天该时刻已过则顺延到明天）
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)

            wait_seconds = max(1.0, (target - now).total_seconds())
            logger.info(
                "每日汇总将在 %s 触发（UTC %02d:%02d）",
                target.isoformat(), hour, minute,
            )
            await asyncio.sleep(wait_seconds)

            await _send_daily_summary(store, notifier)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("每日汇总任务异常")
            await asyncio.sleep(60)


async def _send_daily_summary(store: StateStore, notifier: NotifierManager) -> None:
    """生成并发送每日汇总。"""
    from datetime import datetime, timezone

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trades = await store.get_trades(limit=500)
        executions = await store.get_executions(limit=500)
        signals = await store.get_signals(limit=500)

        today_trades = sum(1 for t in trades if t.timestamp.strftime("%Y-%m-%d") == today)
        today_signals = sum(1 for s in signals if s.created_at.strftime("%Y-%m-%d") == today)
        today_executions = sum(1 for e in executions if e.timestamp.strftime("%Y-%m-%d") == today)
        total_pnl = sum(e.pnl or 0 for e in executions if e.pnl is not None)

        title = f"📊 每日汇总 · {today}"
        message = (
            f"今日交易 {today_trades} 笔 | 信号 {today_signals} 个 | 成交 {today_executions} 笔\n"
            f"累计盈亏 {total_pnl:+,.2f} USDC"
        )
        data = {
            "date": today,
            "today_trades": today_trades,
            "today_signals": today_signals,
            "today_executions": today_executions,
            "total_pnl": total_pnl,
        }
        await notifier.send(EventType.DAILY_SUMMARY, title, message, data, bypass_filters=False)
        logger.info("每日汇总已发送")
    except Exception:
        logger.exception("生成每日汇总失败")


async def _load_persisted_notification_config(store: StateStore, settings: Any) -> Any:
    """从 DB config 表加载持久化的通知配置。

    前端保存的通知配置优先于 YAML / 环境变量（前端保存时会同时写环境变量，
    这里作为双保险）。加载失败时返回 None，保留默认配置。
    """
    from backend.api.routes import NOTIFICATION_CONFIG_KEY
    from backend.config import NotificationConfig

    try:
        raw = await store.get_config(NOTIFICATION_CONFIG_KEY)
    except Exception:
        logger.debug("读取持久化通知配置失败", exc_info=True)
        return None

    if not raw or not isinstance(raw, dict):
        return None

    try:
        return NotificationConfig(**raw)
    except Exception:
        logger.exception("持久化通知配置无效，忽略")
        return None


async def _ws_status_publisher(ws_client: PolymarketWSClient, bus: EventBus) -> None:
    """定期推送 WebSocket 客户端状态到 EventBus。

    每 5 秒推送一次 ws_status 事件，包含连接状态、订阅市场数等。
    前端据此更新 WS 连接状态指示器。
    """
    import asyncio as _asyncio

    while True:
        try:
            await _asyncio.sleep(5)
            status = ws_client.get_status()
            await bus.publish_nowait(Channels.WS_STATUS, status)
        except _asyncio.CancelledError:
            break
        except Exception:
            logger.debug("WS 状态推送失败", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化所有服务，启动监控，关闭时清理。"""
    global _current_app
    _current_app = app

    settings = get_settings()
    # Windows 控制台默认 GBK，日志里的中文/emoji 会触发 UnicodeEncodeError
    # 导致 StreamHandler 崩溃。显式挂一个 UTF-8 容错的 handler。
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    for _h in list(_root.handlers):
        _root.removeHandler(_h)
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    try:
        _handler.stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    _root.addHandler(_handler)

    # ── 初始化事件总线 ──
    bus = EventBus()
    set_event_bus(bus)

    # ── 初始化数据库 ──
    store = StateStore(db_path=settings.db_path)
    await store.connect()
    app.state.store = store
    set_store(store)

    # ── 初始化 API 客户端 ──
    client = PolymarketClient(settings)
    app.state.client = client
    set_polymarket_client(client)

    # ── 初始化 WebSocket 客户端（Market Channel 实时价格） ──
    ws_client: PolymarketWSClient | None = None
    ws_status_task: asyncio.Task | None = None
    if settings.monitor.ws_enabled:
        ws_client = PolymarketWSClient(settings)
        app.state.ws_client = ws_client
        set_ws_client(ws_client)
        await ws_client.connect()
        logger.info("WebSocket Market Channel 客户端已启动")
    else:
        logger.info("WebSocket 实时价格未启用 (monitor.ws_enabled=false)")

    # ── 初始化排行榜客户端 ──
    leaderboard_client = LeaderboardClient(settings)
    app.state.leaderboard_client = leaderboard_client
    set_leaderboard_client(leaderboard_client)

    # ── 初始化执行器管理器 ──
    executor_manager = OrderExecutorManager(settings, store=store, client=client)
    # 启动后必须重新自检与明确启用，不能从上次环境变量恢复真实资金发送权限。
    executor_manager.set_mode('paper')
    settings.executor.mode = 'paper'
    from backend.accounts.service import AccountService
    from backend.accounts.coordinator import AccountCoordinator
    executor_manager.accounts = AccountService(executor_manager)
    await executor_manager.accounts.initialize()
    executor_manager.account_coordinator = AccountCoordinator(executor_manager.accounts)
    from backend.accounts.browser import BrowserSigning
    executor_manager.browser_signing = BrowserSigning(executor_manager.accounts, executor_manager.account_coordinator)
    app.state.executor_manager = executor_manager
    set_executor_manager(executor_manager)

    # ── 恢复组合跟单配置（多钱包权重分配） ──
    try:
        saved = await store.get_config("portfolio_allocation")
        if saved:
            executor_manager.set_portfolio_allocation(
                PortfolioAllocation(**saved)
            )
            logger.info(
                "已恢复组合跟单配置: %d 个钱包",
                len(saved.get("allocations", []) or []),
            )
    except Exception:
        logger.warning("恢复组合跟单配置失败（使用默认配置）", exc_info=True)

    # ── 初始化策略上下文 ──
    from backend.strategies.base import StrategyContext
    app.state.strategy_context = StrategyContext()

    # ── 初始化钱包监控 ──
    monitor = WalletMonitor(settings, client, store, ws_client=ws_client)
    app.state.monitor = monitor
    set_wallet_monitor(monitor)

    # 注册新交易回调
    monitor.on_new_trade(_on_new_trade)

    # ── 初始化通知管理器 ──
    # 先从 DB 读取持久化的通知配置（前端保存过则覆盖 YAML / 环境变量）
    notification_cfg = await _load_persisted_notification_config(store, settings)
    notifier = NotifierManager.from_settings(settings, store=store)
    if notification_cfg is not None:
        settings.notification = notification_cfg
        notifier.update_config(notification_cfg)
    app.state.notifier = notifier
    set_notifier(notifier)
    await notifier.start()
    logger.info(
        "通知管理器已启动 (渠道: %s)",
        ", ".join(notifier.get_channels()) or "无（未启用任何通知渠道）",
    )

    # ── 注册通知事件订阅 ──
    await bus.subscribe(Channels.TRADE_UPDATE, _on_trade_update)
    await bus.subscribe(Channels.SIGNAL_GENERATED, _on_signal_generated)
    await bus.subscribe(Channels.EXECUTION_UPDATE, _on_execution_update)

    # ── 注册 WebSocket 到 EventBus ──
    register_ws_with_eventbus()

    # ── 启动钱包监控 ──
    app.state.desk = TradingDesk(store, executor_manager, client)
    from backend.core.smart_trading import SmartTrading
    app.state.desk.smart = SmartTrading(app.state.desk, executor_manager.accounts.vault)
    await app.state.desk.smart.initialize()
    await app.state.desk.recover()
    desk_exit_task = asyncio.create_task(app.state.desk.exit_worker())
    live_health_task = asyncio.create_task(executor_manager.live_connection.worker())
    account_receipt_task = asyncio.create_task(executor_manager.account_coordinator.worker(app.state.desk))
    await monitor.start()
    from backend.core.market_sync import MarketSync
    app.state.market_sync = MarketSync(store, client, monitor, app.state.desk, ws_client)
    app.state.desk.market_data = app.state.market_sync
    await app.state.market_sync.start()
    smart_strategy_task = asyncio.create_task(app.state.desk.smart.worker())

    # ── 启动 WS 状态推送任务 ──
    if ws_client is not None:
        ws_status_task = asyncio.create_task(_ws_status_publisher(ws_client, bus))

    # ── 启动每日汇总任务 ──
    daily_summary_task: asyncio.Task | None = asyncio.create_task(
        _daily_summary_worker(store, notifier)
    )

    logger.info("🚀 Polymarket Trader 已启动 (port=%d, mode=%s)", settings.server.port, settings.executor.mode)
    yield

    # ── 清理 ──
    smart_strategy_task.cancel()
    await asyncio.gather(smart_strategy_task, return_exceptions=True)
    live_health_task.cancel()
    account_receipt_task.cancel()
    await asyncio.gather(account_receipt_task, return_exceptions=True)
    await asyncio.gather(live_health_task, return_exceptions=True)
    await app.state.market_sync.stop()
    desk_exit_task.cancel()
    try:
        await desk_exit_task
    except asyncio.CancelledError:
        pass
    if daily_summary_task and not daily_summary_task.done():
        daily_summary_task.cancel()
        try:
            await daily_summary_task
        except asyncio.CancelledError:
            pass
    await notifier.flush(timeout=5.0)
    await notifier.stop()
    if ws_status_task and not ws_status_task.done():
        ws_status_task.cancel()
        try:
            await ws_status_task
        except asyncio.CancelledError:
            pass
    if ws_client is not None:
        await ws_client.disconnect()
    await monitor.stop()
    await ws_manager.close()
    await leaderboard_client.close()
    await client.close()
    await store.close()
    logger.info("👋 Polymarket Trader 已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    settings = get_settings()

    app = FastAPI(
        title="Polymarket Trader",
        description="实时监测 Polymarket 钱包交易、多策略可组合跟单系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    @app.middleware('http')
    async def local_write_access(request, call_next):
        if request.url.path.startswith('/api/') and (request.method not in ('GET', 'HEAD', 'OPTIONS') or request.url.path == '/api/executor/preflight' or request.url.path.startswith(('/api/accounts','/api/smart'))):
            peer = request.client.host if request.client else ''
            try:
                local = ipaddress.ip_address(peer).is_loopback
            except ValueError:
                local = peer == 'testclient'
            origin = request.headers.get('origin')
            trusted_host = request.url.hostname in ('127.0.0.1', 'localhost', '::1', 'testserver')
            if not local or not trusted_host or (origin and urlsplit(origin).netloc != request.url.netloc):
                return JSONResponse({'detail': '资金与配置操作仅允许本机同源访问'}, status_code=403)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(api_router)
    from backend.accounts.routes import router as accounts_router
    app.include_router(accounts_router)
    from backend.api.smart_routes import router as smart_router
    app.include_router(smart_router)
    app.include_router(desk_router)
    app.include_router(ws_router)

    # 前端静态文件
    frontend_path = Path(settings.frontend_dir)
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")

    return app


app = create_app()
