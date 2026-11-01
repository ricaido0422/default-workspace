"""策略回测引擎。

用历史交易数据在「纸面」环境中重放策略流水线，让用户在下真实跟单前
先验证策略效果。

核心流程：
1. 加载钱包历史交易（优先 SQLite trades 表，无数据时回退 Polymarket Data API）
2. 按时间顺序逐笔送入 StrategyPipelineRunner.process()
3. 通过策略链的信号模拟成交（成交价 = 触发价 ± 滑点，滑点 = tick_size × slippage_ticks）
4. 用同一 token_id 的后续价格序列计算单笔盈亏
5. 与「直接镜像跟单」基准对比，输出超额收益

盈亏约定：
- BUY  ：pnl = size × (exit_price - fill_price)
- SELL ：pnl = size × (fill_price - exit_price)
- exit_price 取该 token_id 在成交时刻之后的第一笔历史成交价；
  若没有后续成交则 exit_price = fill_price，pnl = 0。

本模块不依赖 FastAPI，可独立 import：
    python -c "from backend.core.backtest import BacktestEngine"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.models.schemas import (
    BacktestFilterStat,
    BacktestMetrics,
    BacktestParams,
    BacktestResult,
    BacktestTradeDetail,
    StrategyPipeline,
    StrategyType,
    TradeEvent,
    TradeSide,
)
from backend.strategies.base import STRATEGY_REGISTRY, Strategy, StrategyContext
from backend.strategies.pipeline import StrategyPipelineRunner

# 导入策略包以触发 @register_strategy 注册（保证 STRATEGY_REGISTRY 已填充）
import backend.strategies  # noqa: F401  isort:skip

logger = logging.getLogger(__name__)


# ── 常量 ────────────────────────────────────────────────────

DEFAULT_TICK_SIZE = 0.01
"""默认最小价格变动单位。"""

MAX_API_PAGES = 10
"""回退到 Data API 时最多翻页数。"""

MAX_TRADES = 5000
"""单次回测最多处理的交易数。"""

MIN_PRICE = 0.01
MAX_PRICE = 0.99

PROFIT_FACTOR_CAP = 999.0
"""无亏损时盈亏比的封顶值（避免 JSON 中出现 Infinity）。"""


# ── 策略元信息（用于 /backtest/strategies 与过滤原因归因） ──

STRATEGY_NAMES: dict[str, str] = {
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

STRATEGY_DESCRIPTIONS: dict[str, str] = {
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


# ── 工具函数 ────────────────────────────────────────────────

def _aware(dt: datetime) -> datetime:
    """把 datetime 统一成 UTC 时区感知对象。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _strategy_type_of(strategy: Strategy) -> StrategyType | None:
    """由策略实例反查其 StrategyType。

    STRATEGY_REGISTRY 是 StrategyType → 策略类 的映射，
    这里反向查找以识别策略种类（用于生成过滤原因）。
    """
    for strategy_type, strategy_cls in STRATEGY_REGISTRY.items():
        if isinstance(strategy, strategy_cls):
            return strategy_type
    return None


def _strategy_label(strategy: Strategy) -> str:
    """策略的可读标签。"""
    stype = _strategy_type_of(strategy)
    name = STRATEGY_NAMES.get(stype.value, "") if stype else ""
    if not name:
        name = strategy.__class__.__name__
    if strategy.strategy_id:
        return f"{name} ({strategy.strategy_id})"
    return name


def _describe_filter(strategy: Strategy, trade: TradeEvent | None = None) -> str:
    """生成该策略的过滤原因描述。"""
    stype = _strategy_type_of(strategy)
    params = getattr(strategy, "params", {}) or {}

    if stype == StrategyType.SIZE_FILTER:
        threshold = float(params.get("threshold", 50.0))
        value = (trade.size * trade.price) if trade else 0.0
        return f"数量过滤：交易金额 {value:.2f} 低于阈值 {threshold:.2f}"

    if stype == StrategyType.MARKET_FILTER:
        mode = params.get("mode", "whitelist")
        slugs: list[str] = list(params.get("market_slugs", []) or [])
        slug = trade.market_slug if trade else ""
        if mode == "blacklist":
            return f"市场过滤：{slug or '-'} 位于黑名单"
        if slugs:
            return f"市场过滤：{slug or '-'} 不在白名单 {slugs}"
        return f"市场过滤：{slug or '-'} 不满足过滤条件"

    if stype == StrategyType.POSITION_CAP:
        per_market = float(params.get("max_per_market", 1000.0))
        total = float(params.get("max_total", 10000.0))
        return f"仓位上限：超出限制 (单市场 {per_market:.0f} / 总仓位 {total:.0f})"

    if stype == StrategyType.STOP_LOSS:
        sl = float(params.get("stop_loss_pct", 0.1))
        tp = float(params.get("take_profit_pct", 0.2))
        return f"止盈止损：触发阈值 (止损 {sl:.0%} / 止盈 {tp:.0%})"

    if stype == StrategyType.PRICE_OFFSET:
        return "价格偏移：偏移后价格超出可交易区间"

    if stype == StrategyType.PROPORTIONAL:
        return "比例跟单：缩放后的数量无效"

    if stype == StrategyType.FIXED_AMOUNT:
        return "固定金额：金额参数无效"

    if stype == StrategyType.REVERSE:
        return "反向跟单：方向翻转失败"

    if stype == StrategyType.DELAYED:
        return "延迟执行：延迟参数超出允许范围"

    return f"{_strategy_label(strategy)}：策略条件不满足"


