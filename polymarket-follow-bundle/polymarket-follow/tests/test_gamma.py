"""Gamma 市场解析测试模块。

测试 parse_market 对 JSON 字符串字段的解析。
"""

import unittest

from pmfollow.models import parse_market
from pmfollow.gamma import GammaApi


class TestGammaParseMarket(unittest.TestCase):
    """parse_market 对 clobTokenIds 字符串的解析测试。"""

    def test_clob_token_ids_string_parse(self):
        """clobTokenIds='["a","b"]' 字符串解析为 list。"""
        raw = {
            "conditionId": "0xtest",
            "question": "测试市场?",
            "slug": "test-slug",
            "clobTokenIds": '["a", "b"]',
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
            "active": True,
            "closed": False,
            "acceptingOrders": True,
        }
        market = parse_market(raw)
        self.assertEqual(market.clob_token_ids, ["a", "b"])
        self.assertIsInstance(market.clob_token_ids, list)

    def test_clob_token_ids_already_list(self):
        """clobTokenIds 已经是 list 时直接返回。"""
        raw = {
            "conditionId": "0xtest",
            "clobTokenIds": ["x", "y", "z"],
            "outcomes": ["A", "B", "C"],
        }
        market = parse_market(raw)
        self.assertEqual(market.clob_token_ids, ["x", "y", "z"])
        self.assertEqual(market.outcomes, ["A", "B", "C"])

    def test_clob_token_ids_none(self):
        """clobTokenIds 为 None 时返回空 list。"""
        raw = {"conditionId": "0xtest"}
        market = parse_market(raw)
        self.assertEqual(market.clob_token_ids, [])
        self.assertEqual(market.outcomes, [])

    def test_clob_token_ids_invalid_string(self):
        """clobTokenIds 为无效 JSON 字符串时返回空 list。"""
        raw = {
            "conditionId": "0xtest",
            "clobTokenIds": "not valid json",
        }
        market = parse_market(raw)
        self.assertEqual(market.clob_token_ids, [])


class TestGammaExtractMarkets(unittest.TestCase):
    """extract_markets 从事件列表提取市场测试。"""

    def test_extract_markets_from_events(self):
        """从含 markets 的事件列表提取市场。"""
        events = [
            {
                "id": "1",
                "slug": "event-1",
                "markets": [
                    {
                        "conditionId": "0xcond1",
                        "question": "问题1?",
                        "slug": "market-1",
                        "clobTokenIds": '["t1", "t2"]',
                        "outcomes": '["Yes", "No"]',
                        "active": True,
                        "closed": False,
                        "acceptingOrders": True,
                    },
                    {
                        "conditionId": "0xcond2",
                        "question": "问题2?",
                        "slug": "market-2",
                        "clobTokenIds": '["t3", "t4"]',
                        "outcomes": '["Yes", "No"]',
                        "active": True,
                        "closed": False,
                        "acceptingOrders": True,
                    },
                ],
            },
        ]
        # GammaApi 需要一个 client 参数，但 extract_markets 不使用它
        gamma = GammaApi.__new__(GammaApi)
        markets = gamma.extract_markets(events)
        self.assertEqual(len(markets), 2)
        self.assertEqual(markets[0].condition_id, "0xcond1")
        self.assertEqual(markets[0].clob_token_ids, ["t1", "t2"])
        self.assertEqual(markets[0].event_slug, "event-1")
        self.assertEqual(markets[1].condition_id, "0xcond2")

    def test_extract_markets_dedup(self):
        """重复 condition_id 去重。"""
        events = [
            {
                "slug": "event-1",
                "markets": [
                    {"conditionId": "0xdup", "question": "Q1", "clobTokenIds": "[]"},
                    {"conditionId": "0xdup", "question": "Q1重复", "clobTokenIds": "[]"},
                ],
            },
        ]
        gamma = GammaApi.__new__(GammaApi)
        markets = gamma.extract_markets(events)
        self.assertEqual(len(markets), 1)

    def test_extract_markets_empty(self):
        """空事件列表返回空。"""
        gamma = GammaApi.__new__(GammaApi)
        markets = gamma.extract_markets([])
        self.assertEqual(markets, [])


if __name__ == "__main__":
    unittest.main()
