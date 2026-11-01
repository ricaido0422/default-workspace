"""Polymarket 实时成交同步：RTDS + REST 补拉 + 最终结果同步。"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

import websockets

from backend.api.events import get_event_bus


def utc():
    return datetime.now(timezone.utc).isoformat()


class MarketSync:
    RTDS_URL = 'wss://ws-live-data.polymarket.com'

    def __init__(self, store, client, monitor, desk, market_ws=None):
        self.store, self.client, self.monitor, self.desk = store, client, monitor, desk
        self.market_ws = market_ws
        self.tasks = []
        self.follow_queue = asyncio.Queue(maxsize=2000)
        self.wake_settlement = asyncio.Event()
        self.status = {'enabled': client.settings.realtime.enabled, 'rtds_connected': False,
            'last_ws_message': None, 'last_trade_at': None, 'last_rest_at': None,
            'last_resolution_at': None, 'ws_error': '', 'rest_error': '', 'settlement_error': '',
            'received': 0, 'dropped_follow_events': 0, 'reconnects': 0}

    async def start(self):
        if not self.status['enabled']:
            return
        self.tasks = [asyncio.create_task(f()) for f in
            (self.ws_worker, self.rest_worker, self.follow_worker, self.resolution_worker, self.status_worker)]
        if self.market_ws:
            self.market_ws.on_event(self.market_event)

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        self.status['rtds_connected'] = False

    def market_event(self, event):
        if event.get('event_type') == 'market_resolved':
            # WS 只触发再次查询最终结果，不凭单条通知直接入账。
            self.wake_settlement.set()

    async def ingest(self, raw, source):
        wallet = str(raw.get('proxyWallet') or raw.get('proxy_wallet') or raw.get('wallet') or '').lower()
        trade = self.client._parse_trade(raw, wallet)
        if not trade or not trade.token_id:
            return False
        key_data = [trade.tx_hash, trade.token_id, wallet, trade.side.value,
                    format(trade.price, '.8f'), format(trade.size, '.6f'), trade.timestamp.isoformat()]
        key = hashlib.sha256(json.dumps(key_data).encode()).hexdigest()
        cursor = await self.store._db.execute('INSERT OR IGNORE INTO realtime_trades VALUES (?,?,?,?)',
            (key, trade.model_dump_json(), utc(), source))
        await self.store._db.commit()
        if not cursor.rowcount:
            return False
        self.status['received'] += 1
        self.status['last_trade_at'] = utc()
        await get_event_bus().publish_nowait('platform_trade', trade.model_dump(mode='json'))
        watched = {a.lower(): a for a in self.monitor.get_watched_wallets()}
        if wallet in watched:
            try:
                self.follow_queue.put_nowait(trade.model_copy(update={'wallet': watched[wallet]}))
            except asyncio.QueueFull:
                self.status['dropped_follow_events'] += 1
        if self.status['received'] % 500 == 0:
            await self.store._db.execute('DELETE FROM realtime_trades WHERE rowid NOT IN (SELECT rowid FROM realtime_trades ORDER BY received_at DESC LIMIT 10000)')
            await self.store._db.commit()
        return True

    async def handle_rtds(self, message):
        if not message or str(message).strip().upper() in ('PING', 'PONG'):
            return
        data = json.loads(message)
        self.status['last_ws_message'] = utc()
        if data.get('topic') == 'activity' and data.get('type') == 'trades' and isinstance(data.get('payload'), dict):
            await self.ingest(data['payload'], 'rtds')

    async def ws_worker(self):
        delay = 1
        while True:
            heartbeat = None
            try:
                proxy = self.client.settings.proxy.http or self.client.settings.proxy.https or None
                async with websockets.connect(self.RTDS_URL, proxy=proxy or True, open_timeout=20,
                    close_timeout=3, ping_interval=None, max_size=4*1024*1024) as ws:
                    await ws.send(json.dumps({'action': 'subscribe', 'subscriptions': [{'topic': 'activity', 'type': 'trades'}]}))
                    self.status.update(rtds_connected=True, ws_error='')
                    delay = 1
                    async def ping():
                        while True:
                            await asyncio.sleep(5)
                            await ws.send('PING')
                    heartbeat = asyncio.create_task(ping())
                    async for message in ws:
                        try:
                            await self.handle_rtds(message)
                        except (ValueError, TypeError, KeyError):
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status['ws_error'] = '实时成交连接中断：' + type(exc).__name__
                self.status['reconnects'] += 1
            finally:
                self.status['rtds_connected'] = False
                if heartbeat:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.sleep(delay)
            delay = min(delay*2, 30)

    async def rest_once(self):
        response = await self.client._request_with_retry('GET', 'https://data-api.polymarket.com/trades', params={'limit': 100})
        data = response.json()
        rows = data if isinstance(data, list) else data.get('data') or []
        for raw in reversed(rows):
            await self.ingest(raw, 'rest')
        self.status.update(last_rest_at=utc(), rest_error='')
        if self.market_ws:
            tokens = []  # 只订阅本工具持仓；全平台成交走 RTDS，避免不断累加无关市场订阅
            for mode in ('PAPER', 'LIVE'):
                tokens.extend(p['token_id'] for p in (await self.desk.summary(mode=mode))['positions'])
            await self.market_ws.subscribe(list(dict.fromkeys(t for t in tokens if t)))

    async def rest_worker(self):
        while True:
            try:
                await self.rest_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status['rest_error'] = '补拉失败：' + type(exc).__name__
            await asyncio.sleep(self.client.settings.realtime.rest_interval)

    async def follow_worker(self):
        while True:
            trade = await self.follow_queue.get()
            try:
                await self.monitor.ingest_realtime(trade)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status['rest_error'] = '监控钱包实时事件处理失败，钱包轮询继续补拉'
            finally:
                self.follow_queue.task_done()

    async def recent(self, limit=100):
        async with self.store._db.execute('SELECT payload,source FROM realtime_trades ORDER BY received_at DESC LIMIT ?', (limit,)) as cur:
            rows = await cur.fetchall()
        return [{**json.loads(r['payload']), 'data_source': r['source']} for r in rows]

    async def book(self, token):
        if self.market_ws and self.market_ws.connected:
            cached = self.market_ws.get_cached_book(token, max_age=3)
            if cached:
                if cached.get('min_order_size') is None:
                    metadata = await self.client.get_orderbook(token)
                    if not metadata or metadata.get('min_order_size') is None:
                        return {}
                    cached.update(min_order_size=metadata['min_order_size'], tick_size=metadata.get('tick_size'))
                return {**cached, '_source': 'polymarket_market_ws'}
        return {**await self.client.get_orderbook(token), '_source': 'polymarket_clob_rest'}

    async def mark(self, token):
        book = await self.book(token)
        bids = [float(x['price']) for x in book.get('bids', []) if float(x.get('size', 0)) > 0]
        return {'price': max(bids) if bids else None, 'source': book.get('_source', ''), 'checked_at': utc()}

    async def resolution_once(self):
        paper = (await self.desk.summary(mode='PAPER'))['positions']
        live = (await self.desk.summary(mode='LIVE'))['positions']
        conditions = {p['condition_id'] for p in paper + live if p['condition_id']}
        errors = []
        for condition in conditions:
            try:
                result = await self.client.get_market_resolution(condition)
            except Exception as exc:
                errors.append(type(exc).__name__)
                continue
            if result:
                await self.store._db.execute('INSERT OR REPLACE INTO market_resolutions VALUES (?,?,?)',
                    (condition, json.dumps(result), utc()))
                await self.store._db.commit()
                await self.desk.settle_paper(result)
        self.status.update(last_resolution_at=utc(), settlement_error=('部分市场最终结果暂不可用：'+', '.join(errors)) if errors else '')

    async def resolution_worker(self):
        while True:
            try:
                await self.resolution_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status['settlement_error'] = '结算结果同步失败，暂不入账：' + type(exc).__name__
            try:
                await asyncio.wait_for(self.wake_settlement.wait(), self.client.settings.realtime.settlement_interval)
            except asyncio.TimeoutError:
                pass
            self.wake_settlement.clear()

    async def status_worker(self):
        while True:
            await get_event_bus().publish_nowait('sync_status', dict(self.status))
            await asyncio.sleep(2)
