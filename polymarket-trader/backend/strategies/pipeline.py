"""策略流水线执行器。

将多个策略按顺序组合，依次处理输入的 TradeEvent。
任一策略返回 None 则信号被过滤，管道立即中止。
最终将处理后的 TradeEvent 转为 FollowSignal。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.models.schemas import (
    FollowSignal,
    MarketRef,
    StrategyConfig,
    StrategyPipeline as StrategyPipelineConfig,
    StrategyType,
    TradeEvent,
)
from backend.strategies.base import STRATEGY_REGISTRY, Strategy, StrategyContext

logger = logging.getLogger(__name__)


class StrategyPipelineRunner:
    """策略流水线执行器。

    按顺序执行策略链，任一策略返回 None 则信号被过滤。
    最终将处理后的 TradeEvent 转为 FollowSignal（记录 strategy_chain）。

    Attributes:
        strategies: 有序策略列表
        pipeline_id: 流水线 id
        pipeline_name: 流水线名称
    """

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        pipeline_id: str = "",
        pipeline_name: str = "",
    ) -> None:
        self.strategies: list[Strategy] = strategies or []
        self.pipeline_id = pipeline_id
        self.pipeline_name = pipeline_name

    # ── 核心处理 ───────────────────────────────────────────

    async def process(
        self, trade: TradeEvent, ctx: StrategyContext
    ) -> FollowSignal | None:
        """处理交易事件，生成跟单信号。

        按顺序应用每个策略：
        - 跳过 enabled=False 的策略
        - 任一策略返回 None → 中止，返回 None（被过滤）
        - 全部通过后，将处理后的 TradeEvent 转为 FollowSignal

        Args:
            trade: 源交易事件
            ctx: 策略上下文

        Returns:
            FollowSignal 或 None（被过滤）
        """
        # 复制 trade 避免修改原始对象
        current: TradeEvent = trade.model_copy(deep=True)
        chain: list[str] = []

        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            try:
                result = await strategy.apply(current, ctx)
            except Exception:
                logger.exception("策略 %s 执行失败", strategy)
                return None
            if result is None:
                logger.debug("交易被策略 %s 过滤", strategy)
                return None
            current = result
            chain.append(strategy.strategy_id)

        # 转为 FollowSignal
        signal = FollowSignal(
            source_trade=trade,
            pipeline_id=self.pipeline_id,
            market=MarketRef(
                condition_id=current.condition_id,
                token_id=current.token_id,
                slug=current.market_slug,
                title=current.market_title,
            ),
            side=current.side,
            price=current.price,
            size=current.size,
            strategy_chain=chain,
            signal_id=str(uuid.uuid4()),
        )
        return signal

    # ── 动态管理 ───────────────────────────────────────────

    def add_strategy(self, strategy: Strategy) -> None:
        """追加策略到管道末尾。"""
        self.strategies.append(strategy)

    def remove_strategy(self, strategy_id: str) -> bool:
        """按 strategy_id 移除策略，返回是否成功。"""
        before = len(self.strategies)
        self.strategies = [s for s in self.strategies if s.strategy_id != strategy_id]
        return len(self.strategies) < before

    def insert_strategy(self, index: int, strategy: Strategy) -> None:
        """在指定位置插入策略。"""
        self.strategies.insert(index, strategy)

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> bool:
        """启用/禁用单个策略，返回是否找到。"""
        for s in self.strategies:
            if s.strategy_id == strategy_id:
                s.enabled = enabled
                return True
        return False

    def update_params(self, strategy_id: str, params: dict[str, Any]) -> bool:
        """更新策略参数，返回是否找到。"""
        for s in self.strategies:
            if s.strategy_id == strategy_id:
                s.params = params
                return True
        return False

    # ── 工厂方法 ───────────────────────────────────────────

    @classmethod
    def from_config(cls, pipeline_config: StrategyPipelineConfig) -> StrategyPipelineRunner:
        """从 StrategyPipeline 配置构建管道。

        按 display_order 排序策略，跳过 enabled=False 的 StrategyConfig。
        """
        sorted_configs = sorted(
            pipeline_config.strategies, key=lambda s: s.display_order
        )
        strategies: list[Strategy] = []
        for cfg in sorted_configs:
            if not cfg.enabled:
                continue
            strategy_cls = STRATEGY_REGISTRY.get(cfg.type)
            if strategy_cls is None:
                logger.warning("未知策略类型 %s，跳过", cfg.type)
                continue
            strategy = strategy_cls(
                params=cfg.params,
                strategy_id=cfg.id,
                enabled=cfg.enabled,
            )
            strategies.append(strategy)
        return cls(
            strategies=strategies,
            pipeline_id=pipeline_config.id,
            pipeline_name=pipeline_config.name,
        )

    def __repr__(self) -> str:
        return (
            f"StrategyPipelineRunner(id={self.pipeline_id}, "
            f"name={self.pipeline_name}, strategies={len(self.strategies)})"
        )
