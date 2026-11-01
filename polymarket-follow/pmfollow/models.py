"""数据模型与解析模块。

定义所有 dataclass 和从 API 原始字典构造模型的工厂函数。
字段缺失时用 None/默认值填充，不抛异常。
"""

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """持仓快照。"""

    user: str = ""
    token_id: str = ""
    condition_id: str = ""
    size: float = 0.0
    avg_price: Optional[float] = None
    current_price: Optional[float] = None
    current_value: Optional[float] = None
    total_pnl: Optional[float] = None
    status: str = ""
    outcome: str = ""
    outcome_index: Optional[int] = None
    title: str = ""
    slug: str = ""
    event_slug: str = ""
    end_date: Optional[str] = None
    first_entry_at: Optional[str] = None
    negative_risk: bool = False


@dataclass
class Trade:
    """交易记录。"""

    user: str = ""
    side: str = ""
    token_id: str = ""
    condition_id: str = ""
    size: float = 0.0
    price: Optional[float] = None
    timestamp: Optional[int] = None
    title: str = ""
    slug: str = ""
    outcome: str = ""
    tx_hash: str = ""


@dataclass
class Activity:
    """链上活动记录。"""

    user: str = ""
    timestamp: Optional[int] = None
    condition_id: str = ""
    type: str = ""
    size: float = 0.0
    usdc_size: Optional[float] = None
    price: Optional[float] = None
    token_id: str = ""
    side: str = ""
    tx_hash: str = ""


@dataclass
class MarketInfo:
    """市场元信息。"""

    condition_id: str = ""
    question: str = ""
    slug: str = ""
    end_date: Optional[str] = None
    active: bool = False
    closed: bool = False
    accepting_orders: bool = False
    neg_risk: bool = False
    version: str = ""
    clob_token_ids: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)
    outcome_prices: List[Any] = field(default_factory=list)
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    last_trade_price: Optional[float] = None
    spread: Optional[float] = None
    volume24hr: Optional[float] = None
    event_slug: str = ""


@dataclass
class OrderBook:
    """订单簿快照。"""

    token_id: str = ""
    condition_id: str = ""
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    tick_size: Optional[float] = None
    min_order_size: Optional[float] = None
    neg_risk: bool = False
    last_trade_price: Optional[float] = None


@dataclass
class CopySignal:
    """跟单信号。"""

    wallet: str = ""
    wallet_name: str = ""  # 平台用户名 / 自定义备注名（用于分辨是哪个大户）
    kind: str = ""  # NEW_POSITION / INCREASE / DECREASE / CLOSED / NEW_TRADE
    condition_id: str = ""
    token_id: str = ""
    title: str = ""
    outcome: str = ""
    side: str = ""  # BUY / SELL
    size: float = 0.0
    price: Optional[float] = None
    value_usd: Optional[float] = None
    timestamp: Optional[int] = None

    @property
    def wallet_display(self) -> str:
        """显示名：有用户名用用户名，否则短地址。"""
        if self.wallet_name:
            return self.wallet_name
        w = self.wallet or ""
        if len(w) > 12:
            return f"{w[:6]}…{w[-4:]}"
        return w or "?"


