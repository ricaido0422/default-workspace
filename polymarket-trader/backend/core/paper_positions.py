"""虚拟持仓账本（模拟资金 + 实盘行情盯市结算）。

核心定位：
    模拟盘（paper 模式）接入的是 Polymarket 真实数据（真实钱包交易 + 真实盘口价），
    但投入的是虚拟资金——不下真实单、不动真实 USDC。
    盈亏按「实盘为主」结算：
    - 建仓：按真实成交价建立虚拟持仓（移动加权平均成本）
    - 持有：按 CLOB 实时真实价盯市，计算浮动盈亏
    - 平仓：按真实成交价配对，计算已实现盈亏

记账口径：
    以 (钱包, token_id) 为唯一持仓键，净数量带符号（正=多头，负=空头）。
    采用移动加权平均成本法，平仓时按加权均价结算，支持反手（多翻空 / 空翻多）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.models.schemas import (
    TradeSide,
    VirtualPortfolioSummary,
    VirtualPosition,
    VirtualPositionView,
)

logger = logging.getLogger(__name__)

# 视为"已平仓"的数量阈值（浮点误差容忍）
_QTY_EPS = 1e-9


class VirtualPositionTracker:
    """虚拟持仓账本。

    职责：
    1. 每笔模拟成交后更新虚拟持仓，计算该笔产生的**已实现盈亏**
    2. 用 CLOB 真实价格对未平仓持仓盯市，计算**浮动盈亏**
    """

    def __init__(self, store: Any, client: Any) -> None:
        self.store = store
        self.client = client

    # ── 成交记账 ──────────────────────────────────────────

    async def apply_fill(
        self,
        wallet: str,
        token_id: str,
        side: TradeSide,
        fill_price: float,
        fill_size: float,
        *,
        condition_id: str = "",
        market_title: str = "",
        market_slug: str = "",
    ) -> float:
        """记录一笔模拟成交，返回本次成交产生的已实现盈亏 (USDC)。

        移动加权平均成本法：
        - 开仓/加仓：只更新均价与净数量，不产生已实现盈亏（返回 0）
        - 减仓/平仓：按加权均价结算已实现盈亏
        - 反手（平完后再开反向）：先结算已实现，再按成交价开新仓

        Args:
            wallet: 跟单来源钱包（持仓归属）
            token_id: CLOB token id（持仓标的）
            side: BUY / SELL
            fill_price: 真实成交价
            fill_size: 成交数量
            condition_id / market_title / market_slug: 市场元信息

        Returns:
            本次成交产生的已实现盈亏（USDC，正为盈利）
        """
        if not token_id or fill_size <= 0 or fill_price is None:
            return 0.0

        pos = await self.store.get_virtual_position(wallet, token_id)
        now = datetime.now(timezone.utc)

        if pos is None:
            pos = VirtualPosition(
                wallet=wallet,
                token_id=token_id,
                condition_id=condition_id,
                market_title=market_title,
                market_slug=market_slug,
                net_qty=0.0,
                avg_entry=0.0,
                realized_pnl=0.0,
                opened_at=now,
                updated_at=now,
            )
        else:
            # 补齐历史记录缺失的市场信息
            if market_title and not pos.market_title:
                pos.market_title = market_title
            if market_slug and not pos.market_slug:
                pos.market_slug = market_slug
            if condition_id and not pos.condition_id:
                pos.condition_id = condition_id

        # 成交方向：BUY 为 +，SELL 为 -
        signed_fill = fill_size if side == TradeSide.BUY else -fill_size
        realized = 0.0

        if abs(pos.net_qty) < _QTY_EPS:
            # 空仓 → 直接开仓
            pos.net_qty = signed_fill
            pos.avg_entry = fill_price
            pos.opened_at = now
        elif (pos.net_qty > 0) == (signed_fill > 0):
            # 同向 → 加仓，重算加权均价
            total_qty = abs(pos.net_qty) + abs(signed_fill)
            pos.avg_entry = (
                pos.avg_entry * abs(pos.net_qty) + fill_price * abs(signed_fill)
            ) / total_qty
            pos.net_qty += signed_fill
        else:
            # 反向 → 先平掉可平部分，结算已实现盈亏
            closing_qty = min(abs(signed_fill), abs(pos.net_qty))
            if pos.net_qty > 0:
                # 平多：卖出价 - 均价
                realized = (fill_price - pos.avg_entry) * closing_qty
            else:
                # 平空：均价 - 买入价
                realized = (pos.avg_entry - fill_price) * closing_qty

            pos.realized_pnl += realized
            pos.net_qty += signed_fill

            remaining = abs(signed_fill) - closing_qty
            if remaining > _QTY_EPS:
                # 反手开仓：成交价即新均价
                pos.avg_entry = fill_price
                pos.opened_at = now
            elif abs(pos.net_qty) < _QTY_EPS:
                # 完全平仓，均价归零（保留已实现盈亏）
                pos.net_qty = 0.0
                pos.avg_entry = 0.0

        pos.updated_at = now
        await self.store.upsert_virtual_position(pos)

        logger.info(
            "[虚拟持仓] %s %s %s %.2f @ %.4f → 净持仓 %.2f @ %.4f，本次已实现 %+.2f USDC",
            wallet[:10],
            side.value,
            token_id[:12],
            fill_size,
            fill_price,
            pos.net_qty,
            pos.avg_entry,
            realized,
        )
        return realized

    # ── 盯市（以实盘价结算） ──────────────────────────────

    async def _real_price(self, token_id: str, net_qty: float) -> tuple[float | None, str]:
        """获取用于盯市的真实价格。

        多头按可卖出价（bid）估值，空头按需回补价（ask）估值；
        两者取不到时退用中间价 (midpoint)。

        Returns:
            (价格, 来源说明)；取不到返回 (None, "unavailable")
        """
        if not self.client:
            return None, "unavailable"

        # 优先按方向取可执行价：多头看 bid，空头看 ask
        want_side = "SELL" if net_qty > 0 else "BUY"
        fallback_side = "BUY" if net_qty > 0 else "SELL"
        try:
            price = await self.client.get_best_price(token_id, side=want_side)
            if price is not None and 0 < price < 1:
                return price, "bid" if net_qty > 0 else "ask"
            price = await self.client.get_best_price(token_id, side=fallback_side)
            if price is not None and 0 < price < 1:
                return price, "ask" if net_qty > 0 else "bid"
        except Exception:
            logger.debug("取 %s 最优价失败，改用中间价", token_id[:12], exc_info=True)

        try:
            mid = await self.client.get_midpoint(token_id)
            if mid is not None and 0 < mid < 1:
                return mid, "midpoint"
        except Exception:
            logger.debug("取 %s 中间价失败", token_id[:12], exc_info=True)

        return None, "unavailable"

    async def mark_position(self, pos: VirtualPosition) -> VirtualPositionView:
        """对单个持仓按真实价盯市。"""
        mark_price: float | None = None
        source = "flat"
        if abs(pos.net_qty) > _QTY_EPS:
            mark_price, source = await self._real_price(pos.token_id, pos.net_qty)

        # 浮动盈亏：多头 (现价-均价)×数量；空头 (均价-现价)×数量
        if mark_price is None or abs(pos.net_qty) < _QTY_EPS:
            floating = 0.0
        elif pos.net_qty > 0:
            floating = (mark_price - pos.avg_entry) * pos.net_qty
        else:
            floating = (pos.avg_entry - mark_price) * abs(pos.net_qty)

        qty = abs(pos.net_qty)
        value = (mark_price or pos.avg_entry) * qty
        side_label = "FLAT"
        if pos.net_qty > _QTY_EPS:
            side_label = "LONG"
        elif pos.net_qty < -_QTY_EPS:
            side_label = "SHORT"

        return VirtualPositionView(
            wallet=pos.wallet,
            token_id=pos.token_id,
            condition_id=pos.condition_id,
            market_title=pos.market_title,
            market_slug=pos.market_slug,
            net_qty=pos.net_qty,
            avg_entry=pos.avg_entry,
            side=side_label,
            mark_price=mark_price,
            price_source=source,
            position_value=value,
            realized_pnl=pos.realized_pnl,
            floating_pnl=floating,
            total_pnl=pos.realized_pnl + floating,
            updated_at=pos.updated_at,
        )

    async def snapshot(self, wallet: str | None = None) -> VirtualPortfolioSummary:
        """生成组合盯市快照（并发取真实价）。

        Args:
            wallet: 指定钱包则只盯市该钱包持仓；None 为全部

        Returns:
            VirtualPortfolioSummary，含逐仓明细与汇总
        """
        positions = await self.store.get_virtual_positions(wallet=wallet)
        views = await asyncio.gather(
            *(self.mark_position(p) for p in positions),
            return_exceptions=True,
        )

        result: list[VirtualPositionView] = []
        for view in views:
            if isinstance(view, BaseException):
                logger.debug("盯市单项失败，跳过", exc_info=view)
                continue
            result.append(view)

        total_realized = sum(v.realized_pnl for v in result)
        total_floating = sum(v.floating_pnl for v in result)
        return VirtualPortfolioSummary(
            open_positions=sum(1 for v in result if v.side != "FLAT"),
            total_realized_pnl=total_realized,
            total_floating_pnl=total_floating,
            total_pnl=total_realized + total_floating,
            positions=result,
        )
