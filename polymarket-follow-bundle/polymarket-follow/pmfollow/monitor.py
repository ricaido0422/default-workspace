"""平台市场监测模块。

监测市场价格变动、价差、流动性、未平仓量和解析状态。
"""

import time
from typing import Dict, List, Optional

from .config import MonitorConfig
from .models import MarketAlert, MarketInfo
from .store import StateStore


class MarketMonitor:
    """市场监测器。"""

    def __init__(
        self,
        clob_api,
        gamma_api,
        data_api,
        config: MonitorConfig,
        store: StateStore,
        alerter,
        state: dict,
    ):
        self.clob_api = clob_api
        self.gamma_api = gamma_api
        self.data_api = data_api
        self.config = config
        self.store = store
        self.alerter = alerter
        self.state = state
        self._market_cache: Dict[str, MarketInfo] = {}

    def resolve_markets(self) -> List[MarketInfo]:
        """解析监测市场列表。

        config.markets（condition_id 或 slug）+ 若 from_top_volume.enabled 则取热门市场。
        去重，缓存。
        """
        markets: List[MarketInfo] = []
        seen_conditions: set = set()

        # 手动指定
        for m in self.config.markets:
            try:
                if isinstance(m, str):
                    if m.startswith("0x"):
                        # condition_id
                        results = self.gamma_api.markets_by_condition([m])
                        for mi in results:
                            if mi.condition_id not in seen_conditions:
                                seen_conditions.add(mi.condition_id)
                                markets.append(mi)
                    else:
                        # slug
                        mi = self.gamma_api.market_by_slug(m)
                        if mi and mi.condition_id not in seen_conditions:
                            seen_conditions.add(mi.condition_id)
                            markets.append(mi)
            except Exception as e:
                print(f"[监测] 解析市场 {m} 失败: {e}")

        # 从热门市场：逐事件最多 max_per_event 个，按成交量降序，总量截到 limit
        ftv = self.config.from_top_volume
        if ftv and ftv.get("enabled"):
            try:
                limit = ftv.get("limit", 10)
                max_per_event = ftv.get("max_per_event", 3)
                events = self.gamma_api.events(
                    active=True, closed=False,
                    order="volume24hr", ascending=False,
                    limit=limit,
                )
                candidates: List[MarketInfo] = []
                for ev in events:
                    ev_markets = self.gamma_api.extract_markets([ev])
                    ev_markets.sort(
                        key=lambda m: float(m.volume24hr or 0), reverse=True
                    )
                    for mi in ev_markets[:max_per_event]:
                        if mi.condition_id and mi.condition_id not in seen_conditions:
                            candidates.append(mi)
                candidates.sort(
                    key=lambda m: float(m.volume24hr or 0), reverse=True
                )
                for mi in candidates[:limit]:
                    seen_conditions.add(mi.condition_id)
                    markets.append(mi)
            except Exception as e:
                print(f"[监测] 获取热门市场失败: {e}")

        return markets

    def poll_market(self, market: MarketInfo) -> List[MarketAlert]:
        """轮询单个市场，返回告警列表。"""
        alerts: List[MarketAlert] = []
        condition_id = market.condition_id

        # 获取上一轮快照
        old_snap = self.store.get_market_snapshot(self.state, condition_id)

        # 对每个 clob_token_id 取 book/midpoint/last_trade_price
        last_price: Optional[float] = None
        spread: Optional[float] = None
        has_liquidity = True

        for token_id in market.clob_token_ids:
            try:
                book = self.clob_api.book(token_id)
                if book is None or (not book.bids and not book.asks):
                    has_liquidity = False
                    alerts.append(MarketAlert(
                        kind="NO_LIQUIDITY",
                        condition_id=condition_id,
                        token_id=token_id,
                        title=market.question,
                        detail=f"token {token_id[:16]}... 无流动性（订单簿为空或 404）",
                        severity="warning",
                    ))
                else:
                    # 价差检查
                    if book.asks and book.bids:
                        best_ask = book.asks[0][0]
                        best_bid = book.bids[0][0]
                        spread = best_ask - best_bid
                        if spread > self.config.spread_alert_max:
                            alerts.append(MarketAlert(
                                kind="SPREAD_WIDE",
                                condition_id=condition_id,
                                token_id=token_id,
                                title=market.question,
                                detail=f"价差 {spread:.4f} 超过阈值 {self.config.spread_alert_max}",
                                severity="warning",
                                value=spread,
                            ))

                # 最后成交价
                ltp = self.clob_api.last_trade_price(token_id)
                if ltp is not None:
                    last_price = ltp
            except Exception as e:
                print(f"[监测] 获取 {token_id[:16]}... 订单簿失败: {e}")

        # 价格变动检查
        if last_price is not None:
            old_price = old_snap.get("last_price")
            if old_price is not None and old_price > 0:
                pct_change = abs(last_price - old_price) / old_price * 100
                if pct_change > self.config.price_move_alert_pct:
                    alerts.append(MarketAlert(
                        kind="PRICE_MOVE",
                        condition_id=condition_id,
                        token_id="",
                        title=market.question,
                        detail=(
                            f"价格变动 {pct_change:.2f}% 超过阈值 "
                            f"{self.config.price_move_alert_pct}% "
                            f"({old_price:.4f} → {last_price:.4f})"
                        ),
                        severity="warning",
                        value=pct_change,
                    ))

        # 未平仓量检查
        oi_value: Optional[float] = None
        try:
            oi_data = self.data_api.oi(condition=condition_id)
            if isinstance(oi_data, list) and oi_data:
                oi_value = oi_data[0].get("value")
                old_oi = old_snap.get("oi")
                if old_oi is not None and old_oi > 0 and oi_value is not None:
                    oi_change = abs(oi_value - old_oi) / old_oi * 100
                    if oi_change > 20:
                        alerts.append(MarketAlert(
                            kind="OI_SURGE",
                            condition_id=condition_id,
                            token_id="",
                            title=market.question,
                            detail=(
                                f"未平仓量变动 {oi_change:.1f}% "
                                f"({old_oi:.2f} → {oi_value:.2f})"
                            ),
                            severity="info",
                            value=oi_change,
                        ))
        except Exception as e:
            print(f"[监测] 获取 {condition_id[:16]}... OI 失败: {e}")

        # 解析状态检查
        try:
            resolutions = self.data_api.resolutions(condition_id)
            if isinstance(resolutions, list):
                for r in resolutions:
                    if r.get("status") == "resolved":
                        alerts.append(MarketAlert(
                            kind="MARKET_RESOLVED",
                            condition_id=condition_id,
                            token_id="",
                            title=market.question,
                            detail=f"市场已解析，payout={r.get('price')}",
                            severity="critical",
                        ))
        except Exception as e:
            print(f"[监测] 获取 {condition_id[:16]}... 解析状态失败: {e}")

        # 保存快照
        self.store.save_market_snapshot(self.state, condition_id, {
            "last_price": last_price,
            "spread": spread,
            "oi": oi_value,
            "has_liquidity": has_liquidity,
        })

        return alerts

    def run_cycle(self) -> int:
        """运行一个监测轮次，返回告警数。"""
        markets = self.resolve_markets()
        total_alerts = 0
        for market in markets:
            try:
                alerts = self.poll_market(market)
                for alert in alerts:
                    ts_min = int(time.time() / 60)
                    key = f"{alert.kind}:{alert.condition_id}:{alert.token_id}:{ts_min}"
                    title = f"[监测] {alert.kind}: {alert.title}"
                    self.alerter.alert(key, title, alert.detail, severity=alert.severity)
                    total_alerts += 1
            except Exception as e:
                print(f"[监测] 市场 {market.condition_id[:16]}... 处理失败（已隔离）: {e}")

        return total_alerts
