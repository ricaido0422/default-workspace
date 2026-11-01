"""真实 CLOB 执行；仅限价 FOK，提交超时不自动重试。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from backend.models.schemas import ExecutionMode, ExecutionRecord, ExecutionStatus, TradeSide


class LiveExecutor:
    def __init__(self, settings, store=None, client=None):
        self.settings, self.store, self.client = settings, store, client
        self._clob_client = None
        self._lock = asyncio.Lock()
        self.armed = settings.executor.mode == 'live'

    def reset(self):
        self._clob_client = None

    def _init_clob_client(self):
        from backend.accounts.clob_signing import CurrentClobClient as ClobClient
        from py_clob_client.clob_types import ApiCreds
        from py_clob_client.http_helpers import helpers
        import httpx
        c = self.settings.polymarket
        if not all((c.api_key, c.api_secret, c.api_passphrase, c.private_key)):
            raise ValueError('请完整配置 API Key、Secret、Passphrase 和签名私钥')
        if c.signature_type and not c.funder:
            raise ValueError('代理钱包签名必须填写资金钱包 Funder')
        from eth_account import Account
        from eth_utils import is_address
        if c.funder and not is_address(c.funder):
            raise ValueError('代理钱包 Funder 地址无效')
        if c.wallet_address and not is_address(c.wallet_address):
            raise ValueError('代理钱包地址无效')
        signer = Account.from_key(c.private_key).address.lower()
        if c.signature_type == 0 and c.funder and c.funder.lower() != signer:
            raise ValueError('代理钱包配置不匹配：EOA 的 Funder 应为空或等于签名钱包')
        if c.signature_type == 0 and c.wallet_address and c.wallet_address.lower() != signer:
            raise ValueError('代理钱包配置不匹配：EOA 钱包地址与签名私钥不一致')
        self._clob_client = ClobClient(
            'https://clob.polymarket.com', key=c.private_key, chain_id=137,
            creds=ApiCreds(c.api_key, c.api_secret, c.api_passphrase),
            signature_type=c.signature_type, funder=c.funder or None,
        )
        from backend.accounts.clob_signing import configure_transport
        configure_transport(self.settings)

    async def preflight(self):
        """只读检查：认证、余额与授权，不签订单、不发送交易。"""
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        if self._clob_client is None:
            await asyncio.to_thread(self._init_clob_client)
        result = await asyncio.to_thread(self._clob_client.get_balance_allowance,
                                        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        if not isinstance(result, dict) or 'balance' not in result:
            raise ValueError('无效的账户余额响应')
        balance = Decimal(str(result['balance'])) / Decimal(1_000_000)
        if not balance.is_finite() or balance < 0:
            raise ValueError('无效的账户余额响应')
        allowances = result.get('allowances') or {'default': result.get('allowance', '0')}
        values = [Decimal(str(v)) for v in allowances.values()]
        if any(not v.is_finite() or v < 0 for v in values):
            raise ValueError('无效的授权额度响应')
        approved = any(v > 0 for v in values)
        return {'authenticated': True, 'balance_usdc': float(balance), 'allowance_ready': approved,
                'ready': balance > 0 and approved, 'order_type': 'FOK',
                'signature_type': self.settings.polymarket.signature_type}

    async def token_balance(self, token_id):
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        if self._clob_client is None:
            await asyncio.to_thread(self._init_clob_client)
        result = await asyncio.to_thread(self._clob_client.get_balance_allowance,
            BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
        return float(Decimal(str(result.get('balance', 0))) / Decimal(1_000_000))

    async def execute(self, signal):
        async with self._lock:
            return await self._execute(signal)

    async def _execute(self, signal):
        existing = await self.store.get_execution(signal.signal_id) if self.store else None
        if existing:
            return existing
        record = ExecutionRecord(signal_id=signal.signal_id, account_id=signal.account_id, status=ExecutionStatus.FAILED,
                                 mode=ExecutionMode.LIVE)
        try:
            age = (datetime.now(timezone.utc) - signal.source_trade.timestamp).total_seconds()
            if age > self.settings.executor.max_signal_age_seconds or age < -10:
                raise ValueError('源交易已过期或时间异常，已拒绝追单')
            if self._clob_client is None:
                await asyncio.to_thread(self._init_clob_client)
            from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
            token = signal.market.token_id
            if not token.isdigit() or not 0 < signal.price < 1:
                raise ValueError('实盘标的或价格无效')
            if signal.side == TradeSide.SELL and await self.token_balance(token) + 1e-8 < signal.size:
                raise ValueError('实盘标的可卖份额不足')
            tick = str(await asyncio.to_thread(self._clob_client.get_tick_size, token))
            negative = await asyncio.to_thread(self._clob_client.get_neg_risk, token)
            rounding = ROUND_DOWN if signal.side == TradeSide.BUY else ROUND_UP
            price = (Decimal(str(signal.price)) / Decimal(tick)).to_integral_value(rounding=rounding) * Decimal(tick)
            size = Decimal(str(signal.size)).quantize(Decimal('.01'), rounding=ROUND_DOWN)
            if not Decimal('0') < price < Decimal('1') or size <= 0:
                raise ValueError('按最小价格和数量精度取整后订单无效')
            args = OrderArgs(token_id=token, price=float(price), size=float(size), side=signal.side.value)
            signed = await asyncio.to_thread(self._clob_client.create_order, args,
                                             PartialCreateOrderOptions(tick_size=tick, neg_risk=negative))
            if (datetime.now(timezone.utc)-signal.source_trade.timestamp).total_seconds() > self.settings.executor.max_signal_age_seconds:
                raise ValueError('源交易在签名前检查期间过期，未提交订单')
        except ValueError as exc:
            record.error = str(exc) if str(exc).startswith(('请完整', '代理钱包', '源交易', '实盘标的', '按最小')) else '签名或订单参数无效，请检查配置'
            if self.store:
                await self.store.save_execution(record)
            return record
        except Exception:
            record.error = '下单前检查失败，请检查网络、凭证、签名类型与资金钱包'
            if self.store:
                await self.store.save_execution(record)
            return record

        # 提交前先持久化，崩溃/断网不能被当作未下单而自动补单。
        record.status = ExecutionStatus.PENDING
        if isinstance(getattr(signed, 'order_hash', None), str):
            record.order_id = signed.order_hash
        record.error = '已准备提交，结果待核对；不会自动重试'
        if self.store:
            await self.store.save_execution(record)
        try:
            if getattr(self, 'submission_guard', None):
                await self.submission_guard(signal)
        except Exception:
            record.status = ExecutionStatus.FAILED
            record.error = '提交前策略或模式校验失败，订单未发送'
            if self.store:
                await self.store.update_execution(record)
            return record
        if not self.armed:
            record.status = ExecutionStatus.FAILED
            record.error = '实盘已停止，签名订单未提交'
            if self.store:
                await self.store.save_execution(record)
            return record
        try:
            response = await asyncio.to_thread(self._clob_client.post_order, signed, OrderType.FOK)
            if not isinstance(response, dict):
                raise ValueError('Invalid response')
            record.order_id = str(response.get('orderID') or response.get('order_id') or record.order_id or '')
            status = str(response.get('status', '')).lower()
            if response.get('success') is False:
                record.status = ExecutionStatus.FAILED
                record.error = '交易所拒绝订单，请检查余额、授权、价格与流动性'
            elif status == 'matched' and record.order_id:
                # 实际成交金额来自交易所，不能把信号价伪装成成交价。
                making, taking = Decimal(str(response.get('makingAmount', 0))), Decimal(str(response.get('takingAmount', 0)))
                shares, cash = (taking, making) if signal.side == TradeSide.BUY else (making, taking)
                if shares > 0 and cash > 0:
                    record.status = ExecutionStatus.FILLED
                    record.fill_size, record.fill_price = float(shares), float(cash / shares)
                    record.error = ''
                else:
                    record.error = '订单已匹配，成交明细待核对'
            else:
                record.error = '交易所已接收，最终结果待核对'
        except Exception as exc:
            if getattr(exc, 'status_code', None) in (400, 401, 403, 404, 422, 429):
                record.status = ExecutionStatus.FAILED
                record.error = '交易所拒绝提交，请检查认证、资金、授权、订单参数与限流情况'
            else:
                record.error = '提交结果未知，实盘已暂停；请核对交易所订单，禁止直接重试'
        if self.store:
            await self.store.update_execution(record)
        return record

    async def cancel(self, record):
        if not record.order_id:
            raise ValueError('缺少订单号，不能撤单')
        if self._clob_client is None:
            await asyncio.to_thread(self._init_clob_client)
        # 撤单响应不是未成交证明；仍查询成交数量和明细。
        await asyncio.to_thread(self._clob_client.cancel, record.order_id)
        return await self.reconcile(record)

    async def reconcile(self, record):
        if not record.order_id:
            raise ValueError('缺少订单号，需先在 Polymarket 核对；系统不会自动重发')
        if self._clob_client is None:
            await asyncio.to_thread(self._init_clob_client)
        result = await asyncio.to_thread(self._clob_client.get_order, record.order_id)
        status = str(result.get('status', '')).upper()
        matched = float(result.get('size_matched') or 0)
        if status in ('MATCHED', 'CANCELED', 'CANCELLED', 'EXPIRED', 'UNMATCHED'):
            if matched > 0:
                from py_clob_client.clob_types import TradeParams
                cash = shares = 0.0
                seen = set()
                for trade_id in result.get('associate_trades', result.get('associated_trades', [])) or []:
                    trades = await asyncio.to_thread(self._clob_client.get_trades, TradeParams(id=trade_id))
                    for trade in trades if isinstance(trades, list) else []:
                        key = str(trade.get('id') or trade_id)
                        if key in seen or str(trade.get('status', '')).upper() not in ('MATCHED', 'MINED', 'CONFIRMED'):
                            continue
                        seen.add(key)
                        if trade.get('taker_order_id') == record.order_id:
                            qty, price = float(trade['size']), float(trade['price'])
                            shares += qty
                            cash += qty*price
                        else:
                            for maker in trade.get('maker_orders', []):
                                if maker.get('order_id') == record.order_id:
                                    qty, price = float(maker['matched_amount']), float(maker['price'])
                                    shares += qty
                                    cash += qty*price
                if shares > 0 and abs(shares-matched) < 1e-5:
                    record.status = ExecutionStatus.FILLED if matched >= float(result.get('original_size') or matched) else ExecutionStatus.PARTIAL
                    record.fill_size, record.fill_price = shares, cash/shares
                    record.error = ''
                else:
                    record.status = ExecutionStatus.PENDING
                    record.error = '已匹配，等待交易所完整成交明细；继续暂停新实盘单'
            elif status in ('CANCELED', 'CANCELLED', 'EXPIRED', 'UNMATCHED'):
                record.status = ExecutionStatus.CANCELLED
                record.error = '交易所确认未成交'
        if self.store:
            await self.store.update_execution(record)
        return record
