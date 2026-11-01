"""跟单核心模块。

对比钱包持仓和交易变化，产生跟单信号。
diff 逻辑抽成无副作用的纯函数，便于单元测试。
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .config import FollowConfig
from .models import CopySignal, Position, Trade
from .store import StateStore


# ---------------------------------------------------------------------------
# 纯 diff 函数（无副作用，便于单测）
# ---------------------------------------------------------------------------

def diff_positions(
    old_snap: Dict[str, dict],
    new_snap: Dict[str, dict],
    eps: float = 1e-6,
) -> List[CopySignal]:
    """对比新旧持仓快照，产生变化信号。

    old_snap / new_snap: {token_id: {size, price, value, title, outcome, condition_id, ...}}
    返回 CopySignal 列表，不含 wallet 字段（由调用方填充）。

    规则:
    - 新增 token 且 size > eps → NEW_POSITION, side=BUY
    - size 增大 → INCREASE, side=BUY, delta=new-old
    - size 减小 → DECREASE, side=SELL, delta=old-new
    - 旧有新无或 size≈0 → CLOSED, side=SELL
    """
    signals: List[CopySignal] = []
    old_keys = set(old_snap.keys())
    new_keys = set(new_snap.keys())

    # 新增或变化
    for token_id in new_keys:
        new_info = new_snap[token_id]
        new_size = float(new_info.get("size", 0))
        old_info = old_snap.get(token_id)
        old_size = float(old_info.get("size", 0)) if old_info else 0.0

        condition_id = new_info.get("condition_id", "")
        title = new_info.get("title", "")
        outcome = new_info.get("outcome", "")
        price = new_info.get("price")
        value_usd = new_info.get("value")
        ts = new_info.get("ts")

        if old_info is None:
            # 新增持仓
            if new_size > eps:
                signals.append(CopySignal(
                    kind="NEW_POSITION",
                    condition_id=condition_id,
                    token_id=token_id,
                    title=title,
                    outcome=outcome,
                    side="BUY",
                    size=new_size,
                    price=_to_float(price),
                    value_usd=_to_float(value_usd),
                    timestamp=ts,
                ))
        else:
            # 已有持仓，比较 size
            delta = new_size - old_size
            if abs(delta) < eps:
                continue  # 无变化
            if delta > 0:
                signals.append(CopySignal(
                    kind="INCREASE",
                    condition_id=condition_id,
                    token_id=token_id,
                    title=title,
                    outcome=outcome,
                    side="BUY",
                    size=delta,
                    price=_to_float(price),
                    value_usd=_to_float(value_usd),
                    timestamp=ts,
                ))
            else:
                signals.append(CopySignal(
                    kind="DECREASE",
                    condition_id=condition_id,
                    token_id=token_id,
                    title=title,
                    outcome=outcome,
                    side="SELL",
                    size=abs(delta),
                    price=_to_float(price),
                    value_usd=_to_float(value_usd),
                    timestamp=ts,
                ))

    # 关闭的持仓
    for token_id in old_keys:
        if token_id not in new_keys:
            old_info = old_snap[token_id]
            old_size = float(old_info.get("size", 0))
            if old_size > eps:
                signals.append(CopySignal(
                    kind="CLOSED",
                    condition_id=old_info.get("condition_id", ""),
                    token_id=token_id,
                    title=old_info.get("title", ""),
                    outcome=old_info.get("outcome", ""),
                    side="SELL",
                    size=old_size,
                    price=_to_float(old_info.get("price")),
                    value_usd=_to_float(old_info.get("value")),
                    timestamp=old_info.get("ts"),
                ))
        else:
            # 新快照中 size≈0 视为关闭
            new_info = new_snap[token_id]
            new_size = float(new_info.get("size", 0))
            old_info = old_snap[token_id]
            old_size = float(old_info.get("size", 0))
            if new_size <= eps and old_size > eps:
                signals.append(CopySignal(
                    kind="CLOSED",
                    condition_id=old_info.get("condition_id", ""),
                    token_id=token_id,
                    title=old_info.get("title", ""),
                    outcome=old_info.get("outcome", ""),
                    side="SELL",
                    size=old_size,
                    price=_to_float(old_info.get("price")),
                    value_usd=_to_float(old_info.get("value")),
                    timestamp=old_info.get("ts"),
                ))

    return signals


def diff_trades(seen_set: Set[str], new_trades: List[Trade]) -> List[CopySignal]:
    """对比已见交易集合，产生新交易信号。

    返回 CopySignal 列表，wallet 字段由调用方填充。
    """
    signals: List[CopySignal] = []
    for trade in new_trades:
        if trade.tx_hash and trade.tx_hash not in seen_set:
            signals.append(CopySignal(
                kind="NEW_TRADE",
                condition_id=trade.condition_id,
                token_id=trade.token_id,
                title=trade.title,
                outcome=trade.outcome,
                side=trade.side,
                size=trade.size,
                price=trade.price,
                value_usd=round((trade.size or 0) * (trade.price or 0), 4),
                timestamp=trade.timestamp,
            ))
    return signals


def _to_float(val) -> Optional[float]:
    """安全转 float。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CopyTracker
