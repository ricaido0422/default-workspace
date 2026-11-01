"""无真实资金：实际 DPAPI、EIP712 签名验证与跨账户执行边界。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eth_account import Account
from eth_account.messages import encode_typed_data
from backend.accounts.contracts import AccountProfile, OrderIntent, Submission, utc
from backend.accounts.service import AccountService
from backend.accounts.coordinator import AccountCoordinator
from backend.accounts.browser import BrowserSigning
from backend.config import Settings
from backend.core.order_executor import OrderExecutorManager
from backend.models.schemas import FollowSignal, MarketRef, TradeEvent, TradeSide
from backend.store.state import StateStore


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(str(Path(self.tmp.name)/'accounts.db'))
        await self.store.connect()
        self.manager = OrderExecutorManager(Settings(), self.store, AsyncMock())
        self.manager.accounts = self.s = AccountService(self.manager)
        await self.s.initialize()
        self.manager.account_coordinator = self.c = AccountCoordinator(self.s)
        self.manager.browser_signing = self.browser = BrowserSigning(self.s, self.c)
        self.wallet = Account.from_key('1'*64)

    async def asyncTearDown(self):
        await self.store.close()
        self.tmp.cleanup()

    async def add(self, kind='magic_hmac', id='wallet2'):
        p = AccountProfile(id=id, name='测试账户', kind=kind, address=self.wallet.address,
                           relay_url='http://127.0.0.1:9000' if kind=='magic_oauth' else '')
        secrets = {'oauth_token': 'secret-token'} if kind=='magic_oauth' else {'api_key': 'key', 'api_secret': 'c2VjcmV0', 'api_passphrase': 'pass'}
        if kind=='magic_hmac':
            secrets['private_key'] = '1'*64
        await self.s.save(p, secrets)
        await self.s.select(id)
        return self.s.adapter()

    def signal(self, id='intent-1'):
        return FollowSignal(signal_id=id, account_id=self.s.active_id, pipeline_id='manual',
            source_trade=TradeEvent(wallet=self.wallet.address, token_id='123456789012345678901234567890', condition_id='m', side='BUY', price=.5, size=10),
            market=MarketRef(condition_id='m', token_id='123456789012345678901234567890'), side='BUY', price=.5, size=10)

    def sdk(self, adapter):
        sdk = MagicMock()
        sdk.get_balance_allowance.return_value = {'balance': '100000000', 'allowances': {'exchange': '1000000000'}}
        sdk.get_tick_size.return_value = '.01'
        sdk.get_neg_risk.return_value = False
        sdk.get_fee_rate_bps.return_value = 0
        sdk.get_book_context.return_value = {'asset_id':'123456789012345678901234567890', 'tick_size':'.01', 'min_order_size':'1', 'neg_risk':False}
        sdk.post_order.return_value = {'success': True, 'orderID': 'order-1'}
        adapter.engine._clob_client = sdk
        return sdk

    async def test_vault_encrypted_restart_and_no_secret_in_public(self):
        await self.add()
        raw = self.s.vault.path.read_bytes()
        self.assertNotIn(b'111111111111111111', raw)
        self.assertNotIn(b'c2VjcmV0', raw)
        self.assertNotIn('c2VjcmV0', json.dumps(self.s.public()))
        fresh = AccountService(self.manager)
        await fresh.initialize()
        self.assertEqual(fresh.active_id, 'wallet2')
        self.assertEqual(fresh.vault.get('wallet2')['private_key'], '1'*64)

    async def test_switch_forbidden_while_live_and_health_invalidated(self):
        await self.add()
        self.manager.live_connection.result = {'ready': True}
        self.manager.set_mode('live')
        with self.assertRaises(ValueError):
            await self.s.select('legacy')
        self.manager.set_mode('paper')
        await self.s.select('legacy')
        self.assertIsNone(self.manager.live_connection.result)
        self.assertEqual(self.manager.settings.polymarket.private_key, '')

    async def test_okx_never_accepts_private_key(self):
        p = AccountProfile(id='okx1', name='OKX', kind='okx', address=self.wallet.address)
        with self.assertRaises(ValueError):
            await self.s.save(p, {'private_key':'1'*64})

    async def test_okx_real_sdk_l2_balance_needs_no_private_key(self):
        a = await self.add('okx')
        a.init_client()
        a.online_until = utc()+timedelta(seconds=30)
        with patch('py_clob_client.client.get', return_value={'balance':'10000000','allowances':{'exchange':'100000000'}}) as request:
            result = await a.check_connection()
        self.assertTrue(result.ready)
        self.assertFalse(hasattr(a.engine._clob_client.signer, 'private_key'))
        self.assertEqual(request.call_args.kwargs['headers']['POLY_ADDRESS'], self.wallet.address)
        self.assertIn('signature_type=0', request.call_args.args[0])

    async def test_old_account_signal_cannot_execute_after_switch(self):
        await self.add()
        signal = self.signal()
        await self.s.select('legacy')
        self.manager.set_mode('live')
        record = await self.manager.execute(signal)
        self.assertEqual(record.status.value, 'FAILED')
        self.assertEqual(record.account_id, 'wallet2')

    async def test_okx_eip712_large_token_sign_and_one_post(self):
        adapter = await self.add('okx')
        sdk = self.sdk(adapter)
        adapter.online_until = utc()+timedelta(seconds=30)
        self.manager.set_mode('live')
        signal = self.signal()
        record = await self.c.execute(signal)
        self.assertEqual(record.status.value, 'PENDING')
        async with self.store._db.execute("SELECT * FROM wallet_jobs WHERE kind='order'") as cur:
            job = await cur.fetchone()
        self.assertIsNotNone(job)
        typed = json.loads(job['payload'])['typed_data']
        self.assertEqual(typed['message']['tokenId'], signal.market.token_id)
        sig = Account.sign_message(encode_typed_data(full_message=typed), self.wallet.key).signature.hex()
        await self.browser.complete(job['id'], '0x'+sig.removeprefix('0x'))
        self.assertEqual(sdk.post_order.call_count, 1)
        with self.assertRaises(ValueError):
            await self.browser.complete(job['id'], '0x'+sig.removeprefix('0x'))
        self.assertEqual(sdk.post_order.call_count, 1)

    async def test_wrong_signer_and_stop_do_not_submit(self):
        adapter = await self.add('okx')
        sdk = self.sdk(adapter)
        adapter.online_until = utc()+timedelta(seconds=30)
        self.manager.set_mode('live')
        await self.c.execute(self.signal())
        async with self.store._db.execute("SELECT * FROM wallet_jobs WHERE kind='order'") as cur:
            job = await cur.fetchone()
        typed = json.loads(job['payload'])['typed_data']
        bad = Account.sign_message(encode_typed_data(full_message=typed), '2'*64).signature.hex()
        with self.assertRaises(ValueError):
            await self.browser.complete(job['id'], '0x'+bad.removeprefix('0x'))
        self.manager.set_mode('paper')
        good = Account.sign_message(encode_typed_data(full_message=typed), self.wallet.key).signature.hex()
        with self.assertRaises(ValueError):
            await self.browser.complete(job['id'], '0x'+good.removeprefix('0x'))
        await self.c.tick()
        sdk.post_order.assert_not_called()
        self.assertEqual((await self.store.get_execution('intent-1')).status.value, 'FAILED')

    async def test_unknown_post_is_not_retried(self):
        adapter = await self.add('okx')
        sdk = self.sdk(adapter)
        sdk.post_order.side_effect = TimeoutError('never leak this secret')
        sdk.get_order.side_effect = TimeoutError()
        adapter.online_until = utc()+timedelta(seconds=30)
        self.manager.set_mode('live')
        signal = self.signal()
        await self.c.execute(signal)
        async with self.store._db.execute("SELECT * FROM wallet_jobs WHERE kind='order'") as cur:
            job = await cur.fetchone()
        sig = Account.sign_message(encode_typed_data(full_message=json.loads(job['payload'])['typed_data']), self.wallet.key).signature.hex()
        await self.browser.complete(job['id'], '0x'+sig.removeprefix('0x'))
        record = await self.store.get_execution(signal.signal_id)
        self.assertTrue(record.order_id.startswith('0x'))
        self.assertEqual(record.status.value, 'PENDING')
        await self.c.execute(signal)
        self.assertEqual(sdk.post_order.call_count, 1)

    async def test_late_strategy_guard_blocks_plugin_before_claiming_job(self):
        adapter=await self.add('okx');sdk=self.sdk(adapter)
        adapter.online_until=utc()+timedelta(seconds=30);self.manager.set_mode('live')
        await self.c.execute(self.signal())
        async with self.store._db.execute("SELECT * FROM wallet_jobs WHERE kind='order'") as cur:
            job=await cur.fetchone()
        async def paused(signal):raise ValueError('策略已暂停')
        self.manager.submission_guards.append(paused)
        sig=Account.sign_message(encode_typed_data(full_message=json.loads(job['payload'])['typed_data']),self.wallet.key).signature.hex()
        with self.assertRaises(ValueError):await self.browser.complete(job['id'],'0x'+sig.removeprefix('0x'))
        async with self.store._db.execute('SELECT state FROM wallet_jobs WHERE id=?',(job['id'],)) as cur:
            self.assertEqual((await cur.fetchone())[0],'WAITING')
        sdk.post_order.assert_not_called()

    async def test_oauth_requires_bridge_and_matching_address(self):
        a = await self.add('magic_oauth')
        a.request = AsyncMock(return_value={'protocol':'other', 'address':self.wallet.address, 'chain_id':137})
        self.assertFalse((await a.check_connection()).ready)
        a.request.return_value = {'protocol':'polymarket-account-bridge/1', 'address':self.wallet.address,
            'chain_id':137, 'authenticated':True, 'allowance_ready':True, 'balance_usdc':10, 'positions':[]}
        self.assertTrue((await a.check_connection()).ready)
        self.assertEqual((await a.read_account()).balance_usdc, 10)
        with self.assertRaises(ValueError):
            a.validated({'account_id':'other', 'intent_id':'x', 'state':'PENDING'}, 'x')

    async def test_hmac_closed_loop_uses_same_core_and_original_account_receipt(self):
        a = await self.add('magic_hmac')
        sdk = self.sdk(a)
        sdk.post_order.return_value = {'success':True, 'status':'live', 'orderID':'order-1'}
        self.manager.set_mode('live')
        signal = self.signal()
        r = await self.manager.execute(signal)
        self.assertEqual(r.status.value, 'PENDING')
        self.assertEqual(r.account_id, 'wallet2')
        self.manager.set_mode('paper')
        await self.s.select('legacy')
        sdk.get_order.return_value = {'status':'MATCHED', 'size_matched':'10', 'original_size':'10', 'associate_trades':['trade1']}
        sdk.get_trades.return_value = [{'id':'trade1','status':'CONFIRMED','taker_order_id':'order-1','size':'10','price':'.49'}]
        result = await self.c.reconcile(r)
        self.assertEqual(result.account_id, 'wallet2')
        self.assertEqual(result.status.value, 'FILLED')
        self.assertAlmostEqual(result.fill_price, .49)
        self.assertEqual(sdk.post_order.call_count, 1)

    async def test_okx_cancel_requires_separate_interactive_signature(self):
        a = await self.add('okx')
        sdk = self.sdk(a)
        a.online_until = utc()+timedelta(seconds=30)
        self.manager.set_mode('live')
        r = await self.c.execute(self.signal())
        r.order_id='order-1'
        await self.store.update_execution(r)
        await self.c.cancel(r)
        sdk.cancel.assert_not_called()
        async with self.store._db.execute("SELECT * FROM wallet_jobs WHERE kind='cancel'") as cur:
            job = await cur.fetchone()
        typed = json.loads(job['payload'])['typed_data']
        sig = Account.sign_message(encode_typed_data(full_message=typed), self.wallet.key).signature.hex()
        sdk.get_order.return_value = {'status':'CANCELED', 'size_matched':'0'}
        await self.browser.complete(job['id'], '0x'+sig.removeprefix('0x'))
        sdk.cancel.assert_called_once_with('order-1')
        self.assertEqual((await self.store.get_execution(r.signal_id)).status.value, 'CANCELLED')

    async def test_okx_offline_fails_without_order(self):
        a = await self.add('okx')
        sdk = self.sdk(a)
        self.manager.set_mode('live')
        record = await self.c.execute(self.signal())
        self.assertEqual(record.status.value, 'FAILED')
        sdk.post_order.assert_not_called()

    async def test_relay_url_rejects_embedded_secrets_and_remote_http(self):
        for url in ('http://example.com:9000', 'https://user:password@example.com', 'https://example.com?token=x'):
            with self.assertRaises(ValueError):
                AccountProfile(id='x', name='x', kind='magic_oauth', relay_url=url)

    async def test_current_protocol_signatures_and_amounts(self):
        from backend.accounts.clob_signing import build_typed_order, SignedOrder, order_hash, DOMAINS
        for protocol, neg, domain in ((None,False,'ctf'), (None,True,'neg_risk'), ('v2',False,'v2')):
            book = {'asset_id':'123','version':protocol,'neg_risk':neg,'tick_size':'.005','min_order_size':'1'}
            typed = build_typed_order(self.wallet.address, '', 0, '123', 'BUY', .523, 10.129, book)
            self.assertEqual(typed['domain']['version'], DOMAINS[domain][0])
            self.assertEqual(typed['domain']['verifyingContract'], DOMAINS[domain][1])
            self.assertEqual(typed['message']['makerAmount'], '5262400')
            self.assertEqual(typed['message']['takerAmount'], '10120000')
            self.assertNotIn('feeRateBps', typed['message'])
            signed = Account.sign_message(encode_typed_data(full_message=typed), self.wallet.key)
            self.assertEqual('0x'+signed.message_hash.hex().removeprefix('0x'), order_hash(typed))
            order = SignedOrder(typed, '0x'+signed.signature.hex().removeprefix('0x')).dict()
            self.assertEqual(order['side'], 'BUY')
            self.assertEqual(order['expiration'], '0')
        with self.assertRaises(ValueError):
            build_typed_order(self.wallet.address, '', 0, '123', 'BUY', .5, 10,
                {'asset_id':'123','version':'future','tick_size':'.01'})
