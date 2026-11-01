"""可选 WebSocket 实时行情模块。

仅当 websocket-client（包名 websocket）可导入时可用。
import 失败时 MarketWsClient 与 WsMonitor 均设为 None 并打印一次提示。

功能：
- MarketWsClient：CLOB 市场 WS 客户端，支持代理连接、自动重连（指数退避）、可配心跳。
- WsMonitor：把 WS 事件转成告警的包装类，产生 PRICE_MOVE / SPREAD_WIDE /
  NO_LIQUIDITY / MARKET_RESOLVED 等信号。
"""

import json
import threading
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

# 尝试导入 websocket-client
_ws_available = False
try:
    import websocket  # type: ignore
    _ws_available = True
except ImportError:
    print("[WebSocket] 未安装 websocket-client，降级为纯轮询模式。")
    print("[WebSocket] 可通过 pip install websocket-client 启用实时推送。")


def _safe_float(val) -> Optional[float]:
    """安全转 float，失败或 None 返回 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_proxy_url(proxy: Optional[str]):
    """从代理 URL 解析 (host, port)。

    支持 http://host:port、socks5://host:port、host:port 等格式。
    无代理或解析失败时返回 (None, None)。
    """
    if not proxy:
        return None, None
    url = proxy
    # 无 scheme 时 urlparse 会把 host 误判为 scheme，补一个默认 scheme
    if "://" not in url:
        url = "http://" + url
    try:
        parsed = urlparse(url)
        return parsed.hostname, parsed.port
    except Exception:
        return None, None


if _ws_available:

    class MarketWsClient:
        """CLOB 市场 WebSocket 客户端。

        订阅 market 频道，定时发 PING 心跳。
        收到消息后 JSON 解析并回调 on_event(event_type, data)。
        支持代理连接、自动重连（指数退避）、可配心跳间隔。
        """

        WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        # 自动重连参数
        MAX_RECONNECTS = 3          # 最大重连次数
        RECONNECT_BASE_DELAY = 5   # 基础退避秒数，实际延迟 = base * 2^(n-1)

        def __init__(
            self,
            clob_api,
            on_event: Callable[[str, dict], None],
            asset_ids: List[str],
            proxy: Optional[str] = None,
            ping_interval: int = 10,
        ):
            """
            :param clob_api: CLOB API 客户端（保留接口，WS 模块本身不直接使用）
            :param on_event: 事件回调，签名 (event_type: str, data: dict) -> None
            :param asset_ids: 要订阅的 token_id 列表
            :param proxy: 代理 URL，如 http://127.0.0.1:7890；None 表示直连
            :param ping_interval: 心跳间隔秒数，默认 10
            """
            self.clob_api = clob_api
            self.on_event = on_event
            self.asset_ids = asset_ids
            self.proxy = proxy
            self.ping_interval = ping_interval
            # 解析代理 host:port
            self._proxy_host, self._proxy_port = _parse_proxy_url(proxy)
            self._ws = None
            self._thread: Optional[threading.Thread] = None
            self._stop_flag = threading.Event()
            self._reconnect_attempts = 0

        def _on_message(self, ws, message: str) -> None:
            """处理收到的消息。"""
            if message == "PONG":
                return
            try:
                data = json.loads(message)
                # 消息可能是数组
                if isinstance(data, list):
                    for item in data:
                        self._dispatch(item)
                else:
                    self._dispatch(data)
            except (json.JSONDecodeError, ValueError):
                pass

        def _dispatch(self, data: dict) -> None:
            """分发单条消息到回调。"""
            event_type = data.get("event_type", "unknown")
            try:
                self.on_event(event_type, data)
            except Exception as e:
                print(f"[WebSocket] 回调处理失败: {e}")

        def _on_error(self, ws, error) -> None:
            """处理错误。"""
            print(f"[WebSocket] 错误: {error}")

        def _on_close(self, ws, close_status_code, close_msg) -> None:
            """连接关闭回调。

            若非主动停止且未超过重连上限，则指数退避后自动重连。
            """
            print("[WebSocket] 连接已关闭")
            if self._stop_flag.is_set():
                return
            if self._reconnect_attempts >= self.MAX_RECONNECTS:
                print(
                    f"[WebSocket] 已达最大重连次数 {self.MAX_RECONNECTS}，放弃重连"
                )
                return
            self._reconnect_attempts += 1
            delay = self.RECONNECT_BASE_DELAY * (
                2 ** (self._reconnect_attempts - 1)
            )
            print(
                f"[WebSocket] {delay}s 后尝试重连"
                f"（第 {self._reconnect_attempts}/{self.MAX_RECONNECTS} 次）"
            )
            t = threading.Thread(
                target=self._reconnect, args=(delay,), daemon=True
            )
            t.start()

        def _reconnect(self, delay: float) -> None:
            """延迟后重新建立连接（在独立线程中运行）。"""
            time.sleep(delay)
            if self._stop_flag.is_set():
                return
            self._do_connect()

        def _on_open(self, ws) -> None:
            """连接建立后发送订阅消息并启动心跳。"""
            # 重连成功，重置计数器
            self._reconnect_attempts = 0
            sub_msg = {
                "type": "market",
                "assets_ids": self.asset_ids,
                "custom_feature_enabled": True,
            }
            ws.send(json.dumps(sub_msg))
            print(f"[WebSocket] 已订阅 {len(self.asset_ids)} 个资产")

            # 启动心跳线程
            def heartbeat():
                while not self._stop_flag.is_set():
                    time.sleep(self.ping_interval)
                    if not self._stop_flag.is_set():
                        try:
                            ws.send("PING")
                        except Exception:
                            break

            t = threading.Thread(target=heartbeat, daemon=True)
            t.start()

        def _do_connect(self) -> None:
            """实际创建 WebSocketApp 并在后台线程运行。

            代理参数通过 run_forever 的 http_proxy_host / http_proxy_port 传入。
            """
            kwargs = {}
            if self._proxy_host:
                kwargs["http_proxy_host"] = self._proxy_host
                if self._proxy_port:
                    kwargs["http_proxy_port"] = self._proxy_port
            self._ws = websocket.WebSocketApp(
                self.WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._thread = threading.Thread(
                target=self._ws.run_forever,
                daemon=True,
                kwargs=kwargs,
            )
            self._thread.start()

        def connect(self) -> None:
            """建立 WebSocket 连接（非阻塞，在后台线程运行）。"""
            self._stop_flag.clear()
            self._reconnect_attempts = 0
            self._do_connect()

        def stop(self) -> None:
            """停止 WebSocket 连接。"""
            self._stop_flag.set()
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
            print("[WebSocket] 已停止")

    class WsMonitor:
        """WebSocket 行情监测器，把 WS 事件转成告警。

        内部维护 token_id → {last_price, last_spread, title} 字典，
        根据 WS 事件产生 PRICE_MOVE / SPREAD_WIDE / NO_LIQUIDITY /
        MARKET_RESOLVED 告警，通过 alerter.alert() 触发（自带去重）。
        """

        def __init__(
            self,
            config,
            alerter,
            state: dict,
            proxy: Optional[str] = None,
        ):
            """
            :param config: MonitorConfig，提供 price_move_alert_pct /
                           spread_alert_max 等阈值
            :param alerter: Alerter，调用其 alert(key, title, body, severity) 触发告警
            :param state: 共享状态字典
            :param proxy: 代理 URL，传给底层 MarketWsClient
            """
            self.config = config
            self.alerter = alerter
            self.state = state
            self.proxy = proxy
            # token_id → {last_price, last_spread, title}
            self._token_state: Dict[str, dict] = {}
            self._client: Optional[MarketWsClient] = None

        def subscribe(self, asset_ids: List[str]) -> None:
            """启动 WS 连接订阅给定 token_ids。"""
            if not asset_ids:
                print("[WsMonitor] 无 token_id 可订阅，跳过")
                return
            self._client = MarketWsClient(
                clob_api=None,
                on_event=self._on_event,
                asset_ids=asset_ids,
                proxy=self.proxy,
            )
            self._client.connect()
            print(f"[WsMonitor] 已订阅 {len(asset_ids)} 个 token")

        def _on_event(self, event_type: str, data: dict) -> None:
            """处理 WS 事件，产生告警。

            event_type 可能值:
              book / price_change / last_trade_price / tick_size_change /
              best_bid_ask / market_resolved

            - price_change: 价格变动超 config.price_move_alert_pct% → PRICE_MOVE
            - book: best_bid/best_ask 算 spread，超 config.spread_alert_max → SPREAD_WIDE
            - book: 无 bid 或无 ask → NO_LIQUIDITY
            - market_resolved → MARKET_RESOLVED
            - last_trade_price / best_bid_ask: 更新内部状态（不直接告警）
            """
            asset_id = str(data.get("asset_id", ""))
            ts_min = int(time.time() / 60)

            if event_type == "price_change":
                self._handle_price_change(data, asset_id, ts_min)
            elif event_type == "book":
                self._handle_book(data, asset_id, ts_min)
            elif event_type == "last_trade_price":
                self._handle_last_trade_price(data, asset_id)
            elif event_type == "best_bid_ask":
                self._handle_best_bid_ask(data, asset_id)
            elif event_type == "market_resolved":
                self._handle_market_resolved(data, asset_id, ts_min)
            # tick_size_change 等暂不产生告警

        # ------------------------------------------------------------------
        # 各事件处理
        # ------------------------------------------------------------------
        def _get_token_state(self, asset_id: str) -> dict:
            """获取或创建 token 状态记录。"""
            return self._token_state.setdefault(
                asset_id,
                {"last_price": None, "last_spread": None, "title": ""},
            )

        def _handle_price_change(
            self, data: dict, asset_id: str, ts_min: int
        ) -> None:
            """price_change 事件：与上次价格比较，超阈值则告警。

            数据格式: {event_type, asset_id, changes: [{price, side, size}], timestamp}
            """
            changes = data.get("changes", [])
            if not changes:
                return
            # changes 数组取最后一项作为最新价格
            new_price = _safe_float(changes[-1].get("price"))
            if new_price is None:
                return

            token_state = self._get_token_state(asset_id)
            old_price = token_state.get("last_price")
            token_state["last_price"] = new_price

            if old_price is not None and old_price > 0:
                pct_change = abs(new_price - old_price) / old_price * 100
                if pct_change > self.config.price_move_alert_pct:
                    key = f"ws:price_change:{asset_id}:{ts_min}"
                    title = f"[WS] PRICE_MOVE: token {asset_id[:16]}..."
                    body = (
                        f"价格变动 {pct_change:.2f}% 超过阈值 "
                        f"{self.config.price_move_alert_pct}% "
                        f"({old_price:.4f} → {new_price:.4f})"
                    )
                    self.alerter.alert(key, title, body, severity="warning")

        def _handle_book(self, data: dict, asset_id: str, ts_min: int) -> None:
            """book 事件：检查流动性和价差。

            数据格式: {event_type, asset_id, market, bids: [{price, size}],
                       asks: [{price, size}], hash, timestamp}
            """
            bids = data.get("bids", []) or []
            asks = data.get("asks", []) or []
            token_state = self._get_token_state(asset_id)

            # 无 bid 或无 ask → NO_LIQUIDITY
            if not bids or not asks:
                key = f"ws:book:{asset_id}:{ts_min}"
                title = f"[WS] NO_LIQUIDITY: token {asset_id[:16]}..."
                body = "订单簿缺少 bid 或 ask，流动性不足"
                self.alerter.alert(key, title, body, severity="warning")
                return

            best_bid = _safe_float(bids[0].get("price"))
            best_ask = _safe_float(asks[0].get("price"))
            if best_bid is None or best_ask is None:
                return

            spread = best_ask - best_bid
            token_state["last_spread"] = spread

            if spread > self.config.spread_alert_max:
                key = f"ws:book:{asset_id}:{ts_min}"
                title = f"[WS] SPREAD_WIDE: token {asset_id[:16]}..."
                body = (
                    f"价差 {spread:.4f} 超过阈值 "
                    f"{self.config.spread_alert_max}"
                )
                self.alerter.alert(key, title, body, severity="warning")

        def _handle_last_trade_price(self, data: dict, asset_id: str) -> None:
            """last_trade_price 事件：更新最新价格（不产生告警）。

            数据格式: {event_type, asset_id, price, timestamp, side, size, fee_rate_bps}
            """
            price = _safe_float(data.get("price"))
            if price is not None:
                self._get_token_state(asset_id)["last_price"] = price

        def _handle_best_bid_ask(self, data: dict, asset_id: str) -> None:
            """best_bid_ask 事件：更新最新价差（不产生告警）。

            数据格式: {event_type, asset_id, best_bid, best_ask, timestamp}
            """
            best_bid = _safe_float(data.get("best_bid"))
            best_ask = _safe_float(data.get("best_ask"))
            if best_bid is not None and best_ask is not None:
                self._get_token_state(asset_id)["last_spread"] = (
                    best_ask - best_bid
                )

        def _handle_market_resolved(
            self, data: dict, asset_id: str, ts_min: int
        ) -> None:
            """market_resolved 事件：市场已解析，发严重告警。

            数据格式: {event_type, asset_id, market, resolution}
            """
            resolution = data.get("resolution")
            key = f"ws:market_resolved:{asset_id}:{ts_min}"
            title = f"[WS] MARKET_RESOLVED: token {asset_id[:16]}..."
            body = f"市场已解析，resolution={resolution}"
            self.alerter.alert(key, title, body, severity="critical")

        def stop(self) -> None:
            """停止 WS 连接。"""
            if self._client:
                self._client.stop()
                self._client = None
            print("[WsMonitor] 已停止")

else:
    # websocket-client 不可用时，两个类均为 None
    MarketWsClient = None  # type: ignore
    WsMonitor = None  # type: ignore
