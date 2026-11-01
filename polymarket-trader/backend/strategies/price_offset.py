"""价格偏移策略。

在源价格基础上偏移一定量，提高成交概率。
BUY 时加 offset（愿意出更高价），SELL 时减 offset（愿意出更低价）。
类别: TRANSFORM
参数:
  offset (float, 0-0.5, default 0.01)
  direction (enum: buy_up / sell_down / auto, default auto)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeSide, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.PRICE_OFFSET)
class PriceOffsetStrategy(Strategy):
    """价格偏移：price = trade.price ± offset。"""

    category = StrategyCategory.TRANSFORM
    param_schema = {
        "offset": {
            "type": "float",
            "default": 0.01,
            "min": 0.0,
            "max": 0.5,
            "label": "价格偏移量",
            "description": "BUY 时加 offset 提高成交概率，SELL 时减 offset",
        },
        "direction": {
            "type": "enum",
            "default": "auto",
            "label": "偏移方向",
            "description": "buy_up=仅买单加价, sell_down=仅卖单减价, auto=自动判断",
            "options": ["buy_up", "sell_down", "auto"],
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        offset = float(self.params.get("offset", 0.01))
        direction = self.params.get("direction", "auto")

        if direction == "buy_up" and trade.side == TradeSide.BUY:
            trade.price = trade.price + offset
        elif direction == "sell_down" and trade.side == TradeSide.SELL:
            trade.price = trade.price - offset
        elif direction == "auto":
            if trade.side == TradeSide.BUY:
                trade.price = trade.price + offset
            else:
                trade.price = trade.price - offset

        # 价格限制在 [0.01, 0.99]
        trade.price = max(0.01, min(0.99, trade.price))
        return trade
