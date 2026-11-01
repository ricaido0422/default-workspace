"""反向跟单策略。

翻转交易方向：BUY → SELL，SELL → BUY。
类别: TRANSFORM
参数: 无
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeSide, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.REVERSE)
class ReverseStrategy(Strategy):
    """反向跟单：side 翻转（BUY ↔ SELL）。"""

    category = StrategyCategory.TRANSFORM
    param_schema: dict[str, dict] = {}

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        trade.side = TradeSide.SELL if trade.side == TradeSide.BUY else TradeSide.BUY
        return trade