def build_strategy_catalog() -> list[dict[str, Any]]:
    """生成可用于回测的策略类型目录（复用 STRATEGY_REGISTRY）。"""
    catalog: list[dict[str, Any]] = []
    for strategy_type, strategy_cls in STRATEGY_REGISTRY.items():
        type_val = strategy_type.value
        catalog.append({
            "type": type_val,
            "category": (
                strategy_cls.category.value
                if hasattr(strategy_cls, "category")
                else ""
            ),
            "name": STRATEGY_NAMES.get(type_val, type_val),
            "description": STRATEGY_DESCRIPTIONS.get(type_val, ""),
            "params_schema": getattr(strategy_cls, "param_schema", {}) or {},
        })
    return catalog


def _build_price_series(
    trades: list[TradeEvent],
) -> dict[str, list[tuple[datetime, float]]]:
    """构建 token_id → [(时间, 价格)] 的价格序列（按时间升序）。"""
    series: dict[str, list[tuple[datetime, float]]] = {}
    for trade in trades:
        if not trade.token_id:
            continue
        series.setdefault(trade.token_id, []).append(
            (_aware(trade.timestamp), float(trade.price))
        )
    for points in series.values():
        points.sort(key=lambda p: p[0])
    return series


def _lookup_exit_price(
    series: dict[str, list[tuple[datetime, float]]],
    token_id: str,
    after: datetime,
) -> float | None:
    """查找成交时刻之后该 token_id 的第一笔价格。"""
    points = series.get(token_id)
    if not points:
        return None
    after = _aware(after)
    for ts, price in points:
        if ts > after:
            return price
    return None


def _build_runner(
    pipeline_config: StrategyPipeline | StrategyPipelineRunner,
) -> tuple[StrategyPipelineRunner, list["_StrategyProbe"]]:
    """从流水线配置构建执行器（带过滤原因探针）。

    Returns:
        (runner, probes)：probes 与 runner.strategies 一一对应。
    """
    if isinstance(pipeline_config, StrategyPipelineRunner):
        return pipeline_config, []

    probes: list[_StrategyProbe] = []
    strategies: list[Strategy] = []
    for cfg in sorted(pipeline_config.strategies, key=lambda s: s.display_order):
        if not cfg.enabled:
            continue
        strategy_cls = STRATEGY_REGISTRY.get(cfg.type)
        if strategy_cls is None:
            logger.warning("未知策略类型 %s，跳过", cfg.type)
            continue
        inner = strategy_cls(params=cfg.params, strategy_id=cfg.id, enabled=cfg.enabled)
        probe = _StrategyProbe(inner)
        probes.append(probe)
        strategies.append(probe)

    runner = StrategyPipelineRunner(
        strategies=strategies,
        pipeline_id=pipeline_config.id,
        pipeline_name=pipeline_config.name,
    )
    return runner, probes


# ── 策略探针 ────────────────────────────────────────────────

class _StrategyProbe(Strategy):
    """策略探针：包装真实策略，记录其过滤原因（供归因分析）。"""

    def __init__(self, inner: Strategy) -> None:
        super().__init__(
            params=inner.params,
            strategy_id=inner.strategy_id,
            enabled=inner.enabled,
        )
        self.inner = inner
        self.category = inner.category
        self.filter_reason: str = ""

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        try:
            result = await self.inner.apply(trade, ctx)
        except Exception:
            logger.debug("策略 %s 执行异常", self.strategy_id, exc_info=True)
            self.filter_reason = f"{_strategy_label(self.inner)}：策略执行异常"
            return None
        if result is None:
            self.filter_reason = _describe_filter(self.inner, trade)
            return None
        return result


