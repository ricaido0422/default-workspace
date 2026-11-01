"""快捷跟单规则。沿用交易台审批、盘口和执行器，不另建资金执行通道。"""
import hashlib
import math
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.models.schemas import FollowSignal, MarketRef, TradeSide, ExecutionMode


class CopyRule(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    id: str = Field(pattern=r'^copy_[a-f0-9]{32}$')
    wallet: str = Field(pattern=r'^0x[0-9a-fA-F]{40}$')
    name: str = Field(min_length=1, max_length=80)
    mode: Literal['PAPER', 'LIVE'] = 'PAPER'
    account_id: str = 'legacy'
    enabled: bool = True
    sizing: Literal['fixed', 'ratio'] = 'fixed'
    amount: float = Field(default=10, ge=1, le=10000)
    ratio: float = Field(default=.1, ge=.001, le=10)
    max_amount: float = Field(default=25, ge=1, le=10000)
    min_source_amount: float = Field(default=0, ge=0, le=10000000)
    min_price: float = Field(default=.01, gt=0, lt=1)
    max_price: float = Field(default=.99, gt=0, lt=1)
    follow_sells: bool = True

    @model_validator(mode='after')
    def valid_range(self):
        if self.min_price > self.max_price:
            raise ValueError('买入价格下限不能高于上限')
        if self.sizing == 'fixed' and self.amount > self.max_amount:
            raise ValueError('每笔固定金额不能超过规则单笔上限')
        return self


async def get_rules(store):
    return [CopyRule(**row) for row in (await store.get_config('copy_rules') or [])]


async def rule_quantity(desk, rule, token):
    async with desk.db.execute('SELECT signal,size FROM desk_fills WHERE mode=? ORDER BY created_at,rowid', (rule.mode,)) as cur:
        rows = await cur.fetchall()
    lots = []
    own_net = 0.
    for row in rows:
        signal = FollowSignal.model_validate_json(row['signal'])
        if rule.mode == 'LIVE' and signal.account_id != rule.account_id:
            continue
        if signal.market.token_id != token:
            continue
        if signal.pipeline_id == rule.id:
            own_net += row['size'] * (1 if signal.side == TradeSide.BUY else -1)
        if signal.side == TradeSide.BUY:
            lots.append([signal.pipeline_id, row['size']])
        else:
            remaining = row['size']
            for lot in lots:
                take = min(remaining, lot[1])
                lot[1] -= take
                remaining -= take
                if remaining <= 1e-8:
                    break
    return max(0., min(own_net, sum(lot[1] for lot in lots if lot[0] == rule.id)))


async def build_signal(desk, rule, trade):
    if rule.mode == 'LIVE' and hasattr(desk.manager, 'accounts') and rule.account_id != desk.manager.accounts.active_id:
        return None
    if not rule.enabled or rule.mode != desk.manager.mode.value or rule.wallet.lower() != trade.wallet.lower():
        return None
    activated = await desk.store.get_config('copy_rule_activation:' + rule.id)
    if activated and trade.timestamp < datetime.fromisoformat(activated):
        return None
    if not 0 < trade.price < 1 or trade.size * trade.price < rule.min_source_amount:
        return None
    if trade.side == TradeSide.BUY and not rule.min_price <= trade.price <= rule.max_price:
        return None
    size = (rule.amount / trade.price) if rule.sizing == 'fixed' else trade.size * rule.ratio
    size = min(size, rule.max_amount / trade.price)
    if trade.side == TradeSide.SELL:
        if not rule.follow_sells:
            return None
        owned = await rule_quantity(desk, rule, trade.token_id)
        # 同一资金池中其他规则或手动单的持仓不作为本规则卖出额度。
        held = sum(p['quantity'] for p in (await desk.summary(mode=rule.mode))['positions'] if p['token_id'] == trade.token_id)
        size = min(size, owned, held)
    size = math.floor(size * 100) / 100
    if size <= 0:
        return None
    identity = '|'.join(map(str, (rule.id, trade.tx_hash, trade.token_id, trade.side.value,
                                  trade.timestamp.isoformat(), trade.price, trade.size)))
    return FollowSignal(signal_id='copy-' + hashlib.sha256(identity.encode()).hexdigest(),
        source_trade=trade, pipeline_id=rule.id,
        market=MarketRef(condition_id=trade.condition_id, token_id=trade.token_id,
                         slug=trade.market_slug, title=trade.market_title),
        side=trade.side, price=trade.price, size=size, expected_mode=ExecutionMode(rule.mode), account_id=rule.account_id)
