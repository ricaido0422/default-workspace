"""仓位上限策略。

检查当前市场仓位和总仓位，超限则过滤掉该交易。
类别: RISK
参数:
  max_per_market (float, default 1000)
  max_total (float, default 10000)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.POSITION_CAP)
class PositionCapStrategy(Strategy):
    """仓位上限：检查当前仓位，超限则过滤（返回 None）。"""

    category = StrategyCategory.RISK
    param_schema = {
        "max_per_market": {
            "type": "float",
            "default": 1000.0,
            "min": 0.0,
            "label": "单市场仓位上限 (USDC)",
            "description": "单个市场的最大仓位价值，超限则不跟单",
        },
        "max_total": {
            "type": "float",
            "default": 10000.0,
            "min": 0.0,
            "label": "总仓位上限 (USDC)",
            "description": "所有市场的最大仓位总价值，超限则不跟单",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        max_per_market = float(self.params.get("max_per_market", 1000.0))
        max_total = float(self.params.get("max_total", 10000.0))

        trade_value = trade.size * trade.price

        # 检查单市场仓位上限
        position = ctx.positions.get(trade.condition_id, {})
        current_position_value = float(position.get("value", 0.0))
        if current_position_value + trade_value > max_per_market:
            return None

        # 检查总仓位上限
        total_position_value = sum(
            float(pos.get("value", 0.0)) for pos in ctx.positions.values()
        )
        if total_position_value + trade_value > max_total:
            return None

        return trade
