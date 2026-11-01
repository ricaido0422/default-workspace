"""持仓 diff 和交易 diff 测试模块。

测试 tracker 模块中的纯 diff 函数。
"""

import unittest

from pmfollow.models import CopySignal, Trade
from pmfollow.tracker import diff_positions, diff_trades


class TestDiffPositions(unittest.TestCase):
    """持仓快照 diff 测试。"""

    def _make_snap(self, token_id, size, **kw):
        """构造快照条目。"""
        info = {"size": size, "condition_id": "0xcond", "title": "测试", "outcome": "Yes"}
        info.update(kw)
        return {token_id: info}

    def test_new_position(self):
        """新增持仓 → NEW_POSITION。"""
        old = {}
        new = self._make_snap("token_a", 100.0, price=0.5, value=50.0)
        signals = diff_positions(old, new)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].kind, "NEW_POSITION")
        self.assertEqual(signals[0].side, "BUY")
        self.assertAlmostEqual(signals[0].size, 100.0)

    def test_increase(self):
        """持仓增大 → INCREASE。"""
        old = self._make_snap("token_a", 100.0)
        new = self._make_snap("token_a", 150.0, price=0.5, value=75.0)
        signals = diff_positions(old, new)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].kind, "INCREASE")
        self.assertEqual(signals[0].side, "BUY")
        self.assertAlmostEqual(signals[0].size, 50.0)  # delta = 150 - 100

    def test_decrease(self):
        """持仓减小 → DECREASE。"""
        old = self._make_snap("token_a", 100.0)
        new = self._make_snap("token_a", 60.0, price=0.5, value=30.0)
        signals = diff_positions(old, new)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].kind, "DECREASE")
        self.assertEqual(signals[0].side, "SELL")
        self.assertAlmostEqual(signals[0].size, 40.0)  # delta = 100 - 60

    def test_closed(self):
        """持仓消失 → CLOSED。"""
        old = self._make_snap("token_a", 100.0)
        new = {}
        signals = diff_positions(old, new)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].kind, "CLOSED")
        self.assertEqual(signals[0].side, "SELL")
        self.assertAlmostEqual(signals[0].size, 100.0)

    def test_closed_by_zero_size(self):
        """持仓 size 降为 ~0 → CLOSED。"""
        old = self._make_snap("token_a", 100.0)
        new = self._make_snap("token_a", 0.0)
        signals = diff_positions(old, new)
        closed_signals = [s for s in signals if s.kind == "CLOSED"]
        self.assertEqual(len(closed_signals), 1)
        self.assertEqual(closed_signals[0].side, "SELL")

    def test_no_change(self):
        """持仓无变化 → 无信号。"""
        old = self._make_snap("token_a", 100.0)
        new = self._make_snap("token_a", 100.0)
        signals = diff_positions(old, new)
        self.assertEqual(len(signals), 0)

    def test_multiple_changes(self):
        """多个 token 同时变化。"""
        old = {
            "token_a": {"size": 100, "condition_id": "0xc1", "title": "A", "outcome": "Yes"},
            "token_b": {"size": 50, "condition_id": "0xc2", "title": "B", "outcome": "No"},
        }
        new = {
            "token_a": {"size": 120, "condition_id": "0xc1", "title": "A", "outcome": "Yes"},
            "token_c": {"size": 30, "condition_id": "0xc3", "title": "C", "outcome": "Yes"},
        }
        signals = diff_positions(old, new)
        kinds = sorted(s.kind for s in signals)
        # token_a: INCREASE, token_b: CLOSED, token_c: NEW_POSITION
        self.assertEqual(kinds, ["CLOSED", "INCREASE", "NEW_POSITION"])

    def test_eps_threshold(self):
        """微小变化（< eps）不产生信号。"""
        old = self._make_snap("token_a", 100.0)
        new = self._make_snap("token_a", 100.0000001)  # 差异 < 1e-6
        signals = diff_positions(old, new, eps=1e-6)
        self.assertEqual(len(signals), 0)


class TestDiffTrades(unittest.TestCase):
    """交易 diff 测试。"""

    def test_new_trade(self):
        """未见过的 tx_hash → NEW_TRADE。"""
        seen = {"0xold_tx"}
        new_trades = [
            Trade(tx_hash="0xold_tx", side="BUY", size=10, price=0.5),
            Trade(tx_hash="0xnew_tx", side="SELL", size=20, price=0.6,
                  condition_id="0xcond", token_id="tok", title="测试"),
        ]
        signals = diff_trades(seen, new_trades)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].kind, "NEW_TRADE")
        self.assertEqual(signals[0].side, "SELL")
        self.assertAlmostEqual(signals[0].size, 20.0)
        self.assertEqual(signals[0].tx_hash if hasattr(signals[0], 'tx_hash') else None, None)
        self.assertEqual(signals[0].token_id, "tok")

    def test_all_seen(self):
        """全部已见 → 无信号。"""
        seen = {"0xtx1", "0xtx2"}
        new_trades = [
            Trade(tx_hash="0xtx1", side="BUY", size=10, price=0.5),
            Trade(tx_hash="0xtx2", side="SELL", size=20, price=0.6),
        ]
        signals = diff_trades(seen, new_trades)
        self.assertEqual(len(signals), 0)

    def test_empty_trades(self):
        """空交易列表 → 无信号。"""
        signals = diff_trades(set(), [])
        self.assertEqual(len(signals), 0)


if __name__ == "__main__":
    unittest.main()
