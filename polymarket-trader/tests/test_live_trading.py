"""实盘边界测试。所有 SDK 调用均为桩，不接触真实订单。"""
import asyncio
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Settings
from backend.core.order_executor import OrderExecutorManager
from backend.models.schemas import FollowSignal, MarketRef, TradeEvent, TradeSide
from backend.store.state import StateStore


class LiveTradingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name) / 'test.db'))
        await self.store.connect()
        self.settings = Settings(executor={'mode': 'live'})
        self.manager = OrderExecutorManager(self.settings, self.store)
        self.sdk = MagicMock()
        self.sdk.get_tick_size.return_value = '0.01'
        self.sdk.get_neg_risk.return_value = False
        self.sdk.get_balance_allowance.return_value = {'balance': '1000000000'}
        self.sdk.post_order.return_value = {'success': True, 'status': 'matched', 'orderID': 'order-1',
                                            'makingAmount': '4.9', 'takingAmount': '10'}
        self.manager._live._clob_client = self.sdk

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    def signal(self, price=.505, side=TradeSide.BUY):
        trade = TradeEvent(wallet='0x' + '1' * 40, token_id='12345', condition_id='market',
                           price=price, size=10, side=side, tx_hash='tx')
        return FollowSignal(source_trade=trade, pipeline_id='test', signal_id=uuid.uuid4().hex,
                            market=MarketRef(condition_id='market', token_id='12345'),
                            side=side, price=price, size=10)

    async def test_fok_real_fill_and_idempotency(self):
        signal = self.signal()
        result = await self.manager.execute(signal)
        self.assertEqual(result.status.value, 'FILLED')
        self.assertAlmostEqual(result.fill_price, .49)
        args = self.sdk.create_order.call_args.args[0]
        self.assertEqual(args.price, .5)  # BUY 不越过限价
        self.assertEqual(self.sdk.post_order.call_args.args[1], 'FOK')
        await self.manager.execute(signal)
        self.assertEqual(self.sdk.post_order.call_count, 1)
        self.assertEqual(len(await self.store.get_executions()), 1)

    async def test_timeout_blocks_following_orders_and_restart(self):
        self.sdk.post_order.side_effect = TimeoutError('unknown')
        signal = self.signal()
        result = await self.manager.execute(signal)
        self.assertEqual(result.status.value, 'PENDING')
        new_manager = OrderExecutorManager(self.settings, self.store)
        new_manager._live._clob_client = self.sdk
        blocked = await new_manager.execute(self.signal())
        self.assertEqual(blocked.status.value, 'FAILED')
        self.assertIn('待核对', blocked.error)
        self.assertEqual(self.sdk.post_order.call_count, 1)

    async def test_expired_source_does_not_submit(self):
        signal = self.signal()
        signal.source_trade.timestamp = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = await self.manager.execute(signal)
        self.assertEqual(result.status.value, 'FAILED')
        self.sdk.post_order.assert_not_called()

    async def test_rejection_is_not_fill(self):
        self.sdk.post_order.return_value = {'success': False, 'errorMsg': 'no liquidity'}
        result = await self.manager.execute(self.signal())
        self.assertEqual(result.status.value, 'FAILED')
        self.assertIsNone(result.fill_price)

    async def test_http_rejection_does_not_become_unknown(self):
        error = RuntimeError('rejected')
        error.status_code = 400
        self.sdk.post_order.side_effect = error
        result = await self.manager.execute(self.signal())
        self.assertEqual(result.status.value, 'FAILED')

    async def test_real_sdk_signs_offline_with_test_key(self):
        from backend.config import PolymarketCredentials
        self.settings.polymarket = PolymarketCredentials(api_key='test-key', api_secret='dGVzdA==',
            api_passphrase='test', private_key='0x' + '11' * 32)
        self.manager._live._init_clob_client()
        sdk = self.manager._live._clob_client
        sdk.get_tick_size = MagicMock(return_value='0.01')
        sdk.get_neg_risk = MagicMock(return_value=False)
        sdk.get_fee_rate_bps = MagicMock(return_value=0)
        sdk.get_book_context = MagicMock(return_value={'asset_id':'12345', 'tick_size':'0.01', 'min_order_size':'1', 'neg_risk':False})
        sdk.post_order = MagicMock(return_value={'success': True, 'status': 'matched',
            'orderID': 'offline-test', 'makingAmount': '5', 'takingAmount': '10'})
        result = await self.manager.execute(self.signal())
        self.assertEqual(result.status.value, 'FILLED')
        signed = sdk.post_order.call_args.args[0]
        self.assertTrue(signed.signature)

    async def test_sell_limit_rounds_up_and_actual_amounts(self):
        self.sdk.post_order.return_value = {'success': True, 'status': 'matched', 'orderID': 'sell',
                                            'makingAmount': '10', 'takingAmount': '5.2'}
        result = await self.manager.execute(self.signal(side=TradeSide.SELL))
        self.assertAlmostEqual(result.fill_price, .52)
        self.assertEqual(self.sdk.create_order.call_args.args[0].price, .51)

    async def test_daily_budget_survives_restart(self):
        self.settings.risk.max_daily_trades = 1
        await self.manager.execute(self.signal())
        new_manager = OrderExecutorManager(self.settings, self.store)
        new_manager._live._clob_client = self.sdk
        result = await new_manager.execute(self.signal())
        self.assertEqual(result.status.value, 'FAILED')
        self.assertIn('日交易数', result.error)
        self.assertEqual(self.sdk.post_order.call_count, 1)

    async def test_cancel_requires_exchange_confirmation_before_unlocking(self):
        self.sdk.post_order.return_value = {'success': True, 'status': 'delayed', 'orderID': 'cancel-me'}
        record = await self.manager.execute(self.signal())
        self.sdk.cancel.return_value = {'canceled': ['cancel-me']}
        self.sdk.get_order.return_value = {'status': 'CANCELED', 'size_matched': '0', 'original_size': '10'}
        result = await self.manager._live.cancel(record)
        self.assertEqual(result.status.value, 'CANCELLED')
        self.assertIsNone(result.fill_price)
        self.sdk.cancel.assert_called_once_with('cancel-me')

    async def test_preflight_only_reads(self):
        self.sdk.get_balance_allowance.return_value = {'balance': '12000000', 'allowances': {'exchange': '99999999'}}
        result = await self.manager._live.preflight()
        self.assertTrue(result['ready'])
        self.assertEqual(result['balance_usdc'], 12)
        self.sdk.create_order.assert_not_called()
        self.sdk.post_order.assert_not_called()

    async def test_reconcile_keeps_unconfirmed_order_pending(self):
        self.sdk.post_order.return_value = {'success': True, 'status': 'delayed', 'orderID': 'known'}
        result = await self.manager.execute(self.signal())
        self.sdk.get_order.return_value = {'status': 'LIVE', 'size_matched': '0', 'original_size': '10'}
        result = await self.manager._live.reconcile(result)
        self.assertEqual(result.status.value, 'PENDING')
        self.sdk.get_order.return_value = {'status': 'MATCHED', 'size_matched': '10', 'original_size': '10'}
        result = await self.manager._live.reconcile(result)
        self.assertEqual(result.status.value, 'PENDING')
        self.assertIsNone(result.fill_price)
        self.sdk.get_order.return_value['associate_trades'] = ['trade-1']
        self.sdk.get_trades.return_value = [{'id': 'trade-1', 'status': 'CONFIRMED', 'taker_order_id': 'known', 'size': '10', 'price': '.48'}]
        result = await self.manager._live.reconcile(result)
        self.assertEqual(result.status.value, 'FILLED')
        self.assertEqual(result.fill_price, .48)

