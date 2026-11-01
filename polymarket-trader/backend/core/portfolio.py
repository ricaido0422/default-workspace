"""组合跟单分配器（多钱包分散）。

把「策略给出的目标仓位」按权重切分到多个巨鲸钱包上，降低单点依赖：

- 权重归一化：weight 只表示相对大小，实际占比 = weight / Σweight
- 三种权重模式：``equal`` 等权 / ``manual`` 手动 / ``pnl`` 按历史盈亏加权
- 强制分散：单钱包占比不得超过 ``max_per_wallet_share``，超出则把余量
  按比例让渡给其他钱包（而不是简单拒绝）
- 组合联动风控：所有钱包合计敞口不得超过 ``max_total_exposure``
- 单钱包上限：``WalletAllocation.max_amount`` 可覆盖全局单笔上限

设计约束：
- 本模块不依赖 FastAPI / 数据库，可独立 import 与单元测试
- 线程/协程安全由调用方（单事件循环）保证，内部仅做纯计算
"""

from __future__ import annotations

import logging
from typing import Any

from backend.models.schemas import (
    AllocationCheckResult,
    PortfolioAllocation,
    WalletAllocation,
)

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """组合跟单分配器。

    Args:
        allocation: 组合配置（权重列表 + 联动风控上限）
    """

    def __init__(self, allocation: PortfolioAllocation | None = None) -> None:
        self._allocation = allocation or PortfolioAllocation()
        self.__init_pnl_cache()
        self.__init_exposure()

    # ── 配置管理 ──────────────────────────────────────────

    @property
    def allocation(self) -> PortfolioAllocation:
        """当前组合配置。"""
        return self._allocation

    def update(self, allocation: PortfolioAllocation) -> None:
        """整体替换配置（前端保存时调用）。"""
        self._allocation = allocation

    def set_weights(self, weights: dict[str, float]) -> None:
        """按 {wallet: weight} 更新权重，自动切换到 manual 模式。

        未出现在 weights 中的钱包保持原有权重；新钱包追加为默认权重 1.0。
        """
        existing = {a.wallet: a for a in self._allocation.allocations}
        for wallet, weight in weights.items():
            if weight <= 0:
                continue
            if wallet in existing:
                existing[wallet].weight = float(weight)
            else:
                existing[wallet] = WalletAllocation(
                    wallet=wallet, weight=float(weight), enabled=True
                )
        self._allocation.allocations = list(existing.values())
        self._allocation.weight_mode = "manual"

    # ── 权重计算 ──────────────────────────────────────────

    def _active_wallets(self) -> list[WalletAllocation]:
        """启用的钱包配置列表（保持配置顺序）。"""
        return [a for a in self._allocation.allocations if a.enabled and a.wallet]

    def normalized_weights(self) -> dict[str, float]:
        """计算归一化权重（考虑等权/手动/pnl 三种模式 + 强制分散上限）。

        Returns:
            {wallet: share}，share 之和为 1（无启用钱包时为空 dict）

        强制分散实现：先把原始权重归一化，若某钱包 share 超过
        ``max_per_wallet_share`` 则截顶，余量按其他钱包的原始占比
        再分配，迭代至收敛（上限 10 轮防止极端配置死循环）。
        """
        wallets = self._active_wallets()
        if not wallets:
            return {}

        raw: dict[str, float] = {}
        for a in wallets:
            raw[a.wallet] = self._raw_weight(a)

        total = sum(raw.values())
        if total <= 0:
            return {}

        shares = {w: v / total for w, v in raw.items()}

        cap = self._allocation.max_per_wallet_share
        if cap >= 1.0:
            return shares

        # 迭代截顶 + 余量再分配
        for _ in range(10):
            over = [w for w, s in shares.items() if s > cap + 1e-12]
            if not over:
                break
            excess = sum(shares[w] - cap for w in over)
            for w in over:
                shares[w] = cap
            under = {w: raw[w] for w in shares if w not in over}
            under_total = sum(under.values())
            if under_total <= 0:
                # 所有钱包都触顶：无法再分配，直接收敛
                break
            if excess <= 1e-12:
                break
            for w, v in under.items():
                shares[w] += excess * (v / under_total)
        return shares

    def _raw_weight(self, a: WalletAllocation) -> float:
        """单个钱包的原始权重（未归一化）。"""
        mode = self._allocation.weight_mode
        if mode == "equal":
            return 1.0
        if mode == "pnl":
            # pnl 模式：权重 = max(pnl, 0) + 1（保底项，避免全负权重时无钱包可选）
            pnl = float(self._pnl_cache.get(a.wallet, 0.0) or 0.0)
            return max(pnl, 0.0) + 1.0
        return float(a.weight) if a.weight > 0 else 0.0

    # ── 绩效缓存（pnl 权重模式用） ────────────────────────

    def __init_pnl_cache(self) -> None:
        if not hasattr(self, "_pnl_cache"):
            self._pnl_cache: dict[str, float] = {}

    def set_pnl_cache(self, pnl_by_wallet: dict[str, float]) -> None:
        """注入各钱包历史盈亏，供 weight_mode="pnl" 使用。"""
        self.__init_pnl_cache()
        self._pnl_cache = dict(pnl_by_wallet or {})

    # ── 敞口跟踪 ──────────────────────────────────────────

    def __init_exposure(self) -> None:
        if not hasattr(self, "_exposure"):
            self._exposure: dict[str, float] = {}

    def record_execution(self, wallet: str, amount: float) -> None:
        """记录一次成功执行，累加该钱包敞口。"""
        if amount <= 0:
            return
        self.__init_exposure()
        self._exposure[wallet] = self._exposure.get(wallet, 0.0) + float(amount)

    def release_exposure(self, wallet: str, amount: float) -> None:
        """平仓时释放敞口。"""
        self.__init_exposure()
        self._exposure[wallet] = max(0.0, self._exposure.get(wallet, 0.0) - float(amount))

    def total_exposure(self) -> float:
        """组合当前总敞口。"""
        self.__init_exposure()
        return sum(self._exposure.values())

    def wallet_exposure(self) -> dict[str, float]:
        """各钱包当前敞口。"""
        self.__init_exposure()
        return dict(self._exposure)

    # ── 分配检查 ──────────────────────────────────────────

    def allocate(
        self,
        wallet: str,
        target_amount: float,
        global_max_amount: float | None = None,
    ) -> AllocationCheckResult:
        """对一笔目标仓位做分配检查。

        Args:
            wallet: 策略信号对应的被跟单钱包（即信号的来源钱包）
            target_amount: 策略给出的目标金额 (USDC) = price × size
            global_max_amount: 全局单笔上限（来自 RiskConfig.max_trade_amount）

        Returns:
            AllocationCheckResult；``allowed=False`` 时 reason 说明拒绝原因
        """
        if target_amount <= 0:
            return AllocationCheckResult(allowed=False, wallet=wallet, reason="目标金额非正数")

        shares = self.normalized_weights()
        if not shares:
            # 未配置组合：视为单钱包全量通过（向后兼容旧行为）
            return AllocationCheckResult(
                allowed=True, wallet=wallet, weight=1.0,
                allocated_amount=target_amount, reason="未配置组合权重，全额执行",
            )

        if wallet not in shares:
            return AllocationCheckResult(
                allowed=False, wallet=wallet,
                reason=f"钱包 {wallet[:12]} 不在组合跟单配置中",
            )

        # 1. 单钱包占比分配
        share = shares[wallet]
        allocated = target_amount * share

        # 2. 单钱包金额上限（优先用该钱包自己的 max_amount）
        alloc_cfg = next(
            (a for a in self._active_wallets() if a.wallet == wallet), None
        )
        cap = alloc_cfg.max_amount if (alloc_cfg and alloc_cfg.max_amount) else global_max_amount
        if cap is not None and allocated > cap:
            allocated = cap

        # 3. 组合总敞口联动风控
        self.__init_exposure()
        limit = self._allocation.max_total_exposure
        remaining = limit - self.total_exposure()
        if remaining <= 0:
            return AllocationCheckResult(
                allowed=False, wallet=wallet,
                reason=f"组合总敞口 {self.total_exposure():.2f} 已达上限 {limit:.2f}",
            )
        if allocated > remaining:
            allocated = remaining

        if allocated <= 0:
            return AllocationCheckResult(
                allowed=False, wallet=wallet, reason="分配后金额为 0（敞口已用尽）"
            )

        return AllocationCheckResult(
            allowed=True,
            wallet=wallet,
            weight=share,
            allocated_amount=allocated,
            reason=f"权重 {share:.1%}，分配 {allocated:.2f} USDC",
        )

    # ── 敞口快照 ──────────────────────────────────────────

    def exposure_snapshot(self) -> dict[str, Any]:
        """构造前端展示用的敞口快照。"""
        shares = self.normalized_weights()
        total = self.total_exposure()
        limit = self._allocation.max_total_exposure
        wallets = []
        for wallet, share in shares.items():
            exposure = self._exposure.get(wallet, 0.0)
            wallets.append({
                "wallet": wallet,
                "weight": share,
                "exposure": exposure,
                "share": (exposure / total) if total > 0 else 0.0,
            })
        wallets.sort(key=lambda w: w["exposure"], reverse=True)
        return {
            "total_exposure": total,
            "total_limit": limit,
            "utilization": (total / limit) if limit > 0 else 0.0,
            "wallets": wallets,
        }
