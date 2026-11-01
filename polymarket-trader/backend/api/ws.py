"""WebSocket 端点。

/ws 连接支持服务端推送和客户端订阅/取消订阅。
包含心跳保活和频道过滤。与 EventBus 集成。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.events import Channels, get_event_bus

logger = logging.getLogger(__name__)

router = APIRouter()

# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 30
# 心跳超时（秒），超过此时间无响应则断开
HEARTBEAT_TIMEOUT = 60


class ConnectionManager:
    """WebSocket 连接管理器。

    管理连接池、频道订阅和心跳。
    与 EventBus 集成：EventBus 发布事件 → 推送给订阅了对应频道的连接。
    """

    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        # 每个连接的订阅频道
        self.subscriptions: dict[WebSocket, set[str]] = {}
        # 每个连接的最后活跃时间
        self._last_active: dict[WebSocket, float] = {}
        self._heartbeat_task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> None:
        """接受新连接并加入连接池。"""
        await ws.accept()
        self.active.append(ws)
        self.subscriptions[ws] = set()
        self._last_active[ws] = time.time()
        logger.info("WebSocket 连接已建立 (共 %d 个)", len(self.active))

        # 启动心跳任务
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def disconnect(self, ws: WebSocket) -> None:
        """移除连接。"""
        if ws in self.active:
            self.active.remove(ws)
        self.subscriptions.pop(ws, None)
        self._last_active.pop(ws, None)
        logger.info("WebSocket 连接已断开 (剩余 %d 个)", len(self.active))

    def subscribe(self, ws: WebSocket, channels: list[str]) -> None:
        """订阅频道。"""
        self.subscriptions.setdefault(ws, set()).update(channels)

    def unsubscribe(self, ws: WebSocket, channels: list[str]) -> None:
        """取消订阅频道。"""
        subs = self.subscriptions.get(ws, set())
        for ch in channels:
            subs.discard(ch)

    def touch(self, ws: WebSocket) -> None:
        """更新连接最后活跃时间。"""
        self._last_active[ws] = time.time()

    # ── 推送 ──────────────────────────────────────────────

    async def push(self, channel: str, data: Any) -> None:
        """向订阅了指定频道的连接推送消息。

        无订阅（空集合）的连接接收所有频道。
        """
        if not self.active:
            return

        message = json.dumps({"type": channel, "data": data}, default=_json_default)
        disconnected: list[WebSocket] = []

        for ws in self.active:
            subs = self.subscriptions.get(ws, set())
            # 空订阅 = 接收全部；否则只接收已订阅频道
            if not subs or channel in subs:
                try:
                    await ws.send_text(message)
                    self._last_active[ws] = time.time()
                except Exception:
                    disconnected.append(ws)

        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast(self, msg_type: str, data: Any) -> None:
        """向所有连接广播消息。"""
        await self.push(msg_type, data)

    # ── 心跳 ──────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """心跳循环：定期发送 PING，清理超时连接。"""
        while self.active:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.time()
            disconnected: list[WebSocket] = []

            for ws in self.active:
                last = self._last_active.get(ws, 0)
                if now - last > HEARTBEAT_TIMEOUT:
                    disconnected.append(ws)
                else:
                    # 发送心跳
                    try:
                        await ws.send_text(json.dumps({"type": "ping", "data": {"ts": int(now)}}))
                    except Exception:
                        disconnected.append(ws)

            for ws in disconnected:
                self.disconnect(ws)

    async def close(self) -> None:
        """关闭所有连接和心跳任务。"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        for ws in self.active:
            try:
                await ws.close()
            except Exception:
                pass
        self.active.clear()
        self.subscriptions.clear()
        self._last_active.clear()


def _json_default(obj: Any) -> Any:
    """JSON 序列化默认处理器。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"无法序列化 {type(obj)}")


# 全局连接管理器
manager = ConnectionManager()


async def _ws_push_handler(data: Any) -> None:
    """EventBus 事件 → WebSocket 推送的桥接处理器。

    注册到 EventBus 的每个频道，将事件推送给订阅了该频道的 WebSocket 连接。
    """
    # 此函数通过 closure 捕获 channel，见下方注册逻辑
    pass


def register_ws_with_eventbus() -> None:
    """将 WebSocket 推送注册到 EventBus 的所有频道。

    EventBus.publish(channel, data) → manager.push(channel, data)
    """
    bus = get_event_bus()

    for channel in Channels.ALL:
        async def handler(data: Any, ch: str = channel) -> None:
            await manager.push(ch, data)

        # 使用 asyncio.create_task 避免在同步上下文中调用 async subscribe
        asyncio.ensure_future(bus.subscribe(channel, handler))

    logger.info("WebSocket 已注册到 EventBus (%d 个频道)", len(Channels.ALL))


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """WebSocket 端点。

    客户端可发送:
      {"type": "subscribe", "channels": ["trade_update", "signal_generated", ...]}
      {"type": "unsubscribe", "channels": [...]}
      {"type": "ping"} → 服务端回 {"type": "pong"}

    服务端推送:
      {"type": "trade_update", "data": TradeEvent}
      {"type": "signal_generated", "data": FollowSignal}
      {"type": "execution_update", "data": ExecutionRecord}
      {"type": "wallet_status", "data": {...}}
      {"type": "stats_update", "data": DashboardStats}
      {"type": "ping", "data": {"ts": ...}}
    """
    await manager.connect(ws)
    try:
        while True:
            text = await ws.receive_text()
            manager.touch(ws)

            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                await ws.send_text(
                    json.dumps({"type": "error", "data": {"message": "无效的 JSON"}})
                )
                continue

            msg_type = msg.get("type", "")

            if msg_type == "subscribe":
                channels = msg.get("channels", [])
                manager.subscribe(ws, channels)
                await ws.send_text(
                    json.dumps({"type": "subscribed", "data": {"channels": channels}})
                )

            elif msg_type == "unsubscribe":
                channels = msg.get("channels", [])
                manager.unsubscribe(ws, channels)
                await ws.send_text(
                    json.dumps({"type": "unsubscribed", "data": {"channels": channels}})
                )

            elif msg_type == "ping":
                await ws.send_text(
                    json.dumps({"type": "pong", "data": {"ts": int(time.time())}})
                )

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        logger.exception("WebSocket 异常")
        manager.disconnect(ws)
