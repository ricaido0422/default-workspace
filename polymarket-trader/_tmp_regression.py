# -*- coding: utf-8 -*-
"""临时：全量回归冒烟（所有 GET 端点 + 静态资源 + 关键新端点）。"""
import sys
sys.path.insert(0, '.')
from backend.main import create_app
from fastapi.testclient import TestClient

app = create_app()
GETS = [
    '/api/health', '/api/stats', '/api/wallets', '/api/trades', '/api/executions',
    '/api/pipelines', '/api/strategies/available', '/api/config',
    '/api/ws/status', '/api/notifications/config', '/api/notifications/log',
    '/api/backtest/strategies', '/api/analytics/summary', '/api/analytics/wallets',
    '/api/analytics/strategies', '/api/analytics/markets', '/api/analytics/daily',
    '/api/portfolio', '/api/portfolio/exposure', '/api/portfolio/weights',
    '/api/portfolio/positions', '/api/wallets/positions', '/api/simulate/status',
    '/api/trades/stats',
]
ok = fail = 0
with TestClient(app) as c:
    for ep in GETS:
        try:
            r = c.get(ep)
            if r.status_code == 200:
                ok += 1
                print('PASS', ep, 200)
            else:
                fail += 1
                print('FAIL', ep, r.status_code)
        except Exception as e:
            fail += 1
            print('FAIL', ep, 'EXC', e)
    for s in ['/', '/js/app.js', '/js/api.js', '/js/panels/trades.js',
              '/js/panels/wallet-positions.js', '/js/panels/positions.js', '/css/style.css']:
        try:
            r = c.get(s)
            if r.status_code == 200:
                ok += 1
                print('PASS', s, 200)
            else:
                fail += 1
                print('FAIL', s, r.status_code)
        except Exception as e:
            fail += 1
            print('FAIL', s, 'EXC', e)
print('=== 回归冒烟 %d/%d PASS ===' % (ok, ok + fail))
