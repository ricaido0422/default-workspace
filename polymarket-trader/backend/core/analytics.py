"""绩效分析引擎。

把跟单系统沉淀下来的三张表聚合成「跟了谁、跟对了吗、赚没赚钱」的可视化数据：

- ``signals``    : 策略链产生的跟单信号（含 strategy_chain，用于策略归因）
- ``executions`` : 订单执行记录（状态、成交价、盈亏、错误信息）
- ``trades``     : 被监控钱包的源交易（提供钱包 / 市场维度）

统计维度：
1. 总览：信号数、执行数、成交率、总盈亏、胜率、平均持仓时长、平均下单延迟
2. 按钱包：信号数 / 盈亏 / 胜率 / 贡献占比
3. 按策略：触发次数、通过率、贡献盈亏（含流水线中的位置）
4. 按市场：盈利 Top10 / 亏损 Top10
5. 时间序列：每日盈亏、每日信号数
6. 执行质量：风控拦截、失败、取消、下单延迟分布（P50 / P90 / P95 / Max）

本模块不依赖 FastAPI，可独立 import：
    python -c "from backend.core.analytics import AnalyticsEngine"

``days <= 0`` 表示统计全部历史。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.models.schemas import (
    DailyPerformance,
    MarketPerformance,
    PerformanceSummary,
    StrategyPerformance,
    WalletPerformance,
)

logger = logging.getLogger(__name__)


# ── 常量 ────────────────────────────────────────────────────

FILLED_STATUSES = {"FILLED", "PARTIAL"}
"""视为「已成交」的执行状态。"""

RISK_ERROR_MARKERS = ("风控", "risk", "仓位超限", "金额超限", "超出上限")
"""用于识别「被风控拦截」的 error 文本标记。"""

MAX_ROWS = 200000
"""单次查询最多拉取的行数（防止极端数据量拖垮内存）。"""

TOP_N = 10
"""市场榜单条数（盈利 / 亏损各取 N 条）。"""

STRATEGY_TYPE_NAMES: dict[str, str] = {
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


# ── 工具函数 ────────────────────────────────────────────────

def _aware(dt: datetime) -> datetime:
    """把 datetime 统一成 UTC 时区感知对象。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    """解析 DB 中的 ISO 时间字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _aware(datetime.fromisoformat(text))
    except ValueError:
        # 兼容 "2024-01-01 00:00:00" 这类以空格分隔的写法
        try:
            return _aware(datetime.strptime(text, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return None


def _load_json(raw: Any, default: Any) -> Any:
    """安全解析 DB 中的 JSON 文本列。"""
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        value = json.loads(raw)
    except Exception:
        return default
    return value if value is not None else default


def _percentile(values: list[float], pct: float) -> float:
    """计算百分位数（线性插值近似）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (pct / 100.0) * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return float(ordered[low] * (1 - frac) + ordered[high] * frac)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """安全除法（分母为 0 时返回 0）。"""
    if not denominator:
        return 0.0
    return numerator / denominator


def _round(value: float, digits: int = 6) -> float:
    """统一保留小数位。"""
    try:
        return round(float(value), digits)
    except Exception:
        return 0.0


# ── 内部数据结构 ────────────────────────────────────────────

@dataclass
class _SignalRec:
    """signals 表的一行（已解析 JSON 字段）。"""
    signal_id: str
    pipeline_id: str
    source_wallet: str
    source_timestamp: datetime | None
    condition_id: str
    token_id: str
    slug: str
    title: str
    side: str
    price: float
    size: float
    strategy_chain: list[str]
    created_at: datetime


@dataclass
class _ExecutionRec:
    """executions 表的一行。"""
    exec_id: int
    signal_id: str
    status: str
    fill_price: float | None
    fill_size: float | None
    pnl: float | None
    mode: str
    timestamp: datetime
    error: str


@dataclass
class _TradeRec:
    """trades 表的一行（精简版）。"""
    wallet: str
    condition_id: str
    token_id: str
    slug: str
    title: str
    price: float
    size: float
    timestamp: datetime


