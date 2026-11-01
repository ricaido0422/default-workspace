"""固定金额策略。

使用固定 USDC 金额下单，根据当前价格重新计算 size。
类别: TRANSFORM
参数: amount (float, 1-10000, default 100)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.FIXED_AMOUNT)
class FixedAmountStrategy(Strategy):
    """固定金额：size = amount / price。"""

    category = StrategyCategory.TRANSFORM
    param_schema = {
        "amount": {
            "type": "float",
            "default": 100.0,
            "min": 1.0,
            "max": 10000.0,
            "label": "固定金额 (USDC)",
            "description": "每次跟单使用固定 USDC 金额，size = amount / price",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        amount = float(self.params.get("amount", 100.0))
        if trade.price > 0:
            trade.size = amount / trade.price
        return trade