@dataclass
class MarketAlert:
    """市场监测告警。"""

    kind: str = ""  # PRICE_MOVE / SPREAD_WIDE / NO_LIQUIDITY / OI_SURGE / MARKET_RESOLVED
    condition_id: str = ""
    token_id: str = ""
    title: str = ""
    detail: str = ""
    severity: str = "info"  # info / warning / critical
    value: Optional[float] = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> Optional[float]:
    """安全转 float，失败返回 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    """安全转 int，失败返回 None。"""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_json_string(val: Any) -> Any:
    """解析 JSON 编码的字符串字段。

    Gamma API 的 clobTokenIds/outcomes/outcomePrices 是 JSON 字符串。
    若已经是 list 则直接返回，None 返回空 list。
    """
    if val is None:
        return []
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def parse_position(d: dict, user: str = "") -> Position:
    """从 Data API v2 原始字典构造 Position。"""
    return Position(
        user=user or d.get("proxy_wallet", ""),
        token_id=str(d.get("token_id", "")),
        condition_id=str(d.get("condition_id", "")),
        size=_safe_float(d.get("current_size")) or 0.0,
        avg_price=_safe_float(d.get("avg_price")),
        current_price=_safe_float(d.get("current_price")),
        current_value=_safe_float(d.get("current_value")),
        total_pnl=_safe_float(d.get("total_pnl")),
        status=d.get("status", ""),
        outcome=d.get("outcome", ""),
        outcome_index=_safe_int(d.get("outcome_index")),
        title=d.get("title", ""),
        slug=d.get("slug", ""),
        event_slug=d.get("event_slug", ""),
        end_date=d.get("end_date"),
        first_entry_at=d.get("first_entry_at"),
        negative_risk=bool(d.get("negative_risk", False)),
    )


def parse_trade(d: dict, user: str = "") -> Trade:
    """从 Data API v2 原始字典构造 Trade。"""
    return Trade(
        user=user or d.get("proxy_wallet", ""),
        side=d.get("side", ""),
        token_id=str(d.get("token_id", "")),
        condition_id=str(d.get("condition_id", "")),
        size=_safe_float(d.get("size")) or 0.0,
        price=_safe_float(d.get("price")),
        timestamp=_safe_int(d.get("timestamp")),
        title=d.get("title", ""),
        slug=d.get("slug", ""),
        outcome=d.get("outcome", ""),
        tx_hash=d.get("transaction_hash", ""),
    )


def parse_activity(d: dict, user: str = "") -> Activity:
    """从 Data API v2 原始字典构造 Activity。"""
    return Activity(
        user=user or d.get("proxy_wallet", ""),
        timestamp=_safe_int(d.get("timestamp")),
        condition_id=str(d.get("condition_id", "")),
        type=d.get("type", ""),
        size=_safe_float(d.get("size")) or 0.0,
        usdc_size=_safe_float(d.get("usdc_size")),
        price=_safe_float(d.get("price")),
        token_id=str(d.get("token_id", "")),
        side=d.get("side", ""),
        tx_hash=d.get("transaction_hash", ""),
    )


def parse_market(d: dict) -> MarketInfo:
    """从 Gamma 原始市场字典构造 MarketInfo。

    处理 JSON 字符串字段：clobTokenIds / outcomes / outcomePrices。
    """
    return MarketInfo(
        condition_id=str(d.get("conditionId", "")),
        question=d.get("question", ""),
        slug=d.get("slug", ""),
        end_date=d.get("endDate"),
        active=bool(d.get("active", False)),
        closed=bool(d.get("closed", False)),
        accepting_orders=bool(d.get("acceptingOrders", False)),
        neg_risk=bool(d.get("negRisk", False)),
        version=d.get("version", ""),
        clob_token_ids=_parse_json_string(d.get("clobTokenIds")),
        outcomes=_parse_json_string(d.get("outcomes")),
        outcome_prices=_parse_json_string(d.get("outcomePrices")),
        best_bid=_safe_float(d.get("bestBid")),
        best_ask=_safe_float(d.get("bestAsk")),
        last_trade_price=_safe_float(d.get("lastTradePrice")),
        spread=_safe_float(d.get("spread")),
        volume24hr=_safe_float(d.get("volume24hr")),
        event_slug=d.get("event_slug", ""),
    )


def parse_book(d: dict, token_id: str = "") -> OrderBook:
    """从 CLOB /book 响应构造 OrderBook。

    bids/asks 的 price/size 是字符串，转为 float。
    tick_size/min_order_size 也是字符串。
    """
    def _parse_levels(levels: list) -> List[Tuple[float, float]]:
        """解析价格档位数组。"""
        result = []
        if not levels:
            return result
        for lv in levels:
            p = _safe_float(lv.get("price"))
            s = _safe_float(lv.get("size"))
            if p is not None and s is not None:
                result.append((p, s))
        return result

    return OrderBook(
        token_id=token_id or str(d.get("asset_id", "")),
        condition_id=str(d.get("market", "")),
        bids=_parse_levels(d.get("bids")),
        asks=_parse_levels(d.get("asks")),
        tick_size=_safe_float(d.get("tick_size")),
        min_order_size=_safe_float(d.get("min_order_size")),
        neg_risk=bool(d.get("neg_risk", False)),
        last_trade_price=_safe_float(d.get("last_trade_price")),
    )
