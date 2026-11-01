"""比例跟单策略。

按比例系数缩放交易数量。
类别: TRANSFORM
参数: ratio (float, 0.01-10, default 1.0)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.PROPORTIONAL)
class ProportionalStrategy(Strategy):
    """比例跟单：size *= ratio。"""

    category = StrategyCategory.TRANSFORM
    param_schema = {
        "ratio": {
            "type": "float",
            "default": 1.0,
            "min": 0.01,
            "max": 10.0,
            "label": "比例系数",
            "description": "跟单数量 = 源交易数量 × 比例系数",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        ratio = float(self.params.get("ratio", 1.0))
        trade.size = trade.size * ratio
        return trade
