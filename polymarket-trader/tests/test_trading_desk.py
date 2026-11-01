import sys
import tempfile
import unittest
import uuid
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Settings
from backend.core.order_executor import OrderExecutorManager
from backend.core.trading_desk import TradingDesk, DeskPolicy, ManualOrder
from backend.models.schemas import FollowSignal, MarketRef, TradeEvent, TradeSide
from backend.store.state import StateStore


class TradingDeskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name)/'desk.db'))
        await self.store.connect()
        self.client = AsyncMock()
        self.book(.5)
        self.client.get_market_by_condition_id.return_value = {'events': [{'id': 'event-1'}], 'question': 'Team A vs. Team B'}
        self.client.get_best_price.return_value = .5
        self.manager = OrderExecutorManager(Settings(), self.store, self.client)
        self.desk = TradingDesk(self.store, self.manager, self.client)

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    def book(self, ask, bid=None):
        self.client.get_orderbook.return_value = {'market': 'condition-1', 'min_order_size': '1',
            'asks': [{'price': str(ask), 'size': '10000'}],
            'bids': [{'price': str(bid if bid is not None else ask-.01), 'size': '10000'}]}

    def signal(self, side='BUY', price=.5, size=10, token='12345', wallet='source-A'):
        trade = TradeEvent(wallet=wallet, token_id=token, condition_id='condition-1', market_title='Team A vs. Team B',
            side=TradeSide(side), price=price, size=size)
        return FollowSignal(source_trade=trade, pipeline_id='test', market=MarketRef(condition_id='condition-1', token_id=token, title=trade.market_title),
            side=trade.side, price=price, size=size, signal_id=uuid.uuid4().hex)

    async def buy(self, size=10):
        s = self.signal(size=size)
        await self.desk.submit(s)
        return await self.desk.approve(s.signal_id)

    async def test_review_does_not_execute_until_approval_and_no_double_submit(self):
        s = self.signal()
        await self.desk.submit(s)
        self.assertEqual(await self.store.get_executions(), [])
        self.assertEqual((await self.desk.get_item(s.signal_id))['status'], 'waiting')
        self.assertEqual((await self.desk.approve(s.signal_id))['status'], 'FILLED')
        with self.assertRaises(ValueError):
            await self.desk.approve(s.signal_id)
        self.assertEqual(len(await self.store.get_executions()), 1)

    async def test_auto_and_observe(self):
        await self.desk.set_policy(DeskPolicy(workflow='observe'))
        observed = await self.desk.submit(self.signal())
        self.assertEqual(observed['status'], 'observed')
        await self.desk.set_policy(DeskPolicy(workflow='auto'))
        automatic = await self.desk.submit(self.signal())
        self.assertEqual(automatic['status'], 'filled')

    async def test_buy_sell_inventory_fifo_fees_and_attribution(self):
        self.assertEqual((await self.buy())['status'], 'FILLED')
        self.book(.61, .6)
        s = self.signal('SELL', .6, 10, wallet='manual')
        await self.desk.submit(s)
        self.assertEqual((await self.desk.approve(s.signal_id))['status'], 'FILLED')
        state = await self.desk.summary()
        self.assertEqual(state['positions'], [])
        self.assertAlmostEqual(state['gross_realized_pnl'], 1)
        self.assertAlmostEqual(state['fee_estimate'], .022)
        self.assertAlmostEqual(state['net_pnl_estimate'], .978)
        self.assertEqual(state['wallet_sectors'][0]['wallet'], 'source-A')
        self.assertAlmostEqual(state['wallet_sectors'][0]['net_pnl_estimate'], .978)
        self.assertEqual(self.manager.risk._positions['condition-1'], 0)

    async def test_no_naked_sell(self):
        s = self.signal('SELL', .49)
        await self.desk.submit(s)
        result = await self.desk.approve(s.signal_id)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('可卖份额', result['error'])
        self.assertEqual(await self.store.get_executions(), [])

    async def test_reprice_expiry_depth_and_spread(self):
        s = self.signal()
        await self.desk.submit(s)
        self.book(.6, .59)
        self.assertIn('价格偏离', (await self.desk.approve(s.signal_id))['error'])
        self.book(.5)
        s = self.signal()
        s.source_trade.timestamp = datetime.now(timezone.utc)-timedelta(minutes=5)
        await self.desk.submit(s)
        self.assertIn('信号过期', (await self.desk.approve(s.signal_id))['error'])
        s = self.signal(size=20000)
        await self.desk.submit(s)
        self.assertIn('数量不足', (await self.desk.approve(s.signal_id))['error'])
        self.book(.5, .3)
        s = self.signal()
        await self.desk.submit(s)
        self.assertIn('价差', (await self.desk.approve(s.signal_id))['error'])

    async def test_mode_bound_queue(self):
        s = self.signal()
        await self.desk.submit(s)
        self.manager.set_mode('live')
        with self.assertRaises(ValueError):
            await self.desk.approve(s.signal_id)
        self.assertEqual(await self.store.get_executions(), [])

    async def test_same_event_limit_across_wallets_and_tokens(self):
        await self.desk.set_policy(DeskPolicy(max_event_exposure=7))
        await self.buy()
        s = self.signal(token='54321', wallet='source-B')
        await self.desk.submit(s)
        result = await self.desk.approve(s.signal_id)
        self.assertIn('同一事件', result['error'])

    async def test_daily_loss_stops_buy_but_allows_sell(self):
        await self.buy(size=20)
        self.book(.31, .3)
        sell = self.signal('SELL', .3, 10)
        await self.desk.submit(sell)
        await self.desk.approve(sell.signal_id)
        await self.desk.set_policy(DeskPolicy(max_daily_loss=1))
        self.book(.5)
        s = self.signal()
        await self.desk.submit(s)
        self.assertIn('亏损熔断', (await self.desk.approve(s.signal_id))['error'])
        self.book(.31, .3)
        s = self.signal('SELL', .3, 10)
        await self.desk.submit(s)
        self.assertEqual((await self.desk.approve(s.signal_id))['status'], 'FILLED')

    async def test_recovery_records_fill_once_never_resubmits(self):
        await self.buy()
        await self.desk.recover()
        await self.desk.recover()
        self.assertEqual(len((await self.desk.summary())['journal']), 1)
        self.assertEqual(len(await self.store.get_executions()), 1)

    async def test_manual_is_idempotent_and_requires_review_even_in_auto(self):
        await self.desk.set_policy(DeskPolicy(workflow='auto'))
        request = ManualOrder(token_id='12345', condition_id='condition-1', side='BUY', price=.5, size=10, client_order_id='test-order-123')
        row = await self.desk.manual(request)
        await self.desk.manual(request)
        self.assertEqual(row['status'], 'waiting')
        self.assertEqual(len(await self.desk.queue()), 1)
        self.assertEqual(await self.store.get_executions(), [])

    async def test_concurrent_approval_only_executes_once(self):
        s = self.signal()
        await self.desk.submit(s)
        results = await asyncio.gather(self.desk.approve(s.signal_id), self.desk.approve(s.signal_id), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, ValueError) for x in results), 1)
        self.assertEqual(len(await self.store.get_executions()), 1)

    async def test_exit_trigger_respects_review_and_auto(self):
        await self.buy()
        await self.desk.set_policy(DeskPolicy(exits_enabled=True, workflow='review'))
        self.client.get_best_price.return_value = .4
        self.book(.41, .4)
        await self.desk.evaluate_exits()
        rows = await self.desk.queue()
        exits = [r for r in rows if r['signal']['pipeline_id'] == 'risk-exit']
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]['status'], 'waiting')
        self.assertEqual(len(await self.store.get_executions()), 1)
        await self.desk.approve(exits[0]['signal_id'])
        self.assertEqual((await self.desk.summary())['positions'], [])

    async def test_auto_exit_executes_and_cooldown_prevents_duplicates(self):
        await self.buy()
        await self.desk.set_policy(DeskPolicy(exits_enabled=True, workflow='auto'))
        self.client.get_best_price.return_value = .7
        self.book(.71, .7)
        await self.desk.evaluate_exits()
        await self.desk.evaluate_exits()
        self.assertEqual(len(await self.store.get_executions()), 2)
        self.assertEqual((await self.desk.summary())['positions'], [])

    async def test_specialty_filter_does_not_treat_activity_as_skill(self):
        await self.desk.set_policy(DeskPolicy(follow_specialties_only=True))
        s = self.signal()
        await self.desk.submit(s)
        self.assertIn('擅长板块', (await self.desk.approve(s.signal_id))['error'])
        await self.store.set_config('wallet_profile:source-a', {'specialties': ['体育']})
        self.assertEqual((await self.buy())['status'], 'FILLED')

    async def test_mode_change_during_quote_never_turns_paper_order_live(self):
        s = self.signal()
        await self.desk.submit(s)
        book = self.client.get_orderbook.return_value
        async def switch_mode(*args, **kwargs):
            self.manager.set_mode('live')
            return book
        self.client.get_orderbook.side_effect = switch_mode
        result = await self.desk.approve(s.signal_id)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('执行模式', result['error'])

    async def test_changed_payload_cannot_reuse_manual_id(self):
        req = ManualOrder(token_id='12345', condition_id='condition-1', side='BUY', price=.5, size=10, client_order_id='unique-abc')
        await self.desk.manual(req)
        with self.assertRaises(ValueError):
            await self.desk.manual(req.model_copy(update={'size': 20}))

    async def test_stale_book_never_submits(self):
        self.client.get_orderbook.return_value['timestamp'] = str(int((datetime.now(timezone.utc)-timedelta(minutes=2)).timestamp()*1000))
        s = self.signal()
        await self.desk.submit(s)
        self.assertIn('订单簿时间', (await self.desk.approve(s.signal_id))['error'])

    async def test_manual_bypasses_source_weights_but_keeps_total_limit(self):
        from backend.models.schemas import PortfolioAllocation, WalletAllocation
        self.manager.set_portfolio_allocation(PortfolioAllocation(allocations=[WalletAllocation(wallet='other-wallet', weight=1)], max_total_exposure=10))
        request = ManualOrder(token_id='12345', condition_id='condition-1', side='BUY', price=.5, size=10, client_order_id='manual-weights')
        row = await self.desk.manual(request)
        self.assertEqual((await self.desk.approve(row['signal_id']))['status'], 'FILLED')
        request = request.model_copy(update={'client_order_id': 'manual-over-cap', 'size': 12})
        row = await self.desk.manual(request)
        result = await self.desk.approve(row['signal_id'])
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('组合总', result['error'])

    def test_public_trade_parsing_never_invents_side_or_fresh_timestamp(self):
        from backend.core.polymarket_client import PolymarketClient
        raw = {'side': 'SELL', 'asset': '12345', 'price': .5, 'size': 10, 'timestamp': '1760000000', 'transactionHash': '0xabc'}
        trade = PolymarketClient._parse_trade(raw, 'wallet')
        self.assertEqual(trade.side.value, 'SELL')
        self.assertEqual(trade.token_id, '12345')
        self.assertEqual(trade.tx_hash, '0xabc')
        self.assertIsNone(PolymarketClient._parse_trade({**raw, 'timestamp': ''}, 'wallet'))
        self.assertIsNone(PolymarketClient._parse_trade({**raw, 'side': 'REDEEM'}, 'wallet'))

    async def test_weighted_paper_order_below_market_minimum_never_fakes_fill(self):
        from backend.models.schemas import PortfolioAllocation, WalletAllocation
        self.manager.set_portfolio_allocation(PortfolioAllocation(allocations=[
            WalletAllocation(wallet='source-A', weight=1), WalletAllocation(wallet='source-B', weight=1)], max_per_wallet_share=.5))
        self.client.get_orderbook.return_value['min_order_size'] = '5'
        s = self.signal(size=6)
        await self.desk.submit(s)
        result = await self.desk.approve(s.signal_id)
        self.assertEqual(result['status'], 'FAILED')
        self.assertIn('最小委托量', result['error'])
        self.assertEqual((await self.desk.summary())['journal'], [])

    async def test_expired_review_exit_does_not_suppress_new_exit_forever(self):
        await self.buy()
        await self.desk.set_policy(DeskPolicy(exits_enabled=True, workflow='review'))
        self.client.get_best_price.return_value = .4
        self.book(.41, .4)
        await self.desk.evaluate_exits()
        row = next(r for r in await self.desk.queue() if r['signal']['pipeline_id'] == 'risk-exit')
        signal = FollowSignal.model_validate(row['signal'])
        signal.source_trade.timestamp = datetime.now(timezone.utc)-timedelta(minutes=5)
        await self.desk.db.execute('UPDATE desk_queue SET signal=? WHERE signal_id=?', (signal.model_dump_json(), signal.signal_id))
        await self.desk.db.commit()
        await self.store.set_config('desk_exit:PAPER:12345', 0)
        await self.desk.evaluate_exits()
        exits = [r for r in await self.desk.queue() if r['signal']['pipeline_id'] == 'risk-exit']
        self.assertEqual(len(exits), 2)
        self.assertEqual(sum(r['expired'] for r in exits), 1)
        self.assertEqual(len(await self.store.get_executions()), 1)


    async def test_virtual_cash_budget_and_capital_lock(self):
        await self.desk.set_paper_capital(4)
        result = await self.buy()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('虚拟资金不足', result['error'])
        await self.desk.set_paper_capital(100)
        await self.buy()
        self.assertAlmostEqual((await self.desk.summary())['cash_balance'], 94.99)
        with self.assertRaises(ValueError):
            await self.desk.set_paper_capital(1000)

    async def test_final_winner_settlement_idempotent_and_restart(self):
        await self.buy()
        result = {'condition_id':'condition-1','final':True,'source':'polymarket_clob_final_winner','payouts':{'12345':1}}
        self.assertEqual(await self.desk.settle_paper(result), 1)
        self.assertEqual(await self.desk.settle_paper(result), 0)
        restarted = TradingDesk(self.store, self.manager, self.client)
        await restarted.recover()
        self.assertEqual(await restarted.settle_paper(result), 0)
        state = await restarted.summary()
        self.assertEqual(state['positions'], [])
        self.assertAlmostEqual(state['cash_balance'], 10004.99)
        self.assertAlmostEqual(state['net_pnl_estimate'], 4.99)
        self.assertEqual(state['journal'][0]['kind'], 'settlement')
        legacy = await self.store.get_virtual_position('desk-account', '12345')
        self.assertEqual(legacy.net_qty, 0)

    async def test_loser_settlement_while_live_never_uses_live_funds(self):
        await self.buy()
        from backend.models.schemas import ExecutionMode
        self.manager.set_mode('live')
        self.manager._live.execute = AsyncMock()
        result = {'condition_id':'condition-1','final':True,'source':'polymarket_clob_final_winner','payouts':{'12345':0}}
        self.assertEqual(await self.desk.settle_paper(result), 1)
        paper = await self.desk.summary(mode='PAPER')
        self.assertEqual(paper['positions'], [])
        self.assertAlmostEqual(paper['net_pnl_estimate'], -5.01)
        self.assertAlmostEqual(paper['cash_balance'], 9994.99)
        self.assertEqual((await self.desk.summary(mode='LIVE'))['journal'], [])
        self.manager._live.execute.assert_not_called()

    async def test_unconfirmed_or_untrusted_result_does_not_settle(self):
        await self.buy()
        for result in [dict(final=False,source='polymarket_clob_final_winner'),dict(final=True,source='price_guess')]:
            self.assertEqual(await self.desk.settle_paper({**result,'condition_id':'condition-1','payouts':{'12345':1}}), 0)
        self.assertEqual(len((await self.desk.summary())['positions']), 1)

    async def test_missing_mark_is_unknown_instead_of_zero_profit(self):
        await self.buy()
        self.client.get_best_price.return_value = None
        state = await self.desk.account_snapshot()
        self.assertIsNone(state['equity'])
        self.assertIsNone(state['floating_pnl'])
        self.assertEqual(state['marks_missing'], 1)
