"""Data API v2 客户端。

对应 https://data-api.polymarket.com，无鉴权。
统一信封 {"data": [...], "pagination": {...}}。
"""

from typing import Any, List, Optional, Tuple

from .httpclient import HttpClient, NotFoundError
from .models import (
    Activity,
    MarketInfo,
    Position,
    Trade,
    parse_activity,
    parse_position,
    parse_trade,
)


class DataApi:
    """Data API v2 封装。"""

    def __init__(
        self,
        client: HttpClient,
        base: str = "https://data-api.polymarket.com",
    ):
        self.client = client
        self.base = base.rstrip("/")

    def _get(self, path: str, params: dict = None) -> Any:
        """发起 GET 请求并返回原始 JSON。"""
        url = f"{self.base}{path}"
        return self.client.get_json(url, params=params)

    def _extract_data(self, resp: Any) -> Any:
        """从信封中提取 data 字段。"""
        if resp is None:
            return None
        if isinstance(resp, dict):
            return resp.get("data")
        return resp

    def _extract_pagination(self, resp: Any) -> dict:
        """从信封中提取 pagination 字段。"""
        if isinstance(resp, dict):
            return resp.get("pagination") or {}
        return {}

    # ---- 排行榜 ----

    def leaderboard(
        self,
        window: str = "ALL",
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[dict], dict]:
        """获取排行榜。

        返回 (原始 data 列表, pagination 字典)。
        """
        params = {"time_period": window.upper(), "limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = self._get("/v2/leaderboard", params)
        data = self._extract_data(resp) or []
        return data, self._extract_pagination(resp)

    # ---- 持仓 ----

    def positions(
        self,
        user: str,
        status: str = "OPEN",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Position], dict]:
        """获取指定钱包的持仓。

        返回 (Position 列表, pagination 字典)。
        404 时返回 ([], {})。
        """
        params = {"user": user, "status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = self._get("/v2/positions", params)
        except NotFoundError:
            return [], {}
        data = self._extract_data(resp) or []
        positions = [parse_position(d, user=user) for d in data]
        return positions, self._extract_pagination(resp)

    # ---- 交易 ----

    def trades(
        self,
        user: str,
        limit: int = 50,
        cursor: Optional[str] = None,
        condition: Optional[str] = None,
        side: Optional[str] = None,
        taker_only: Optional[bool] = None,
    ) -> Tuple[List[Trade], dict]:
        """获取指定钱包的交易记录。

        返回 (Trade 列表, pagination 字典)。
        """
        params: dict = {"user": user, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if condition:
            params["condition"] = condition
        if side:
            params["side"] = side
        if taker_only is not None:
            params["taker_only"] = str(taker_only).lower()
        try:
            resp = self._get("/v2/trades", params)
        except NotFoundError:
            return [], {}
        data = self._extract_data(resp) or []
        trades = [parse_trade(d, user=user) for d in data]
        return trades, self._extract_pagination(resp)

    # ---- 活动 ----

    def activity(
        self,
        user: str,
        type: str = "TRADE",
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Activity], dict]:
        """获取指定钱包的链上活动。

        type 一次只传一个值。
        返回 (Activity 列表, pagination 字典)。
        """
        params: dict = {"user": user, "type": type, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = self._get("/v2/activity", params)
        except NotFoundError:
            return [], {}
        data = self._extract_data(resp) or []
        activities = [parse_activity(d, user=user) for d in data]
        return activities, self._extract_pagination(resp)

    # ---- 持有者 ----

    def holders(
        self,
        condition: str,
        limit: int = 10,
        include_pnl: bool = True,
    ) -> Any:
        """获取市场持有者。返回原始 data。"""
        params = {"condition": condition, "limit": limit}
        if include_pnl:
            params["include_pnl"] = "true"
        resp = self._get("/v2/holders", params)
        return self._extract_data(resp)

    # ---- 未平仓量 ----

    def oi(
        self,
        condition: Optional[str] = None,
        global_: bool = False,
        slug: Optional[str] = None,
        event: Optional[str] = None,
    ) -> Any:
        """获取未平仓量。返回原始 data。"""
        params: dict = {}
        if condition:
            params["condition"] = condition
        if global_:
            params["global"] = "true"
        if slug:
            params["slug"] = slug
        if event:
            params["event"] = event
        resp = self._get("/v2/oi", params)
        return self._extract_data(resp)

    # ---- 解析状态 ----

    def resolutions(self, condition_id: str) -> Any:
        """获取市场解析状态。返回原始 data。"""
        resp = self._get("/v2/resolutions", {"condition_id": condition_id})
        return self._extract_data(resp)

    # ---- 组合价值 ----

    def value(self, user: str) -> Any:
        """获取钱包组合总价值。返回原始 data。"""
        resp = self._get("/v2/value", {"user": user})
        return self._extract_data(resp)

    # ---- 用户统计 ----

    def user_stats(self, user: str) -> Any:
        """获取用户统计信息。返回原始 data。"""
        resp = self._get("/v2/user-stats", {"user": user})
        return self._extract_data(resp)
