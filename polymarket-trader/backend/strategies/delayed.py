"""延迟跟单策略。

标记延迟 N 秒执行，由执行器读取 trade.delay_seconds 延迟处理。
类别: EXECUTION
参数: delay_seconds (int, 0-3600, default 0)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.DELAYED)
class DelayedStrategy(Strategy):
    """延迟跟单：在 TradeEvent 上标记 delay_seconds，由执行器延迟处理。"""

    category = StrategyCategory.EXECUTION
    param_schema = {
        "delay_seconds": {
            "type": "int",
            "default": 0,
            "min": 0,
            "max": 3600,
            "label": "延迟秒数",
            "description": "延迟 N 秒后执行跟单，0 表示立即执行",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        delay = int(self.params.get("delay_seconds", 0))
        trade.delay_seconds = delay
        return trade
