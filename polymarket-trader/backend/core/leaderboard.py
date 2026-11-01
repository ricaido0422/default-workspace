"""排行榜模块。

从 Polymarket Data API v2 拉取排行榜数据，支持一键导入巨鲸钱包。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.config import Settings
from backend.models.schemas import LeaderboardEntry

logger = logging.getLogger(__name__)


class LeaderboardClient:
    """Polymarket 排行榜客户端。

    通过 Data API v2 /v2/leaderboard 获取排名数据，
    并提供 get_top_whales 供一键导入巨鲸钱包地址。
    """

    DATA_API = "https://data-api.polymarket.com/v2"

    TIMEOUT = 15.0
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 0.5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        proxy = settings.proxy.http or settings.proxy.https or None
        self._client = httpx.AsyncClient(timeout=self.TIMEOUT, proxy=proxy)

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.aclose()

    async def _request_with_retry(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """带重试的 GET 请求。"""
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                last_exc = e
            except Exception as e:
                last_exc = e

            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(self.RETRY_BASE_DELAY * (2 ** attempt))

        logger.warning("排行榜请求失败: %s", last_exc)
        return {}

    async def fetch_leaderboard(
        self, window: str = "week", limit: int = 20
    ) -> list[LeaderboardEntry]:
        """获取排行榜数据。

        Args:
            window: 时间窗口 (day/week/month/all)
            limit: 返回条目数

        Returns:
            排行榜条目列表，按排名排序
        """
        url = "https://data-api.polymarket.com/v1/leaderboard"
        params = {"timePeriod": window.upper(), "orderBy": "PNL", "limit": limit}

        try:
            data = await self._request_with_retry(url, params)
        except Exception:
            logger.exception("获取排行榜失败 (window=%s)", window)
            return []

        items: list[dict[str, Any]] = []
        if isinstance(data, dict):
            items = data.get("data", data.get("leaderboard", [])) or []
        elif isinstance(data, list):
            items = data

        entries: list[LeaderboardEntry] = []
        for i, item in enumerate(items):
            try:
                entries.append(
                    LeaderboardEntry(
                        rank=i + 1,
                        wallet=str(item.get("proxyWallet") or item.get("user") or item.get("wallet") or item.get("address") or ""),
                        username=str(item.get("userName") or item.get("username") or ""),
                        pnl=float(item.get("pnl", item.get("profit", 0))),
                        volume=float(item.get("vol", item.get("volume", item.get("volume24hr", 0)))),
                        markets_traded=int(item.get("markets_traded", item.get("marketsCount", 0))),
                    )
                )
            except Exception:
                logger.debug("解析排行榜条目失败: %s", item, exc_info=True)

        logger.info("排行榜获取成功: %d 条 (window=%s)", len(entries), window)
        return entries

    async def get_top_whales(
        self, n: int = 10, window: str = "month"
    ) -> list[str]:
        """获取顶级巨鲸钱包地址，供一键导入。

        Args:
            n: 返回钱包数量
            window: 时间窗口 (day/week/month/all)

        Returns:
            钱包地址列表（按 PnL 降序）
        """
        entries = await self.fetch_leaderboard(window=window, limit=n * 2)
        whales = [
            e.wallet
            for e in sorted(entries, key=lambda x: x.pnl, reverse=True)
            if e.wallet
        ][:n]
        logger.info("获取 %d 个巨鲸钱包 (window=%s)", len(whales), window)
        return whales

    async def fetch_and_format(
        self, window: str = "week", limit: int = 20
    ) -> list[dict[str, Any]]:
        """获取排行榜并格式化为前端友好的字典列表。"""
        entries = await self.fetch_leaderboard(window, limit)
        return [e.model_dump() for e in entries]
