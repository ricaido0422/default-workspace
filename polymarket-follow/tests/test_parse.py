"""解析测试模块。

测试各 parse_* 工厂函数对 fixtures 和合成数据的正确解析。
"""

import json
import os
import unittest

from pmfollow.models import (
    parse_activity,
    parse_book,
    parse_market,
    parse_position,
    parse_trade,
)

# fixtures 目录
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load_fixture(name: str):
    """加载 fixture JSON 文件，不存在返回 None。"""
    path = os.path.join(FIXTURES_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestParseLeaderboard(unittest.TestCase):
    """排行榜解析测试。"""

    def test_leaderboard_user_id(self):
        """断言 leaderboard data[0].user_id 存在且为 0x 开头。"""
        data = _load_fixture("leaderboard.json")
        self.assertIsNotNone(data, "leaderboard.json 不存在")
        rows = data.get("data", [])
        self.assertGreater(len(rows), 0, "leaderboard data 为空")
        first = rows[0]
        self.assertIn("user_id", first, "缺少 user_id 字段")
        uid = first["user_id"]
        self.assertTrue(uid.startswith("0x"), f"user_id 不是 0x 开头: {uid}")


class TestParsePositions(unittest.TestCase):
    """持仓解析测试。

    positions.json 的 data 为空，所以用合成 dict 测试 parse_position。
    """

    def test_synthetic_position(self):
        """用合成 dict 测试 parse_position 字段映射。"""
        raw = {
            "proxy_wallet": "0xabc123",
            "token_id": "123456789",
            "condition_id": "0xcond",
            "current_size": 100.5,
            "avg_price": 0.45,
            "current_price": 0.55,
            "current_value": 55.275,
            "total_pnl": 10.0,
            "status": "OPEN",
            "outcome": "Yes",
            "outcome_index": 0,
            "title": "测试市场",
            "slug": "test-market",
            "event_slug": "test-event",
            "end_date": "2026-12-31T23:59:00Z",
            "first_entry_at": "2026-01-01T00:00:00Z",
            "negative_risk": False,
        }
        pos = parse_position(raw, user="0xabc123")
        self.assertEqual(pos.user, "0xabc123")
        self.assertEqual(pos.token_id, "123456789")
        self.assertEqual(pos.condition_id, "0xcond")
        self.assertAlmostEqual(pos.size, 100.5)
        self.assertAlmostEqual(pos.avg_price, 0.45)
        self.assertAlmostEqual(pos.current_price, 0.55)
        self.assertAlmostEqual(pos.current_value, 55.275)
        self.assertAlmostEqual(pos.total_pnl, 10.0)
        self.assertEqual(pos.status, "OPEN")
        self.assertEqual(pos.outcome, "Yes")
        self.assertEqual(pos.outcome_index, 0)
        self.assertEqual(pos.title, "测试市场")
        self.assertEqual(pos.slug, "test-market")
        self.assertEqual(pos.event_slug, "test-event")
        self.assertEqual(pos.end_date, "2026-12-31T23:59:00Z")
        self.assertFalse(pos.negative_risk)

    def test_position_missing_fields(self):
        """测试字段缺失时用默认值。"""
        pos = parse_position({})
        self.assertEqual(pos.user, "")
        self.assertEqual(pos.token_id, "")
        self.assertEqual(pos.size, 0.0)
        self.assertIsNone(pos.avg_price)
        self.assertIsNone(pos.current_price)

    def test_real_positions_fixture(self):
        """读真实 positions.json（data 可能为空，跳过解析）。"""
        data = _load_fixture("positions.json")
        if data is None:
            self.skipTest("positions.json 不存在")
        rows = data.get("data", [])
        if not rows:
            self.skipTest("positions.json data 为空，跳过")
        pos = parse_position(rows[0])
        self.assertIsNotNone(pos.token_id)


class TestParseTrades(unittest.TestCase):
    """交易解析测试。"""

    def test_parse_trade_fields(self):
        """读 trades.json 断言 parse_trade 得到 side/size/price。"""
        data = _load_fixture("trades.json")
        self.assertIsNotNone(data, "trades.json 不存在")
        rows = data.get("data", [])
        self.assertGreater(len(rows), 0, "trades data 为空")
        trade = parse_trade(rows[0])
        self.assertIn(trade.side, ["BUY", "SELL"], f"side 异常: {trade.side}")
        self.assertGreater(trade.size, 0, "size 应大于 0")
        self.assertIsNotNone(trade.price, "price 不应为 None")
        self.assertTrue(trade.tx_hash.startswith("0x"), "tx_hash 应为 0x 开头")
        self.assertTrue(trade.token_id, "token_id 不应为空")
        self.assertTrue(trade.condition_id.startswith("0x"), "condition_id 应为 0x 开头")


class TestParseActivity(unittest.TestCase):
    """活动解析测试。"""

    def test_parse_activity_fields(self):
        """读 activity_trade.json 断言 parse_activity 字段映射。"""
        data = _load_fixture("activity_trade.json")
        self.assertIsNotNone(data, "activity_trade.json 不存在")
        rows = data.get("data", [])
        self.assertGreater(len(rows), 0, "activity data 为空")
        act = parse_activity(rows[0])
        self.assertEqual(act.type, "TRADE")
        self.assertIn(act.side, ["BUY", "SELL"])
        self.assertGreater(act.size, 0)
        self.assertIsNotNone(act.usdc_size)
        self.assertTrue(act.tx_hash.startswith("0x"))


class TestParseMarket(unittest.TestCase):
    """市场解析测试。"""

    def test_parse_market_json_string_fields(self):
        """读 market.json 断言 parse_market 后 clob_token_ids 是 list 且 len==2。"""
        data = _load_fixture("market.json")
        self.assertIsNotNone(data, "market.json 不存在")
        market = parse_market(data)
        self.assertIsInstance(market.clob_token_ids, list, "clob_token_ids 应为 list")
        self.assertEqual(len(market.clob_token_ids), 2, "clob_token_ids 应有 2 个")
        self.assertIsInstance(market.outcomes, list, "outcomes 应为 list")
        self.assertEqual(market.outcomes, ["Yes", "No"], f"outcomes 不匹配: {market.outcomes}")
        self.assertIsInstance(market.outcome_prices, list, "outcome_prices 应为 list")
        self.assertTrue(market.condition_id.startswith("0x"), "condition_id 应为 0x 开头")
        self.assertTrue(market.active, "市场应为活跃")
        self.assertFalse(market.closed, "市场不应已关闭")
        self.assertTrue(market.accepting_orders, "市场应接受订单")

    def test_parse_market_synthetic(self):
        """用合成 dict 测试 clobTokenIds 字符串解析。"""
        raw = {
            "conditionId": "0xtest",
            "question": "测试问题?",
            "slug": "test-slug",
            "clobTokenIds": '["a", "b"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "negRisk": False,
            "version": "v1",
        }
        market = parse_market(raw)
        self.assertEqual(market.clob_token_ids, ["a", "b"])
        self.assertEqual(market.outcomes, ["Yes", "No"])
        self.assertEqual(market.outcome_prices, ["0.5", "0.5"])
        self.assertTrue(market.active)
        self.assertTrue(market.accepting_orders)


class TestParseBook(unittest.TestCase):
    """订单簿解析测试。

    fixtures 中可能没有 book.json（404 时未存成），用合成 dict 测试。
    """

    def test_parse_book_synthetic(self):
        """用合成 dict 测试 parse_book。"""
        raw = {
            "market": "0xcond",
            "asset_id": "12345",
            "bids": [{"price": "0.45", "size": "100"}, {"price": "0.44", "size": "200"}],
            "asks": [{"price": "0.46", "size": "150"}],
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "last_trade_price": "0.455",
        }
        book = parse_book(raw, token_id="12345")
        self.assertEqual(book.token_id, "12345")
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(len(book.asks), 1)
        self.assertAlmostEqual(book.bids[0][0], 0.45)
        self.assertAlmostEqual(book.bids[0][1], 100.0)
        self.assertAlmostEqual(book.asks[0][0], 0.46)
        self.assertAlmostEqual(book.tick_size, 0.01)
        self.assertAlmostEqual(book.min_order_size, 5.0)
        self.assertFalse(book.neg_risk)
        self.assertAlmostEqual(book.last_trade_price, 0.455)

    def test_parse_book_real_fixture(self):
        """若有 book.json 则测试真实数据，否则跳过。"""
        data = _load_fixture("book.json")
        if data is None:
            self.skipTest("book.json 不存在，跳过")
        book = parse_book(data)
        self.assertIsInstance(book.bids, list)
        self.assertIsInstance(book.asks, list)


if __name__ == "__main__":
    unittest.main()
