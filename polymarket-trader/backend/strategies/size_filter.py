"""金额过滤策略。

只跟单金额（size × price）大于等于阈值的交易。
类别: FILTER
参数: threshold (float, 0-100000, default 50)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.SIZE_FILTER)
class SizeFilterStrategy(Strategy):
    """金额过滤：只跟单 size × price >= threshold 的交易。"""

    category = StrategyCategory.FILTER
    param_schema = {
        "threshold": {
            "type": "float",
            "default": 50.0,
            "min": 0.0,
            "max": 100000.0,
            "label": "最小金额 (USDC)",
            "description": "交易金额（size × price）低于此值则过滤",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        threshold = float(self.params.get("threshold", 50.0))
        trade_value = trade.size * trade.price
        if trade_value < threshold:
            return None
        return trade
