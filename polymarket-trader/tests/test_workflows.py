"""隔离数据库和配置，验证可操作流程：Python312/python.exe -m unittest discover -s tests -v。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'config.yaml'
        self.config.write_text('monitor:\n  ws_enabled: false\nrealtime:\n  enabled: false\n', encoding='utf-8')
        self.env = patch.dict(os.environ, {
            'CONFIG_FILE': str(self.config),
            'DB_PATH': str(Path(self.temp.name) / 'test.db'),
            'EXECUTOR_MODE': 'paper',
        })
        self.env.start()
        from backend.config import get_settings
        get_settings.cache_clear()
        from backend.main import create_app
        self.app = create_app()
        self.activity = patch('backend.core.polymarket_client.PolymarketClient.get_wallet_activity',
                              new=AsyncMock(return_value={'trades': [], 'next_cursor': None}))
        self.activity.start()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.activity.stop()
        self.env.stop()
        from backend.config import get_settings
        get_settings.cache_clear()
        self.temp.cleanup()

    def test_settings_validation_and_persistence(self):
        c = self.client
        self.assertEqual(c.put('/api/config', json={'monitor_interval': 0}).status_code, 422)
        self.assertEqual(c.put('/api/config', json={'monitor_min_amount': 20, 'monitor_max_amount': 10}).status_code, 422)
        self.assertEqual(c.put('/api/config', json={'risk': {'max_trade_amount': -1}}).status_code, 422)
        risk = {'max_trade_amount': 25, 'max_position_per_market': 100, 'max_daily_trades': 5}
        self.assertEqual(c.put('/api/config', json={'monitor_interval': 1, 'risk': risk}).status_code, 200)
        self.assertEqual(c.get('/api/config').json()['risk'], risk)
        self.assertEqual(self.app.state.executor_manager.risk.settings.risk.max_trade_amount, 25)
        import yaml
        saved = yaml.safe_load(self.config.read_text(encoding='utf-8'))
        self.assertEqual(saved['risk'], risk)
        self.assertEqual(saved['monitor']['interval'], 1)
        c.put('/api/config', json={'monitor_min_amount': 20})
        response = c.post('/api/simulate/trade', json={'wallet': '0x' + '1' * 40, 'price': 0.5, 'size': 10})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(c.get('/api/trades').json(), [])
        c.put('/api/config', json={'proxy': {'http': 'http://127.0.0.1:7890'}})
        c.put('/api/config', json={'proxy': {'http': '', 'https': ''}})
        self.assertEqual(c.get('/api/config').json()['proxy'], {'http': '', 'https': ''})

    def test_wallet_pipeline_and_paper_execution(self):
        c = self.client
        c.put('/api/desk/policy', json={'workflow': 'auto'})
        wallet = '0x' + '1' * 40
        self.assertEqual(c.post('/api/wallets', json={'address': wallet, 'alias': '回归测试'}).status_code, 200)
        self.assertIn(wallet, self.app.state.monitor.get_watched_wallets())
        c.post(f'/api/wallets/{wallet}/toggle')
        self.assertNotIn(wallet, self.app.state.monitor.get_watched_wallets())
        c.post(f'/api/wallets/{wallet}/toggle')
        c.put('/api/config', json={'monitor_interval': 3})
        self.assertEqual(self.app.state.monitor.get_wallet_state(wallet).interval, 3)
        pipeline = c.post('/api/pipelines', json={'name': '测试管道'}).json()
        strategy = {'id': 'mirror', 'type': 'MIRROR', 'category': 'EXECUTION', 'name': '镜像'}
        self.assertEqual(c.put('/api/pipelines/' + pipeline['id'], json={'strategies': [strategy]}).status_code, 200)
        with patch.object(self.app.state.client, 'get_best_price', new=AsyncMock(return_value=0.5)):
            response = c.post('/api/simulate/trade', json={'wallet': wallet, 'price': 0.5, 'size': 10})
        self.assertEqual(response.status_code, 200, response.text)
        records = c.get('/api/executions').json()
        self.assertEqual(records[0]['status'], 'FILLED')
        self.assertEqual(records[0]['mode'], 'PAPER')
        self.assertEqual(c.get('/api/stats').json()['today_signals'], 1)
        from backend.models.schemas import TradeEvent
        trade = TradeEvent(**response.json())
        with patch.object(self.app.state.client, 'get_wallet_activity', new=AsyncMock(return_value={'trades': [trade]})):
            replay = self.client.portal.call(self.app.state.monitor._check_wallet, wallet)
        self.assertEqual(replay, [])
        self.assertEqual(len(c.get('/api/trades').json()), 1)
        self.assertEqual(c.delete('/api/pipelines/' + pipeline['id']).status_code, 200)
        self.assertEqual(c.delete('/api/wallets/' + wallet).status_code, 200)

    def test_leaderboard_null_response(self):
        from backend.core.leaderboard import LeaderboardClient
        with patch.object(LeaderboardClient, '_request_with_retry', new=AsyncMock(return_value={'data': None})):
            response = self.client.get('/api/leaderboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_wallet_identity_and_credentials(self):
        c = self.client
        wallet = '0x' + '2' * 40
        c.post('/api/wallets', json={'address': wallet})
        profile = {'alias': '体育观察', 'username': 'Trader', 'specialties': ['体育', '网球'], 'notes': '先观察样本'}
        self.assertEqual(c.put(f'/api/wallets/{wallet}/profile', json=profile).status_code, 200)
        saved = c.get('/api/wallets').json()[0]
        self.assertEqual(saved['specialties'], ['体育', '网球'])
        self.assertEqual(saved['username'], 'Trader')
        self.assertEqual(c.post('/api/executor/credentials', json={'api_key': 'fake'}).status_code, 200)
        c.post('/api/executor/credentials', json={'signature_type': 2, 'funder': wallet})
        from backend.config import get_settings
        self.assertEqual(get_settings().polymarket.api_key, 'fake')
        self.assertEqual(get_settings().polymarket.signature_type, 2)
        self.assertNotIn('private_key', c.get('/api/config').json()['polymarket'])

    def test_multi_account_port_configuration_and_secret_redaction(self):
        c = self.client
        p = {'id':'relay-test','name':'中继测试','kind':'magic_oauth','address':'0x'+'2'*40,
             'relay_url':'http://127.0.0.1:9901'}
        response = c.put('/api/accounts', json={'profile':p,'secrets':{'oauth_token':'test-only-secret'}})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('test-only-secret', response.text)
        self.assertEqual(c.post('/api/accounts/relay-test/select').status_code, 200)
        state = c.get('/api/executor/status').json()
        self.assertEqual(state['configuration']['account_id'], 'relay-test')
        self.assertEqual(state['state'], 'unchecked')
        self.assertNotIn('test-only-secret', c.get('/api/accounts').text)
        self.assertEqual(c.get('/api/accounts', headers={'Origin':'https://example.com'}).status_code, 403)
        self.assertEqual(c.post('/api/accounts/legacy/select').status_code, 200)
        self.assertEqual(c.post('/api/executor/mode', json={'mode': 'live'}).status_code, 400)
        self.assertEqual(c.get('/api/config').json()['executor']['mode'], 'paper')

    def test_cross_origin_writes_rejected(self):
        response = self.client.put('/api/config', json={'monitor_interval': 1}, headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(response.status_code, 403)

    def test_smart_strategy_routes_estimate_stop_and_encrypted_model(self):
        c=self.client
        state=c.get('/api/smart/state').json()
        self.assertEqual(len(state['templates']),5)
        self.assertIsNone(state['templates']['momentum']['estimate']['history']['win_rate'])
        config={'id':'smart_'+'a'*16,'name':'自动模拟测试','kind':'momentum','tokens':['12345'],
                'enabled':True,'workflow':'auto','mode':'PAPER'}
        r=c.put('/api/smart/strategies/'+config['id'],json=config)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(c.post('/api/smart/estimate',json=config).status_code,200)
        self.assertEqual(c.post('/api/smart/stop').status_code,200)
        self.assertFalse(c.get('/api/smart/state').json()['strategies'][0]['config']['enabled'])
        self.assertEqual(c.put('/api/smart/strategies/'+config['id'],json={**config,'tokens':[]}).status_code,422)
        model={'config':{'enabled':True,'base_url':'http://127.0.0.1:9191/v1','model':'local-test'},'api_key':'private-model-test'}
        r=c.put('/api/smart/model',json=model)
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('private-model-test',r.text)
        self.assertTrue(c.get('/api/smart/state').json()['model']['has_api_key'])
        model['config']['base_url']='http://127.0.0.1:9292/v1';model.pop('api_key')
        self.assertFalse(c.put('/api/smart/model',json=model).json()['has_api_key'])
        self.assertEqual(c.get('/api/smart/state',headers={'Origin':'https://example.com'}).status_code,403)

    def test_desk_manual_routes_prepare_preview_and_approve(self):
        c = self.client
        book = {'market': 'c', 'asks': [{'price': '.5', 'size': '1000'}], 'bids': [{'price': '.49', 'size': '1000'}], 'min_order_size': '1'}
        req = {'token_id': '12345', 'condition_id': 'c', 'side': 'BUY', 'price': .5, 'size': 10, 'client_order_id': 'api-test-order'}
        with patch.object(self.app.state.client, 'get_orderbook', new=AsyncMock(return_value=book)), patch.object(self.app.state.client, 'get_market_by_condition_id', new=AsyncMock(return_value={'events': [{'id': 'event'}]})):
            self.assertEqual(c.post('/api/desk/preview', json=req).status_code, 200)
            response = c.post('/api/desk/orders', json=req)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(c.get('/api/executions').json(), [])
            approve = c.post('/api/desk/queue/manual-api-test-order/approve')
            self.assertEqual(approve.status_code, 200, approve.text)
            self.assertEqual(approve.json()['status'], 'FILLED')
        state = c.get('/api/desk/state').json()
        self.assertEqual(len(state['summary']['positions']), 1)
        self.assertEqual(state['summary']['mode'], 'PAPER')

    def test_copy_rule_api_validation_mode_binding_and_discovery(self):
        from backend.core.copy_rules import CopyRule
        from backend.models.schemas import LeaderboardEntry
        c=self.client
        rule=CopyRule(id='copy_'+'1'*32,wallet='0x'+'2'*40,name='Quick copy').model_dump()
        path='/api/desk/copy-rules/'+rule['id']
        self.assertEqual(c.put(path,json={**rule,'min_price':.9,'max_price':.1}).status_code,422)
        self.assertEqual(c.put(path,json={**rule,'mode':'LIVE'}).status_code,409)
        self.assertEqual(c.put(path,json=rule).status_code,200)
        self.assertEqual(len(c.get('/api/desk/copy-rules').json()['rules']),1)
        self.assertEqual(c.get('/api/wallets').json()[0]['address'],rule['wallet'])
        self.assertEqual(c.put(path,json={**rule,'mode':'LIVE'}).status_code,409)
        self.assertEqual(c.put('/api/desk/copy-rules/copy_'+'3'*32,json={**rule,'id':'copy_'+'3'*32}).status_code,409)
        self.assertEqual(c.put(path,json={**rule,'enabled':False}).status_code,200)
        with patch.object(self.app.state.leaderboard_client,'fetch_leaderboard',new=AsyncMock(return_value=[LeaderboardEntry(rank=1,wallet=rule['wallet'],username='Example',pnl=12,volume=30)])):
            response=c.get('/api/desk/discovery?window=week')
            self.assertEqual(response.status_code,200)
            row=response.json()['rows'][0]
            self.assertEqual(row['username'],'Example')
            self.assertEqual(row['pnl'],12)
            self.assertTrue(row['watched'])
        self.assertEqual(c.get('/api/desk/discovery?window=invalid').status_code,422)
        self.assertEqual(c.get('/js/panels/copy-hub.js').status_code,200)

    def test_live_status_configuration_and_fresh_start_gate(self):
        c=self.client
        status=c.get('/api/executor/status')
        self.assertEqual(status.status_code,200)
        self.assertFalse(status.json()['ready'])
        self.assertEqual(status.json()['mode'],'PAPER')
        response=c.post('/api/executor/mode',json={'mode':'live'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(c.post('/api/executor/mode',json={'mode':'paper'}).json()['status']['mode'],'PAPER')
        with patch.object(self.app.state.client,'_request_with_retry',new=AsyncMock(side_effect=TimeoutError('test-secret'))):
            checked=c.post('/api/executor/check')
            self.assertEqual(checked.status_code,200)
            self.assertFalse(checked.json()['ready'])
            self.assertNotIn('test-secret',checked.text)
        self.assertEqual(c.get('/js/live-controls.js').status_code,200)


if __name__ == '__main__':
    unittest.main()
