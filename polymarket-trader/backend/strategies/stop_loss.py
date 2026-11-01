"""止盈止损策略。

检查已有仓位的盈亏，触发止损或止盈时生成平仓信号（翻转 side 以平仓）。
类别: RISK
参数:
  stop_loss_pct (float, default 0.1, 即 10% 亏损)
  take_profit_pct (float, default 0.2, 即 20% 盈利)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeSide, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.STOP_LOSS)
class StopLossStrategy(Strategy):
    """止盈止损：检查仓位盈亏，触发则生成平仓信号。

    平仓逻辑：如果当前持仓是 BUY，止损/止盈时生成 SELL 信号（卖出平仓）；
    如果当前持仓是 SELL，止损/止盈时生成 BUY 信号（买入平仓）。
    平仓 size = 持仓 size。
    """

    category = StrategyCategory.RISK
    param_schema = {
        "stop_loss_pct": {
            "type": "float",
            "default": 0.1,
            "min": 0.0,
            "max": 1.0,
            "label": "止损比例",
            "description": "亏损达到此比例时触发止损平仓（0.1 = 10%）",
        },
        "take_profit_pct": {
            "type": "float",
            "default": 0.2,
            "min": 0.0,
            "max": 1.0,
            "label": "止盈比例",
            "description": "盈利达到此比例时触发止盈平仓（0.2 = 20%）",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        stop_loss_pct = float(self.params.get("stop_loss_pct", 0.1))
        take_profit_pct = float(self.params.get("take_profit_pct", 0.2))

        position = ctx.positions.get(trade.condition_id)
        if not position:
            # 无持仓，正常跟单
            return trade

        entry_price = float(position.get("entry_price", 0.0))
        current_price = float(position.get("current_price", trade.price))
        pos_side = position.get("side", "")
        pos_size = float(position.get("size", 0.0))

        if entry_price <= 0 or pos_size <= 0:
            return trade

        # 计算盈亏比例
        if pos_side == TradeSide.BUY.value:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        # 触发止损或止盈
        if pnl_pct <= -stop_loss_pct or pnl_pct >= take_profit_pct:
            # 生成平仓信号：翻转 side，size = 持仓 size
            trade.side = TradeSide.SELL if pos_side == TradeSide.BUY.value else TradeSide.BUY
            trade.size = pos_size
            trade.price = current_price

        return trade
