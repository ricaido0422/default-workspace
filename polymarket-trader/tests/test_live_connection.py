import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Settings
from backend.core.order_executor import OrderExecutorManager
from backend.models.schemas import ExecutionRecord, FollowSignal, MarketRef, TradeEvent, TradeSide
from backend.store.state import StateStore


class LiveConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name)/'live.db'))
        await self.store.connect()
        self.client = AsyncMock()
        self.client._request_with_retry.return_value = MagicMock(status_code=200, text='"OK"')
        self.settings = Settings(polymarket={'api_key':'test-key','api_secret':'test-secret',
            'api_passphrase':'test-pass','private_key':'1'*64})
        self.manager = OrderExecutorManager(self.settings,self.store,self.client)
        self.sdk = MagicMock()
        self.sdk.get_balance_allowance.return_value = {'balance':'12000000','allowances':{'exchange':'99000000'}}
        self.manager._live._clob_client = self.sdk
        self.health = self.manager.live_connection

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def test_status_and_check_are_read_only_and_never_expose_secrets(self):
        self.assertEqual((await self.health.snapshot())['state'],'unchecked')
        result=await self.health.check()
        self.assertTrue(result['ready'])
        self.assertEqual(result['balance_usdc'],12)
        self.assertEqual(len(result['checks']),5)
        self.assertEqual(self.manager.mode.value,'PAPER')
        encoded=json.dumps(result)
        for secret in ['test-key','test-secret','test-pass','1'*64]:
            self.assertNotIn(secret,encoded)
        self.sdk.create_order.assert_not_called()
        self.sdk.post_order.assert_not_called()

    async def test_zero_balance_missing_allowance_and_bad_response_block(self):
        for value in [{'balance':'0','allowances':{'exchange':'99'}},
                      {'balance':'12000000','allowances':{'exchange':'0'}},
                      {'balance':'NaN','allowances':{'exchange':'99'}}, {}]:
            self.sdk.get_balance_allowance.return_value=value
            self.assertFalse((await self.health.check())['ready'])

    async def test_network_failure_does_not_call_authenticated_sdk(self):
        self.client._request_with_retry.side_effect=TimeoutError('must-not-echo-secret')
        result=await self.health.check()
        self.assertFalse(result['ready'])
        self.assertNotIn('must-not-echo-secret',json.dumps(result))
        self.sdk.get_balance_allowance.assert_not_called()

    async def test_pending_orders_stale_check_and_config_change_invalidate_readiness(self):
        self.assertTrue((await self.health.check())['ready'])
        self.health.result['checked_at']=(datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat()
        self.assertEqual((await self.health.snapshot())['state'],'stale')
        await self.health.check()
        await self.store.save_execution(ExecutionRecord(signal_id='uncertain',mode='LIVE',status='PENDING'))
        result=await self.health.snapshot()
        self.assertEqual(result['state'],'pending_orders')
        self.assertFalse(result['ready'])
        self.health.invalidate()
        self.assertIsNone((await self.health.snapshot())['checked_at'])

    async def test_stop_during_connection_check_cannot_rearm_stale_result(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def wait_network(*args,**kwargs):
            entered.set();await release.wait()
            return MagicMock(status_code=200,text='OK')
        self.client._request_with_retry.side_effect=wait_network
        task=asyncio.create_task(self.health.check())
        await entered.wait()
        self.health.invalidate();self.manager.set_mode('paper')
        release.set()
        result=await task
        self.assertFalse(result['ready'])
        self.assertIsNone(result['checked_at'])

    async def test_stop_after_signing_never_posts_live_order(self):
        self.manager.set_mode('live')
        self.sdk.get_tick_size.return_value='0.01'
        self.sdk.get_neg_risk.return_value=False
        self.sdk.create_order.side_effect=lambda *args: self.manager.set_mode('paper') or {'signed':True}
        trade=TradeEvent(wallet='source',condition_id='c',token_id='12345',side=TradeSide.BUY,price=.5,size=10)
        signal=FollowSignal(source_trade=trade,pipeline_id='test',market=MarketRef(condition_id='c',token_id='12345'),
            signal_id='stopped-before-post',side=TradeSide.BUY,price=.5,size=10)
        result=await self.manager._live.execute(signal)
        self.assertEqual(result.status.value,'FAILED')
        self.sdk.post_order.assert_not_called()
