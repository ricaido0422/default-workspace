"""下单执行层。

将跟单信号转化为实际订单，支持 Paper（模拟）和 Live（实盘）两种模式。
包含风控检查：单笔金额上限、单市场仓位上限、日交易数上限。
"""

from __future__ import annotations

import logging
import asyncio
import uuid
from decimal import Decimal, ROUND_DOWN
from abc import ABC, abstractmethod
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any

from backend.config import Settings
from backend.core.paper_positions import VirtualPositionTracker
from backend.core.polymarket_client import PolymarketClient
from backend.core.portfolio import PortfolioAllocator
from backend.models.schemas import (
    ExecutionMode,
    ExecutionRecord,
    ExecutionStatus,
    FollowSignal,
    PortfolioAllocation,
    TradeSide,
)
from backend.store.state import StateStore

logger = logging.getLogger(__name__)

# 执行回调类型
ExecutionCallback = Callable[[ExecutionRecord], Awaitable[None]]


# ── 风控 ────────────────────────────────────────────────────

class RiskCheckResult:
    """风控检查结果。"""
    def __init__(self, passed: bool, reason: str = "") -> None:
        self.passed = passed
        self.reason = reason


class RiskManager:
    """风控管理器。

    检查项：
    - 单笔金额上限 (max_trade_amount)
    - 单市场仓位上限 (max_position_per_market)
    - 日最大交易数 (max_daily_trades)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # 当日交易计数
        self._daily_count: int = 0
        self._daily_date: str = ""
        # 每市场持仓缓存 {condition_id: position_value}
        self._positions: dict[str, float] = {}

    def _reset_daily_if_needed(self) -> None:
        """跨日重置计数。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0

    def check(self, signal: FollowSignal) -> RiskCheckResult:
        """执行风控检查。

        Returns:
            RiskCheckResult(passed=True) 或 RiskCheckResult(passed=False, reason=...)
        """
        self._reset_daily_if_needed()
        risk = self.settings.risk

        # 1. 单笔金额 = price * size
        trade_amount = signal.price * signal.size
        if trade_amount > risk.max_trade_amount:
            return RiskCheckResult(
                False,
                f"单笔金额 {trade_amount:.2f} 超过上限 {risk.max_trade_amount:.2f}",
            )

        # 卖出由交易台与交易所检查实际份额，不能因买入额度耗尽而阻止减仓。
        if signal.side == TradeSide.SELL:
            return RiskCheckResult(True)

        # 2. 单市场仓位
        condition_id = signal.market.condition_id
        current_pos = self._positions.get(condition_id, 0.0)
        if current_pos + trade_amount > risk.max_position_per_market:
            return RiskCheckResult(
                False,
                f"市场 {condition_id[:12]} 仓位 {current_pos + trade_amount:.2f} 超过上限 {risk.max_position_per_market:.2f}",
            )

        # 3. 日交易数
        if self._daily_count >= risk.max_daily_trades:
            return RiskCheckResult(
                False,
                f"日交易数 {self._daily_count} 达到上限 {risk.max_daily_trades}",
            )

        return RiskCheckResult(True)

    def record_execution(self, signal: FollowSignal) -> None:
        """记录一次成功执行，更新风控状态。"""
        self._reset_daily_if_needed()
        self._daily_count += 1
        trade_amount = signal.price * signal.size
        condition_id = signal.market.condition_id
        if signal.side == TradeSide.BUY:
            self._positions[condition_id] = self._positions.get(condition_id, 0.0) + trade_amount

    def update_position(self, condition_id: str, delta: float) -> None:
        """手动更新持仓（用于平仓时减少仓位）。"""
        self._positions[condition_id] = max(0.0, self._positions.get(condition_id, 0.0) + delta)

    def get_daily_count(self) -> int:
        """获取当日交易数。"""
        self._reset_daily_if_needed()
        return self._daily_count