# ── 仓位账本 ────────────────────────────────────────────────

class _PositionBook:
    """极简仓位账本：跟踪各市场敞口，用于资金 / 仓位上限约束。"""

    def __init__(self, initial_capital: float, max_position_per_market: float) -> None:
        self.initial_capital = float(initial_capital)
        self.max_position_per_market = float(max_position_per_market)
        self.markets: dict[str, dict[str, float | str]] = {}

    def exposure(self, condition_id: str) -> float:
        pos = self.markets.get(condition_id)
        return float(pos["value"]) if pos else 0.0

    def total_exposure(self) -> float:
        return sum(float(p["value"]) for p in self.markets.values())

    def can_open(self, condition_id: str, notional: float) -> bool:
        """检查是否还能开仓（单市场仓位 + 初始资金约束）。"""
        if notional <= 0:
            return False
        eps = 1e-9
        if self.exposure(condition_id) + notional > self.max_position_per_market + eps:
            return False
        if self.total_exposure() + notional > self.initial_capital + eps:
            return False
        return True

    def open(
        self,
        condition_id: str,
        side: TradeSide,
        notional: float,
        price: float,
        size: float,
    ) -> None:
        """记录一笔开仓。"""
        pos = self.markets.setdefault(
            condition_id,
            {"value": 0.0, "size": 0.0, "entry_price": price, "side": side.value},
        )
        if side == TradeSide.BUY:
            pos["value"] = float(pos["value"]) + notional
            pos["size"] = float(pos["size"]) + size
            pos["side"] = TradeSide.BUY.value
        else:
            pos["value"] = max(0.0, float(pos["value"]) - notional)
            pos["size"] = max(0.0, float(pos["size"]) - size)
            pos["side"] = TradeSide.SELL.value
        pos_size = float(pos["size"])
        pos["entry_price"] = (float(pos["value"]) / pos_size) if pos_size > 0 else price

    def to_ctx_positions(self) -> dict[str, dict[str, Any]]:
        """转换为 StrategyContext 需要的 positions 结构。"""
        result: dict[str, dict[str, Any]] = {}
        for condition_id, pos in self.markets.items():
            result[condition_id] = {
                "size": float(pos["size"]),
                "side": str(pos["side"]),
                "entry_price": float(pos["entry_price"]),
                "current_price": float(pos["entry_price"]),
                "value": float(pos["value"]),
            }
        return result


# ── 回测引擎 ────────────────────────────────────────────────

