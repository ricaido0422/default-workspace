"""策略模块。

导入所有策略子模块以触发 @register_strategy 注册，
使 STRATEGY_REGISTRY 在 import backend.strategies 后即填充完毕。
"""

from __future__ import annotations

# 导入 base 先定义 Strategy / StrategyContext / STRATEGY_REGISTRY
from backend.strategies.base import Strategy, StrategyContext, STRATEGY_REGISTRY, register_strategy

# 导入所有策略子模块，触发注册装饰器
from backend.strategies.mirror import MirrorStrategy
from backend.strategies.proportional import ProportionalStrategy
from backend.strategies.fixed_amount import FixedAmountStrategy
from backend.strategies.price_offset import PriceOffsetStrategy
from backend.strategies.delayed import DelayedStrategy
from backend.strategies.market_filter import MarketFilterStrategy
from backend.strategies.size_filter import SizeFilterStrategy
from backend.strategies.reverse import ReverseStrategy
from backend.strategies.position_cap import PositionCapStrategy
from backend.strategies.stop_loss import StopLossStrategy

# 导入管道执行器
from backend.strategies.pipeline import StrategyPipelineRunner

__all__ = [
    "Strategy",
    "StrategyContext",
    "STRATEGY_REGISTRY",
    "register_strategy",
    "StrategyPipelineRunner",
    "MirrorStrategy",
    "ProportionalStrategy",
    "FixedAmountStrategy",
    "PriceOffsetStrategy",
    "DelayedStrategy",
    "MarketFilterStrategy",
    "SizeFilterStrategy",
    "ReverseStrategy",
    "PositionCapStrategy",
    "StopLossStrategy",
]
