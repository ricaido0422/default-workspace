"""跟单执行器（可选模块）。

收到 CopySignal 信号后，用 py-clob-client SDK 在 Polymarket 下单复制大户操作。
本模块为可选依赖：仅在实际下单（enabled=True 且 dry_run=False）时需要
py-clob-client，dry-run 模式只需 Python 标准库即可运行。

安装依赖：
    pip install py-clob-client

安全说明：
    - enabled 默认 False，必须显式开启才会执行。
    - dry_run 默认 True，只打印"将要执行"的详情，不下真单。
    - 私钥永不打印，日志仅输出钱包地址前 10 字符。
    - max_order_usd / max_daily_usd 为硬限额，超限直接拒绝。
    - 每日累计花费为内存态，跨日自动重置，进程重启清零。
"""

import os
import time
from typing import Optional

from .config import ExecutorConfig
from .models import CopySignal


__all__ = ["CopyExecutor"]


# 跟单执行日志前缀
_LOG_PREFIX = "[执行]"

# 触发买入的信号类型
_BUY_KINDS = ("NEW_POSITION", "INCREASE", "NEW_TRADE")
# 触发卖出的信号类型
_SELL_KINDS = ("DECREASE", "CLOSED")


class CopyExecutor:
    """跟单执行器：收到信号后下单复制。"""

    def __init__(self, config: ExecutorConfig, alerter=None):
        self.config = config
        self.alerter = alerter
        self._client = None          # py_clob_client ClobClient 实例
        self._daily_spent = 0.0      # 今日已花费（USD）
        self._daily_date = ""        # 今日日期字符串（跨日重置用）
        self._sdk_ok = False         # SDK 是否成功初始化
        self._init_client()

    # ------------------------------------------------------------------
    # 客户端初始化
    # ------------------------------------------------------------------
    def _init_client(self):
        """初始化 ClobClient。

        - enabled=False：打印提示并返回，不初始化。
        - dry_run=True：打印提示，不实际连接 CLOB。
        - 否则尝试 import py_clob_client 并初始化；import 或初始化失败时
          打印安装指引/错误，并降级为 dry_run，不崩溃。
        - 若 config.proxy 非空，设置 HTTPS_PROXY 环境变量
          （py_clob_client 底层用 requests，自动读取该环境变量）。
        """
        cfg = self.config

        # 代理：requests 读 HTTPS_PROXY / https_proxy 环境变量
        if cfg.proxy:
            os.environ["HTTPS_PROXY"] = cfg.proxy
            os.environ["https_proxy"] = cfg.proxy

        if not cfg.enabled:
            print(f"{_LOG_PREFIX} 执行器未启用（enabled=False），仅观察不下单。")
            return

        if cfg.dry_run:
            print(f"{_LOG_PREFIX} dry-run 模式，不连接 CLOB，仅打印将要执行的订单。")
            return

        # 需要真实下单，尝试导入 SDK
        try:
            from py_clob_client.client import ClobClient  # noqa: F401
        except ImportError:
            print(f"{_LOG_PREFIX} 未安装 py-clob-client，无法真实下单。")
            print(f"{_LOG_PREFIX} 安装指引: pip install py-clob-client")
            print(f"{_LOG_PREFIX} 已自动降级为 dry-run 模式。")
            cfg.dry_run = True
            return

        # 校验必要凭证
        if not cfg.private_key or not cfg.wallet_address:
            print(f"{_LOG_PREFIX} 缺少 private_key 或 wallet_address，无法初始化客户端。")
            print(f"{_LOG_PREFIX} 已自动降级为 dry-run 模式。")
            cfg.dry_run = True
            return

        # 构造 API 凭证（若配置了三件套则用，否则后续从私钥派生）
        creds = None
        if cfg.api_key and cfg.api_secret and cfg.api_passphrase:
            try:
                from py_clob_client.clob_types import ApiCreds
                creds = ApiCreds(
                    api_key=cfg.api_key,
                    api_secret=cfg.api_secret,
                    api_passphrase=cfg.api_passphrase,
                )
            except Exception as e:
                print(f"{_LOG_PREFIX} 构造 ApiCreds 失败，将尝试 derive_api_key: {e}")
                creds = None

        # 构造客户端
        try:
            from py_clob_client.client import ClobClient
            kwargs = dict(
                host="https://clob.polymarket.com",
                key=cfg.api_key,
                chain_id=137,                 # Polygon
                private_key=cfg.private_key,
                signature_type=cfg.signature_type,
                funder=cfg.wallet_address,
            )
            if creds is not None:
                kwargs["creds"] = creds
            client = ClobClient(**kwargs)
            # 未提供凭证则从私钥派生 L2 API key
            if creds is None:
                client.set_api_creds(client.derive_api_key())
            self._client = client
            self._sdk_ok = True
            print(f"{_LOG_PREFIX} CLOB 客户端初始化成功（本地钱包 {self._mask_wallet()}）。")
        except Exception as e:
            print(f"{_LOG_PREFIX} ClobClient 初始化失败: {e}")
            print(f"{_LOG_PREFIX} 已自动降级为 dry-run 模式。")
            cfg.dry_run = True
            self._client = None

    # ------------------------------------------------------------------
    # 信号执行主流程
    # ------------------------------------------------------------------
    def execute_signal(self, signal: CopySignal) -> dict:
        """执行跟单信号。

        返回 {success: bool, action: str, detail: str, order_id: str}。

        流程:
        1. 检查 enabled
        2. 根据 signal.kind 决定操作（买入/卖出/跳过）
        3. 计算下单 size = signal.size * follow_ratio
        4. 获取最优价格，估算下单金额
        5. 风控检查（单笔/每日限额、价格范围、token_id）
        6. 真实下单前滑点检查
        7. dry_run=True → 只 log，不下单
        8. dry_run=False → 创建并提交订单
        9. 通过 alerter 发送执行结果告警
        """
        cfg = self.config

        # 1. 总开关
        if not cfg.enabled:
            return self._result(False, "SKIP", "执行器未启用", "")

        # 2. 决定操作方向
        action = self._decide_action(signal)
        if action == "SKIP":
            detail = f"信号类型 {signal.kind}/{signal.side} 不触发跟单，跳过。"
            return self._result(False, "SKIP", detail, "")

        # 3. 计算跟单数量
        size = signal.size * cfg.follow_ratio
        if size <= 0:
            return self._result(False, "SKIP", f"计算数量 {size} <= 0，跳过。", "")

        # 4. 获取执行价格（客户端可用时取实时最优价，否则回退信号价）
        best = self._get_best_price(signal.token_id, action)
        price = best if best is not None else signal.price

        # 5. 估算下单金额（USD）
        if price is not None and price > 0:
            order_usd = size * price
        elif signal.value_usd is not None:
            order_usd = signal.value_usd * cfg.follow_ratio
        else:
            order_usd = 0.0

        # 6. 风控检查
        passed, reason = self._check_risk(signal, order_usd)
        if not passed:
            self._log_order(signal, action, price, size, order_usd,
                            cfg.dry_run, rejected=reason)
            self._alert_reject(signal, action, reason, order_usd)
            return self._result(False, "REJECT", reason, "")

        # 7. 真实下单前的滑点检查（仅非 dry_run 且有实时价与信号价时）
        if (not cfg.dry_run and best is not None
                and signal.price is not None and signal.price > 0):
            slip = abs(best - signal.price) / signal.price
            if slip > cfg.slippage_pct:
                reason = (
                    f"滑点 {slip:.2%} 超过容忍 {cfg.slippage_pct:.2%}"
                    f"（信号价 {signal.price} → 实时价 {best}）"
                )
                self._log_order(signal, action, price, size, order_usd,
                                cfg.dry_run, rejected=reason)
                self._alert_reject(signal, action, reason, order_usd)
                return self._result(False, "REJECT", reason, "")

        # 8. dry-run：只记录不下单
        if cfg.dry_run:
            # 模拟累计每日花费，便于观察限额触发行为（内存态，跨日/重启清零）
            self._daily_spent += order_usd
            self._log_order(signal, action, price, size, order_usd, dry_run=True)
            self._alert_exec(signal, action, price, size, order_usd,
                             dry_run=True, order_id="")
            detail = (f"dry-run 模拟 {action}: token={signal.token_id} "
                      f"size={size:.4f} price={price}")
            return self._result(True, "DRY_RUN", detail, "")

        # 9. 真实下单
        place = self._place_order(signal.token_id, action, price, size)
        if place["success"]:
            self._daily_spent += order_usd
        self._log_order(signal, action, price, size, order_usd, dry_run=False,
                        order_id=place.get("order_id", ""), ok=place["success"])
        self._alert_exec(signal, action, price, size, order_usd,
                         dry_run=False, order_id=place.get("order_id", ""),
                         ok=place["success"])
        return self._result(
            place["success"],
            action if place["success"] else "FAIL",
            place.get("detail", ""),
            place.get("order_id", ""),
        )

    def _decide_action(self, signal: CopySignal) -> str:
        """根据信号类型和方向决定操作：BUY / SELL / SKIP。

        - NEW_POSITION/INCREASE/NEW_TRADE + side=BUY → 买入
        - DECREASE/CLOSED + side=SELL → 卖出
        - 其它 → 跳过
        """
        if signal.kind in _BUY_KINDS and signal.side == "BUY":
            return "BUY"
        if signal.kind in _SELL_KINDS and signal.side == "SELL":
            return "SELL"
        return "SKIP"

    # ------------------------------------------------------------------
    # 风控
    # ------------------------------------------------------------------
    def _check_risk(self, signal: CopySignal, order_usd: float) -> tuple:
        """风控检查，返回 (passed: bool, reason: str)。

        1. token_id 非空
        2. 单笔限额 max_order_usd
        3. 每日限额 max_daily_usd（跨日重置 _daily_spent）
        4. 价格范围 min_price/max_price（仅买入侧，用信号参考价）
        """
        cfg = self.config

        # 1. token_id 非空
        if not signal.token_id:
            return False, "token_id 为空，无法下单。"

        # 2. 单笔限额
        if order_usd > cfg.max_order_usd:
            return False, f"单笔 {order_usd:.2f} USD 超过限额 {cfg.max_order_usd:.2f} USD。"

        # 3. 每日限额（跨日重置）
        self._rollover_daily()
        if self._daily_spent + order_usd > cfg.max_daily_usd:
            return False, (
                f"每日累计 {self._daily_spent:.2f} + 本笔 {order_usd:.2f} USD "
                f"超过限额 {cfg.max_daily_usd:.2f} USD。"
            )

        # 4. 价格范围（仅买入；卖出不限制价格区间）
        if signal.side == "BUY" and signal.price is not None and signal.price > 0:
            if signal.price < cfg.min_price:
                return False, (
                    f"买入价 {signal.price} 低于下限 {cfg.min_price}"
                    f"（避免接盘归零市场）。"
                )
            if signal.price > cfg.max_price:
                return False, (
                    f"买入价 {signal.price} 高于上限 {cfg.max_price}"
                    f"（避免追高）。"
                )

        return True, ""

    def _rollover_daily(self) -> None:
        """跨日重置每日累计花费。"""
        today = time.strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_spent = 0.0

    # ------------------------------------------------------------------
    # 行情与下单
    # ------------------------------------------------------------------
    def _get_best_price(self, token_id: str, side: str) -> Optional[float]:
        """获取最优可成交价格。BUY→best_ask, SELL→best_bid。

        客户端未初始化（dry-run 或 SDK 不可用）时返回 None，
        由调用方回退到信号价。
        """
        if self._client is None:
            return None
        try:
            book = self._client.get_order_book(token_id)
        except Exception as e:
            print(f"{_LOG_PREFIX} 获取订单簿失败（token={token_id}）: {e}")
            return None
        if book is None:
            return None
        try:
            if side == "BUY":
                asks = getattr(book, "asks", None)
                if asks:
                    return float(asks[0].price)
                return None
            bids = getattr(book, "bids", None)
            if bids:
                return float(bids[0].price)
            return None
        except (TypeError, ValueError, AttributeError) as e:
            print(f"{_LOG_PREFIX} 解析订单簿价格失败（token={token_id}）: {e}")
            return None

    def _place_order(self, token_id: str, side: str, price, size: float) -> dict:
        """实际下单，返回 {success, order_id, detail}。"""
        if self._client is None:
            return {"success": False, "order_id": "", "detail": "客户端未初始化，无法下单。"}
        if price is None or price <= 0:
            return {"success": False, "order_id": "", "detail": f"无效价格 {price}，取消下单。"}

        # 导入下单所需类型（Side 在不同版本可能位于 clob_types 或 constants）
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType, Side
        except ImportError:
            try:
                from py_clob_client.clob_types import OrderArgs, OrderType
                from py_clob_client.constants import Side
            except ImportError as e:
                return {"success": False, "order_id": "", "detail": f"SDK 类型不可用: {e}"}

        try:
            side_enum = Side.BUY if side == "BUY" else Side.SELL
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side_enum,
            )
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)
            order_id = self._extract_order_id(resp)
            detail = f"订单已提交: {resp}"
            return {"success": True, "order_id": order_id, "detail": detail}
        except Exception as e:
            return {"success": False, "order_id": "", "detail": f"下单异常: {e}"}

    @staticmethod
    def _extract_order_id(resp) -> str:
        """从 post_order 响应中提取订单 id，兼容多种返回结构。"""
        if resp is None:
            return ""
        if isinstance(resp, dict):
            for key in ("order_id", "id", "tx_hash", "transactionHash"):
                val = resp.get(key)
                if val:
                    return str(val)
        return ""

    # ------------------------------------------------------------------
    # 日志与告警
    # ------------------------------------------------------------------
    def _log_order(self, signal, action, price, size, usd, dry_run,
                   rejected: str = "", order_id: str = "", ok: bool = True):
        """打印订单信息。

        安全：永不输出 private_key，仅输出本地钱包地址前 10 字符。
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        wallet_tag = self._mask_wallet()
        leader_tag = (signal.wallet[:10] + "...") if signal.wallet else "N/A"
        mode = "DRY-RUN" if dry_run else "LIVE"
        status = "REJECT" if rejected else ("OK" if ok else "FAIL")

        print(f"{_LOG_PREFIX} {ts} [{mode}] [{status}] {action} {signal.kind}")
        print(f"  跟踪钱包={leader_tag}  本地钱包={wallet_tag}")
        print(f"  市场={signal.title}  outcome={signal.outcome}  token={signal.token_id}")
        print(f"  信号 size={signal.size:.4f}  跟单 size={size:.4f}  "
              f"价格={price}  金额=${usd:.2f}")
        if order_id:
            print(f"  订单 id={order_id}")
        if rejected:
            print(f"  拒绝原因={rejected}")

    def _alert_exec(self, signal, action, price, size, usd,
                    dry_run: bool, order_id: str, ok: bool = True):
        """发送执行结果告警。"""
        if not self.alerter:
            return
        mode = "dry-run 模拟" if dry_run else "真实下单"
        severity = "info" if ok else "warning"
        title = f"[跟单执行] {mode} {action} {'成功' if ok else '失败'}: {signal.title}"
        body = (
            f"跟踪钱包={signal.wallet[:10]}... 市场={signal.title} "
            f"size={size:.4f} 价格={price} 金额=${usd:.2f}"
        )
        if order_id:
            body += f" 订单={order_id}"
        key = f"exec:{signal.wallet}:{signal.token_id}:{action}:{int(time.time() / 60)}"
        try:
            self.alerter.alert(key, title, body, severity=severity)
        except Exception as e:
            print(f"{_LOG_PREFIX} 告警发送失败（不影响执行）: {e}")

    def _alert_reject(self, signal, action, reason, usd):
        """发送风控拒绝告警。"""
        if not self.alerter:
            return
        title = f"[跟单执行] 风控拒绝 {action}: {signal.title}"
        body = f"跟踪钱包={signal.wallet[:10]}... 金额=${usd:.2f} 原因={reason}"
        key = f"exec-reject:{signal.wallet}:{signal.token_id}:{int(time.time() / 60)}"
        try:
            self.alerter.alert(key, title, body, severity="warning")
        except Exception as e:
            print(f"{_LOG_PREFIX} 告警发送失败（不影响执行）: {e}")

    # ------------------------------------------------------------------
    # 批量执行
    # ------------------------------------------------------------------
    def run_cycle(self, signals: list) -> int:
        """批量执行信号列表，返回成功执行数。

        单信号异常隔离，不影响其余信号。
        """
        count = 0
        for sig in signals:
            try:
                result = self.execute_signal(sig)
                if result.get("success"):
                    count += 1
            except Exception as e:
                print(f"{_LOG_PREFIX} 信号执行异常（已隔离）: {e}")
        return count

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _mask_wallet(self) -> str:
        """脱敏显示本地钱包地址（前 10 字符）。"""
        w = self.config.wallet_address
        return (w[:10] + "...") if w else "N/A"

    @staticmethod
    def _result(success: bool, action: str, detail: str, order_id: str) -> dict:
        """构造统一返回结构。"""
        return {"success": success, "action": action, "detail": detail, "order_id": order_id}
