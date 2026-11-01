"""状态持久化存储模块。

使用 JSON 文件保存跟单/监测的快照状态，原子写入。
"""

import json
import os
import time
from typing import Any, Dict, List, Optional, Set


class StateStore:
    """JSON 文件状态存储。

    结构:
    {
      "wallets": {
        <addr>: {
          "positions": {<token_id>: {size, price, value, title, outcome, ts}},
          "seen_trades": [<tx_hash>, ...]
        }
      },
      "markets": {<condition_id>: {last_price, spread, oi, ts}},
      "alerts": {<key>: last_ts}
    }
    """

    def __init__(self, path: str = "state.json"):
        self.path = path

    def load(self) -> dict:
        """加载状态。文件不存在返回空字典。"""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, state: dict) -> None:
        """原子写入状态。

        先写临时文件再 os.replace，避免写一半导致损坏。
        """
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    # ---- 钱包快照 ----

    def get_wallet_snapshot(self, state: dict, wallet: str) -> dict:
        """获取指定钱包的快照。"""
        return state.get("wallets", {}).get(wallet, {})

    def save_wallet_snapshot(
        self,
        state: dict,
        wallet: str,
        positions: Dict[str, dict],
    ) -> None:
        """保存指定钱包的持仓快照。"""
        wallets = state.setdefault("wallets", {})
        w = wallets.setdefault(wallet, {})
        w["positions"] = positions
        w["ts"] = int(time.time())

    def get_positions_snapshot(self, state: dict, wallet: str) -> Dict[str, dict]:
        """获取指定钱包的持仓快照（token_id -> info）。"""
        return state.get("wallets", {}).get(wallet, {}).get("positions", {})

    # ---- 已见交易 ----

    def get_seen_trades(self, state: dict, wallet: str) -> Set[str]:
        """获取已见交易 tx_hash 集合。"""
        seen = state.get("wallets", {}).get(wallet, {}).get("seen_trades", [])
        return set(seen)

    def add_seen_trades(self, state: dict, wallet: str, tx_hashes: List[str]) -> None:
        """添加已见交易 tx_hash。"""
        wallets = state.setdefault("wallets", {})
        w = wallets.setdefault(wallet, {})
        seen = set(w.get("seen_trades", []))
        seen.update(tx_hashes)
        # 只保留最近 500 条，避免无限增长
        w["seen_trades"] = list(seen)[-500:]

    # ---- 市场快照 ----

    def get_market_snapshot(self, state: dict, condition_id: str) -> dict:
        """获取指定市场的上一轮快照。"""
        return state.get("markets", {}).get(condition_id, {})

    def save_market_snapshot(
        self,
        state: dict,
        condition_id: str,
        snapshot: dict,
    ) -> None:
        """保存指定市场的快照。"""
        markets = state.setdefault("markets", {})
        snapshot["ts"] = int(time.time())
        markets[condition_id] = snapshot

    # ---- 告警去重 ----

    def is_alert_due(self, state: dict, key: str, window: int) -> bool:
        """判断告警是否到期（距上次告警超过 window 秒）。"""
        alerts = state.get("alerts", {})
        last_ts = alerts.get(key)
        if last_ts is None:
            return True
        return (time.time() - last_ts) >= window

    def mark_alert(self, state: dict, key: str) -> None:
        """标记告警已触发。"""
        alerts = state.setdefault("alerts", {})
        alerts[key] = time.time()

    # ---- 排行榜缓存 ----

    def get_cached_wallets(self, state: dict) -> List[str]:
        """获取缓存的排行榜钱包列表。"""
        return state.get("cached_leaderboard_wallets", [])

    def set_cached_wallets(self, state: dict, wallets: List[str]) -> None:
        """缓存排行榜钱包列表。"""
        state["cached_leaderboard_wallets"] = wallets
        state["cached_leaderboard_ts"] = int(time.time())

    # ---- 市场元信息缓存 ----

    def get_cached_market(self, state: dict, condition_id: str) -> Optional[dict]:
        """获取缓存的市场元信息。"""
        return state.get("market_cache", {}).get(condition_id)

    def set_cached_market(self, state: dict, condition_id: str, info: dict) -> None:
        """缓存市场元信息。"""
        cache = state.setdefault("market_cache", {})
        cache[condition_id] = info
