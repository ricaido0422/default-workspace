"""CLOB API 客户端。

对应 https://clob.polymarket.com，读操作无鉴权。
提供订单簿、价格、价差等查询。
"""

from typing import Any, List, Optional

from .httpclient import HttpClient, NotFoundError
from .models import OrderBook, parse_book


class ClobApi:
    """CLOB API 封装。"""

    def __init__(
        self,
        client: HttpClient,
        base: str = "https://clob.polymarket.com",
    ):
        self.client = client
        self.base = base.rstrip("/")

    def _get(self, path: str, params: dict = None) -> Any:
        """发起 GET 请求并返回原始 JSON。"""
        url = f"{self.base}{path}"
        return self.client.get_json(url, params=params)

    def _post(self, path: str, body: Any) -> Any:
        """发起 POST 请求并返回原始 JSON。"""
        url = f"{self.base}{path}"
        return self.client.post_json(url, body)

    def book(self, token_id: str) -> Optional[OrderBook]:
        """获取单个 token 的订单簿。

        空簿返回 404，此时返回 None。
        """
        try:
            resp = self._get("/book", {"token_id": token_id})
        except NotFoundError:
            return None
        if not isinstance(resp, dict):
            return None
        return parse_book(resp, token_id=token_id)

    def books(self, token_ids: List[str]) -> List[OrderBook]:
        """批量获取订单簿（POST /books，≤500）。

        body 为扁平数组 [{"token_id": "..."}]。
        """
        if not token_ids:
            return []
        body = [{"token_id": tid} for tid in token_ids[:500]]
        try:
            resp = self._post("/books", body)
        except NotFoundError:
            return []
        if not isinstance(resp, list):
            return []
        result = []
        for item in resp:
            if isinstance(item, dict):
                result.append(parse_book(item))
        return result

    def midpoint(self, token_id: str) -> Optional[float]:
        """获取中间价。返回 float 或 None。"""
        try:
            resp = self._get("/midpoint", {"token_id": token_id})
        except NotFoundError:
            return None
        if isinstance(resp, dict):
            mid = resp.get("mid")
            if mid is not None:
                try:
                    return float(mid)
                except (TypeError, ValueError):
                    return None
        return None

    def price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """获取最优可成交价格。

        BUY 返回最优 ask，SELL 返回最优 bid。
        """
        try:
            resp = self._get("/price", {"token_id": token_id, "side": side})
        except NotFoundError:
            return None
        if isinstance(resp, dict):
            p = resp.get("price")
            if p is not None:
                try:
                    return float(p)
                except (TypeError, ValueError):
                    return None
        return None

    def last_trade_price(self, token_id: str) -> Optional[float]:
        """获取最后成交价。"""
        try:
            resp = self._get("/last-trade-price", {"token_id": token_id})
        except NotFoundError:
            return None
        if isinstance(resp, dict):
            p = resp.get("price")
            if p is not None:
                try:
                    return float(p)
                except (TypeError, ValueError):
                    return None
        return None

    def spread(self, token_id: str) -> Optional[float]:
        """获取价差。"""
        try:
            resp = self._get("/spread", {"token_id": token_id})
        except NotFoundError:
            return None
        if isinstance(resp, dict):
            s = resp.get("spread")
            if s is not None:
                try:
                    return float(s)
                except (TypeError, ValueError):
                    return None
        return None

    def price_history(
        self,
        token_id: str,
        interval: str = "1d",
        fidelity: Optional[int] = None,
    ) -> List[dict]:
        """获取历史价格序列。

        返回 [{"t": ..., "p": ...}] 列表。
        """
        params: dict = {"market": token_id, "interval": interval}
        if fidelity is not None:
            params["fidelity"] = fidelity
        try:
            resp = self._get("/prices-history", params)
        except NotFoundError:
            return []
        if isinstance(resp, dict):
            return resp.get("history", [])
        return []
