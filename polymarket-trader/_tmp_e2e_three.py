# -*- coding: utf-8 -*-
"""三项需求的端到端验证：
1. 订单金额范围（monitor_min_amount / monitor_max_amount）
2. 交易流买卖方向统计（/api/trades/stats）
3. 目标钱包真实持仓（/api/wallets/positions + 归一化）
"""
import sys
import asyncio
sys.path.insert(0, '.')
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
import uuid

from backend.main import create_app
from fastapi.testclient import TestClient
from backend.models.schemas import TradeEvent, TradeSide
from backend.config import get_settings
from backend.api import routes as R

results = []


def chk(cond, desc):
    results.append((bool(cond), desc))
    print(('PASS' if cond else 'FAIL'), desc)


def mk(wallet, side, price, size):
    return TradeEvent(
        wallet=wallet, condition_id='c', token_id='tk', side=TradeSide(side),
        price=price, size=size, timestamp=datetime.now(timezone.utc),
        tx_hash='0x' + uuid.uuid4().hex[:16],
    )


MOCK_POSITIONS = [
    {"title": "BTC 100k", "slug": "btc-100k", "conditionId": "0xa", "outcome": "Yes",
     "size": 1500.0, "avgPrice": 0.42, "curPrice": 0.58, "currentValue": 870.0,
     "initialValue": 630.0, "cashPnl": 240.0, "percentPnl": 38.1, "redeemable": False},
    {"title": "ETH ETF", "slug": "eth-etf", "conditionId": "0xb", "outcome": "No",
     "size": 300.0, "avgPrice": 0.65, "curPrice": 0.40, "currentValue": 120.0,
     "initialValue": 195.0, "cashPnl": -75.0, "percentPnl": -38.5, "redeemable": True},
]


async def main():
    app = create_app()
    with TestClient(app) as c:
        print('\n--- 需求 3：订单金额范围 ---')
        r = c.put('/api/config', json={'monitor_min_amount': 250.0, 'monitor_max_amount': 8000.0})
        chk(r.status_code == 200, 'PUT /api/config 保存金额范围 200')
        cfg = c.get('/api/config').json()
        chk(cfg['monitor']['min_amount'] == 250.0 and cfg['monitor']['max_amount'] == 8000.0,
            'GET /api/config 回读金额范围')
        mon = R._wallet_monitor
        t_in = mk('0xw', 'BUY', 0.5, 1000)     # 500 in [250, 8000]
        t_low = mk('0xw', 'BUY', 0.5, 100)     # 50 < 250
        t_high = mk('0xw', 'BUY', 0.5, 20000)  # 10000 > 8000
        chk(mon.in_amount_range(t_in) is True, '区间内交易通过')
        chk(mon.in_amount_range(t_low) is False, '低于下限被过滤')
        chk(mon.in_amount_range(t_high) is False, '高于上限被过滤')

        print('\n--- 需求 1：买卖方向统计 ---')
        # 先清掉历史注入的同钱包测试行，保证计数从零开始
        from backend.store.state import StateStore
        import sqlite3
        con = sqlite3.connect(get_settings().db_path)
        con.execute("DELETE FROM trades WHERE wallet = ?", ("0xdir",))
        con.commit()
        con.close()
        for side, price, size in [('BUY', 0.4, 250.0), ('SELL', 0.6, 100.0), ('BUY', 0.5, 400.0)]:
            rr = c.post('/api/simulate/trade', json={
                'wallet': '0xdir', 'market_title': 'dir-test', 'market_slug': 'dir-test',
                'side': side, 'price': price, 'size': size})
            chk(rr.status_code == 200, '注入 %s 交易 200' % side)
        s = c.get('/api/trades/stats?wallet=0xdir').json()
        chk(s['buy_count'] == 2 and s['sell_count'] == 1, '买卖计数正确 (%d buy / %d sell)' % (s['buy_count'], s['sell_count']))
        chk(abs(s['buy_amount'] - 300.0) < 0.01, '买入总额 300 (0.4*250 + 0.5*400)')
        chk(abs(s['sell_amount'] - 60.0) < 0.01, '卖出总额 60 (0.6*100)')
        chk(abs(s['avg_buy_amount'] - 150.0) < 0.01, '买入均额 150')

        print('\n--- 需求 2：目标钱包真实持仓 ---')
        client = R.get_polymarket_client()
        client.get_wallet_positions = AsyncMock(return_value=MOCK_POSITIONS)
        p = c.get('/api/wallets/positions?wallet=0x5268527977f700f9bf9b6d5cd843859e4e70135d&status=OPEN').json()
        chk(p['count'] == 2, '返回 2 条持仓')
        chk(abs(p['total_value'] - 990.0) < 0.01, '持仓总市值 990 (870+120)')
        chk(abs(p['total_pnl'] - 165.0) < 0.01, '累计盈亏 165 (240-75)')
        first = p['positions'][0]
        chk(first['market_title'] == 'BTC 100k', 'camelCase 字段已归一化 (title->market_title)')
        chk(first['avg_price'] == 0.42 and first['cur_price'] == 0.58, 'avgPrice/curPrice -> avg_price/cur_price')
        chk(first['cash_pnl'] == 240.0, 'cashPnl -> cash_pnl')
        chk(p['positions'][1]['redeemable'] is True, 'redeemable 标记保留')

        print('\n--- 清理 ---')
        con = sqlite3.connect(get_settings().db_path)
        con.execute("DELETE FROM trades WHERE wallet = ?", ("0xdir",))
        con.commit()
        con.close()
        c.put('/api/config', json={'monitor_min_amount': 0.0, 'monitor_max_amount': 0.0})
        cfg2 = c.get('/api/config').json()
        chk(cfg2['monitor']['min_amount'] == 0.0 and cfg2['monitor']['max_amount'] == 0.0, '金额范围已恢复不限制')

    ok = sum(1 for v, _ in results if v)
    print('\n=== 总计 %d/%d PASS ===' % (ok, len(results)))
    print('=== 结论: %s ===' % ('全部通过 ✅' if ok == len(results) else '存在失败 ❌'))


asyncio.run(main())