@dataclass
class _StrategyMeta:
    """策略实例的元信息（来自 pipelines 表）。"""
    strategy_id: str
    strategy_type: str = ""
    name: str = ""
    category: str = ""
    pipeline_id: str = ""
    pipeline_name: str = ""
    position: int = 0


@dataclass
class _Dataset:
    """一次聚合所需的全部原始数据。"""
    signals: list[_SignalRec] = field(default_factory=list)
    signal_index: dict[str, _SignalRec] = field(default_factory=dict)
    executions: list[_ExecutionRec] = field(default_factory=list)
    trades: list[_TradeRec] = field(default_factory=list)
    strategies: dict[str, _StrategyMeta] = field(default_factory=dict)
    wallet_aliases: dict[str, str] = field(default_factory=dict)
    start_time: datetime | None = None
    end_time: datetime | None = None
    period_days: int = 0


# ── 分析引擎 ────────────────────────────────────────────────

class AnalyticsEngine:
    """跟单绩效分析引擎。

    Attributes:
        store: StateStore 实例（提供 DB 连接）。允许为 None，
               此时所有查询返回空结果（前端展示空数据引导）。
    """

    def __init__(self, store: Any = None) -> None:
        self.store = store

    # ── 公开 API ───────────────────────────────────────────

    async def get_performance_summary(self, days: int = 7) -> PerformanceSummary:
        """获取绩效总览。"""
        data = await self._collect(days)
        return self._build_summary(data)

    async def get_wallet_performance(self, days: int = 7) -> list[WalletPerformance]:
        """获取按钱包分组的绩效。"""
        data = await self._collect(days)
        return self._build_wallets(data)

    async def get_strategy_performance(self, days: int = 7) -> list[StrategyPerformance]:
        """获取按策略分组的绩效（归因）。"""
        data = await self._collect(days)
        return self._build_strategies(data)

    async def get_market_performance(self, days: int = 7) -> list[MarketPerformance]:
        """获取按市场分组的绩效（盈利 Top10 + 亏损 Top10）。"""
        data = await self._collect(days)
        return self._build_markets(data)

    async def get_daily_performance(self, days: int = 30) -> list[DailyPerformance]:
        """获取每日绩效时间序列。"""
        data = await self._collect(days)
        return self._build_daily(data)

    # ── 数据采集 ───────────────────────────────────────────

    async def _collect(self, days: int) -> _Dataset:
        """采集并解析统计数据。

        Args:
            days: 统计周期天数；``<= 0`` 表示全部历史。

        Returns:
            填充好的 ``_Dataset``。
        """
        days = int(days or 0)
        end_time = datetime.now(timezone.utc)
        start_time: datetime | None = None
        if days > 0:
            start_time = end_time - timedelta(days=days)

        data = _Dataset(start_time=start_time, end_time=end_time, period_days=days)

        try:
            await self._load_all(data, start_time)
        except Exception:
            logger.exception("读取绩效数据失败，返回空结果")
        return data

    async def _load_all(self, data: _Dataset, start_time: datetime | None) -> None:
        """从数据库加载 signals / executions / trades / pipelines。"""
        store = self.store
        if store is None:
            return

        db = getattr(store, "_db", None)
        if db is None:
            # 数据库尚未连接（后端未启动）→ 退化为空数据集
            return

        since = start_time.isoformat() if start_time else None

        # ── signals ────────────────────────────────────────
        sql_signals = "SELECT * FROM signals"
        params: tuple[Any, ...] = ()
        if since:
            sql_signals += " WHERE created_at >= ?"
            params = (since,)
        sql_signals += " ORDER BY created_at ASC LIMIT ?"
        rows = await self._fetch(db, sql_signals, params + (MAX_ROWS,))
        for row in rows:
            rec = self._parse_signal(row)
            if rec is not None:
                data.signals.append(rec)
                if rec.signal_id:
                    data.signal_index.setdefault(rec.signal_id, rec)

        # ── executions ─────────────────────────────────────
        sql_execs = "SELECT * FROM executions"
        params = ()
        if since:
            sql_execs += " WHERE timestamp >= ?"
            params = (since,)
        sql_execs += " ORDER BY timestamp ASC LIMIT ?"
        rows = await self._fetch(db, sql_execs, params + (MAX_ROWS,))
        for row in rows:
            rec = self._parse_execution(row)
            if rec is not None:
                data.executions.append(rec)

        # ── trades（用于钱包/市场维度的源交易计数）─────────
        sql_trades = "SELECT * FROM trades"
        params = ()
        if since:
            sql_trades += " WHERE timestamp >= ?"
            params = (since,)
        sql_trades += " ORDER BY timestamp ASC LIMIT ?"
        rows = await self._fetch(db, sql_trades, params + (MAX_ROWS,))
        for row in rows:
            rec = self._parse_trade(row)
            if rec is not None:
                data.trades.append(rec)

        # ── pipelines（策略元信息）─────────────────────────
        rows = await self._fetch(db, "SELECT id, name, strategies FROM pipelines", ())
        for row in rows:
            self._register_pipeline(data, row)

        # ── wallets（别名）────────────────────────────────
        try:
            rows = await self._fetch(db, "SELECT address, alias FROM wallets", ())
            for row in rows:
                data.wallet_aliases[str(row.get("address") or "")] = str(
                    row.get("alias") or ""
                )
        except Exception:
            logger.debug("读取钱包别名失败", exc_info=True)

    @staticmethod
    async def _fetch(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """执行 SQL 并返回字典列表。"""
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── 行解析 ─────────────────────────────────────────────

    @staticmethod
    def _parse_signal(row: dict[str, Any]) -> _SignalRec | None:
        """signals 表行 → _SignalRec。"""
        created_at = _parse_dt(row.get("created_at"))
        if created_at is None:
            return None

        source = _load_json(row.get("source_trade"), {})
        if not isinstance(source, dict):
            source = {}
        market = _load_json(row.get("market"), {})
        if not isinstance(market, dict):
            market = {}

        chain_raw = _load_json(row.get("strategy_chain"), [])
        chain = [str(x) for x in chain_raw] if isinstance(chain_raw, list) else []

        return _SignalRec(
            signal_id=str(row.get("signal_id") or ""),
            pipeline_id=str(row.get("pipeline_id") or ""),
            source_wallet=str(source.get("wallet") or ""),
            source_timestamp=_parse_dt(source.get("timestamp")),
            condition_id=str(market.get("condition_id") or source.get("condition_id") or ""),
            token_id=str(market.get("token_id") or source.get("token_id") or ""),
            slug=str(market.get("slug") or source.get("market_slug") or ""),
            title=str(market.get("title") or source.get("market_title") or ""),
            side=str(row.get("side") or source.get("side") or ""),
            price=float(row.get("price") or 0.0),
            size=float(row.get("size") or 0.0),
            strategy_chain=chain,
            created_at=created_at,
        )

    @staticmethod
    def _parse_execution(row: dict[str, Any]) -> _ExecutionRec | None:
        """executions 表行 → _ExecutionRec。"""
        timestamp = _parse_dt(row.get("timestamp"))
        if timestamp is None:
            return None
        return _ExecutionRec(
            exec_id=int(row.get("id") or 0),
            signal_id=str(row.get("signal_id") or ""),
            status=str(row.get("status") or "").upper(),
            fill_price=row.get("fill_price"),
            fill_size=row.get("fill_size"),
            pnl=row.get("pnl"),
            mode=str(row.get("mode") or ""),
            timestamp=timestamp,
            error=str(row.get("error") or ""),
        )

    @staticmethod
    def _parse_trade(row: dict[str, Any]) -> _TradeRec | None:
        """trades 表行 → _TradeRec。"""
        timestamp = _parse_dt(row.get("timestamp"))
        if timestamp is None:
            return None
        return _TradeRec(
            wallet=str(row.get("wallet") or ""),
            condition_id=str(row.get("condition_id") or ""),
            token_id=str(row.get("token_id") or ""),
            slug=str(row.get("market_slug") or ""),
            title=str(row.get("market_title") or ""),
            price=float(row.get("price") or 0.0),
            size=float(row.get("size") or 0.0),
            timestamp=timestamp,
        )

    @staticmethod
    def _register_pipeline(data: _Dataset, row: dict[str, Any]) -> None:
        """把 pipelines 行中的策略元信息登记到 data.strategies。"""
        pipeline_id = str(row.get("id") or "")
        pipeline_name = str(row.get("name") or "")
        strategies = _load_json(row.get("strategies"), [])
        if not isinstance(strategies, list):
            return

        def sort_key(item: Any) -> tuple[int, str]:
            if isinstance(item, dict):
                try:
                    return (int(item.get("display_order") or 0), str(item.get("id") or ""))
                except Exception:
                    return (0, str(item.get("id") or ""))
            return (0, str(item))

        for position, cfg in enumerate(sorted(strategies, key=sort_key), start=1):
            if not isinstance(cfg, dict):
                continue
            sid = str(cfg.get("id") or "")
            if not sid:
                continue
            stype = str(cfg.get("type") or "")
            meta = _StrategyMeta(
                strategy_id=sid,
                strategy_type=stype,
                name=str(cfg.get("name") or STRATEGY_TYPE_NAMES.get(stype, stype or sid)),
                category=str(cfg.get("category") or ""),
                pipeline_id=pipeline_id,
                pipeline_name=pipeline_name,
                position=position,
            )
            data.strategies[sid] = meta

    # ── 聚合辅助 ───────────────────────────────────────────

    @staticmethod
    def _exec_by_signal(data: _Dataset) -> dict[str, list[_ExecutionRec]]:
        """signal_id → 该信号的所有执行记录。"""
        mapping: dict[str, list[_ExecutionRec]] = {}
        for exec in data.executions:
            if not exec.signal_id:
                continue
            mapping.setdefault(exec.signal_id, []).append(exec)
        return mapping

    @staticmethod
    def _is_risk_blocked(exec: _ExecutionRec) -> bool:
        """判断执行记录是否属于「被风控拦截」。"""
        if exec.status != "FAILED":
            return False
        text = exec.error.lower()
        return any(marker.lower() in text for marker in RISK_ERROR_MARKERS)

    @staticmethod
    def _market_key(signal: _SignalRec) -> str:
        """市场唯一键。"""
        if signal.condition_id and signal.token_id:
            return f"{signal.condition_id}:{signal.token_id}"
        return signal.condition_id or signal.token_id or signal.slug or signal.title

    # ── 总览 ───────────────────────────────────────────────

    def _build_summary(self, data: _Dataset) -> PerformanceSummary:
        """构造 PerformanceSummary。"""
        total_signals = len(data.signals)
        total_executions = len(data.executions)

        filled = 0
        failed = 0
        cancelled = 0
        risk_blocked = 0
        total_pnl = 0.0
        wins = 0
        pnl_count = 0
        hold_seconds: list[float] = []
        latencies: list[float] = []
        markets: set[str] = set()
        wallets: set[str] = set()

        # 钱包 / 市场维度按全部信号统计
        for signal in data.signals:
            if signal.source_wallet:
                wallets.add(signal.source_wallet)
            key = self._market_key(signal)
            if key:
                markets.add(key)

        for exec in data.executions:
            status = exec.status
            if status in FILLED_STATUSES:
                filled += 1
            elif status == "FAILED":
                failed += 1
                if self._is_risk_blocked(exec):
                    risk_blocked += 1
            elif status == "CANCELLED":
                cancelled += 1

            if exec.pnl is not None:
                total_pnl += float(exec.pnl)
                pnl_count += 1
                if float(exec.pnl) > 0:
                    wins += 1

            signal = self._find_signal(data, exec.signal_id)
            if signal is not None:
                latencies.append(
                    max(0.0, (exec.timestamp - signal.created_at).total_seconds() * 1000.0)
                )
                if signal.source_timestamp is not None:
                    hold_seconds.append(
                        max(0.0, (exec.timestamp - signal.source_timestamp).total_seconds())
                    )

        win_rate = _safe_ratio(wins, pnl_count)
        avg_pnl = _safe_ratio(total_pnl, pnl_count)
        fill_rate = _safe_ratio(filled, total_executions)

        return PerformanceSummary(
            period_days=int(data.period_days),
            start_time=data.start_time,
            end_time=data.end_time,
            total_signals=total_signals,
            total_executions=total_executions,
            filled_executions=filled,
            fill_rate=_round(fill_rate),
            total_pnl=_round(total_pnl, 4),
            win_rate=_round(win_rate),
            avg_pnl=_round(avg_pnl, 4),
            avg_hold_seconds=_round(sum(hold_seconds) / len(hold_seconds), 2) if hold_seconds else 0.0,
            avg_latency_ms=_round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            total_wallets=len(wallets),
            total_markets=len(markets),
            risk_blocked_count=risk_blocked,
            failed_count=failed,
            cancelled_count=cancelled,
            latency_p50_ms=_round(_percentile(latencies, 50), 2),
            latency_p90_ms=_round(_percentile(latencies, 90), 2),
            latency_p95_ms=_round(_percentile(latencies, 95), 2),
            latency_max_ms=_round(max(latencies), 2) if latencies else 0.0,
            has_data=bool(total_signals or total_executions),
        )

    @staticmethod
    def _find_signal(data: _Dataset, signal_id: str) -> _SignalRec | None:
        """按 signal_id 查找信号（走索引，避免线性扫描）。"""
        if not signal_id:
            return None
        return data.signal_index.get(signal_id)

    # ── 按钱包 ─────────────────────────────────────────────

    def _build_wallets(self, data: _Dataset) -> list[WalletPerformance]:
        """构造按钱包分组的绩效列表。"""
        exec_by_signal = self._exec_by_signal(data)

        agg: dict[str, dict[str, Any]] = {}

        def bucket(wallet: str) -> dict[str, Any]:
            if wallet not in agg:
                agg[wallet] = {
                    "signal_count": 0,
                    "execution_count": 0,
                    "filled_count": 0,
                    "pnl": 0.0,
                    "pnl_count": 0,
                    "wins": 0,
                    "latencies": [],
                    "last_active": None,
                }
            return agg[wallet]

        for signal in data.signals:
            wallet = signal.source_wallet
            if not wallet:
                continue
            b = bucket(wallet)
            b["signal_count"] += 1
            if b["last_active"] is None or signal.created_at > b["last_active"]:
                b["last_active"] = signal.created_at

            for exec in exec_by_signal.get(signal.signal_id, []):
                b["execution_count"] += 1
                if exec.status in FILLED_STATUSES:
                    b["filled_count"] += 1
                if exec.pnl is not None:
                    b["pnl"] += float(exec.pnl)
                    b["pnl_count"] += 1
                    if float(exec.pnl) > 0:
                        b["wins"] += 1
                b["latencies"].append(
                    max(0.0, (exec.timestamp - signal.created_at).total_seconds() * 1000.0)
                )

        # 仅在 trades 中出现、但还没有信号的钱包也列出来（信号数为 0）
        for trade in data.trades:
            if trade.wallet and trade.wallet not in agg:
                b = bucket(trade.wallet)
                if b["last_active"] is None or trade.timestamp > b["last_active"]:
                    b["last_active"] = trade.timestamp

        total_pnl = sum(float(b["pnl"]) for b in agg.values())
        result: list[WalletPerformance] = []
        for wallet, b in agg.items():
            pnl = float(b["pnl"])
            contribution = (
                (pnl / total_pnl * 100.0) if abs(total_pnl) > 1e-12 else 0.0
            )
            latencies: list[float] = b["latencies"]
            result.append(
                WalletPerformance(
                    wallet=wallet,
                    alias=data.wallet_aliases.get(wallet, ""),
                    signal_count=int(b["signal_count"]),
                    execution_count=int(b["execution_count"]),
                    filled_count=int(b["filled_count"]),
                    fill_rate=_round(_safe_ratio(b["filled_count"], b["execution_count"])),
                    total_pnl=_round(pnl, 4),
                    win_rate=_round(_safe_ratio(b["wins"], b["pnl_count"])),
                    avg_pnl=_round(_safe_ratio(pnl, b["pnl_count"]), 4),
                    contribution_pct=_round(contribution, 2),
                    avg_latency_ms=_round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
                    last_active=b["last_active"],
                )
            )

        result.sort(key=lambda w: (-w.signal_count, -w.total_pnl))
        return result

    # ── 按策略 ─────────────────────────────────────────────

    def _build_strategies(self, data: _Dataset) -> list[StrategyPerformance]:
        """构造按策略分组的绩效列表（含流水线位置归因）。"""
        exec_by_signal = self._exec_by_signal(data)

        agg: dict[str, dict[str, Any]] = {}

        def bucket(meta: _StrategyMeta) -> dict[str, Any]:
            if meta.strategy_id not in agg:
                agg[meta.strategy_id] = {
                    "meta": meta,
                    "triggered": 0,
                    "passed": 0,
                    "pnl": 0.0,
                    "pnl_count": 0,
                    "wins": 0,
                }
            return agg[meta.strategy_id]

        # 先为 pipelines 中登记的每个策略建立桶（即使 0 次触发也展示）
        for meta in data.strategies.values():
            bucket(meta)

        for signal in data.signals:
            executions = exec_by_signal.get(signal.signal_id, [])
            filled = any(e.status in FILLED_STATUSES for e in executions)
            pnl_sum = sum(float(e.pnl) for e in executions if e.pnl is not None)
            pnl_count = sum(1 for e in executions if e.pnl is not None)
            wins = sum(1 for e in executions if e.pnl is not None and float(e.pnl) > 0)

            for sid in signal.strategy_chain:
                meta = data.strategies.get(sid) or _StrategyMeta(
                    strategy_id=sid,
                    strategy_type="",
                    name=sid,
                )
                b = bucket(meta)
                b["triggered"] += 1
                if filled:
                    b["passed"] += 1
                b["pnl"] += pnl_sum
                b["pnl_count"] += pnl_count
                b["wins"] += wins

        total_pnl = sum(float(b["pnl"]) for b in agg.values())
        result: list[StrategyPerformance] = []
        for sid, b in agg.items():
            meta: _StrategyMeta = b["meta"]
            pnl = float(b["pnl"])
            contribution = (pnl / total_pnl * 100.0) if abs(total_pnl) > 1e-12 else 0.0
            result.append(
                StrategyPerformance(
                    strategy_id=sid,
                    strategy_type=meta.strategy_type,
                    name=meta.name or STRATEGY_TYPE_NAMES.get(meta.strategy_type, sid),
                    category=meta.category,
                    pipeline_id=meta.pipeline_id,
                    pipeline_name=meta.pipeline_name,
                    position=meta.position,
                    triggered_count=int(b["triggered"]),
                    passed_count=int(b["passed"]),
                    pass_rate=_round(_safe_ratio(b["passed"], b["triggered"])),
                    contributed_pnl=_round(pnl, 4),
                    contribution_pct=_round(contribution, 2),
                    win_rate=_round(_safe_ratio(b["wins"], b["pnl_count"])),
                )
            )

        result.sort(key=lambda s: (-s.triggered_count, -s.contributed_pnl))
        return result

    # ── 按市场 ─────────────────────────────────────────────

    def _build_markets(self, data: _Dataset) -> list[MarketPerformance]:
        """构造按市场分组的绩效列表（盈利 Top10 + 亏损 Top10）。"""
        exec_by_signal = self._exec_by_signal(data)

        trade_counts: dict[str, int] = {}
        for trade in data.trades:
            key = self._trade_market_key(trade)
            if key:
                trade_counts[key] = trade_counts.get(key, 0) + 1

        agg: dict[str, dict[str, Any]] = {}

        def bucket(signal: _SignalRec) -> dict[str, Any]:
            key = self._market_key(signal)
            if key not in agg:
                agg[key] = {
                    "condition_id": signal.condition_id,
                    "token_id": signal.token_id,
                    "slug": signal.slug,
                    "title": signal.title,
                    "executions": 0,
                    "filled": 0,
                    "pnl": 0.0,
                    "pnl_count": 0,
                    "wins": 0,
                    "best": 0.0,
                    "worst": 0.0,
                }
            return agg[key]

        for signal in data.signals:
            b = bucket(signal)
            for exec in exec_by_signal.get(signal.signal_id, []):
                b["executions"] += 1
                if exec.status in FILLED_STATUSES:
                    b["filled"] += 1
                if exec.pnl is not None:
                    value = float(exec.pnl)
                    b["pnl"] += value
                    b["pnl_count"] += 1
                    if value > 0:
                        b["wins"] += 1
                    b["best"] = max(b["best"], value)
                    b["worst"] = min(b["worst"], value)

        entries: list[MarketPerformance] = []
        for key, b in agg.items():
            entries.append(
                MarketPerformance(
                    market_key=key,
                    condition_id=str(b["condition_id"]),
                    token_id=str(b["token_id"]),
                    title=str(b["title"]) or str(b["slug"]) or key,
                    slug=str(b["slug"]),
                    trade_count=trade_counts.get(key, 0),
                    execution_count=int(b["executions"]),
                    filled_count=int(b["filled"]),
                    total_pnl=_round(float(b["pnl"]), 4),
                    win_rate=_round(_safe_ratio(b["wins"], b["pnl_count"])),
                    avg_pnl=_round(_safe_ratio(float(b["pnl"]), b["pnl_count"]), 4),
                    best_pnl=_round(float(b["best"]), 4),
                    worst_pnl=_round(float(b["worst"]), 4),
                )
            )

        profits = sorted(
            [e for e in entries if e.total_pnl > 0],
            key=lambda e: e.total_pnl,
            reverse=True,
        )[:TOP_N]
        losses = sorted(
            [e for e in entries if e.total_pnl < 0],
            key=lambda e: e.total_pnl,
        )[:TOP_N]
        flat = [e for e in entries if e.total_pnl == 0]

        for rank, item in enumerate(profits, start=1):
            item.bucket = "profit"
            item.rank = rank
        for rank, item in enumerate(losses, start=1):
            item.bucket = "loss"
            item.rank = rank
        for rank, item in enumerate(flat, start=1):
            item.bucket = "flat"
            item.rank = rank

        return profits + losses + flat

    @staticmethod
    def _trade_market_key(trade: _TradeRec) -> str:
        """trades 表行的市场键（与信号侧保持一致的构造方式）。"""
        if trade.condition_id and trade.token_id:
            return f"{trade.condition_id}:{trade.token_id}"
        return trade.condition_id or trade.token_id or trade.slug or trade.title

    # ── 时间序列 ───────────────────────────────────────────

    def _build_daily(self, data: _Dataset) -> list[DailyPerformance]:
        """构造每日绩效时间序列。"""
        exec_by_signal = self._exec_by_signal(data)

        days: dict[str, dict[str, Any]] = {}

        def bucket(date_str: str) -> dict[str, Any]:
            if date_str not in days:
                days[date_str] = {
                    "pnl": 0.0,
                    "pnl_count": 0,
                    "wins": 0,
                    "signals": 0,
                    "executions": 0,
                    "filled": 0,
                }
            return days[date_str]

        for signal in data.signals:
            bucket(signal.created_at.strftime("%Y-%m-%d"))["signals"] += 1

        for exec in data.executions:
            b = bucket(exec.timestamp.strftime("%Y-%m-%d"))
            b["executions"] += 1
            if exec.status in FILLED_STATUSES:
                b["filled"] += 1
            if exec.pnl is not None:
                b["pnl"] += float(exec.pnl)
                b["pnl_count"] += 1
                if float(exec.pnl) > 0:
                    b["wins"] += 1

        result: list[DailyPerformance] = []
        cumulative = 0.0
        for date_str in sorted(days.keys()):
            b = days[date_str]
            cumulative += float(b["pnl"])
            result.append(
                DailyPerformance(
                    date=date_str,
                    pnl=_round(float(b["pnl"]), 4),
                    signal_count=int(b["signals"]),
                    execution_count=int(b["executions"]),
                    filled_count=int(b["filled"]),
                    win_rate=_round(_safe_ratio(b["wins"], b["pnl_count"])),
                    cumulative_pnl=_round(cumulative, 4),
                )
            )
        return result
