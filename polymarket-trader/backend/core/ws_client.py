"""Polymarket WebSocket 客户端。

Market Channel 无需认证，用于实时缓存市场订单簿和价格。
端点: wss://ws-subscriptions-clob.polymarket.com/ws/market

订阅消息: {"type": "market", "assets_ids": ["TOKEN_ID1", ...], "custom_feature_enabled": true}
心跳: 每 10 秒发送 "PING" 字符串，服务端返回 "PONG"
事件类型: "book" | "price_change" | "last_trade_price" | "tick_size_change" | "best_bid_ask"
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from backend.config import Settings

logger = logging.getLogger(__name__)

# Polymarket Market Channel WebSocket 端点
WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 10
# 重连初始延迟（秒）
RECONNECT_BASE_DELAY = 1.0
# 重连最大延迟（秒）
RECONNECT_MAX_DELAY = 60.0


class PolymarketWSClient:
    """Polymarket Market Channel WebSocket 客户端。

    无需认证，订阅市场 token_id 后实时接收订单簿和价格更新。
    - 自动重连（指数退避，最大 60s）
    - PING/PONG 心跳保活（10s 间隔）
    - 内存价格缓存：token_id → {mid, best_bid, best_ask, last_trade_price, timestamp}
    - 代理支持：通过 websockets 的 proxy 参数或环境变量 ALL_PROXY
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # 连接状态
        self._ws: Any = None  # websockets.WebClientProtocol
        self._connected: bool = False
        self._connect_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._receive_task: asyncio.Task | None = None
        self._running: bool = False

        # 订阅管理
        self._subscribed_assets: set[str] = set()
        self._subscribe_lock = asyncio.Lock()

        # 价格缓存：token_id → {mid, best_bid, best_ask, last_trade_price, timestamp}
        self._price_cache: dict[str, dict[str, Any]] = {}
        self._event_handlers = []
        self._initial_subscription_sent = False

        # 连接时间（用于计算 uptime）
        self._connect_time: float = 0.0

        # 代理配置
        proxy = settings.proxy.http or settings.proxy.https or None
        self._proxy: str | None = proxy

    # ── 生命周期 ──────────────────────────────────────────

    async def connect(self) -> None:
        """启动 WebSocket 客户端（后台任务）。

        创建后台任务运行连接循环，包含自动重连。
        """
        if self._running:
            return
        self._running = True
        self._connect_task = asyncio.create_task(self._connect_loop())
        logger.info("Polymarket WS 客户端已启动")

    async def disconnect(self) -> None:
        """断开 WebSocket 连接，停止所有后台任务。"""
        self._running = False

        # 取消后台任务
        for task in (self._heartbeat_task, self._receive_task, self._connect_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._heartbeat_task = None
        self._receive_task = None
        self._connect_task = None

        # 关闭连接
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._connected = False
        logger.info("Polymarket WS 客户端已断开")

    @property
    def connected(self) -> bool:
        """是否已连接。"""
        return self._connected

    @property
    def uptime(self) -> float:
        """已连接时长（秒）。"""
        if not self._connected or self._connect_time == 0:
            return 0.0
        return time.time() - self._connect_time

    # ── 连接循环 ──────────────────────────────────────────

    async def _connect_loop(self) -> None:
        """连接循环：建立连接 → 启动心跳和接收 → 断开后重连。

        指数退避重连，最大延迟 60 秒。
        """
        retry_delay = RECONNECT_BASE_DELAY

        while self._running:
            try:
                # 构建 WebSocket 连接参数
                connect_kwargs: dict[str, Any] = {
                    "ping_interval": None,  # 我们自己管理心跳
                    "ping_timeout": None,
                }
                # 代理支持（websockets>=12.0 支持 proxy 参数）
                if self._proxy:
                    connect_kwargs["proxy"] = self._proxy

                logger.info("正在连接 Polymarket WS: %s", WS_MARKET_URL)
                self._ws = await websockets.connect(WS_MARKET_URL, **connect_kwargs)
                self._connected = True
                self._initial_subscription_sent = False
                self._connect_time = time.time()
                retry_delay = RECONNECT_BASE_DELAY  # 重置退避
                logger.info("Polymarket WS 已连接")

                # 重新订阅之前的 asset_ids
                if self._subscribed_assets:
                    await self._send_subscribe(list(self._subscribed_assets))

                # 启动心跳和接收任务
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                self._receive_task = asyncio.create_task(self._receive_loop())

                # 等待接收任务结束（连接断开时）
                await self._receive_task

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Polymarket WS 连接失败: %s", e)
            finally:
                self._connected = False
                # 清理心跳任务
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except asyncio.CancelledError:
                        pass
                self._heartbeat_task = None
                self._receive_task = None

                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

            # 重连退避
            if not self._running:
                break

            logger.info("Polymarket WS 将在 %.1fs 后重连", retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, RECONNECT_MAX_DELAY)

    # ── 心跳 ──────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """心跳循环：每 10 秒发送 PING 字符串。"""
        while self._running and self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self._ws and self._connected:
                    # Polymarket WS 心跳：发送 "PING" 字符串
                    await self._ws.send("PING")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("WS 心跳发送失败: %s", e)
                break

    # ── 接收循环 ──────────────────────────────────────────

    async def _receive_loop(self) -> None:
        """接收循环：持续接收并处理 WebSocket 消息。"""
        while self._running and self._connected:
            try:
                if self._ws is None:
                    break
                raw = await self._ws.recv()
                await self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except websockets.ConnectionClosed:
                logger.info("Polymarket WS 连接已关闭")
                break
            except Exception as e:
                logger.warning("WS 接收消息失败: %s", e)
                break

    async def _handle_message(self, raw: Any) -> None:
        """处理单条 WebSocket 消息。

        支持 "PONG" 字符串响应和 JSON 事件消息。
        单个消息解析失败不影响整体连接。
        """
        # PONG 心跳响应
        if isinstance(raw, str) and raw.strip().upper() == "PONG":
            return

        # 解析 JSON 消息
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug("WS 消息解析失败: %s (raw=%r)", e, raw[:100] if raw else raw)
            return

        # Polymarket WS 可能返回数组（批量事件）
        if isinstance(data, list):
            for item in data:
                self._process_event(item)
        elif isinstance(data, dict):
            self._process_event(data)

    def _process_event(self, event: dict[str, Any]) -> None:
        """处理单个市场事件，更新价格缓存。

        事件类型: book | price_change | last_trade_price | tick_size_change | best_bid_ask
        """
        try:
            for handler in self._event_handlers:
                handler(event)
            event_type = event.get("event_type", "")
            if event_type == 'price_change' and isinstance(event.get('price_changes'), list):
                for change in event['price_changes']:
                    self._process_event({**change, 'event_type': 'price_change', 'market': event.get('market'), 'timestamp': event.get('timestamp')})
                return
            asset_id = str(event.get("asset_id", event.get("assetId", "")))
            if not asset_id:
                return

            now = time.time()
            cache = self._price_cache.get(asset_id, {})

            if event_type == "book":
                # 完整订单簿快照
                bids = event.get("bids", [])
                asks = event.get("asks", [])
                best_bid = max((float(x['price']) for x in bids if float(x.get('size', 0)) > 0), default=0.)
                best_ask = min((float(x['price']) for x in asks if float(x.get('size', 0)) > 0), default=0.)
                cache['book'] = {'market': event.get('market', ''), 'bids': bids, 'asks': asks,
                    'timestamp': str(int(now*1000)), 'min_order_size': event.get('min_order_size')}
                cache['book_timestamp'] = now
                if best_bid > 0 and best_ask > 0:
                    cache["mid"] = (best_bid + best_ask) / 2
                cache["best_bid"] = best_bid
                cache["best_ask"] = best_ask

            elif event_type == "price_change":
                if cache.get('book') and event.get('side') in ('BUY', 'SELL') and 'size' in event:
                    name = 'bids' if event['side'] == 'BUY' else 'asks'
                    levels = [x for x in cache['book'][name] if float(x['price']) != float(event['price'])]
                    if float(event['size']) > 0:
                        levels.append({'price': str(event['price']), 'size': str(event['size'])})
                    cache['book'][name] = levels
                    cache['book']['timestamp'] = str(int(now*1000))
                    cache['book_timestamp'] = now
                # 价格变动
                price = float(event.get("price", 0))
                if price > 0:
                    cache["mid"] = price
                # 可能附带 best_bid/best_ask
                if "best_bid" in event:
                    cache["best_bid"] = float(event["best_bid"])
                if "best_ask" in event:
                    cache["best_ask"] = float(event["best_ask"])

            elif event_type == "last_trade_price":
                # 最近成交价
                cache["last_trade_price"] = float(event.get("price", 0))

            elif event_type == "best_bid_ask":
                # 最优买卖价
                best_bid = float(event.get("best_bid", 0))
                best_ask = float(event.get("best_ask", 0))
                cache["best_bid"] = best_bid
                cache["best_ask"] = best_ask
                if best_bid > 0 and best_ask > 0:
                    cache["mid"] = (best_bid + best_ask) / 2

            elif event_type == "tick_size_change":
                # tick size 变更，不影响价格缓存
                pass

            cache["timestamp"] = now
            self._price_cache[asset_id] = cache

        except (TypeError, ValueError, KeyError, IndexError) as e:
            logger.debug("WS 事件处理失败: %s (event=%s)", e, event)
        except Exception:
            logger.debug("WS 事件处理异常", exc_info=True)

    # ── 订阅管理 ──────────────────────────────────────────

    async def subscribe(self, asset_ids: list[str]) -> None:
        """订阅市场 token_id 列表。

        如果已连接，立即发送订阅消息；否则缓存待连接后自动订阅。
        """
        if not asset_ids:
            return

        async with self._subscribe_lock:
            new_ids = [aid for aid in asset_ids if aid and aid not in self._subscribed_assets]
            if not new_ids:
                return
            self._subscribed_assets.update(new_ids)

        if self._connected and self._ws:
            await self._send_subscribe(new_ids)
            logger.info("WS 已订阅 %d 个市场 token: %s", len(new_ids), new_ids[:3])
        else:
            logger.debug("WS 未连接，已缓存 %d 个待订阅 token", len(self._subscribed_assets))

    async def _send_subscribe(self, asset_ids: list[str]) -> None:
        """发送订阅消息到 WebSocket。"""
        if not self._ws or not self._connected:
            return
        msg = {
            "assets_ids": asset_ids,
            "custom_feature_enabled": True,
        }
        if self._initial_subscription_sent:
            msg['operation'] = 'subscribe'
        else:
            msg['type'] = 'market'
        try:
            await self._ws.send(json.dumps(msg))
            self._initial_subscription_sent = True
        except Exception as e:
            logger.warning("WS 发送订阅消息失败: %s", e)

    async def unsubscribe(self, asset_ids: list[str]) -> None:
        """取消订阅（从缓存集合移除）。

        Polymarket Market Channel 不支持显式取消订阅，
        移除后不再主动维护该 token 的缓存。
        """
        async with self._subscribe_lock:
            for aid in asset_ids:
                self._subscribed_assets.discard(aid)

    # ── 价格缓存查询 ──────────────────────────────────────

    def get_cached_price(self, token_id: str) -> dict[str, Any] | None:
        """获取缓存的实时价格。

        Returns:
            {mid, best_bid, best_ask, last_trade_price, timestamp} 或 None
        """
        return self._price_cache.get(token_id)

    def on_event(self, handler):
        self._event_handlers.append(handler)

    def get_cached_book(self, token_id, max_age=3):
        cached = self._price_cache.get(token_id, {})
        if cached.get('book') and time.time() - cached.get('book_timestamp', 0) <= max_age:
            return cached['book']
        return None

    def get_all_cached_prices(self) -> dict[str, dict[str, Any]]:
        """获取所有缓存的价格（副本）。"""
        return dict(self._price_cache)

    # ── 状态查询 ──────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """获取 WS 客户端状态。"""
        return {
            "connected": self._connected,
            "subscribed_markets": list(self._subscribed_assets),
            "price_cache_size": len(self._price_cache),
            "uptime": round(self.uptime, 1),
        }
