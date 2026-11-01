"""镜像跟单策略。

完全复制目标交易的方向、价格和数量，不做任何修改。
类别: EXECUTION
参数: 无
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.MIRROR)
class MirrorStrategy(Strategy):
    """镜像跟单：直接透传 TradeEvent，side/price/size 不变。"""

    category = StrategyCategory.EXECUTION
    param_schema: dict[str, dict] = {}

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        return trade
