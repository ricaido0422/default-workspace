"""钱包交易监测引擎。

后台 asyncio 任务轮询被监控钱包的交易活动，
按 tx_hash + token_id 去重，发现新交易后触发回调。
支持动态 add/remove/start/stop，每个钱包可独立配置轮询间隔。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import Settings
from backend.core.polymarket_client import PolymarketClient
from backend.core.ws_client import PolymarketWSClient
from backend.models.schemas import TradeEvent, WalletWatch
from backend.store.state import StateStore

logger = logging.getLogger(__name__)

# 回调类型：接收新交易事件（附带可选 WS 缓存价格）
TradeCallback = Callable[[TradeEvent], Awaitable[None]]


@dataclass
class WalletState:
    """单个钱包的运行时状态。"""
    address: str
    alias: str = ""
    enabled: bool = True
    interval: int = 2           # 该钱包的轮询间隔（秒），秒级实时默认 2s
    last_cursor: str | None = None       # 分页游标
    last_trade_ts: datetime | None = None  # 最后看到的交易时间
    trade_count: int = 0         # 累计捕获交易数
    seen_keys: set[str] = field(default_factory=set)  # 去重集合 (tx_hash + token_id)
    error_count: int = 0         # 连续错误计数
    last_checked: datetime | None = None
    ws_subscribed_markets: set[str] = field(default_factory=set)  # 已通过 WS 订阅的市场 token_id


class WalletMonitor:
    """钱包交易监控器。

    管理多个监测钱包，后台 asyncio 任务轮询 Data API v2 activity 端点。
    - 按 tx_hash + token_id 去重
    - 新交易 → 调用注册的 on_new_trade 回调
    - 可动态 add_wallet / remove_wallet / start / stop
    - 单个钱包查询失败不影响其他钱包
    """

    # 去重集合上限（防止内存无限增长）
    MAX_SEEN_KEYS = 5000
    # 连续错误上限，超过后暂停该钱包轮询
    MAX_CONSECUTIVE_ERRORS = 10

    def __init__(
        self,
        settings: Settings,
        client: PolymarketClient,
        store: StateStore,
        ws_client: PolymarketWSClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.store = store
        self.ws_client = ws_client  # 可选的 WS 客户端，用于实时价格订阅

        self._wallets: dict[str, WalletState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._callbacks: list[TradeCallback] = []
        self._running = False
        self._main_task: asyncio.Task | None = None
        # 去重锁：保护高频并发下的 seen_keys 集合
        self._dedup_lock = asyncio.Lock()

    # ── 回调管理 ──────────────────────────────────────────

    def on_new_trade(self, callback: TradeCallback) -> None:
        """注册新交易回调。"""
        self._callbacks.append(callback)

    async def _notify_callbacks(self, trade: TradeEvent) -> None:
        """通知所有注册的回调。

        如果 WS 客户端可用且该交易 token_id 有缓存价格，
        将实时价格附加到 trade 的 metadata 中。
        """
        # 附带 WS 缓存价格（如果有）
        ws_price = None
        if self.ws_client and trade.token_id:
            ws_price = self.ws_client.get_cached_price(trade.token_id)

        for cb in self._callbacks:
            try:
                await cb(trade, ws_price=ws_price)
            except TypeError:
                # 兼容不接受 ws_price 参数的旧回调签名
                try:
                    await cb(trade)
                except Exception:
                    logger.exception("交易回调执行失败")
            except Exception:
                logger.exception("交易回调执行失败")

    # ── 钱包管理 ──────────────────────────────────────────

    async def add_wallet(
        self,
        address: str,
        alias: str = "",
        interval: int | None = None,
    ) -> None:
        """动态添加监控钱包。

        如果监控器正在运行，立即为该钱包启动轮询任务。
        """
        if address in self._wallets:
            logger.debug("钱包 %s 已存在，跳过添加", address)
            return

        state = WalletState(
            address=address,
            alias=alias,
            interval=interval or self.settings.monitor.interval,
        )
        self._wallets[address] = state
        logger.info("已添加监控钱包: %s (%s)", alias or address[:10], address)

        if self._running:
            self._start_wallet_task(address)

    async def remove_wallet(self, address: str) -> None:
        """动态移除监控钱包。"""
        # 取消轮询任务
        task = self._tasks.pop(address, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._wallets.pop(address, None)
        logger.info("已移除监控钱包: %s", address)

    def get_watched_wallets(self) -> list[str]:
        """获取当前监控的钱包地址列表。"""
        return list(self._wallets.keys())

    def get_wallet_state(self, address: str) -> WalletState | None:
        """获取钱包运行时状态。"""
        return self._wallets.get(address)

    # ── 订单金额范围过滤 ───────────────────────────────────

    def get_amount_range(self) -> tuple[float, float]:
        """返回当前生效的订单金额监测范围 (min_amount, max_amount)。

        min_amount = 0 表示不设下限，max_amount = 0 表示不设上限。
        配置直接来自 config.yaml / 环境变量（settings.monitor）。
        """
        mon = self.settings.monitor
        return float(getattr(mon, "min_amount", 0.0) or 0.0), float(getattr(mon, "max_amount", 0.0) or 0.0)

    def in_amount_range(self, trade: TradeEvent) -> bool:
        """判断一笔交易的成交金额是否落在监测范围内。

        成交金额 = 价格 × 数量（USDC）。区间外返回 False（该交易被直接丢弃，
        不会触发跟单链路，也不会写入前端交易流）。
        """
        lo, hi = self.get_amount_range()
        if lo <= 0 and hi <= 0:
            return True  # 未设置范围 → 全部放行
        amount = trade.price * trade.size
        if lo > 0 and amount < lo:
            return False
        if hi > 0 and amount > hi:
            return False
        return True

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动监控。

        为每个已注册钱包启动独立的轮询任务。
        """
        if self._running:
            return
        self._running = True

        # 从数据库加载已保存的钱包
        await self._load_wallets_from_store()

        # 为每个钱包启动轮询任务
        for address in self._wallets:
            self._start_wallet_task(address)

        logger.info(
            "钱包监控已启动: %d 个钱包，默认间隔 %ds",
            len(self._wallets), self.settings.monitor.interval,
        )

    async def stop(self) -> None:
        """停止监控，取消所有轮询任务。"""
        self._running = False

        # 取消所有钱包任务
        for address in list(self._tasks.keys()):
            task = self._tasks.pop(address, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("钱包监控已停止")

    async def _load_wallets_from_store(self) -> None:
        """从数据库加载已保存的监控钱包。"""
        try:
            wallets = await self.store.get_wallets()
            for w in wallets:
                if w.address not in self._wallets:
                    self._wallets[w.address] = WalletState(
                        address=w.address,
                        alias=w.alias,
                        enabled=w.enabled,
                        interval=self.settings.monitor.interval,
                        trade_count=w.trade_count,
                        last_checked=w.last_checked,
                    )
        except Exception:
            logger.exception("从数据库加载钱包失败")

    def _start_wallet_task(self, address: str) -> None:
        """为单个钱包启动轮询任务。"""
        if address in self._tasks and not self._tasks[address].done():
            return
        task = asyncio.create_task(self._wallet_loop(address))
        self._tasks[address] = task

    # ── 轮询循环 ──────────────────────────────────────────

    async def _wallet_loop(self, address: str) -> None:
        """单个钱包的轮询循环。

        每个钱包独立运行，互不影响。支持秒级轮询（最小 1s）。
        """
        min_interval = getattr(self.settings.monitor, "min_interval", 1)
        while self._running:
            state = self._wallets.get(address)
            if state is None or not state.enabled:
                await asyncio.sleep(state.interval if state else min_interval)
                continue

            # 连续错误过多，暂停该钱包
            if state.error_count >= self.MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    "钱包 %s 连续错误 %d 次，暂停轮询",
                    address, state.error_count,
                )
                state.enabled = False
                await asyncio.sleep(60)  # 60 秒后重试
                state.enabled = True
                state.error_count = 0
                continue

            try:
                new_trades = await self._check_wallet(address)
                state.error_count = 0
                if new_trades:
                    for trade in new_trades:
                        await self._notify_callbacks(trade)
            except Exception:
                state.error_count += 1
                logger.exception(
                    "检查钱包 %s 失败 (连续错误 %d)",
                    address, state.error_count,
                )

            # 确保间隔不小于最小值
            sleep_interval = max(state.interval, min_interval)
            await asyncio.sleep(sleep_interval)

    async def _check_wallet(self, address: str) -> list[TradeEvent]:
        """检查单个钱包的新交易。

        使用 get_wallet_activity 拉取交易，按 tx_hash + token_id 去重。
        用 asyncio.Lock 保护去重集合，避免高频并发问题。
        发现新交易时，如果 WS 客户端可用，自动将 token_id 加入 WS 订阅。
        返回新发现的 TradeEvent 列表。
        """
        state = self._wallets[address]
        result = await self.client.get_wallet_activity(
            user=address,
            activity_type="TRADE",
            cursor=None,  # 每次从头拉取最新
            limit=50,
        )

        all_trades: list[TradeEvent] = result.get("trades", [])
        new_trades: list[TradeEvent] = []

        # 用锁保护去重集合，避免高频并发下的竞态条件
        async with self._dedup_lock:
            for trade in all_trades:
                # 去重：tx_hash + token_id
                dedup_key = f"{trade.tx_hash}:{trade.token_id}"
                if not trade.tx_hash:
                    # 无 tx_hash 时用 timestamp + token_id 去重
                    dedup_key = f"{trade.timestamp.isoformat()}:{trade.token_id}"

                if dedup_key in state.seen_keys:
                    continue
                if await self.store.has_trade(trade):
                    state.seen_keys.add(dedup_key)
                    continue

                # 订单金额范围过滤：区间外的交易直接忽略（不入库、不跟单）
                # 注意先计入去重集合，避免同一笔区间外交易每轮都被重新判定
                if not self.in_amount_range(trade):
                    state.seen_keys.add(dedup_key)
                    continue

                # 新交易
                new_trades.append(trade)
                state.seen_keys.add(dedup_key)

                # 限制去重集合大小
                if len(state.seen_keys) > self.MAX_SEEN_KEYS:
                    # 保留最近一半
                    state.seen_keys = set(list(state.seen_keys)[-self.MAX_SEEN_KEYS // 2:])

        # 更新状态
        if new_trades:
            state.last_trade_ts = max(t.timestamp for t in new_trades)
            state.trade_count += len(new_trades)

            # 发现交易 → 订阅 WS 市场 → 实时跟踪价格
            if self.ws_client:
                new_token_ids = []
                for trade in new_trades:
                    if trade.token_id and trade.token_id not in state.ws_subscribed_markets:
                        new_token_ids.append(trade.token_id)
                        state.ws_subscribed_markets.add(trade.token_id)
                if new_token_ids:
                    try:
                        await self.ws_client.subscribe(new_token_ids)
                        logger.info(
                            "钱包 %s 新交易触发 WS 订阅 %d 个市场",
                            state.alias or address[:10], len(new_token_ids),
                        )
                    except Exception:
                        logger.debug("WS 订阅市场失败", exc_info=True)

            # 保存到数据库
            for trade in new_trades:
                try:
                    await self.store.save_trade(trade)
                except Exception:
                    logger.exception("保存交易到数据库失败")

            try:
                await self.store.update_wallet_status(
                    address,
                    datetime.now(timezone.utc),
                    state.trade_count,
                )
            except Exception:
                logger.debug("更新钱包状态失败", exc_info=True)

            logger.info(
                "钱包 %s 发现 %d 笔新交易 (累计 %d)",
                state.alias or address[:10], len(new_trades), state.trade_count,
            )

        state.last_checked = datetime.now(timezone.utc)
        return new_trades

    async def ingest_realtime(self, trade: TradeEvent) -> None:
        """公开实时成交进入同一钱包链路，与轮询共享去重锁。"""
        state = self._wallets.get(trade.wallet)
        if state is None or not state.enabled or not self.in_amount_range(trade):
            return
        key = f'{trade.tx_hash or trade.timestamp.isoformat()}:{trade.token_id}'
        async with self._dedup_lock:
            if key in state.seen_keys or await self.store.has_trade(trade):
                return
            await self.store.save_trade(trade)
            state.seen_keys.add(key)
            state.trade_count += 1
            state.last_checked = datetime.now(timezone.utc)
            await self.store.update_wallet_status(trade.wallet, state.last_checked, state.trade_count)
        await self._notify_callbacks(trade)

    def get_monitor_status(self) -> dict[str, Any]:
        """获取所有钱包的监控状态摘要。

        Returns:
            {address: {last_checked, trade_count, error_count, ws_subscribed_markets}}
        """
        status: dict[str, Any] = {}
        for address, state in self._wallets.items():
            status[address] = {
                "alias": state.alias,
                "enabled": state.enabled,
                "interval": state.interval,
                "last_checked": state.last_checked.isoformat() if state.last_checked else None,
                "trade_count": state.trade_count,
                "error_count": state.error_count,
                "ws_subscribed_markets": list(state.ws_subscribed_markets),
            }
        return status