# ── 执行器基类 ──────────────────────────────────────────────

class OrderExecutorBase(ABC):
    """订单执行器基类。"""

    def __init__(self, settings: Settings, store: StateStore | None = None) -> None:
        self.settings = settings
        self.store = store

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """执行模式。"""
        ...

    @abstractmethod
    async def execute(self, signal: FollowSignal) -> ExecutionRecord:
        """执行跟单信号，返回执行记录。"""
        ...

    async def _save_record(self, record: ExecutionRecord) -> None:
        """保存执行记录到数据库。"""
        if self.store:
            try:
                await self.store.save_execution(record)
            except Exception:
                logger.exception("保存执行记录失败")


# ── Paper 执行器 ────────────────────────────────────────────

class PaperExecutor(OrderExecutorBase):
    """模拟资金跟单执行器（实盘数据 + 虚拟资金 + 实盘价结算）。

    不实际下单、不动真实 USDC，但：
    - 信号来自真实钱包交易
    - 成交价取 CLOB 真实盘口价
    - 盈亏通过虚拟持仓账本按真实价结算（盯市浮动 + 平仓已实现）
    """

    def __init__(
        self,
        settings: Settings,
        store: StateStore | None = None,
        client: PolymarketClient | None = None,
        tracker: VirtualPositionTracker | None = None,
    ) -> None:
        super().__init__(settings, store)
        self.client = client
        self.tracker = tracker

    def set_tracker(self, tracker: VirtualPositionTracker | None) -> None:
        """设置虚拟持仓账本（运行时可注入）。"""
        self.tracker = tracker

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.PAPER

    async def execute(self, signal: FollowSignal) -> ExecutionRecord:
        """模拟执行：按真实盘口价成交，按虚拟持仓账本结算真实盈亏。"""
        # 尝试获取 orderbook 最优价格作为模拟成交价
        fill_price = signal.quoted_price or signal.price
        if self.client and signal.quoted_price is None:
            try:
                side_str = "BUY" if signal.side == TradeSide.BUY else "SELL"
                best = await self.client.get_best_price(
                    signal.market.token_id, side=side_str
                )
                if best is not None and 0 < best < 1:
                    fill_price = best
            except Exception:
                logger.debug("获取 orderbook 价格失败，使用信号价格", exc_info=True)

        if getattr(self, 'submission_guard', None):
            await self.submission_guard(signal)
        # 已实现盈亏：交由虚拟持仓账本按真实成交价配对结算
        realized_pnl = 0.0
        if self.tracker and self.store:
            try:
                realized_pnl = await self.tracker.apply_fill(
                    wallet='desk-account' if signal.expected_mode is not None else signal.source_trade.wallet,
                    token_id=signal.market.token_id,
                    side=signal.side,
                    fill_price=fill_price,
                    fill_size=signal.size,
                    condition_id=signal.market.condition_id,
                    market_title=signal.market.title,
                    market_slug=signal.market.slug,
                )
            except Exception:
                logger.exception("更新虚拟持仓失败，本次不计已实现盈亏")

        order_id = f"paper_{signal.signal_id}_{uuid.uuid4().hex[:8]}"

        record = ExecutionRecord(
            signal_id=signal.signal_id,
            status=ExecutionStatus.FILLED,
            order_id=order_id,
            fill_price=fill_price,
            fill_size=signal.size,
            pnl=realized_pnl,
            mode=ExecutionMode.PAPER,
            error="",
        )

        logger.info(
            "[PAPER] 信号 %s 模拟成交: %s %.2f @ %.4f (已实现 PnL: %.2f)",
            signal.signal_id,
            signal.side.value,
            signal.size,
            fill_price,
            realized_pnl,
        )

        await self._save_record(record)
        return record


# ── Live 执行器 ──────────────────────────────────────────────

from backend.core.live_clob import LiveExecutor


# ── 执行器管理器 ────────────────────────────────────────────

