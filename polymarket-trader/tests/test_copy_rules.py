import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Settings
from backend.core.copy_rules import CopyRule, build_signal
from backend.core.order_executor import OrderExecutorManager
from backend.core.trading_desk import TradingDesk
from backend.models.schemas import TradeEvent, TradeSide
from backend.store.state import StateStore


class CopyRuleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name) / 'copy.db'))
        await self.store.connect()
        self.client = AsyncMock()
        self.client.get_orderbook.return_value = {'market':'c','min_order_size':'1',
            'asks':[{'price':'.5','size':'10000'}], 'bids':[{'price':'.49','size':'10000'}]}
        self.client.get_market_by_condition_id.return_value = {}
        self.manager = OrderExecutorManager(Settings(), self.store, self.client)
        self.desk = TradingDesk(self.store, self.manager, self.client)
        self.rule = CopyRule(id='copy_'+'a'*32,wallet='0x'+'1'*40,name='Test')
        await self.store.set_config('copy_rules', [self.rule.model_dump()])

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    def trade(self, side='BUY', size=100, price=.5):
        return TradeEvent(wallet=self.rule.wallet,condition_id='c',token_id='12345',
            side=TradeSide(side),size=size,price=price,tx_hash='0xabc')

    async def test_fixed_ratio_caps_and_source_price_filters(self):
        self.assertEqual((await build_signal(self.desk,self.rule,self.trade())).size,20)
        ratio=self.rule.model_copy(update={'sizing':'ratio','ratio':.1})
        self.assertEqual((await build_signal(self.desk,ratio,self.trade(size=1000))).size,50)
        self.assertIsNone(await build_signal(self.desk,self.rule,self.trade(price=.001)))
        filtered=self.rule.model_copy(update={'min_source_amount':100})
        self.assertIsNone(await build_signal(self.desk,filtered,self.trade()))

    async def test_sell_never_spends_other_rule_inventory(self):
        signal=await build_signal(self.desk,self.rule,self.trade())
        await self.desk.submit(signal)
        self.assertEqual((await self.desk.approve(signal.signal_id))['status'],'FILLED')
        sell=await build_signal(self.desk,self.rule,self.trade('SELL',size=1000,price=.49))
        self.assertEqual(sell.size,20)
        other=self.rule.model_copy(update={'id':'copy_'+'b'*32})
        self.assertIsNone(await build_signal(self.desk,other,self.trade('SELL')))
        await self.store.set_config('copy_rules',[self.rule.model_dump()])
        await self.desk.submit(sell)
        self.assertEqual((await self.desk.approve(sell.signal_id))['status'],'FILLED')
        self.assertIsNone(await build_signal(self.desk,self.rule,self.trade('SELL')))

    async def test_pause_rule_blocks_waiting_approval(self):
        signal=await build_signal(self.desk,self.rule,self.trade())
        await self.desk.submit(signal)
        await self.store.set_config('copy_rules',[self.rule.model_copy(update={'enabled':False}).model_dump()])
        self.assertEqual((await self.desk.approve(signal.signal_id))['status'],'BLOCKED')
        self.assertEqual(await self.store.get_executions(),[])

    async def test_mode_change_before_submit_and_old_backfill_are_blocked(self):
        signal=await build_signal(self.desk,self.rule,self.trade())
        self.manager.set_mode('live')
        with self.assertRaises(ValueError):
            await self.desk.submit(signal)
        self.assertIsNone(await build_signal(self.desk,self.rule,self.trade()))
        self.manager.set_mode('paper')
        await self.store.set_config('copy_rule_activation:'+self.rule.id,datetime.now(timezone.utc).isoformat())
        old=self.trade().model_copy(update={'timestamp':datetime.now(timezone.utc)-timedelta(minutes=1)})
        self.assertIsNone(await build_signal(self.desk,self.rule,old))

    async def test_rule_identity_deduplicates_same_source(self):
        trade=self.trade()
        first=await build_signal(self.desk,self.rule,trade)
        second=await build_signal(self.desk,self.rule,trade)
        self.assertEqual(first.signal_id,second.signal_id)
        await self.desk.submit(first)
        await self.desk.submit(second)
        self.assertEqual(len(await self.desk.queue()),1)

    async def test_global_exit_consumes_fifo_rule_lots_before_another_rule_buys(self):
        from backend.core.copy_rules import rule_quantity
        from backend.models.schemas import ExecutionRecord
        buy=await build_signal(self.desk,self.rule,self.trade())
        await self.desk.capture(buy, ExecutionRecord(signal_id=buy.signal_id,mode='PAPER',status='FILLED',fill_price=.5,fill_size=20))
        exit_signal=buy.model_copy(update={'signal_id':'exit-test','pipeline_id':'risk-exit','side':TradeSide.SELL})
        await self.desk.capture(exit_signal, ExecutionRecord(signal_id=exit_signal.signal_id,mode='PAPER',status='FILLED',fill_price=.5,fill_size=20))
        other=buy.model_copy(update={'signal_id':'other-buy','pipeline_id':'copy_'+'b'*32})
        await self.desk.capture(other, ExecutionRecord(signal_id=other.signal_id,mode='PAPER',status='FILLED',fill_price=.5,fill_size=20))
        self.assertEqual(await rule_quantity(self.desk,self.rule,'12345'),0)
