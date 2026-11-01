import asyncio
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend.config import Settings
from backend.accounts.vault import CredentialVault
from backend.core.order_executor import OrderExecutorManager
from backend.core.trading_desk import TradingDesk, DeskPolicy
from backend.core.smart_trading import SmartTrading, SmartStrategy, ModelDecision, ModelConfig, estimate, now
from backend.models.schemas import TradeEvent
from backend.store.state import StateStore


class SmartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=StateStore(str(Path(self.tmp.name)/'smart.db'));await self.store.connect()
        self.client=AsyncMock()
        self.client.get_market_by_condition_id.return_value={'question':'Test market','events':[{'id':'event1'}]}
        self.manager=OrderExecutorManager(Settings(),self.store,self.client)
        self.desk=TradingDesk(self.store,self.manager,self.client)
        self.e=SmartTrading(self.desk,CredentialVault(Path(self.tmp.name)/'vault'))
        self.desk.smart=self.e;await self.e.initialize()
        self.s=SmartStrategy(id='smart_'+'1'*16,name='测试动量',kind='momentum',tokens=['12345'],
            enabled=True,workflow='auto',min_samples=3,threshold=.03,cooldown_seconds=30)
        await self.save(self.s)
        self.book(.53,.52)

    async def asyncTearDown(self):
        await self.store.close();self.tmp.cleanup()

    async def save(self,s):
        await self.store.set_config('smart_strategies',[s.model_dump()])

    def book(self,ask,bid):
        self.client.get_orderbook.return_value={'market':'condition1','min_order_size':'1','tick_size':'.01',
            'asks':[{'price':str(ask),'size':'10000'}], 'bids':[{'price':str(bid),'size':'10000'}]}

    def trade(self,p=.53,index=0,wallet=None):
        return TradeEvent(wallet=wallet or '0x'+'1'*40,token_id='12345',condition_id='condition1',
            market_title='Test market',side='BUY',price=p,size=5000,tx_hash='tx'+str(index),
            timestamp=now()-timedelta(seconds=10-index))

    async def trigger(self):
        for i,p in enumerate((.50,.51,.53)):await self.e.ingest(self.trade(p,i))

    async def test_auto_strategy_executes_with_global_review_and_caps(self):
        self.assertEqual((await self.desk.policy()).workflow,'review')
        await self.trigger()
        records=await self.store.get_executions()
        self.assertEqual(len(records),1);self.assertEqual(records[0].status.value,'FILLED')
        self.assertLessEqual(records[0].fill_size*records[0].fill_price,10)
        await self.e.ingest(self.trade(.54,4))
        self.assertEqual(len(await self.store.get_executions()),1)

    async def test_review_strategy_does_not_auto_even_global_auto(self):
        await self.desk.set_policy(DeskPolicy(workflow='auto'))
        await self.save(self.s.model_copy(update={'workflow':'review'}))
        await self.trigger()
        self.assertEqual(await self.store.get_executions(),[])
        self.assertEqual((await self.desk.queue())[0]['status'],'waiting')

    async def test_global_observe_vetoes_auto(self):
        await self.desk.set_policy(DeskPolicy(workflow='observe'))
        await self.trigger()
        self.assertEqual(await self.store.get_executions(),[])
        self.assertEqual((await self.desk.queue())[0]['status'],'observed')

    async def test_modified_config_invalidates_queued_order(self):
        await self.save(self.s.model_copy(update={'workflow':'review'}));await self.trigger()
        row=(await self.desk.queue())[0]
        await self.save(self.s.model_copy(update={'workflow':'review','amount':5}))
        result=await self.desk.approve(row['signal_id'])
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(await self.store.get_executions(),[])

    async def test_model_unconfigured_or_failed_never_falls_back_to_buy(self):
        await self.save(self.s.model_copy(update={'use_llm':True}))
        await self.trigger()
        self.assertEqual(await self.store.get_executions(),[])
        self.e.model_decision=AsyncMock(side_effect=TimeoutError('sensitive-secret'))
        await self.e.ingest(self.trade(.54,4))
        async with self.store._db.execute('SELECT reason FROM smart_decisions') as c:rows=await c.fetchall()
        self.assertTrue(rows)
        self.assertNotIn('sensitive-secret',str([r[0] for r in rows]))
        self.assertEqual(await self.store.get_executions(),[])

    async def test_pause_during_model_call_vetoes_late_approval(self):
        await self.save(self.s.model_copy(update={'use_llm':True}))
        async def late(*args):
            await self.save(self.s.model_copy(update={'use_llm':True,'enabled':False}));return True
        self.e.model_decision=AsyncMock(side_effect=late)
        await self.trigger()
        self.assertEqual(await self.store.get_executions(),[])

    async def test_take_profit_exits_owned_lots_even_model_offline(self):
        await self.trigger()
        self.assertEqual(len(await self.store.get_executions()),1)
        await self.store._db.execute("UPDATE smart_orders SET created_at=?",((now()-timedelta(seconds=60)).isoformat(),));await self.store._db.commit()
        self.book(.65,.64)
        await self.e.ingest(self.trade(.64,5))
        records=await self.store.get_executions()
        self.assertEqual(len(records),2)
        quantity,_,closed=await self.e.owned(self.s,'12345')
        self.assertAlmostEqual(quantity,0)
        self.assertEqual(len(closed),1);self.assertGreater(closed[0]['pnl'],0)

    async def test_stop_loss_checks_book_without_new_source_trade_or_cooldown_wait(self):
        await self.trigger()
        self.book(.45,.44)
        await self.e.check_exits()
        self.assertEqual(len(await self.store.get_executions()),2)
        qty,_,closed=await self.e.owned(self.s,'12345')
        self.assertAlmostEqual(qty,0)
        self.assertLess(closed[0]['pnl'],0)

    async def test_stale_or_wrong_token_does_not_trade(self):
        t=self.trade();t.timestamp=now()-timedelta(minutes=5)
        await self.e.ingest(t)
        t=self.trade();t.token_id='999';await self.e.ingest(t)
        self.assertEqual(await self.store.get_executions(),[])
        self.assertFalse(self.e.histories)

    async def test_mode_mismatch_no_live_orders(self):
        await self.save(self.s.model_copy(update={'mode':'LIVE'}));await self.trigger()
        self.assertEqual(await self.store.get_executions(),[])

    async def test_estimates_disclose_hypothesis_and_costs(self):
        e=estimate(self.s)
        self.assertIsNone(e['history']['win_rate'])
        self.assertEqual(e['history']['closed_trades'],0)
        self.assertAlmostEqual(e['scenarios'][1]['net_month'],-1)
        self.assertAlmostEqual(e['break_even_win_rate'],.525)
        costly=estimate(self.s.model_copy(update={'use_llm':True,'assumed_model_cost_usdc':.1}))
        self.assertLess(costly['scenarios'][1]['net_month'],e['scenarios'][1]['net_month'])
        h=estimate(self.s,[{'pnl':1,'cost':10}]*20)['history']
        self.assertEqual(h['win_rate'],1);self.assertEqual(h['mean_interval_95'],[1,1])

    async def test_model_schema_cannot_override_orders(self):
        for payload in ('{"action":"buy","confidence":1.0,"reason":"x"}',
            '{"action":"approve","confidence":NaN,"reason":"x"}',
            '{"action":"approve","confidence":0.9,"reason":"x","amount":9999}'):
            with self.assertRaises(ValueError):ModelDecision.model_validate_json(payload)
        with self.assertRaises(ValueError):ModelConfig(base_url='https://user:secret@example.com/v1')

    async def test_model_http_contract_and_daily_call_budget(self):
        import httpx
        config=ModelConfig(enabled=True,base_url='http://127.0.0.1:9191/v1',model='test-model',max_calls_day=1)
        await self.store.set_config('smart_model',config.model_dump())
        self.e.vault.put('smart_model',{'api_key':'only-for-model'})
        requests=[]
        async def response(request):
            requests.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"action":"approve","confidence":0.9,"reason":"rule consistency only"}'}}]})
        original=httpx.AsyncClient
        with patch('httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(response),**kw)):
            self.assertTrue(await self.e.model_decision(self.s,self.trade(),[self.trade()]))
            with self.assertRaises(ValueError):await self.e.model_decision(self.s,self.trade(),[self.trade()])
        self.assertEqual(len(requests),1)
        self.assertEqual(str(requests[0].url),'http://127.0.0.1:9191/v1/chat/completions')
        self.assertEqual(requests[0].headers['Authorization'],'Bearer only-for-model')
        self.assertNotIn('only-for-model',requests[0].content.decode())
        self.assertNotIn('private_key',requests[0].content.decode())

    async def test_consensus_counts_unique_wallets_not_number_of_trades(self):
        s=self.s.model_copy(update={'kind':'consensus','min_wallets':3})
        window=[self.trade(.5,i) for i in range(3)]
        self.assertFalse(self.e.candidate(s,window[-1],window))
        for i,t in enumerate(window):t.wallet='0x'+str(i+1)*40
        self.assertTrue(self.e.candidate(s,window[-1],window))
