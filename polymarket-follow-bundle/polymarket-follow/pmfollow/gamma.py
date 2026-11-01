"""Gamma API 客户端。

对应 https://gamma-api.polymarket.com，无鉴权。
提供事件、市场、搜索等查询。
"""

from typing import Any, List, Optional

from .httpclient import HttpClient, NotFoundError
from .models import MarketInfo, parse_market


class GammaApi:
    """Gamma API 封装。"""

    def __init__(
        self,
        client: HttpClient,
        base: str = "https://gamma-api.polymarket.com",
    ):
        self.client = client
        self.base = base.rstrip("/")

    def _get(self, path: str, params: dict = None) -> Any:
        """发起 GET 请求并返回原始 JSON。"""
        url = f"{self.base}{path}"
        return self.client.get_json(url, params=params)

    def events(
        self,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> List[dict]:
        """获取事件列表（含 markets）。

        sort 字段 camelCase 无下划线。
        返回原始事件 dict 列表。
        """
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
            "limit": limit,
            "offset": offset,
        }
        resp = self._get("/events", params)
        if isinstance(resp, list):
            return resp
        return []

    def event_by_slug(self, slug: str) -> Optional[dict]:
        """按 slug 获取单个事件。"""
        try:
            resp = self._get("/events", {"slug": slug})
        except NotFoundError:
            return None
        if isinstance(resp, list) and resp:
            return resp[0]
        if isinstance(resp, dict):
            return resp
        return None

    def events_keyset(
        self,
        title_search: str = "",
        limit: int = 20,
        active: bool = True,
        closed: bool = False,
        after_cursor: Optional[str] = None,
    ) -> dict:
        """keyset 分页搜索事件。

        返回 {"events": [...], "next_cursor": ...}。
        """
        params: dict = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        if title_search:
            params["title_search"] = title_search
        if after_cursor:
            params["after_cursor"] = after_cursor
        resp = self._get("/events/keyset", params)
        if isinstance(resp, dict):
            return resp
        return {"events": [], "next_cursor": None}

    def markets_by_condition(self, ids: List[str]) -> List[MarketInfo]:
        """按 condition_id 列表获取市场。

        Gamma /markets 接受 condition_ids 参数（扁平重复键）。
        """
        if not ids:
            return []
        # condition_ids 用逗号分隔
        params = {"condition_ids": ",".join(ids)}
        try:
            resp = self._get("/markets", params)
        except NotFoundError:
            return []
        if not isinstance(resp, list):
            return []
        return [parse_market(m) for m in resp]

    def market_by_slug(self, slug: str) -> Optional[MarketInfo]:
        """按 slug 获取单个市场。"""
        try:
            resp = self._get("/markets", {"slug": slug})
        except NotFoundError:
            return None
        if isinstance(resp, list) and resp:
            return parse_market(resp[0])
        if isinstance(resp, dict):
            return parse_market(resp)
        return None

    def search(self, q: str, limit_per_type: int = 10) -> dict:
        """公开搜索事件/市场/用户。"""
        resp = self._get("/public-search", {"q": q, "limit_per_type": limit_per_type})
        if isinstance(resp, dict):
            return resp
        return {"events": [], "tags": [], "profiles": []}

    def extract_markets(self, events: List[dict]) -> List[MarketInfo]:
        """从事件列表中提取所有市场。

        遍历每个事件的 markets 数组，用 parse_market 解析。
        """
        result: List[MarketInfo] = []
        seen_conditions = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            event_slug = event.get("slug", "")
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue
            for m in markets:
                if not isinstance(m, dict):
                    continue
                market = parse_market(m)
                if not market.event_slug:
                    market.event_slug = event_slug
                if market.condition_id and market.condition_id not in seen_conditions:
                    seen_conditions.add(market.condition_id)
                    result.append(market)
        return result