# ---------------------------------------------------------------------------

class CopyTracker:
    """跟单追踪器。"""

    def __init__(
        self,
        data_api,
        gamma_api,
        config: FollowConfig,
        store: StateStore,
        alerter,
        state: dict,
    ):
        self.data_api = data_api
        self.gamma_api = gamma_api
        self.config = config
        self.store = store
        self.alerter = alerter
        self.state = state
        self._market_cache: Dict[str, dict] = {}
        self.last_signals: List[CopySignal] = []  # 上一轮产出的信号（供 executor 使用）

    def resolve_wallets(self) -> List[str]:
        """解析跟单钱包列表。

        config.wallets + 若 from_leaderboard.enabled 则取排行榜 top N。
        去重，缓存到 store 避免每轮都拉。
        """
        wallets: List[str] = list(self.config.wallets)
        seen: Set[str] = set(wallets)

        fl = self.config.from_leaderboard
        if fl and fl.get("enabled"):
            # 检查缓存是否有效（1 小时内）
            cached = self.store.get_cached_wallets(self.state)
            cache_ts = self.state.get("cached_leaderboard_ts", 0)
            now = int(time.time())
            if cached and (now - cache_ts) < 3600:
                for w in cached:
                    if w not in seen:
                        seen.add(w)
                        wallets.append(w)
            else:
                try:
                    window = fl.get("window", "all")
                    limit = fl.get("limit", 5)
                    data, _ = self.data_api.leaderboard(window=window, limit=limit)
                    lb_wallets = []
                    for row in data:
                        uid = row.get("user_id", "")
                        if uid and uid not in seen:
                            seen.add(uid)
                            wallets.append(uid)
                            lb_wallets.append(uid)
                    self.store.set_cached_wallets(self.state, lb_wallets)
                except Exception as e:
                    print(f"[跟单] 获取排行榜失败: {e}")

        return wallets

    def _is_market_active(self, condition_id: str) -> bool:
        """检查市场是否活跃且接受订单。

        gamma 查询结果带 TTL 持久化到 state（跨进程复用），内存再缓存。
        市场活跃状态极少翻转，TTL 6 小时；由 run_cycle 末尾的 store.save 落盘。
        """
        if not self.config.only_active_markets:
            return True

        if condition_id in self._market_cache:
            return self._market_cache[condition_id].get("active", False)

        # 持久化缓存（带 TTL）
        ttl = 6 * 3600
        now = int(time.time())
        pcache = self.state.setdefault("market_active_cache", {})
        entry = pcache.get(condition_id)
        if entry and (now - int(entry.get("ts", 0))) < ttl:
            active = bool(entry.get("active", False))
            self._market_cache[condition_id] = {"active": active}
            return active

        try:
            markets = self.gamma_api.markets_by_condition([condition_id])
            if markets:
                m = markets[0]
                active = m.active and not m.closed and m.accepting_orders
            else:
                active = False
        except Exception:
            active = False

        self._market_cache[condition_id] = {"active": active}
        pcache[condition_id] = {"active": active, "ts": now}
        return active

    def _is_market_too_old(self, end_date: Optional[str]) -> bool:
        """检查市场是否超过最大年龄。"""
        if not end_date or self.config.max_market_age_days <= 0:
            return False
        try:
            # 解析 ISO 日期
            dt_str = end_date.replace("Z", "+00:00")
            dt = datetime.fromisoformat(dt_str)
            now = datetime.now(timezone.utc)
            age_days = (now - dt).total_seconds() / 86400
            return age_days > self.config.max_market_age_days
        except Exception:
            return False

    def poll_wallet(self, wallet: str) -> List[CopySignal]:
        """轮询单个钱包，返回信号列表。"""
        signals: List[CopySignal] = []

        # 拉取持仓和交易
        try:
            positions, _ = self.data_api.positions(
                user=wallet, status="OPEN", limit=200
            )
        except Exception as e:
            print(f"[跟单] 获取 {wallet} 持仓失败: {e}")
            positions = []

        try:
            trades, _ = self.data_api.trades(user=wallet, limit=50)
        except Exception as e:
            print(f"[跟单] 获取 {wallet} 交易失败: {e}")
            trades = []

        # 构建新快照
        new_snap: Dict[str, dict] = {}
        for pos in positions:
            # 市场过滤
            if self.config.only_active_markets and not self._is_market_active(pos.condition_id):
                continue
            if self._is_market_too_old(pos.end_date):
                continue
            new_snap[pos.token_id] = {
                "size": pos.size,
                "price": pos.current_price,
                "value": pos.current_value,
                "title": pos.title,
                "outcome": pos.outcome,
                "condition_id": pos.condition_id,
                "ts": int(time.time()),
            }

        # 获取旧快照
        old_snap = self.store.get_positions_snapshot(self.state, wallet)

        # 持仓 diff
        pos_signals = diff_positions(old_snap, new_snap)

        # 交易 diff
        seen_trades = self.store.get_seen_trades(self.state, wallet)
        trade_signals = diff_trades(seen_trades, trades)

        # 过滤信号
        for sig in pos_signals:
            sig.wallet = wallet
            # 小额新持仓/加仓降级为 debug
            if sig.kind in ("NEW_POSITION", "INCREASE"):
                val = sig.value_usd or 0
                if val < self.config.min_position_value_usd:
                    # 降级为 debug，仍保留信号但 severity=debug
                    signals.append(sig)
                    continue
            signals.append(sig)

        for sig in trade_signals:
            sig.wallet = wallet
            signals.append(sig)

        # 保存快照
        self.store.save_wallet_snapshot(self.state, wallet, new_snap)

        # 保存已见交易
        new_tx_hashes = [t.tx_hash for t in trades if t.tx_hash]
        if new_tx_hashes:
            self.store.add_seen_trades(self.state, wallet, new_tx_hashes)

        return signals

    def run_cycle(self) -> int:
        """运行一个跟单轮次，返回产生的信号数。"""
        wallets = self.resolve_wallets()
        total_signals = 0
        self.last_signals = []  # 清空上一轮信号
        for wallet in wallets:
            try:
                signals = self.poll_wallet(wallet)
                for sig in signals:
                    # 构建告警 key
                    ts_min = int(time.time() / 60)
                    key = f"{wallet}:{sig.kind}:{sig.token_id}:{ts_min}"
                    # 确定 severity
                    if sig.kind in ("NEW_POSITION", "INCREASE", "CLOSED"):
                        severity = "info"
                    elif sig.kind == "DECREASE":
                        severity = "info"
                    elif sig.kind == "NEW_TRADE":
                        severity = "info"
                    else:
                        severity = "info"

                    # 小额降级
                    if sig.kind in ("NEW_POSITION", "INCREASE"):
                        val = sig.value_usd or 0
                        if val < self.config.min_position_value_usd:
                            severity = "debug"

                    title = f"[{wallet[:10]}...] {sig.kind}: {sig.title}"
                    _val = f"${sig.value_usd:.2f}" if sig.value_usd is not None else "N/A"
                    body = (
                        f"方向={sig.side} 数量={sig.size:.4f} "
                        f"价格={sig.price} 价值={_val} "
                        f"outcome={sig.outcome}"
                    )
                    self.alerter.alert(key, title, body, severity=severity)
                    if severity != "debug":
                        total_signals += 1
                        self.last_signals.append(sig)
            except Exception as e:
                print(f"[跟单] 钱包 {wallet} 处理失败（已隔离）: {e}")

        return total_signals
