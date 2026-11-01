"""市场过滤策略。

只跟单匹配的市场，按白名单/黑名单 + 标签 + 最小交易量过滤。
类别: FILTER
参数:
  mode (enum: whitelist / blacklist, default whitelist)
  market_slugs (list[str], default [])
  tags (list[str], default [])
  min_volume (float, default 0)
"""

from __future__ import annotations

from backend.models.schemas import StrategyCategory, StrategyType, TradeEvent
from backend.strategies.base import Strategy, StrategyContext, register_strategy


@register_strategy(StrategyType.MARKET_FILTER)
class MarketFilterStrategy(Strategy):
    """市场过滤：只跟单匹配的市场。"""

    category = StrategyCategory.FILTER
    param_schema = {
        "mode": {
            "type": "enum",
            "default": "whitelist",
            "label": "过滤模式",
            "description": "whitelist=仅跟单列表中的市场, blacklist=排除列表中的市场",
            "options": ["whitelist", "blacklist"],
        },
        "market_slugs": {
            "type": "list",
            "default": [],
            "label": "市场 slug 列表",
            "description": "白名单或黑名单的市场 slug 列表",
        },
        "tags": {
            "type": "list",
            "default": [],
            "label": "标签列表",
            "description": "仅跟单包含这些标签的市场（白名单模式下生效）",
        },
        "min_volume": {
            "type": "float",
            "default": 0.0,
            "min": 0.0,
            "label": "最小交易量",
            "description": "市场 24h 交易量低于此值则过滤（0 表示不限制）",
        },
    }

    async def apply(self, trade: TradeEvent, ctx: StrategyContext) -> TradeEvent | None:
        mode = self.params.get("mode", "whitelist")
        market_slugs: list[str] = self.params.get("market_slugs", [])
        tags: list[str] = self.params.get("tags", [])
        min_volume = float(self.params.get("min_volume", 0.0))

        slug = trade.market_slug

        # 白名单/黑名单过滤
        if market_slugs:
            in_list = slug in market_slugs
            if mode == "whitelist" and not in_list:
                return None
            if mode == "blacklist" and in_list:
                return None

        # 标签过滤（白名单模式下，如果指定了标签，市场必须包含至少一个匹配标签）
        if tags and mode == "whitelist":
            market_info = ctx.market_cache.get(slug, {})
            market_tags = market_info.get("tags", [])
            if not any(t in market_tags for t in tags):
                return None

        # 最小交易量过滤
        if min_volume > 0:
            market_info = ctx.market_cache.get(slug, {})
            volume = float(market_info.get("volume", 0.0))
            if volume < min_volume:
                return None

        return trade
