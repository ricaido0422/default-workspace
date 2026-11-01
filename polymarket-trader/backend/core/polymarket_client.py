"""Polymarket API 客户端。

封装 Data API v2、Gamma API 和 CLOB 交互。
所有请求带超时(15s)、重试(3次指数退避)和错误处理。代理从 config 读取。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.config import Settings
from backend.models.schemas import TradeEvent, TradeSide

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Polymarket API 客户端。

    封装三类 API:
    - Data API v2: 钱包活动、持仓、统计
    - Gamma API: 事件/市场搜索与详情
    - CLOB: 订单簿、价格
    """

    GAMMA_API = "https://gamma-api.polymarket.com"
    DATA_API = "https://data-api.polymarket.com/v2"
    CLOB_API = "https://clob.polymarket.com"

    # 请求配置
    TIMEOUT = 15.0
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 0.5  # 秒

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        proxy = settings.proxy.http or settings.proxy.https or None
        self._client = httpx.AsyncClient(
            timeout=self.TIMEOUT,
            proxy=proxy,
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()

    # ── 内部请求工具 ──────────────────────────────────────

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """带重试和指数退避的 HTTP 请求。

        失败时重试最多 MAX_RETRIES 次，每次延迟 RETRY_BASE_DELAY * 2^attempt 秒。
        """
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = await self._client.request(
                    method, url, params=params, json=json_body
                )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                # 4xx 不重试（客户端错误）
                if e.response.status_code < 500:
                    raise
                last_exc = e
                logger.warning(
                    "请求 %s 第 %d 次失败 (HTTP %d)，将重试",
                    url, attempt + 1, e.response.status_code,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                    httpx.PoolTimeout) as e:
                last_exc = e
                logger.warning("请求 %s 第 %d 次失败 (%s)，将重试", url, attempt + 1, type(e).__name__)
            except Exception as e:
                last_exc = e
                logger.warning("请求 %s 第 %d 次失败 (%s)，将重试", url, attempt + 1, e)

            if attempt < self.MAX_RETRIES:
                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _safe_get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """安全 GET 请求：失败时返回空结构而非抛异常。"""
        try:
            resp = await self._request_with_retry("GET", url, params=params)
            return resp.json()
        except Exception as e:
            logger.debug("GET %s 失败: %s", url, e)
            return {}

    # ── Data API v2 ───────────────────────────────────────

    async def get_wallet_activity(
        self,
        user: str,
        activity_type: str = "TRADE",
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """获取钱包活动（交易历史）。

        Args:
            user: 钱包地址
            activity_type: 活动类型 (TRADE/SPLIT/MERGE/REDEEM 等)
            cursor: 分页游标
            limit: 每页数量

        Returns:
            {"trades": list[TradeEvent], "next_cursor": str | None}
        """
        url = f"{self.DATA_API}/activity"
        params: dict[str, Any] = {
            "user": user,
            "type": activity_type,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = await self._request_with_retry("GET", url, params=params)
            data = resp.json()
        except Exception as e:
            logger.warning("获取钱包 %s 活动失败: %s", user, e)
            return {"trades": [], "next_cursor": None}

        raw_trades = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(raw_trades, list):
            raw_trades = []

        trades = [self._parse_trade(raw, user) for raw in raw_trades]
        trades = [t for t in trades if t is not None]

        next_cursor = None
        if isinstance(data, dict):
            next_cursor = data.get("next_cursor") or data.get("cursor")

        return {"trades": trades, "next_cursor": next_cursor}

    async def get_wallet_positions(
        self,
        user: str,
        status: str = "OPEN",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """获取钱包持仓。

        Args:
            user: 钱包地址
            status: 持仓状态 (OPEN/REDEEMABLE/CLOSED)
            limit: 返回数量上限

        Returns:
            持仓列表（原始字典）
        """
        url = f"{self.DATA_API}/positions"
        params = {"user": user, "limit": limit}
        if status:
            params["status"] = status

        data = await self._safe_get(url, params)
        result = data.get("data", data) if isinstance(data, dict) else data
        return result if isinstance(result, list) else []

    async def get_user_stats(self, user: str) -> dict[str, Any]:
        """获取用户统计信息。

        Returns 包含 PnL、交易量、市场数等。
        """
        url = f"{self.DATA_API}/stats"
        params = {"user": user}
        data = await self._safe_get(url, params)
        return data

    # ── Gamma API ─────────────────────────────────────────

    async def get_events(
        self,
        limit: int = 20,
        active: bool = True,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        """获取事件列表。

        Args:
            limit: 返回数量
            active: 仅活跃事件
            order: 排序字段 (volume24hr/volume/liquidity 等)
            ascending: 是否升序

        Returns:
            事件列表
        """
        url = f"{self.GAMMA_API}/events"
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        data = await self._safe_get(url, params)
        return data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []

    async def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """按 slug 获取市场详情。

        返回包含 token_id, condition_id, outcomes 等字段。
        """
        url = f"{self.GAMMA_API}/markets"
        params = {"slug": slug}
        data = await self._safe_get(url, params)
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}

    async def get_market_by_id(self, market_id: str) -> dict[str, Any]:
        """按 id 获取市场详情。"""
        url = f"{self.GAMMA_API}/markets/{market_id}"
        data = await self._safe_get(url)
        return data if isinstance(data, dict) else {}

    async def search_events(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """搜索事件。

        Args:
            query: 搜索关键词
            limit: 返回数量

        Returns:
            匹配的事件列表
        """
        url = f"{self.GAMMA_API}/events"
        params = {"title_search": query, "limit": limit, "active": "true"}
        data = await self._safe_get(url, params)
        return data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []

    async def get_market_by_condition_id(self, condition_id: str) -> dict[str, Any]:
        """按 condition_id 获取市场详情。"""
        url = f"{self.GAMMA_API}/markets"
        params = {"condition_ids": condition_id}
        data = await self._safe_get(url, params)
        if isinstance(data, list) and data:
            return data[0]
        return {}

    # ── CLOB ──────────────────────────────────────────────

    async def get_market_resolution(self, condition_id: str) -> dict[str, Any] | None:
        """只接受平台明确最终结果；closed 或接近 0/1 的行情本身不是结算证明。"""
        response = await self._request_with_retry('GET', f'{self.CLOB_API}/markets/{condition_id}')
        data = response.json()
        if str(data.get('condition_id', '')).lower() != condition_id.lower() or data.get('closed') is not True:
            return None
        tokens = data.get('tokens') or []
        winners = [t for t in tokens if t.get('winner') is True]
        if len(winners) == 1 and all(isinstance(t.get('winner'), bool) for t in tokens) and len(tokens) >= 2:
            return {'condition_id': condition_id, 'final': True, 'source': 'polymarket_clob_final_winner',
                'payouts': {str(t['token_id']): 1.0 if t['winner'] else 0.0 for t in tokens},
                'evidence': {'closed': True, 'tokens': tokens}, 'observed_at': datetime.now(timezone.utc).isoformat()}
        # 无效/平局市场可能按 0.5 等比例结算，必须另有已完成的预言机状态。
        market = await self.get_market_by_condition_id(condition_id)
        if market.get('closed') is True and market.get('umaResolutionStatus') == 'resolved':
            import json
            ids, prices = market.get('clobTokenIds', []), market.get('outcomePrices', [])
            ids = json.loads(ids) if isinstance(ids, str) else ids
            prices = json.loads(prices) if isinstance(prices, str) else prices
            values = [float(p) for p in prices]
            if len(ids) >= 2 and len(ids) == len(values) and all(0 <= p <= 1 for p in values) and abs(sum(values)-1) < 1e-8:
                return {'condition_id': condition_id, 'final': True, 'source': 'polymarket_gamma_resolved',
                    'payouts': dict(zip(map(str, ids), values)), 'evidence': market,
                    'observed_at': datetime.now(timezone.utc).isoformat()}
        return None

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """获取订单簿。

        Returns:
            {"bids": [...], "asks": [...], "tick_size": float, ...}
        """
        url = f"{self.CLOB_API}/book"
        params = {"token_id": token_id}
        data = await self._safe_get(url, params)
        return data

    async def get_midpoint(self, token_id: str) -> float | None:
        """获取中间价。

        Returns:
            中间价 (float)，获取失败返回 None
        """
        url = f"{self.CLOB_API}/midpoint"
        params = {"token_id": token_id}
        data = await self._safe_get(url, params)
        mid = data.get("mid") if isinstance(data, dict) else None
        if mid is not None:
            try:
                return float(mid)
            except (TypeError, ValueError):
                return None
        return None

    async def get_price(self, token_id: str) -> dict[str, Any]:
        """获取最优价格 (CLOB API)。"""
        url = f"{self.CLOB_API}/price"
        params = {"token_id": token_id}
        return await self._safe_get(url, params)

    async def get_best_price(self, token_id: str, side: str = "BUY") -> float | None:
        """获取最优可成交价格。

        BUY → best ask, SELL → best bid。
        """
        url = f"{self.CLOB_API}/price"
        params = {"token_id": token_id, "side": side}
        data = await self._safe_get(url, params)
        price = data.get("price") if isinstance(data, dict) else None
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                return None
        return None

    # ── 交易解析 ──────────────────────────────────────────

    @staticmethod
    def _parse_trade(raw: dict[str, Any], wallet: str) -> TradeEvent | None:
        """将 API 原始交易数据解析为 TradeEvent。

        处理 Data API v2 activity 端点返回的字段名变体。
        """
        try:
            # 字段名兼容多种 API 返回格式
            side_raw = str(raw.get("side", raw.get("type", ""))).upper()
            if side_raw not in ('BUY', 'SELL'):
                return None
            side = TradeSide(side_raw)

            price = float(raw.get("price", raw.get("trade_price", 0)))
            size = float(raw.get("size", raw.get("amount", raw.get("shares", 0))))

            # 跳过无效交易
            if price <= 0 or size <= 0:
                return None

            # 时间戳解析
            ts_raw = raw.get("timestamp", raw.get("time", raw.get("date", "")))
            timestamp = PolymarketClient._parse_timestamp(ts_raw)

            return TradeEvent(
                wallet=wallet,
                condition_id=str(raw.get("condition_id", raw.get("conditionId", ""))),
                token_id=str(raw.get("asset_id", raw.get("asset", raw.get("tokenId", raw.get("token_id", ""))))),
                market_slug=str(raw.get("market_slug", raw.get("slug", ""))),
                market_title=str(raw.get("market_title", raw.get("title", ""))),
                side=side,
                price=min(max(price, 0.0), 1.0),
                size=size,
                timestamp=timestamp,
                tx_hash=str(raw.get("transaction_hash", raw.get("transactionHash", raw.get("tx_hash", raw.get("hash", ""))))),
                asset_id=str(raw.get("asset_id", raw.get("assetId", ""))),
            )
        except Exception:
            logger.debug("解析交易数据失败: %s", raw, exc_info=True)
            return None

    @staticmethod
    def _parse_timestamp(ts_raw: Any) -> datetime:
        """解析时间戳，支持 ISO 字符串和 Unix 秒/毫秒。"""
        if isinstance(ts_raw, (int, float)):
            # 毫秒 vs 秒
            if ts_raw > 1e12:
                return datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
            return datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        if isinstance(ts_raw, str) and ts_raw:
            try:
                if ts_raw.replace('.', '', 1).isdigit():
                    return PolymarketClient._parse_timestamp(float(ts_raw))
                result = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        raise ValueError('缺少有效交易时间，不能把旧成交当成新信号')
