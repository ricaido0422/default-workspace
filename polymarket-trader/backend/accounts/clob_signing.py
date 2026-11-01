"""当前官方 CLOB EIP712 格式；CTF domain v2 / Protocol V2 domain v3。

来源 https://docs.polymarket.com/trading/place-orders ，2026-10-27 核对。
复用 SDK 的 L1/L2 HTTP 认证与回执查询，替换已过时的订单构建器。
"""
import secrets
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from py_clob_client.client import ClobClient

ZERO = '0x' + '00'*32
DOMAINS = {'ctf': ('2', '0xE111180000d2663C0091e4f400237545B87B996B'),
           'neg_risk': ('2', '0xe2222d279d744050d28e00520010520000310F59'),
           'v2': ('3', '0xe3333700cA9d93003F00f0F71f8515005F6c00Aa')}


def configure_transport(settings):
    import httpx
    from py_clob_client.http_helpers import helpers
    proxy = settings.proxy.http or settings.proxy.https or None
    # 多钱包共用 SDK 传输；初始化另一个钱包时不能关闭正在核对回执的连接。
    if getattr(helpers, '_trader_proxy', object()) != proxy:
        helpers._http_client = httpx.Client(http2=True, timeout=15, proxy=proxy, follow_redirects=False)
        helpers._trader_proxy = proxy


def build_typed_order(address, funder, signature_type, token, side, price, size, book):
    if signature_type not in (0, 1, 2):
        raise ValueError('当前通道不支持 Deposit Wallet 签名类型')
    if book.get('version') not in (None, '', 'ctf', 'v1', 'v2'):
        raise ValueError('未知市场协议，拒绝使用错误签名域')
    asset = str(book.get('asset_id', ''))
    if not asset or asset != str(token):
        raise ValueError('盘口标的与订单不一致')
    tick = Decimal(str(book['tick_size']))
    precision = {'0.1': 3, '0.01': 4, '0.005': 5, '0.0025': 6, '0.001': 5, '0.0001': 6}
    tick_key = format(tick.normalize(), 'f')
    if tick_key not in precision:
        raise ValueError('不支持的价格精度，拒绝签名')
    p = (Decimal(str(price))/tick).to_integral_value(rounding=ROUND_DOWN if side=='BUY' else ROUND_UP)*tick
    qty = Decimal(str(size)).quantize(Decimal('.01'), rounding=ROUND_DOWN)
    if not 0 < p < 1 or qty <= 0 or qty < Decimal(str(book.get('min_order_size', '0'))):
        raise ValueError('按盘口精度取整后价格或数量无效')
    decimals = precision[tick_key]
    cash = p*qty
    if -cash.as_tuple().exponent > decimals:
        cash = cash.quantize(Decimal(1).scaleb(-decimals-4), rounding=ROUND_UP).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    usd, shares = int(cash*1_000_000), int(qty*1_000_000)
    if min(usd, shares) <= 0:
        raise ValueError('金额精度不足')
    if signature_type == 0 and funder and funder.lower() != address.lower():
        raise ValueError('EOA 资金钱包与签名地址不一致')
    if signature_type and not funder:
        raise ValueError('代理账户缺少 Funder')
    key = 'v2' if book.get('version') == 'v2' else 'neg_risk' if book.get('neg_risk') is True else 'ctf'
    version, contract = DOMAINS[key]
    fields = [('salt','uint256'),('maker','address'),('signer','address'),('tokenId','uint256'),
              ('makerAmount','uint256'),('takerAmount','uint256'),('side','uint8'),('signatureType','uint8'),
              ('timestamp','uint256'),('metadata','bytes32'),('builder','bytes32')]
    message = {'salt': str(secrets.randbelow(2**52)), 'maker': funder or address, 'signer': address,
        'tokenId': str(token), 'makerAmount': str(usd if side=='BUY' else shares),
        'takerAmount': str(shares if side=='BUY' else usd), 'side': '0' if side=='BUY' else '1',
        'signatureType': str(signature_type), 'timestamp': str(int(time.time()*1000)), 'metadata': ZERO, 'builder': ZERO}
    return {'primaryType':'Order', 'domain': {'name':'Polymarket CTF Exchange', 'version':version, 'chainId':137, 'verifyingContract':contract},
            'types': {'Order':[{'name':n, 'type':t} for n,t in fields]}, 'message':message}


def order_hash(typed):
    encoded = encode_typed_data(full_message=typed)
    return '0x'+keccak(b'\x19'+encoded.version+encoded.header+encoded.body).hex()


class SignedOrder:
    def __init__(self, typed, signature):
        self.typed, self.signature = typed, signature
        self.order_hash = order_hash(typed)

    def dict(self):
        order = dict(self.typed['message'])
        order.update(salt=int(order['salt']), side='BUY' if int(order['side'])==0 else 'SELL',
                     signatureType=int(order['signatureType']), expiration='0', signature=self.signature)
        return order


class CurrentClobClient(ClobClient):
    def get_book_context(self, token):
        from py_clob_client.http_helpers.helpers import get
        from urllib.parse import urlencode
        return get(self.host+'/book?'+urlencode({'token_id':token}))

    def create_order(self, args, options=None):
        typed = build_typed_order(self.signer.address(), self.builder.funder, self.builder.sig_type,
            args.token_id, args.side, args.price, args.size, self.get_book_context(args.token_id))
        signature = Account.sign_message(encode_typed_data(full_message=typed), self.signer.private_key).signature.hex()
        return SignedOrder(typed, '0x'+signature.removeprefix('0x'))
