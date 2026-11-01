"""事件总线。

异步 pub/sub 模式：monitor/pipeline/executor 发布事件，
WebSocket 和其他订阅者接收推送。不阻塞主数据流。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable
from typing import Any

logger = logging.getLogger(__name__)

# 事件处理器类型
EventHandler = Callable[[Any], Awaitable[None]]


class EventBus:
    """异步事件总线。

    支持:
    - 按频道订阅/发布
    - 多个订阅者 per 频道
    - 异步非阻塞发布（fire-and-forget）
    - 通配符频道 "*"（接收所有事件）
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, handler: EventHandler) -> None:
        """订阅频道。"""
        async with self._lock:
            self._subscribers.setdefault(channel, []).append(handler)
        logger.debug("订阅频道 %s (共 %d 个订阅者)", channel, len(self._subscribers.get(channel, [])))

    async def unsubscribe(self, channel: str, handler: EventHandler) -> None:
        """取消订阅。"""
        async with self._lock:
            subs = self._subscribers.get(channel, [])
            if handler in subs:
                subs.remove(handler)
        logger.debug("取消订阅频道 %s", channel)

    async def publish(self, channel: str, data: Any) -> None:
        """发布事件到频道。

        同时通知通配符 "*" 订阅者。
        异步执行所有处理器，不阻塞发布者。
        单个处理器异常不影响其他订阅者。
        """
        handlers: list[EventHandler] = []
        async with self._lock:
            handlers = list(self._subscribers.get(channel, []))
            handlers.extend(self._subscribers.get("*", []))

        if not handlers:
            return

        # 并发执行所有处理器，异常隔离
        tasks = [asyncio.create_task(self._safe_call(h, channel, data)) for h in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def publish_nowait(self, channel: str, data: Any) -> None:
        """非阻塞发布（fire-and-forget）。

        创建后台任务执行发布，不等待完成。
        """
        asyncio.create_task(self.publish(channel, data))

    @staticmethod
    async def _safe_call(handler: EventHandler, channel: str, data: Any) -> None:
        """安全调用处理器，捕获异常。"""
        try:
            await handler(data)
        except Exception:
            logger.exception("事件处理器异常 (channel=%s)", channel)

    def get_channels(self) -> list[str]:
        """获取所有有订阅者的频道。"""
        return list(self._subscribers.keys())

    def subscriber_count(self, channel: str) -> int:
        """获取频道订阅者数量。"""
        return len(self._subscribers.get(channel, []))


# ── 事件频道名称 ────────────────────────────────────────────

class Channels:
    """事件频道常量。"""
    TRADE_UPDATE = "trade_update"
    SIGNAL_GENERATED = "signal_generated"
    EXECUTION_UPDATE = "execution_update"
    WALLET_STATUS = "wallet_status"
    STATS_UPDATE = "stats_update"
    WS_STATUS = "ws_status"  # WebSocket 客户端连接状态

    ALL = [
        TRADE_UPDATE,
        SIGNAL_GENERATED,
        EXECUTION_UPDATE,
        WALLET_STATUS,
        STATS_UPDATE,
        WS_STATUS,
        "platform_trade", "sync_status", "settlement_update",
    ]


# 全局事件总线单例
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取全局事件总线。"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """设置全局事件总线。"""
    global _event_bus
    _event_bus = bus
