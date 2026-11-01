"""Pydantic v2 数据模型定义。

所有前后端共享的数据结构在此定义，确保 API 契约一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ProxyConfig 定义在 backend/config.py（那里不反向依赖本模块，无循环导入风险）
from backend.config import ProxyConfig, RiskConfig


# ── 枚举 ────────────────────────────────────────────────────

class StrategyType(str, Enum):
    """策略类型。"""
    MIRROR = "MIRROR"
    PROPORTIONAL = "PROPORTIONAL"
    FIXED_AMOUNT = "FIXED_AMOUNT"
    PRICE_OFFSET = "PRICE_OFFSET"
    DELAYED = "DELAYED"
    MARKET_FILTER = "MARKET_FILTER"
    SIZE_FILTER = "SIZE_FILTER"
    REVERSE = "REVERSE"
    POSITION_CAP = "POSITION_CAP"
    STOP_LOSS = "STOP_LOSS"


class StrategyCategory(str, Enum):
    """策略类别。"""
    FILTER = "FILTER"
    TRANSFORM = "TRANSFORM"
    RISK = "RISK"
    EXECUTION = "EXECUTION"


class TradeSide(str, Enum):
    """交易方向。"""
    BUY = "BUY"
    SELL = "SELL"


class ExecutionStatus(str, Enum):
    """执行状态。"""
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionMode(str, Enum):
    """执行模式。"""
    PAPER = "PAPER"
    LIVE = "LIVE"


# ── 钱包监控 ────────────────────────────────────────────────

class WalletWatch(BaseModel):
    """被监控的钱包。"""
    address: str = Field(..., description="钱包地址 (0x...)")
    alias: str = Field(default="", description="钱包别名")
    username: str = ""
    specialties: list[str] = Field(default_factory=list)
    notes: str = ""
    enabled: bool = Field(default=True, description="是否启用监控")
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="添加时间")
    last_checked: datetime | None = Field(default=None, description="最后检查时间")
    trade_count: int = Field(default=0, ge=0, description="已捕获交易数")


# ── 交易事件 ────────────────────────────────────────────────

class TradeEvent(BaseModel):
    """从链上/API 捕获的交易事件。"""
    wallet: str = Field(..., description="交易者钱包地址")
    condition_id: str = Field(..., description="市场 condition id")
    token_id: str = Field(..., description="CLOB token id")
    market_slug: str = Field(default="", description="市场 slug")
    market_title: str = Field(default="", description="市场标题")
    side: TradeSide = Field(..., description="交易方向")
    price: float = Field(..., ge=0, le=1, description="成交价格 (0-1)")
    size: float = Field(..., gt=0, description="成交数量")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="交易时间")
    tx_hash: str = Field(default="", description="交易哈希")
    asset_id: str = Field(default="", description="资产 id")
    delay_seconds: int = Field(default=0, ge=0, description="延迟执行秒数（由 delayed 策略设置）")


# ── 策略配置 ────────────────────────────────────────────────

class StrategyConfig(BaseModel):
    """单个策略配置。"""
    id: str = Field(..., description="策略实例 id")
    type: StrategyType = Field(..., description="策略类型")
    category: StrategyCategory = Field(..., description="策略类别")
    name: str = Field(..., description="策略显示名称")
    description: str = Field(default="", description="策略描述")
    enabled: bool = Field(default=True, description="是否启用")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数")
    display_order: int = Field(default=0, description="显示排序")


class StrategyPipeline(BaseModel):
    """策略流水线（多个策略组合）。"""
    id: str = Field(..., description="流水线 id")
    name: str = Field(..., description="流水线名称")
    strategies: list[StrategyConfig] = Field(default_factory=list, description="策略列表")
    enabled: bool = Field(default=True, description="是否启用")
    target_wallets: list[str] = Field(default_factory=list, description="目标钱包地址列表")


# ── 跟单信号 ────────────────────────────────────────────────

class MarketRef(BaseModel):
    """市场引用（信号中的目标市场）。"""
    condition_id: str
    token_id: str
    slug: str = ""
    title: str = ""
    sector: str = "未分类"
    event_key: str = ""


class FollowSignal(BaseModel):
    """跟单信号（策略链处理后的输出）。"""
    source_trade: TradeEvent = Field(..., description="源交易事件")
    pipeline_id: str = Field(..., description="产生此信号的流水线 id")
    market: MarketRef = Field(..., description="目标市场")
    side: TradeSide = Field(..., description="交易方向")
    price: float = Field(..., ge=0, le=1, description="目标价格")
    size: float = Field(..., gt=0, description="目标数量")
    strategy_chain: list[str] = Field(default_factory=list, description="策略链 (策略 id 列表)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="信号创建时间")
    signal_id: str = Field(..., description="信号唯一 id")
    quoted_price: float | None = Field(default=None, gt=0, lt=1)
    expected_mode: ExecutionMode | None = None
    min_order_size: float = Field(default=0, ge=0)
    quote_source: str = ''
    account_id: str = 'legacy'


# ── 执行记录 ────────────────────────────────────────────────

class ExecutionRecord(BaseModel):
    """订单执行记录。"""
    signal_id: str = Field(..., description="关联信号 id")
    status: ExecutionStatus = Field(..., description="执行状态")
    order_id: str = Field(default="", description="订单 id")
    account_id: str = "legacy"
    fill_price: float | None = Field(default=None, ge=0, description="成交价格")
    fill_size: float | None = Field(default=None, ge=0, description="成交数量")
    pnl: float | None = Field(default=None, description="盈亏")
    mode: ExecutionMode = Field(..., description="执行模式")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="执行时间")
    error: str = Field(default="", description="错误信息")


# ── 虚拟持仓（模拟资金 + 实盘行情盯市结算） ──────────────────

class VirtualPosition(BaseModel):
    """模拟资金模式下的虚拟持仓。

    以 (钱包, token_id) 为键，采用移动加权平均成本记账。
    净持仓数量带符号：正数 = 多头，负数 = 空头。
    """
    wallet: str = Field(..., description="跟单来源钱包")
    token_id: str = Field(..., description="CLOB token id（持仓标的）")
    condition_id: str = Field(default="", description="市场 condition id")
    market_title: str = Field(default="", description="市场标题")
    market_slug: str = Field(default="", description="市场 slug")
    net_qty: float = Field(default=0.0, description="净持仓数量（正=多头，负=空头）")
    avg_entry: float = Field(default=0.0, ge=0, description="加权平均开仓价")
    realized_pnl: float = Field(default=0.0, description="该持仓累计已实现盈亏 (USDC)")
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="开仓时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="更新时间")


class VirtualPositionView(BaseModel):
    """虚拟持仓 + 实盘盯市结果（API 输出）。"""
    wallet: str = Field(..., description="跟单来源钱包")
    token_id: str = Field(..., description="CLOB token id")
    condition_id: str = Field(default="", description="市场 condition id")
    market_title: str = Field(default="", description="市场标题")
    market_slug: str = Field(default="", description="市场 slug")
    net_qty: float = Field(default=0.0, description="净持仓数量（正=多头，负=空头）")
    avg_entry: float = Field(default=0.0, description="加权平均开仓价")
    side: str = Field(default="FLAT", description="持仓方向: LONG/SHORT/FLAT")
    mark_price: float | None = Field(default=None, description="盯市使用的真实市价")
    price_source: str = Field(default="", description="价格来源 (midpoint/bid/ask/unavailable)")
    position_value: float = Field(default=0.0, description="持仓市值 (USDC)")
    realized_pnl: float = Field(default=0.0, description="已实现盈亏 (USDC)")
    floating_pnl: float = Field(default=0.0, description="浮动盈亏 (USDC)")
    total_pnl: float = Field(default=0.0, description="总盈亏 = 已实现 + 浮动 (USDC)")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="更新时间")


class VirtualPortfolioSummary(BaseModel):
    """虚拟组合盯市汇总。"""
    open_positions: int = Field(default=0, description="未平仓持仓数")
    total_realized_pnl: float = Field(default=0.0, description="总已实现盈亏 (USDC)")
    total_floating_pnl: float = Field(default=0.0, description="总浮动盈亏 (USDC)")
    total_pnl: float = Field(default=0.0, description="总盈亏 (USDC)")
    positions: list[VirtualPositionView] = Field(default_factory=list, description="持仓明细")


# ── 排行榜 ──────────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    """排行榜条目。"""
    rank: int = Field(..., ge=1, description="排名")
    wallet: str = Field(..., description="钱包地址")
    username: str = ""
    pnl: float = Field(default=0.0, description="盈亏 (USDC)")
    volume: float = Field(default=0.0, description="交易量 (USDC)")
    markets_traded: int = Field(default=0, ge=0, description="交易市场数")


# ── 仪表盘统计 ──────────────────────────────────────────────

class DashboardStats(BaseModel):
    """仪表盘统计数据。"""
    watched_wallets: int = Field(default=0, ge=0, description="监控钱包数")
    today_trades: int = Field(default=0, ge=0, description="今日捕获交易数")
    today_signals: int = Field(default=0, ge=0, description="今日生成信号数")
    today_executions: int = Field(default=0, ge=0, description="今日执行数")
    total_pnl: float = Field(default=0.0, description="总盈亏 (USDC)")


# ── WebSocket 消息 ──────────────────────────────────────────

class WSMessage(BaseModel):
    """WebSocket 消息信封。"""
    type: str = Field(..., description="消息类型")
    data: Any = Field(default=None, description="消息数据")


class SubscribeMessage(BaseModel):
    """客户端订阅消息。"""
    type: str = Field(default="subscribe")
    channels: list[str] = Field(default_factory=list, description="订阅频道列表")


# ── 请求体模型 ──────────────────────────────────────────────

class AddWalletRequest(BaseModel):
    """添加钱包请求。"""
    address: str = Field(..., pattern=r'^0x[0-9a-fA-F]{40}$', description="钱包地址")
    alias: str = Field(default="", description="钱包别名")


class CreatePipelineRequest(BaseModel):
    """创建流水线请求。"""
    name: str = Field(..., description="流水线名称")


class UpdatePipelineRequest(BaseModel):
    """更新流水线请求。"""
    strategies: list[StrategyConfig] | None = None
    enabled: bool | None = None
    target_wallets: list[str] | None = None


class ImportLeaderboardRequest(BaseModel):
    """从排行榜导入钱包请求。"""
    wallets: list[str] = Field(..., description="钱包地址列表")
    aliases: list[str] = Field(default_factory=list, description="别名列表")
    usernames: list[str] = Field(default_factory=list)


class UpdateConfigRequest(BaseModel):
    """更新配置请求。"""
    proxy: ProxyConfig | None = None
    risk: RiskConfig | None = None
    monitor_interval: int | None = Field(default=None, ge=1)
    # 订单金额监测范围（USDC）：只跟单成交金额落在此闭区间内的交易
    monitor_min_amount: float | None = Field(
        default=None, ge=0, description="监测金额下限 (USDC)，0 = 不设下限"
    )
    monitor_max_amount: float | None = Field(
        default=None, ge=0, description="监测金额上限 (USDC)，0 = 不设上限"
    )


class ExecutorModeRequest(BaseModel):
    """设置执行模式请求。"""
    mode: str = Field(..., pattern="^(paper|live)$", description="执行模式")


class ExecutorCredentialsRequest(BaseModel):
    """设置执行凭据请求。"""
    api_key: str | None = None
    api_secret: str | None = None
    api_passphrase: str | None = None
    wallet_address: str | None = None
    private_key: str | None = None
    signature_type: int | None = Field(default=None, ge=0, le=2)
    funder: str | None = None


class WalletProfileRequest(BaseModel):
    alias: str = Field(default="", max_length=100)
    username: str = Field(default="", max_length=100)
    specialties: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)


class SimulateTradeRequest(BaseModel):
    """模拟交易注入请求（链路自检用，仅 paper 模式可用）。

    注入后会走与真实捕获完全相同的链路：
    交易事件 → 策略管道 → 风控 → 组合分配 → 执行器 → 落库。
    """
    wallet: str = Field(default="", description="源钱包地址；留空则取第一个监控钱包")
    market_title: str = Field(default="模拟市场-自检", description="市场标题")
    market_slug: str = Field(default="sim-market", description="市场 slug")
    side: str = Field(default="BUY", pattern="^(BUY|SELL)$", description="交易方向")
    price: float = Field(default=0.55, ge=0.0, le=1.0, description="成交价格 (0-1)")
    size: float = Field(default=200.0, gt=0, description="成交数量")


# ── 通知设置请求体 ──────────────────────────────────────────

class NotificationConfigRequest(BaseModel):
    """更新通知配置请求。

    所有字段可选，只更新传入的字段。
    ``telegram_token`` / ``email_password`` 传空字符串表示「保持原值不变」
    （前端不回显敏感字段），传 None 表示不修改。
    """
    telegram_enabled: bool | None = None
    telegram_token: str | None = None
    telegram_chat_ids: list[str] | None = None

    email_enabled: bool | None = None
    email_host: str | None = None
    email_port: int | None = None
    email_user: str | None = None
    email_password: str | None = None
    email_from: str | None = None
    email_to: list[str] | None = None
    email_use_ssl: bool | None = None

    min_notify_amount: float | None = None
    notify_events: list[str] | None = None
    notify_wallets: list[str] | None = None
    cooldown_seconds: float | None = None

    daily_summary_hour: int | None = None
    daily_summary_minute: int | None = None


class NotificationTestRequest(BaseModel):
    """发送测试通知请求。"""
    channel: str = Field(
        default="telegram",
        pattern="^(telegram|email)$",
        description="测试渠道: telegram | email",
    )


# ── 策略回测 ────────────────────────────────────────────────

class BacktestParams(BaseModel):
    """回测参数。"""
    start_time: datetime | None = Field(default=None, description="回测起始时间（含）")
    end_time: datetime | None = Field(default=None, description="回测结束时间（含）")
    initial_capital: float = Field(default=1000.0, gt=0, description="初始资金 (USDC)")
    slippage_ticks: int = Field(
        default=1, ge=0, le=100,
        description="滑点 tick 数（滑点 = tick_size × slippage_ticks）",
    )
    min_amount: float = Field(default=0.0, ge=0, description="单笔最小成交金额 (USDC)")
    max_position_per_market: float = Field(
        default=500.0, gt=0, description="单市场最大仓位 (USDC)"
    )
    tick_size: float = Field(default=0.01, gt=0, description="最小价格变动单位")


class BacktestMetrics(BaseModel):
    """回测核心指标。"""
    # 计数
    total_trades: int = Field(default=0, ge=0, description="历史交易总数")
    total_signals: int = Field(default=0, ge=0, description="通过策略的信号数")
    pass_rate: float = Field(default=0.0, ge=0, le=1, description="策略通过率")
    executed_trades: int = Field(default=0, ge=0, description="实际模拟成交数")
    filtered_trades: int = Field(default=0, ge=0, description="被过滤掉的交易数")
    # 盈亏
    win_rate: float = Field(default=0.0, ge=0, le=1, description="胜率（盈利笔数占比）")
    total_pnl: float = Field(default=0.0, description="总盈亏 (USDC)")
    avg_pnl: float = Field(default=0.0, description="平均每笔盈亏 (USDC)")
    max_win: float = Field(default=0.0, description="最大单笔盈利 (USDC)")
    max_loss: float = Field(default=0.0, description="最大单笔亏损 (USDC，负数)")
    profit_factor: float = Field(default=0.0, ge=0, description="盈亏比 (总盈利 / 总亏损)")
    max_drawdown: float = Field(default=0.0, ge=0, description="最大回撤 (USDC)")
    # 资金
    initial_capital: float = Field(default=0.0, description="初始资金 (USDC)")
    final_equity: float = Field(default=0.0, description="期末权益 (USDC)")
    # 基准对比（直接镜像跟单）
    benchmark_pnl: float = Field(default=0.0, description="基准（镜像跟单）总盈亏 (USDC)")
    benchmark_win_rate: float = Field(default=0.0, ge=0, le=1, description="基准胜率")
    benchmark_trades: int = Field(default=0, ge=0, description="基准成交笔数")
    excess_pnl: float = Field(default=0.0, description="超额收益 (策略 - 基准, USDC)")
    excess_return_pct: float = Field(default=0.0, description="超额收益率 (%，相对初始资金)")


class BacktestTradeDetail(BaseModel):
    """回测逐笔明细。"""
    source_trade: TradeEvent = Field(..., description="源交易事件")
    passed: bool = Field(default=False, description="是否通过策略链")
    executed: bool = Field(default=False, description="是否模拟成交")
    reason: str = Field(default="", description="通过原因 / 过滤原因")
    filter_strategy_id: str = Field(default="", description="触发过滤的策略 id")
    filter_strategy_name: str = Field(default="", description="触发过滤的策略名称")
    strategy_chain: list[str] = Field(default_factory=list, description="通过的策略链")
    side: TradeSide | None = Field(default=None, description="模拟交易方向")
    fill_price: float | None = Field(default=None, description="模拟成交价")
    fill_size: float | None = Field(default=None, description="模拟成交数量")
    exit_price: float | None = Field(default=None, description="用于计算盈亏的平仓价")
    pnl: float | None = Field(default=None, description="该笔盈亏 (USDC)")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="交易时间"
    )


class BacktestFilterStat(BaseModel):
    """过滤原因归因统计。"""
    strategy_id: str = Field(default="", description="触发过滤的策略 id")
    strategy_name: str = Field(default="", description="触发过滤的策略名称")
    reason: str = Field(default="", description="过滤原因描述")
    count: int = Field(default=0, ge=0, description="过滤次数")
    pct: float = Field(default=0.0, ge=0, description="占历史交易总数的比例 (%)")


class BacktestResult(BaseModel):
    """回测结果。"""
    wallet: str = Field(default="", description="被回测的钱包地址")
    pipeline_id: str = Field(default="", description="回测使用的流水线 id")
    pipeline_name: str = Field(default="", description="回测使用的流水线名称")
    params: BacktestParams = Field(
        default_factory=BacktestParams, description="本次回测参数"
    )
    metrics: BacktestMetrics = Field(
        default_factory=BacktestMetrics, description="核心指标"
    )
    trades: list[BacktestTradeDetail] = Field(default_factory=list, description="逐笔明细")
    equity_curve: list[float] = Field(default_factory=list, description="累计盈亏曲线")
    filter_reasons: dict[str, int] = Field(
        default_factory=dict, description="过滤原因 → 次数"
    )
    filter_stats: list[BacktestFilterStat] = Field(
        default_factory=list, description="过滤原因归因统计"
    )
    data_source: str = Field(default="db", description="历史数据来源 (db/api)")


# ── 回测请求体模型 ──────────────────────────────────────────

class BacktestRunRequest(BaseModel):
    """运行回测请求。

    ``pipeline_id`` 与 ``pipeline_config`` 二选一：
    - 只给 ``pipeline_id``：从数据库读取已保存的流水线
    - 给 ``pipeline_config``：直接使用该流水线配置（不落库）
    """
    wallet: str = Field(..., description="钱包地址")
    pipeline_id: str | None = Field(default=None, description="已保存的流水线 id")
    pipeline_config: StrategyPipeline | None = Field(
        default=None, description="内联流水线配置"
    )
    params: BacktestParams = Field(default_factory=BacktestParams, description="回测参数")


class BacktestCompareRequest(BaseModel):
    """多钱包对比回测请求。"""
    wallets: list[str] = Field(default_factory=list, description="钱包地址列表")
    pipeline_id: str | None = Field(default=None, description="已保存的流水线 id")
    pipeline_config: StrategyPipeline | None = Field(
        default=None, description="内联流水线配置"
    )
    params: BacktestParams = Field(default_factory=BacktestParams, description="回测参数")


# ── 绩效分析（N3 任务区段） ────────────────────────────────

class PerformanceSummary(BaseModel):
    """跟单绩效总览。

    聚合 signals / executions / trades 三张表得到的全局指标。
    """
    period_days: int = Field(default=7, ge=0, description="统计周期天数（0 = 全部历史）")
    start_time: datetime | None = Field(default=None, description="统计区间起点")
    end_time: datetime | None = Field(default=None, description="统计区间终点")
    # 总览
    total_signals: int = Field(default=0, ge=0, description="生成的跟单信号总数")
    total_executions: int = Field(default=0, ge=0, description="执行记录总数")
    filled_executions: int = Field(default=0, ge=0, description="成交的执行数")
    fill_rate: float = Field(default=0.0, ge=0, le=1, description="成交率（成交 / 执行）")
    total_pnl: float = Field(default=0.0, description="总盈亏 (USDC)")
    win_rate: float = Field(default=0.0, ge=0, le=1, description="胜率（盈利笔数占比）")
    avg_pnl: float = Field(default=0.0, description="平均每笔盈亏 (USDC)")
    avg_hold_seconds: float = Field(
        default=0.0, ge=0, description="平均持仓时长（秒，近似值）"
    )
    avg_latency_ms: float = Field(
        default=0.0, ge=0, description="平均下单延迟（信号生成 → 执行完成，毫秒）"
    )
    total_wallets: int = Field(default=0, ge=0, description="涉及的钱包数")
    total_markets: int = Field(default=0, ge=0, description="涉及的市场数")
    # 执行质量
    risk_blocked_count: int = Field(default=0, ge=0, description="被风控拦截的次数")
    failed_count: int = Field(default=0, ge=0, description="执行失败次数")
    cancelled_count: int = Field(default=0, ge=0, description="被取消的次数")
    latency_p50_ms: float = Field(default=0.0, ge=0, description="下单延迟 P50 (ms)")
    latency_p90_ms: float = Field(default=0.0, ge=0, description="下单延迟 P90 (ms)")
    latency_p95_ms: float = Field(default=0.0, ge=0, description="下单延迟 P95 (ms)")
    latency_max_ms: float = Field(default=0.0, ge=0, description="下单延迟最大值 (ms)")
    has_data: bool = Field(default=False, description="区间内是否存在统计数据")


class WalletPerformance(BaseModel):
    """单个被监控钱包的跟单表现。"""
    wallet: str = Field(default="", description="钱包地址")
    alias: str = Field(default="", description="钱包别名")
    signal_count: int = Field(default=0, ge=0, description="该钱包产生的信号数")
    execution_count: int = Field(default=0, ge=0, description="该钱包的执行数")
    filled_count: int = Field(default=0, ge=0, description="该钱包的成交数")
    fill_rate: float = Field(default=0.0, ge=0, le=1, description="成交率")
    total_pnl: float = Field(default=0.0, description="该钱包贡献的盈亏 (USDC)")
    win_rate: float = Field(default=0.0, ge=0, le=1, description="胜率")
    avg_pnl: float = Field(default=0.0, description="平均每笔盈亏 (USDC)")
    contribution_pct: float = Field(
        default=0.0, description="盈亏贡献占比（%，该钱包盈亏 / 总盈亏）"
    )
    avg_latency_ms: float = Field(default=0.0, ge=0, description="平均下单延迟 (ms)")
    last_active: datetime | None = Field(default=None, description="最后活动时间")


class StrategyPerformance(BaseModel):
    """单个策略的归因表现。"""
    strategy_id: str = Field(default="", description="策略实例 id")
    strategy_type: str = Field(default="", description="策略类型 (MIRROR / FILTER ...)")
    name: str = Field(default="", description="策略显示名称")
    category: str = Field(default="", description="策略类别 (FILTER / TRANSFORM ...)")
    pipeline_id: str = Field(default="", description="所属流水线 id")
    pipeline_name: str = Field(default="", description="所属流水线名称")
    position: int = Field(default=0, ge=0, description="在流水线中的位置（1 起）")
    triggered_count: int = Field(default=0, ge=0, description="被触发（出现在策略链中）的次数")
    passed_count: int = Field(default=0, ge=0, description="触发后最终成交的次数")
    pass_rate: float = Field(default=0.0, ge=0, le=1, description="通过率（成交 / 触发）")
    contributed_pnl: float = Field(default=0.0, description="贡献盈亏 (USDC)")
    contribution_pct: float = Field(
        default=0.0, description="贡献占比（%，该策略盈亏 / 总盈亏）"
    )
    win_rate: float = Field(default=0.0, ge=0, le=1, description="该策略信号的胜率")


class MarketPerformance(BaseModel):
    """单个市场的跟单表现。"""
    market_key: str = Field(default="", description="市场唯一标识")
    condition_id: str = Field(default="", description="市场 condition id")
    token_id: str = Field(default="", description="CLOB token id")
    title: str = Field(default="", description="市场标题")
    slug: str = Field(default="", description="市场 slug")
    trade_count: int = Field(default=0, ge=0, description="源交易笔数")
    execution_count: int = Field(default=0, ge=0, description="执行笔数")
    filled_count: int = Field(default=0, ge=0, description="成交笔数")
    total_pnl: float = Field(default=0.0, description="总盈亏 (USDC)")
    win_rate: float = Field(default=0.0, ge=0, le=1, description="胜率")
    avg_pnl: float = Field(default=0.0, description="平均每笔盈亏 (USDC)")
    best_pnl: float = Field(default=0.0, description="最佳单笔盈亏 (USDC)")
    worst_pnl: float = Field(default=0.0, description="最差单笔盈亏 (USDC)")
    bucket: str = Field(
        default="flat", description="榜单分组: profit | loss | flat"
    )
    rank: int = Field(default=0, ge=0, description="分组内排名（1 起）")


class DailyPerformance(BaseModel):
    """每日绩效时间序列。"""
    date: str = Field(default="", description="日期 (YYYY-MM-DD, UTC)")
    pnl: float = Field(default=0.0, description="当日盈亏 (USDC)")
    signal_count: int = Field(default=0, ge=0, description="当日信号数")
    execution_count: int = Field(default=0, ge=0, description="当日执行数")
    filled_count: int = Field(default=0, ge=0, description="当日成交数")
    win_rate: float = Field(default=0.0, ge=0, le=1, description="当日胜率")
    cumulative_pnl: float = Field(default=0.0, description="累计盈亏 (USDC)")


# ── 组合跟单（多钱包分散） ───────────────────────────────────

class WalletAllocation(BaseModel):
    """单个被跟单钱包的仓位权重配置。

    权重用于把「策略给出的目标仓位」按比例切分到多个巨鲸钱包上，
    避免单一钱包黑天鹅导致整体跟单组合大幅回撤。
    """

    wallet: str = Field(..., description="被跟单钱包地址 (0x...)")
    weight: float = Field(
        default=1.0, gt=0, le=100, description="权重（相对值，系统按总量归一化）"
    )
    max_amount: float | None = Field(
        default=None, gt=0, description="该钱包单笔金额上限 (USDC)，None 表示用全局风控"
    )
    enabled: bool = Field(default=True, description="是否参与跟单")


class PortfolioAllocation(BaseModel):
    """组合跟单配置（多钱包权重分配）。"""

    allocations: list[WalletAllocation] = Field(
        default_factory=list, description="钱包权重列表"
    )
    # 组合级联动风控：所有钱包合计的敞口上限
    max_total_exposure: float = Field(
        default=1000.0, gt=0, description="组合总敞口上限 (USDC)，超出则拒绝新信号"
    )
    max_per_wallet_share: float = Field(
        default=0.6, gt=0, le=1,
        description="单钱包最大权重占比 (0-1)，用于强制分散；1 表示不限制",
    )
    # 权重模式：equal 等权 / manual 手动权重 / pnl 按历史盈亏加权
    weight_mode: str = Field(
        default="manual", pattern="^(equal|manual|pnl)$",
        description="权重模式: equal 等权 | manual 手动 | pnl 按绩效加权",
    )


class AllocationCheckResult(BaseModel):
    """一次仓位分配检查的结果。"""

    allowed: bool = Field(default=True, description="是否允许执行")
    wallet: str = Field(default="", description="本次分配的目标钱包")
    weight: float = Field(default=0.0, ge=0, description="归一化后的权重 (0-1)")
    allocated_amount: float = Field(default=0.0, ge=0, description="分配给该钱包的金额 (USDC)")
    reason: str = Field(default="", description="拒绝原因 / 说明")


class PortfolioExposure(BaseModel):
    """组合当前敞口快照（供前端展示）。"""

    total_exposure: float = Field(default=0.0, ge=0, description="当前总敞口 (USDC)")
    total_limit: float = Field(default=0.0, ge=0, description="总敞口上限 (USDC)")
    utilization: float = Field(default=0.0, ge=0, description="敞口使用率 (0-1)")
    wallets: list[dict[str, Any]] = Field(
        default_factory=list, description="各钱包敞口明细 [{wallet, exposure, weight, share}]"
    )
