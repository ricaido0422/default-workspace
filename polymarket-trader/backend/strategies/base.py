"""策略基类与注册表。

所有跟单策略继承 Strategy，实现 apply() 方法。
策略分为 FILTER（过滤）、TRANSFORM（变换）、RISK（风控）、EXECUTION（执行控制）四类。

设计要点：
- 每个策略接收 TradeEvent + StrategyContext，返回 Optional[TradeEvent]
- 返回 None 表示过滤掉该交易（不跟单），管道立即中止
- 返回变换后的 TradeEvent 表示继续管道处理
- param_schema 供前端动态渲染参数表单
- STRATEGY_REGISTRY 供管道从配置动态实例化策略
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent


# ── 策略上下文 ──────────────────────────────────────────────

@dataclass
class StrategyContext:
    """策略运行时上下文。

    持有当前仓位、历史交易统计、市场信息缓存等运行时状态，
    供策略在 apply() 中读取和决策。

    Attributes:
        positions: 当前仓位，按 condition_id 索引。
                   每个仓位为 dict，包含 size, side, entry_price, current_price, pnl 等。
        executed_today: 今日已执行交易数。
        total_pnl: 总盈亏 (USDC)。
        market_cache: 市场信息缓存，按 market_slug 索引。
                      包含 volume, tags, title, active 等。
        params: 全局/管道级参数，供策略间共享数据。
    """

    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    executed_today: int = 0
    total_pnl: float = 0.0
    market_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


# ── 策略注册表 ──────────────────────────────────────────────

STRATEGY_REGISTRY: dict[StrategyType, type[Strategy]] = {}
"""策略类型 → 策略类的映射，供动态实例化。由 @register_strategy 装饰器填充。"""


def register_strategy(strategy_type: StrategyType):
    """策略注册装饰器，将策略类注册到 STRATEGY_REGISTRY。

    用法::

        @register_strategy(StrategyType.MIRROR)
        class MirrorStrategy(Strategy):
            ...
    """

    def decorator(cls: type[Strategy]) -> type[Strategy]:
        STRATEGY_REGISTRY[strategy_type] = cls
        return cls

    return decorator


# ── 策略基类 ────────────────────────────────────────────────

class Strategy(ABC):
    """策略基类。

    每个策略接收一个 TradeEvent 和上下文，处理后返回：
    - None：表示过滤掉该交易（不跟单），管道立即中止
    - 变换后的 TradeEvent：表示继续管道处理

    Class Attributes:
        category: 策略类别（FILTER / TRANSFORM / RISK / EXECUTION）
        param_schema: 参数定义，供前端渲染表单。
                      格式: {param_name: {type, default, min, max, label, description, options}}

    Instance Attributes:
        params: 策略参数（从 StrategyConfig.params 传入）
        strategy_id: 策略实例 id（对应 StrategyConfig.id）
        enabled: 是否启用（False 时管道跳过此策略）
    """

    category: StrategyCategory
    param_schema: dict[str, dict[str, Any]] = {}

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        strategy_id: str = "",
        enabled: bool = True,
    ) -> None:
        self.params = params or {}
        self.strategy_id = strategy_id
        self.enabled = enabled

    @abstractmethod
    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        """应用策略。

        Args:
            trade: 输入交易事件
            ctx: 策略上下文（仓位、统计、缓存等）

        Returns:
            变换后的 TradeEvent（继续管道），或 None（过滤掉）
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.strategy_id}, params={self.params})"