class OrderExecutorManager:
    """执行器管理器。

    管理当前执行模式 (paper/live)，可动态切换。
    下单前做风控检查，风控不通过返回 FAILED + reason。
    执行后写 ExecutionRecord 到 DB，触发 on_execution 回调。
    """

    def __init__(
        self,
        settings: Settings,
        store: StateStore | None = None,
        client: PolymarketClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self._execution_lock = asyncio.Lock()

        self._risk = RiskManager(settings)
        self._callbacks: list[ExecutionCallback] = []
        self._paper = PaperExecutor(settings, store, client)
        self._live = LiveExecutor(settings, store, client)
        self.submission_guards = []
        self._paper.submission_guard = self.check_submission
        self._live.submission_guard = self.check_submission
        # 虚拟持仓账本：模拟资金模式下按实盘价盯市结算
        self._positions = VirtualPositionTracker(store, client)
        self._paper.set_tracker(self._positions)
        # 组合跟单分配器（默认未配置任何钱包，向后兼容全额执行）
        self._allocator = PortfolioAllocator()
        self._current_mode: ExecutionMode = (
            ExecutionMode.LIVE if settings.executor.mode == "live" else ExecutionMode.PAPER
        )

        from backend.core.live_connection import LiveConnection
        self.live_connection = LiveConnection(self)
        self._configuration_lock = asyncio.Lock()
        self._live.armed = self._current_mode == ExecutionMode.LIVE

    # ── 模式管理 ──────────────────────────────────────────

    @property
    def mode(self) -> ExecutionMode:
        """当前执行模式。"""
        return self._current_mode

    @property
    def risk_key(self):
        account_id = self.accounts.active_id if hasattr(self, 'accounts') else 'legacy'
        return 'live_risk_state' if account_id == 'legacy' else 'live_risk_state:' + account_id

    async def check_submission(self, signal):
        if signal.expected_mode is not None and signal.expected_mode != self.mode:
            raise ValueError('执行模式已改变，未提交')
        for guard in self.submission_guards:
            await guard(signal)

    def set_mode(self, mode: str) -> None:
        """切换执行模式 (paper/live)。"""
        new_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
        self._live.armed = new_mode == ExecutionMode.LIVE
        if hasattr(self, 'accounts'):
            for adapter in self.accounts.adapters.values():
                if hasattr(adapter, 'engine'):
                    adapter.engine.armed = new_mode == ExecutionMode.LIVE and adapter.profile.id == self.accounts.active_id
        if new_mode == self._current_mode:
            return
        self._current_mode = new_mode
        self._risk = RiskManager(self.settings)
        self._allocator._exposure = {}
        logger.info("执行器模式已切换为: %s", new_mode.value)

    def get_executor(self) -> OrderExecutorBase:
        """获取当前执行器。"""
        return getattr(self, 'account_coordinator', self._live) if self._current_mode == ExecutionMode.LIVE else self._paper

    # ── 回调 ──────────────────────────────────────────────

    def on_execution(self, callback: ExecutionCallback) -> None:
        """注册执行回调。"""
        self._callbacks.append(callback)

    async def _notify_callbacks(self, record: ExecutionRecord) -> None:
        """通知所有执行回调。"""
        for cb in self._callbacks:
            try:
                await cb(record)
            except Exception:
                logger.exception("执行回调失败")

    # ── 风控 ──────────────────────────────────────────────

    @property
    def risk(self) -> RiskManager:
        """风控管理器。"""
        return self._risk

    # ── 组合分配 ──────────────────────────────────────────

    @property
    def allocator(self) -> PortfolioAllocator:
        """组合跟单分配器（多钱包权重分配 + 敞口联动风控）。"""
        return self._allocator

    def set_portfolio_allocation(self, allocation: PortfolioAllocation) -> None:
        """设置组合跟单配置（前端保存时调用）。"""
        self._allocator.update(allocation)

    def set_allocator_pnl_cache(self, pnl_by_wallet: dict[str, float]) -> None:
        """注入各钱包历史盈亏，供 weight_mode="pnl" 加权使用。"""
        self._allocator.set_pnl_cache(pnl_by_wallet)

    # ── 虚拟持仓（模拟资金盯市） ──────────────────────────

    @property
    def positions(self) -> VirtualPositionTracker:
        """虚拟持仓账本（按实盘价盯市结算）。"""
        return self._positions

    # ── 执行 ──────────────────────────────────────────────

    async def execute(self, signal: FollowSignal) -> ExecutionRecord:
        async with self._execution_lock:
            if self._current_mode == ExecutionMode.LIVE and hasattr(self, 'accounts') and signal.account_id != self.accounts.active_id:
                record = ExecutionRecord(signal_id=signal.signal_id, account_id=signal.account_id,
                    status=ExecutionStatus.FAILED, mode=ExecutionMode.LIVE, error='账户已切换，禁止将原账户信号提交到新钱包')
                await self._save_record(record)
                return record
            if signal.expected_mode is not None and signal.expected_mode != self._current_mode:
                record = ExecutionRecord(signal_id=signal.signal_id, status=ExecutionStatus.FAILED,
                    mode=signal.expected_mode, error='审批期间执行模式已变更，已取消委托')
                await self._save_record(record)
                return record
            if self._current_mode == ExecutionMode.LIVE and self.store:
                previous = await self.store.get_execution(signal.signal_id)
                if previous:
                    return previous
                saved = await self.store.get_config(self.risk_key)
                if saved:
                    self._risk._positions = saved.get('positions', {})
                    self._risk._daily_count = saved.get('daily_count', 0)
                    self._risk._daily_date = saved.get('daily_date', '')
                    self._allocator._exposure = saved.get('portfolio_exposure', {})
                records = await self.store.get_executions(limit=1000000)
                if any(r.mode == ExecutionMode.LIVE and r.account_id == signal.account_id and r.status == ExecutionStatus.PENDING for r in records):
                    record = ExecutionRecord(signal_id=signal.signal_id, status=ExecutionStatus.FAILED,
                                             mode=ExecutionMode.LIVE, error='实盘暂停：存在结果待核对订单，请先核对订单')
                    await self._save_record(record)
                    return record
            return await self._execute(signal)

    async def _execute(self, signal: FollowSignal) -> ExecutionRecord:
        """执行跟单信号。

        流程：
        1. 风控检查 → 不通过返回 FAILED + reason
        2. 组合分配检查（多钱包权重 + 总敞口联动）
        3. 调用当前执行器执行
        4. 写 ExecutionRecord 到 DB
        5. 触发 on_execution 回调
        """
        # 1. 风控检查
        risk_result = self._risk.check(signal)
        if not risk_result.passed:
            record = ExecutionRecord(
                signal_id=signal.signal_id,
                status=ExecutionStatus.FAILED,
                mode=self._current_mode,
                error=f"风控拒绝: {risk_result.reason}",
            )
            logger.warning(
                "信号 %s 被风控拒绝: %s", signal.signal_id, risk_result.reason
            )
            await self._save_record(record)
            await self._notify_callbacks(record)
            return record

        # 2. 组合分配检查（未配置组合时全额放行，向后兼容）
        alloc_result = self._allocator.allocate(
            wallet=signal.source_trade.wallet,
            target_amount=signal.price * signal.size,
            global_max_amount=self.settings.risk.max_trade_amount,
        )
        if signal.pipeline_id == 'manual':
            # 自主委托不属于某个被跟单钱包，不按来源钱包权重缩单。
            alloc_result.allowed = True
            alloc_result.allocated_amount = signal.price * signal.size
        if signal.side == TradeSide.BUY and self._allocator.total_exposure() + alloc_result.allocated_amount > self._allocator.allocation.max_total_exposure:
            alloc_result.allowed = False
            alloc_result.reason = '组合总投入额度不足'
        if signal.side == TradeSide.SELL:
            alloc_result.allowed = True
            alloc_result.allocated_amount = signal.price * signal.size
        if not alloc_result.allowed:
            record = ExecutionRecord(
                signal_id=signal.signal_id,
                status=ExecutionStatus.FAILED,
                mode=self._current_mode,
                error=f"组合风控拒绝: {alloc_result.reason}",
            )
            logger.warning(
                "信号 %s 被组合风控拒绝: %s", signal.signal_id, alloc_result.reason
            )
            await self._save_record(record)
            await self._notify_callbacks(record)
            return record

        # 2b. 按分配结果缩放下单量（分配金额 < 目标金额时等比缩减 size）
        if alloc_result.allocated_amount < signal.price * signal.size and signal.price > 0:
            signal = signal.model_copy(
                update={"size": alloc_result.allocated_amount / signal.price}
            )
            logger.debug(
                "信号 %s 按组合权重缩放下单量: %.2f USDC",
                signal.signal_id, alloc_result.allocated_amount,
            )

        # 统一模拟与实盘数量精度；组合缩放后还要检查市场最小份额。
        rounded_size = float(Decimal(str(signal.size)).quantize(Decimal('.01'), rounding=ROUND_DOWN))
        if rounded_size <= 0 or rounded_size < signal.min_order_size:
            record = ExecutionRecord(signal_id=signal.signal_id, status=ExecutionStatus.FAILED,
                mode=self._current_mode, error='组合分配及精度取整后，份额低于市场最小委托量')
            await self._save_record(record)
            return record
        signal = signal.model_copy(update={'size': rounded_size})

        # 3. 执行
        if self._current_mode == ExecutionMode.LIVE:
            if not self.store:
                raise RuntimeError('实盘必须启用持久化存储')
            # 实盘保守预占额度；拒单也保留预算，避免崩溃和重启绕过限额。
            self._risk.record_execution(signal)
            if signal.side == TradeSide.BUY:
                self._allocator.record_execution(signal.source_trade.wallet, signal.price * signal.size)
            await self.store.set_config(self.risk_key, {
                'positions': self._risk._positions, 'daily_count': self._risk._daily_count,
                'daily_date': self._risk._daily_date,
                'portfolio_exposure': self._allocator.wallet_exposure(),
            })
        executor = self.get_executor()
        if signal.expected_mode is not None and signal.expected_mode != self._current_mode:
            record = ExecutionRecord(signal_id=signal.signal_id, status=ExecutionStatus.FAILED,
                mode=signal.expected_mode, error='执行模式已变更，未提交订单')
            await self._save_record(record)
            return record
        try:
            record = await executor.execute(signal)
        except Exception as e:
            logger.exception("执行信号 %s 失败", signal.signal_id)
            record = ExecutionRecord(
                signal_id=signal.signal_id,
                status=ExecutionStatus.FAILED,
                mode=self._current_mode,
                error=str(e),
            )
            await self._save_record(record)

        # 3. 风控状态更新（成功执行才计数）
        if record.status == ExecutionStatus.FILLED:
            if self._current_mode != ExecutionMode.LIVE:
                self._risk.record_execution(signal)
            # 累加组合敞口（按实际成交金额计）
            fill_amount = (record.fill_price or signal.price) * (
                record.fill_size or signal.size
            )
            if self._current_mode != ExecutionMode.LIVE and signal.side == TradeSide.BUY:
                self._allocator.record_execution(signal.source_trade.wallet, fill_amount)

        # 4. 触发回调
        await self._notify_callbacks(record)

        return record

    async def _save_record(self, record: ExecutionRecord) -> None:
        """保存执行记录到数据库。"""
        if self.store:
            try:
                await self.store.save_execution(record)
            except Exception:
                logger.exception("保存执行记录失败")
