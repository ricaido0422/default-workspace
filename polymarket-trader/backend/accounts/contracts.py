from datetime import datetime, timezone
from typing import Protocol, Literal, runtime_checkable
from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc():
    return datetime.now(timezone.utc)


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['okx', 'magic_hmac', 'magic_oauth']
    address: str = ''
    chain_id: Literal[137] = 137
    signature_type: int = Field(default=0, ge=0, le=2)
    funder: str = ''
    relay_url: str = ''

    @field_validator('address', 'funder')
    @classmethod
    def valid_address(cls, value):
        if value:
            from eth_utils import is_address
            if not is_address(value):
                raise ValueError('钱包地址格式无效')
        return value

    @field_validator('relay_url')
    @classmethod
    def valid_relay(cls, value):
        if value:
            from urllib.parse import urlsplit
            p = urlsplit(value)
            if p.username or p.password or p.query or p.fragment:
                raise ValueError('中继地址不能包含凭证、查询参数或片段')
            if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1', '::1')):
                raise ValueError('中继使用 HTTPS，或本机 HTTP 地址及端口')
            if not p.netloc:
                raise ValueError('中继地址缺少主机')
        return value.rstrip('/')


class Capabilities(BaseModel):
    interactive_signing: bool = False
    unattended: bool = True
    description: str


class ConnectionStatus(BaseModel):
    account_id: str
    ready: bool = False
    authenticated: bool = False
    balance_usdc: float | None = None
    allowance_ready: bool = False
    error: str = ''
    capabilities: Capabilities


class AccountSnapshot(BaseModel):
    account_id: str
    address: str
    balance_usdc: float
    positions: list[dict] = Field(default_factory=list)
    complete: bool = True
    observed_at: datetime = Field(default_factory=utc)


class OrderIntent(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    id: str
    account_id: str
    token_id: str = Field(pattern=r'^\d{1,100}$')
    condition_id: str
    side: Literal['BUY', 'SELL']
    limit_price: float = Field(gt=0, lt=1)
    quantity: float = Field(gt=0)
    expires_at: datetime
    order_type: Literal['FOK'] = 'FOK'


class Submission(BaseModel):
    account_id: str
    intent_id: str
    order_id: str = ''
    state: Literal['AWAITING_SIGNATURE', 'SUBMITTING', 'PENDING', 'FILLED', 'PARTIAL', 'FAILED', 'CANCELLED']
    error: str = ''
    job_id: str = ''
    fill_price: float | None = None
    fill_size: float | None = None


class Receipt(Submission):
    evidence: dict = Field(default_factory=dict)


class PnlSnapshot(BaseModel):
    account_id: str
    positions: list[dict]
    complete: bool
    source: str
    observed_at: datetime = Field(default_factory=utc)


@runtime_checkable
class AccountAdapter(Protocol):
    profile: AccountProfile
    capabilities: Capabilities
    async def check_connection(self) -> ConnectionStatus: ...
    async def read_account(self) -> AccountSnapshot: ...
    async def submit_order(self, intent: OrderIntent) -> Submission: ...
    async def get_receipt(self, submission: Submission) -> Receipt: ...
    async def cancel_order(self, submission: Submission) -> Receipt: ...
    async def sync_pnl(self) -> PnlSnapshot: ...
