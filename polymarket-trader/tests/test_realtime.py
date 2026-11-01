import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from backend.config import Settings
from backend.core.market_sync import MarketSync
from backend.core.polymarket_client import PolymarketClient
from backend.core.ws_client import PolymarketWSClient
from backend.store.state import StateStore


class RealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name)/'sync.db'))
        await self.store.connect()
        self.client = PolymarketClient(Settings())
        self.monitor = Mock()
        self.monitor.get_watched_wallets.return_value = ['0xabc']
        self.monitor.ingest_realtime = AsyncMock()
        self.desk = AsyncMock()
        self.desk.summary.return_value = {'positions':[]}
        self.sync = MarketSync(self.store,self.client,self.monitor,self.desk)

    async def asyncTearDown(self):
        await self.client.close()
        await self.store.close()
        self.tmp.cleanup()

    async def test_rtds_rest_dedup_persistence_and_watched_dispatch(self):
        raw = {'proxyWallet':'0xAbC','asset':'12345','conditionId':'condition-1','side':'BUY','size':10,'price':.5,
               'timestamp':1793015542,'transactionHash':'0x111','title':'Example'}
        await self.sync.handle_rtds(json.dumps({'topic':'activity','type':'trades','payload':raw}))
        self.assertFalse(await self.sync.ingest(raw,'rest'))
        self.assertEqual(self.sync.status['received'],1)
        self.assertEqual(len(await self.sync.recent()),1)
        self.assertEqual(self.sync.follow_queue.qsize(),1)
        worker=asyncio.create_task(self.sync.follow_worker())
        await asyncio.wait_for(self.sync.follow_queue.join(),1)
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
        self.monitor.ingest_realtime.assert_awaited_once()
        restarted=MarketSync(self.store,self.client,self.monitor,self.desk)
        self.assertFalse(await restarted.ingest(raw,'rest'))

    async def test_resolution_requires_final_winner_not_just_closed(self):
        response=Mock()
        response.json.return_value={'condition_id':'condition-1','closed':True,'tokens':[
            {'token_id':'1','winner':False},{'token_id':'2','winner':False}]}
        self.client._request_with_retry=AsyncMock(return_value=response)
        self.client.get_market_by_condition_id=AsyncMock(return_value={})
        self.assertIsNone(await self.client.get_market_resolution('condition-1'))
        response.json.return_value['tokens'][1]['winner']=True
        result=await self.client.get_market_resolution('condition-1')
        self.assertEqual(result['payouts'],{'1':0.,'2':1.})

    async def test_gamma_split_requires_resolved_status(self):
        response=Mock()
        response.json.return_value={'condition_id':'condition-1','closed':True,'tokens':[]}
        self.client._request_with_retry=AsyncMock(return_value=response)
        market={'closed':True,'clobTokenIds':'["1","2"]','outcomePrices':'["0.5","0.5"]'}
        self.client.get_market_by_condition_id=AsyncMock(return_value=market)
        self.assertIsNone(await self.client.get_market_resolution('condition-1'))
        market['umaResolutionStatus']='resolved'
        self.assertEqual((await self.client.get_market_resolution('condition-1'))['payouts'],{'1':.5,'2':.5})

    async def test_full_book_and_batched_delta(self):
        ws=PolymarketWSClient(Settings())
        ws._process_event({'event_type':'book','asset_id':'123','market':'c','bids':[{'price':'.4','size':'10'},{'price':'.45','size':'10'}],
            'asks':[{'price':'.6','size':'10'},{'price':'.5','size':'10'}]})
        self.assertEqual(ws._price_cache['123']['best_bid'],.45)
        self.assertEqual(ws._price_cache['123']['best_ask'],.5)
        ws._process_event({'event_type':'price_change','price_changes':[{'asset_id':'123','side':'BUY','price':'.45','size':'0'}]})
        book=ws.get_cached_book('123')
        self.assertEqual(book['bids'],[{'price':'.4','size':'10'}])
        ws._price_cache['123']['book_timestamp']=0
        self.assertIsNone(ws.get_cached_book('123'))