class BacktestEngine:
    """策略回测引擎。

    Attributes:
        store: StateStore（可选），用于读取 trades 表历史交易
        client: PolymarketClient（可选），DB 无数据时回退拉取
        tick_size: 默认最小价格变动单位（可被 params.tick_size 覆盖）
    """

    def __init__(
        self,
        store: Any = None,
        client: Any = None,
        tick_size: float = DEFAULT_TICK_SIZE,
    ) -> None:
        self.store = store
        self.client = client
        self.tick_size = tick_size
        self._owns_client = False

    # ── 生命周期 ───────────────────────────────────────────

    async def aclose(self) -> None:
        """释放自建客户端。"""
        if self.client is not None and self._owns_client:
            try:
                await self.client.close()
            except Exception:
                logger.debug("关闭回测客户端失败", exc_info=True)
            self.client = None
            self._owns_client = False

    async def _ensure_client(self) -> Any:
        """惰性创建 PolymarketClient（仅在需要拉取历史数据时）。"""
        if self.client is None:
            from backend.config import get_settings
            from backend.core.polymarket_client import PolymarketClient

            self.client = PolymarketClient(get_settings())
            self._owns_client = True
        return self.client

    # ── 数据加载 ───────────────────────────────────────────

    async def _load_trades(
        self, wallet_address: str, params: BacktestParams
    ) -> tuple[list[TradeEvent], str]:
        """加载并过滤历史交易。

        优先读取 DB trades 表；表内无数据时回退到 Polymarket Data API。

        Returns:
            (按时间升序的交易列表, 数据来源标记 "db"/"api")
        """
        trades: list[TradeEvent] = []
        source = "db"

        if self.store is not None:
            try:
                trades = list(await self.store.get_trades(wallet=wallet_address, limit=MAX_TRADES))
            except Exception:
                logger.debug("从数据库读取交易失败", exc_info=True)
                trades = []

        if not trades:
            source = "api"
            trades = await self._fetch_trades_from_api(wallet_address)

        # 时间升序
        trades.sort(key=lambda t: _aware(t.timestamp))

        # 时间范围过滤
        start = _aware(params.start_time) if params.start_time else None
        end = _aware(params.end_time) if params.end_time else None
        if start is not None or end is not None:
            filtered: list[TradeEvent] = []
            for trade in trades:
                ts = _aware(trade.timestamp)
                if start is not None and ts < start:
                    continue
                if end is not None and ts > end:
                    continue
                filtered.append(trade)
            trades = filtered

        if len(trades) > MAX_TRADES:
            trades = trades[:MAX_TRADES]
        return trades, source

    async def _fetch_trades_from_api(self, wallet_address: str) -> list[TradeEvent]:
        """从 Data API v2 拉取钱包历史交易（带翻页）。"""
        try:
            client = await self._ensure_client()
        except Exception:
            logger.warning("无法创建 Polymarket 客户端", exc_info=True)
            return []

        collected: list[TradeEvent] = []
        cursor: str | None = None
        for _ in range(MAX_API_PAGES):
            try:
                result = await client.get_wallet_activity(
                    user=wallet_address,
                    activity_type="TRADE",
                    cursor=cursor,
                    limit=500,
                )
            except Exception:
                logger.warning("拉取钱包 %s 历史交易失败", wallet_address, exc_info=True)
                break

            batch = list(result.get("trades", []) or [])
            collected.extend(batch)
            cursor = result.get("next_cursor")
            if not cursor or not batch or len(collected) >= MAX_TRADES:
                break

        return collected[:MAX_TRADES]

    # ── 核心回测 ───────────────────────────────────────────

    async def run_backtest(
        self,
        wallet_address: str,
        pipeline_config: StrategyPipeline | StrategyPipelineRunner,
        params: BacktestParams,
    ) -> BacktestResult:
        """对单个钱包的历史交易运行策略回测。

        Args:
            wallet_address: 钱包地址
            pipeline_config: 策略流水线配置（或已构建好的 runner）
            params: 回测参数

        Returns:
            完整的 BacktestResult（含指标、逐笔明细、盈亏曲线、过滤归因）
        """
        params = params or BacktestParams()
        tick_size = params.tick_size or self.tick_size

        trades, data_source = await self._load_trades(wallet_address, params)
        series = _build_price_series(trades)
        runner, probes = _build_runner(pipeline_config)

        pipeline_id = getattr(runner, "pipeline_id", "") or ""
        pipeline_name = getattr(runner, "pipeline_name", "") or ""

        ctx = StrategyContext()
        book = _PositionBook(params.initial_capital, params.max_position_per_market)

        details: list[BacktestTradeDetail] = []
        equity_curve: list[float] = []
        equity = 0.0

        total_signals = 0
        executed_trades = 0
        wins = 0
        gross_profit = 0.0
        gross_loss = 0.0
        max_win = 0.0
        max_loss = 0.0

        filter_counts: dict[str, int] = {}
        filter_stats: dict[tuple[str, str, str], int] = {}

        for trade in trades:
            # ── 1. 送入策略链 ──────────────────────────────
            for probe in probes:
                probe.filter_reason = ""

            signal = await runner.process(trade, ctx)

            if signal is None:
                reason = "策略链过滤"
                strategy_id = ""
                strategy_name = ""
                for probe in probes:
                    if probe.filter_reason:
                        reason = probe.filter_reason
                        strategy_id = probe.strategy_id
                        stype = _strategy_type_of(probe.inner)
                        strategy_name = (
                            STRATEGY_NAMES.get(stype.value, stype.value)
                            if stype else probe.inner.__class__.__name__
                        )
                        break
                _count_filter(filter_counts, filter_stats, strategy_id, strategy_name, reason)
                details.append(
                    BacktestTradeDetail(
                        source_trade=trade,
                        passed=False,
                        executed=False,
                        reason=reason,
                        filter_strategy_id=strategy_id,
                        filter_strategy_name=strategy_name,
                        timestamp=_aware(trade.timestamp),
                    )
                )
                continue

            total_signals += 1

            # ── 2. 模拟成交价（含滑点）────────────────────
            fill_price = self._apply_slippage(signal.side, signal.price, tick_size, params.slippage_ticks)
            fill_size = float(signal.size)
            notional = fill_price * fill_size

            # ── 3. 回测级约束（最小金额 / 仓位 / 资金）─────
            constraint_reason = self._check_constraints(
                book, trade.condition_id, notional, params
            )
            if constraint_reason:
                _count_filter(filter_counts, filter_stats, "", "回测约束", constraint_reason)
                details.append(
                    BacktestTradeDetail(
                        source_trade=trade,
                        passed=True,
                        executed=False,
                        reason=constraint_reason,
                        strategy_chain=list(signal.strategy_chain),
                        side=signal.side,
                        fill_price=round(fill_price, 6),
                        fill_size=round(fill_size, 6),
                        timestamp=_aware(trade.timestamp),
                    )
                )
                continue

            # ── 4. 计算盈亏（同一 token 的后续价格）────────
            exit_price = _lookup_exit_price(series, trade.token_id, trade.timestamp)
            if exit_price is None:
                exit_price = fill_price
            pnl = self._calc_pnl(signal.side, fill_size, fill_price, exit_price)

            book.open(trade.condition_id, signal.side, notional, fill_price, fill_size)
            equity += pnl
            equity_curve.append(round(equity, 6))
            executed_trades += 1
            if pnl > 0:
                wins += 1
                gross_profit += pnl
                max_win = max(max_win, pnl)
            elif pnl < 0:
                gross_loss += abs(pnl)
                max_loss = min(max_loss, pnl)

            details.append(
                BacktestTradeDetail(
                    source_trade=trade,
                    passed=True,
                    executed=True,
                    reason="策略通过并模拟成交",
                    strategy_chain=list(signal.strategy_chain),
                    side=signal.side,
                    fill_price=round(fill_price, 6),
                    fill_size=round(fill_size, 6),
                    exit_price=round(exit_price, 6),
                    pnl=round(pnl, 6),
                    timestamp=_aware(trade.timestamp),
                )
            )

            # ── 5. 同步策略上下文（仓位 / 市场价格）─────────
            ctx.positions = book.to_ctx_positions()
            latest = series.get(trade.token_id)
            if latest:
                for pos in ctx.positions.values():
                    pos["current_price"] = latest[-1][1]
            if trade.market_slug:
                ctx.market_cache.setdefault(
                    trade.market_slug,
                    {
                        "volume": 0.0,
                        "tags": [],
                        "title": trade.market_title,
                        "active": True,
                    },
                )

            # ── 6. 基准：直接镜像跟单（独立重放一遍）────────────
        bench_pnl_total, bench_trades, bench_wins = self._run_benchmark(
            trades, series, tick_size, params
        )

        # ── 指标汇总 ───────────────────────────────────────
        total_trades = len(trades)
        pass_rate = (total_signals / total_trades) if total_trades else 0.0
        win_rate = (wins / executed_trades) if executed_trades else 0.0
        avg_pnl = (equity / executed_trades) if executed_trades else 0.0
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = PROFIT_FACTOR_CAP
        else:
            profit_factor = 0.0
        profit_factor = min(profit_factor, PROFIT_FACTOR_CAP)

        max_drawdown = self._max_drawdown(equity_curve)

        excess_pnl = equity - bench_pnl_total
        excess_return_pct = (
            (excess_pnl / params.initial_capital * 100.0) if params.initial_capital else 0.0
        )

        metrics = BacktestMetrics(
            total_trades=total_trades,
            total_signals=total_signals,
            pass_rate=round(pass_rate, 6),
            executed_trades=executed_trades,
            filtered_trades=total_trades - total_signals,
            win_rate=round(win_rate, 6),
            total_pnl=round(equity, 6),
            avg_pnl=round(avg_pnl, 6),
            max_win=round(max_win, 6),
            max_loss=round(max_loss, 6),
            profit_factor=round(profit_factor, 6),
            max_drawdown=round(max_drawdown, 6),
            initial_capital=params.initial_capital,
            final_equity=round(params.initial_capital + equity, 6),
            benchmark_pnl=round(bench_pnl_total, 6),
            benchmark_win_rate=round((bench_wins / bench_trades) if bench_trades else 0.0, 6),
            benchmark_trades=bench_trades,
            excess_pnl=round(excess_pnl, 6),
            excess_return_pct=round(excess_return_pct, 6),
        )

        filter_stat_list = self._build_filter_stats(filter_stats, total_trades)

        return BacktestResult(
            wallet=wallet_address,
            pipeline_id=pipeline_id,
            pipeline_name=pipeline_name,
            params=params,
            metrics=metrics,
            trades=details,
            equity_curve=equity_curve,
            filter_reasons=filter_counts,
            filter_stats=filter_stat_list,
            data_source=data_source,
        )

    # ── 基准回测 ───────────────────────────────────────────

    def _run_benchmark(
        self,
        trades: list[TradeEvent],
        series: dict[str, list[tuple[datetime, float]]],
        tick_size: float,
        params: BacktestParams,
    ) -> tuple[float, int, int]:
        """运行「直接镜像跟单」基准。

        基准不使用任何策略过滤（全部源交易都跟），但遵守同样的
        最小金额 / 单市场仓位 / 初始资金约束，保证可比性。

        Returns:
            (总盈亏, 成交笔数, 盈利笔数)
        """
        book = _PositionBook(params.initial_capital, params.max_position_per_market)
        total_pnl = 0.0
        trades_count = 0
        wins = 0

        for trade in trades:
            price = self._apply_slippage(
                trade.side, trade.price, tick_size, params.slippage_ticks
            )
            size = float(trade.size)
            notional = price * size
            if notional < params.min_amount:
                continue
            if not book.can_open(trade.condition_id, notional):
                continue

            exit_price = _lookup_exit_price(series, trade.token_id, trade.timestamp)
            if exit_price is None:
                exit_price = price
            pnl = self._calc_pnl(trade.side, size, price, exit_price)

            book.open(trade.condition_id, trade.side, notional, price, size)
            total_pnl += pnl
            trades_count += 1
            if pnl > 0:
                wins += 1

        return total_pnl, trades_count, wins

    # ── 辅助方法 ───────────────────────────────────────────

    @staticmethod
    def _apply_slippage(
        side: TradeSide, price: float, tick_size: float, slippage_ticks: int
    ) -> float:
        """计算模拟成交价：BUY 加上滑点，SELL 减去滑点。"""
        slip = tick_size * max(0, int(slippage_ticks))
        if side == TradeSide.BUY:
            filled = float(price) + slip
        else:
            filled = float(price) - slip
        return max(MIN_PRICE, min(MAX_PRICE, filled))

    @staticmethod
    def _calc_pnl(
        side: TradeSide, size: float, fill_price: float, exit_price: float
    ) -> float:
        """计算单笔盈亏。"""
        if side == TradeSide.BUY:
            return size * (exit_price - fill_price)
        return size * (fill_price - exit_price)

    @staticmethod
    def _check_constraints(
        book: _PositionBook,
        condition_id: str,
        notional: float,
        params: BacktestParams,
    ) -> str:
        """检查回测级约束，返回拒绝原因（通过则返回空字符串）。

        策略回测与基准回测共用同一套规则，保证两者可比。
        """
        if notional < params.min_amount:
            return f"回测约束：金额 {notional:.2f} 低于最小金额 {params.min_amount:.2f}"
        if not book.can_open(condition_id, notional):
            if book.exposure(condition_id) + notional > book.max_position_per_market + 1e-9:
                return (
                    f"回测约束：单市场仓位超过上限 {book.max_position_per_market:.2f}"
                )
            return f"回测约束：初始资金 {book.initial_capital:.2f} 不足"
        return ""

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> float:
        """基于累计盈亏曲线计算最大回撤（正数）。"""
        peak = 0.0
        drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            drawdown = max(drawdown, peak - value)
        return drawdown

    @staticmethod
    def _build_filter_stats(
        filter_stats: dict[tuple[str, str, str], int], total_trades: int
    ) -> list[BacktestFilterStat]:
        """把过滤计数转换为归因统计列表（按次数降序）。"""
        result: list[BacktestFilterStat] = []
        for (strategy_id, strategy_name, reason), count in filter_stats.items():
            pct = (count / total_trades * 100.0) if total_trades else 0.0
            result.append(
                BacktestFilterStat(
                    strategy_id=strategy_id,
                    strategy_name=strategy_name or "未知策略",
                    reason=reason,
                    count=count,
                    pct=round(pct, 2),
                )
            )
        result.sort(key=lambda s: (-s.count, s.reason))
        return result


# ── 模块级工具 ──────────────────────────────────────────────

def _count_filter(
    filter_counts: dict[str, int],
    filter_stats: dict[tuple[str, str, str], int],
    strategy_id: str,
    strategy_name: str,
    reason: str,
) -> None:
    """累计过滤原因计数。"""
    filter_counts[reason] = filter_counts.get(reason, 0) + 1
    key = (strategy_id, strategy_name, reason)
    filter_stats[key] = filter_stats.get(key, 0) + 1
