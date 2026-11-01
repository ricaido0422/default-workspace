"""主动通知推送模块（Telegram + 邮件）。

为跟单系统提供主动通知能力，让用户不用一直盯屏幕：

- ``NotifierBase``    : 抽象基类，定义 ``async notify(event_type, title, message, data)`` 接口。
- ``TelegramNotifier``: 调用 Telegram Bot API
  (``https://api.telegram.org/bot{TOKEN}/sendMessage``) 推送 Markdown-ish 文本，支持多个 chat_id。
- ``EmailNotifier``   : 基于 ``smtplib`` 发邮件，支持 SSL(465) 与 STARTTLS(587)，支持多收件人。
- ``NotifierManager`` : 管理多个通知器、事件过滤规则与异步发送队列
  （后台 worker 批量发送，不阻塞主流程）。

设计约束：

1. 本模块只依赖 Python 标准库；``httpx`` 存在时优先使用（可选加速），
   因此可以被独立 ``import backend.core.notifier`` 而不会因缺少第三方依赖失败。
2. 所有网络 / 邮件异常都被捕获并降级为 ``False``，绝不向上抛出打断主流程。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示，避免运行时循环导入
    from backend.config import NotificationConfig
    from backend.store.state import StateStore

logger = logging.getLogger(__name__)


# ── 事件类型常量 ────────────────────────────────────────────

class EventType:
    """通知事件类型常量。"""

    #: 检测到大额交易（受最小金额阈值过滤）
    TRADE_DETECTED = "trade_detected"
    #: 策略链生成跟单信号
    SIGNAL_GENERATED = "signal_generated"
    #: 跟单成交
    EXECUTION_FILLED = "execution_filled"
    #: 跟单执行失败
    EXECUTION_FAILED = "execution_failed"
    #: 被风控拦截
    RISK_BLOCKED = "risk_blocked"
    #: 每日汇总
    DAILY_SUMMARY = "daily_summary"

    ALL = [
        TRADE_DETECTED,
        SIGNAL_GENERATED,
        EXECUTION_FILLED,
        EXECUTION_FAILED,
        RISK_BLOCKED,
        DAILY_SUMMARY,
    ]

    #: 测试通道使用的伪事件类型（不参与白名单过滤）
    TEST_EVENT = "test"


#: 测试通道使用的伪事件类型（不参与白名单过滤）
TEST_EVENT = EventType.TEST_EVENT

#: 事件类型中文名（用于标题 / 邮件主题）
EVENT_LABELS: dict[str, str] = {
    EventType.TRADE_DETECTED: "大额交易",
    EventType.SIGNAL_GENERATED: "跟单信号",
    EventType.EXECUTION_FILLED: "成交",
    EventType.EXECUTION_FAILED: "执行失败",
    EventType.RISK_BLOCKED: "风控拦截",
    EventType.DAILY_SUMMARY: "每日汇总",
    TEST_EVENT: "测试",
}


# ── 文本格式化工具 ──────────────────────────────────────────

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def markdown_to_html(text: str) -> str:
    """把 Markdown-ish 文本转换为 Telegram HTML 可用格式。

    支持 ``**粗体**``、`` `等宽` ``、``[文字](链接)`` 三种子集。
    先做 HTML 转义再替换标记，避免内容中的尖括号破坏结构。
    """
    escaped = html.escape(text, quote=True)
    escaped = _MD_LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = _MD_BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _MD_CODE_RE.sub(r"<code>\1</code>", escaped)
    return escaped


def format_value(value: Any) -> str:
    """把任意数据格式化为可读字符串。"""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value)


def extract_amount(data: dict[str, Any] | None) -> float | None:
    """从事件数据中提取「金额」，用于最小金额阈值过滤。

    优先使用显式字段（amount / notional / usdc / value），
    否则用 price * size 计算。无法确定时返回 ``None``（不参与阈值过滤）。
    """
    if not data:
        return None
    for key in ("amount", "notional", "usdc", "trade_amount", "value"):
        val = data.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return float(val)
    price = data.get("price")
    size = data.get("size")
    if isinstance(price, (int, float)) and isinstance(size, (int, float)) and size:
        return float(price) * float(size)
    return None


def _now_iso() -> str:
    """当前 UTC 时间 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


# ── 通知基类 ────────────────────────────────────────────────

class NotifierBase(ABC):
    """通知器抽象基类。

    子类实现 :meth:`notify`，返回 ``True`` 表示发送成功。
    """

    #: 渠道标识（telegram / email）
    channel: str = "base"

    def __init__(self, name: str | None = None) -> None:
        self.name = name or self.channel

    @abstractmethod
    async def notify(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """发送一条通知。

        Args:
            event_type: 事件类型（见 :class:`EventType`）
            title: 标题
            message: 正文
            data: 结构化附加数据

        Returns:
            True 表示发送成功；失败返回 False（不抛异常）。
        """
        ...

    def is_configured(self) -> bool:
        """是否具备发送条件（凭据 / 收件人齐全）。"""
        return True

    async def close(self) -> None:
        """释放资源（HTTP 连接池等）。"""
        return None

    def format_body(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        """构造通用 Markdown-ish 正文。"""
        label = EVENT_LABELS.get(event_type, event_type)
        lines: list[str] = [f"【{label}】{title}"]
        if message:
            lines.extend(["", message])
        if data:
            lines.append("")
            for key, val in data.items():
                lines.append(f"- {key}: {format_value(val)}")
        return "\n".join(lines)


# ── HTTP 辅助（httpx 优先，标准库兜底） ─────────────────────

async def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float = 15.0,
    proxy: str | None = None,
) -> tuple[int, Any]:
    """POST JSON，返回 ``(status_code, 解析后的响应体)``。

    优先使用 httpx（异步）；未安装时回退到标准库 urllib（在线程中执行，
    避免阻塞事件循环）。HTTP 错误同样返回状态码而非抛出。
    """
    try:
        import httpx
    except ImportError:
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if proxy:
            client_kwargs["proxy"] = proxy
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(url, json=payload)
            try:
                body: Any = resp.json()
            except Exception:
                body = {"raw": resp.text}
            return resp.status_code, body

    def _do() -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        handlers: list[Any] = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                try:
                    return int(resp.status), json.loads(raw)
                except Exception:
                    return int(resp.status), {"raw": raw}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "ignore")
            try:
                return int(e.code), json.loads(raw)
            except Exception:
                return int(e.code), {"raw": raw}
        except Exception as e:
            return 0, {"error": str(e)}

    return await asyncio.to_thread(_do)


# ── Telegram 通知器 ─────────────────────────────────────────

class TelegramNotifier(NotifierBase):
    """Telegram Bot API 通知器。

    - 端点: ``https://api.telegram.org/bot{TOKEN}/sendMessage``
    - 支持多个 chat_id（群组 / 多用户）
    - 默认使用 HTML parse_mode（内部把 Markdown-ish 语法转成 HTML），
      解析失败时自动降级为纯文本重试一次。
    """

    channel = "telegram"
    API_BASE = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        chat_ids: list[str] | None = None,
        timeout: float = 15.0,
        parse_mode: str = "HTML",
        proxy: str | None = None,
    ) -> None:
        super().__init__()
        self.token = (token or "").strip()
        self.chat_ids = [str(c).strip() for c in (chat_ids or []) if str(c).strip()]
        self.timeout = timeout
        self.parse_mode = parse_mode
        self.proxy = proxy or None

    def is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_ids)

    def format_body(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        return markdown_to_html(
            super().format_body(event_type, title, message, data)
        )

    async def notify(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """向所有 chat_id 推送消息。任一 chat 失败即整体判定为失败。"""
        if not self.is_configured():
            logger.warning("Telegram 未配置（token 或 chat_id 缺失），跳过通知")
            return False

        body = self.format_body(event_type, title, message, data)
        all_ok = True
        for chat_id in self.chat_ids:
            try:
                ok = await self._send_one(chat_id, body)
            except Exception as e:  # 任何异常都降级，不影响主流程
                logger.warning("Telegram 通知失败 (chat_id=%s): %s", chat_id, e)
                all_ok = False
                continue
            if not ok:
                all_ok = False
        return all_ok

    async def _send_one(self, chat_id: str, body: str) -> bool:
        """向单个 chat_id 发送消息。"""
        url = f"{self.API_BASE}/bot{self.token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": True,
        }
        if self.parse_mode:
            payload["parse_mode"] = self.parse_mode

        status, resp = await _post_json(url, payload, self.timeout, self.proxy)

        # parse_mode 解析失败 → 纯文本重试
        if status == 200 and isinstance(resp, dict) and not resp.get("ok", True):
            desc = str(resp.get("description", ""))
            if self.parse_mode and "parse" in desc.lower():
                logger.warning("Telegram 格式解析失败，降级为纯文本: %s", desc)
                plain = dict(payload)
                plain.pop("parse_mode", None)
                plain["text"] = _strip_markup(body)
                status, resp = await _post_json(url, plain, self.timeout, self.proxy)

        # 429 限流 → 短暂等待后重试一次
        if status == 429 and isinstance(resp, dict):
            retry_after = 1.0
            params = resp.get("parameters")
            if isinstance(params, dict):
                try:
                    retry_after = min(float(params.get("retry_after", 1)), 5.0)
                except Exception:
                    retry_after = 1.0
            logger.warning("Telegram 触发限流，%.1fs 后重试", retry_after)
            await asyncio.sleep(retry_after)
            status, resp = await _post_json(url, payload, self.timeout, self.proxy)

        if status != 200:
            logger.warning("Telegram HTTP %s: %s", status, _short(resp))
            return False
        if isinstance(resp, dict) and not resp.get("ok", True):
            logger.warning("Telegram 发送失败: %s", _short(resp))
            return False
        return True


def _strip_markup(text: str) -> str:
    """去掉 Markdown-ish 标记，用于纯文本降级。"""
    text = _MD_LINK_RE.sub(r"\1 (\2)", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    return _MD_CODE_RE.sub(r"\1", text)


def _short(obj: Any, limit: int = 300) -> str:
    """把任意对象转成简短字符串（用于日志）。"""
    try:
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    return text[:limit]


async def _safe_close(notifier: NotifierBase, name: str) -> None:
    """安全关闭通知器。"""
    try:
        await notifier.close()
    except Exception:
        logger.debug("关闭通知器 %s 失败", name, exc_info=True)


# ── 邮件通知器 ──────────────────────────────────────────────

class EmailNotifier(NotifierBase):
    """基于 smtplib 的邮件通知器。

    - ``email_use_ssl=True``（端口 465）→ ``smtplib.SMTP_SSL``
    - ``email_use_ssl=False``（端口 587）→ ``smtplib.SMTP`` + ``STARTTLS``
    - 支持多收件人（``to_addrs``）
    """

    channel = "email"

    def __init__(
        self,
        host: str,
        port: int = 587,
        user: str = "",
        password: str = "",
        from_addr: str = "",
        to_addrs: list[str] | None = None,
        use_ssl: bool = False,
        timeout: float = 20.0,
    ) -> None:
        super().__init__()
        self.host = (host or "").strip()
        self.port = int(port or 587)
        self.user = user or ""
        self.password = password or ""
        self.from_addr = (from_addr or self.user or "").strip()
        self.to_addrs = [str(a).strip() for a in (to_addrs or []) if str(a).strip()]
        self.use_ssl = bool(use_ssl)
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.host) and bool(self.to_addrs)

    async def notify(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """发送邮件（阻塞部分在线程中执行）。"""
        if not self.is_configured():
            logger.warning("邮件未配置（host 或收件人缺失），跳过通知")
            return False

        try:
            msg = self._build_email(event_type, title, message, data)
        except Exception as e:
            logger.warning("构造邮件失败: %s", e)
            return False

        try:
            await asyncio.to_thread(self._send_sync, msg)
            return True
        except Exception as e:
            logger.warning("邮件发送失败: %s", e)
            return False

    def _build_email(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> EmailMessage:
        """构造邮件对象。"""
        label = EVENT_LABELS.get(event_type, event_type)
        body = self.format_body(event_type, title, message, data)
        msg = EmailMessage()
        msg["Subject"] = f"[Polymarket Trader][{label}] {title}"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg.set_content(body)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        """同步发送邮件（在工作线程中调用）。"""
        use_ssl = self.use_ssl or self.port == 465
        if use_ssl:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)

        try:
            if not use_ssl:
                # STARTTLS（587 必用；其他端口尽力而为）
                try:
                    server.starttls(context=ssl.create_default_context())
                except (smtplib.SMTPNotSupportedError, smtplib.SMTPException) as e:
                    if self.port == 587:
                        raise
                    logger.warning("SMTP 服务器不支持 STARTTLS（继续明文）: %s", e)
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass


# ── 通知管理器 ──────────────────────────────────────────────

class _NotificationItem:
    """队列中的待发送通知。

    用普通类而非 dataclass，使本模块可被任意方式独立加载
    （dataclass 装饰器依赖模块在 ``sys.modules`` 中注册）。
    """

    __slots__ = ("event_type", "title", "message", "data", "created_at")

    def __init__(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        self.event_type = event_type
        self.title = title
        self.message = message
        self.data = data
        self.created_at = created_at or _now_iso()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"_NotificationItem({self.event_type!r}, {self.title!r})"


class NotifierManager:
    """通知管理器。

    职责：

    1. 管理多个通知器（Telegram / Email），支持运行时热更新配置。
    2. 事件过滤规则：
       - 事件类型白名单
       - 最小金额阈值
       - 仅通知指定钱包
       - 冷却期（同一事件不重复通知）
    3. 异步发送队列：``send()`` 只入队即返回，由后台 worker 批量发送，
       避免阻塞主数据流。

    所有通知失败都被捕获并记录，不会影响主流程。
    """

    def __init__(
        self,
        config: "NotificationConfig | None" = None,
        store: "StateStore | None" = None,
        proxy: str | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._proxy = proxy
        self._notifiers: dict[str, NotifierBase] = {}
        self._queue: asyncio.Queue[Any] | None = None
        self._worker: asyncio.Task | None = None
        self._cooldown_state: dict[str, float] = {}
        self._batch_size = 10
        self._batch_wait = 0.3
        self._counters: dict[str, int] = {
            "queued": 0,
            "sent": 0,
            "failed": 0,
            "filtered": 0,
        }
        if config is not None:
            self.apply_config(config)

    # ── 配置 ────────────────────────────────────────────────

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        store: "StateStore | None" = None,
    ) -> "NotifierManager":
        """从全局 Settings 构建（读取 ``settings.notification``）。"""
        cfg = getattr(settings, "notification", None)
        proxy = ""
        proxy_cfg = getattr(settings, "proxy", None)
        if proxy_cfg is not None:
            proxy = getattr(proxy_cfg, "http", "") or getattr(proxy_cfg, "https", "") or ""
        return cls(config=cfg, store=store, proxy=proxy)

    @property
    def config(self) -> Any:
        """当前通知配置。"""
        return self._config

    def set_store(self, store: "StateStore | None") -> None:
        """注入状态存储（用于写通知日志）。"""
        self._store = store

    def get_channels(self) -> list[str]:
        """当前生效的通知渠道列表。"""
        return list(self._notifiers.keys())

    def get_stats(self) -> dict[str, int]:
        """统计信息。"""
        return dict(self._counters)

    def apply_config(self, config: Any) -> None:
        """按配置重建通知器集合（热更新）。"""
        self._config = config
        new_notifiers: dict[str, NotifierBase] = {}

        if bool(getattr(config, "telegram_enabled", False)):
            tg = TelegramNotifier(
                token=getattr(config, "telegram_token", "") or "",
                chat_ids=list(getattr(config, "telegram_chat_ids", []) or []),
                proxy=self._proxy,
            )
            if tg.is_configured():
                new_notifiers["telegram"] = tg
            else:
                logger.warning("Telegram 已启用但 token / chat_id 未配置，通知将被跳过")

        if bool(getattr(config, "email_enabled", False)):
            mail = EmailNotifier(
                host=getattr(config, "email_host", "") or "",
                port=int(getattr(config, "email_port", 587) or 587),
                user=getattr(config, "email_user", "") or "",
                password=getattr(config, "email_password", "") or "",
                from_addr=getattr(config, "email_from", "") or "",
                to_addrs=list(getattr(config, "email_to", []) or []),
                use_ssl=bool(getattr(config, "email_use_ssl", False)),
            )
            if mail.is_configured():
                new_notifiers["email"] = mail
            else:
                logger.warning("邮件已启用但 SMTP 主机或收件人未配置，通知将被跳过")

        # 被移除的旧通知器：Telegram / Email 不持有长连接资源，
        # 仅在存在需要释放的协程时挂到后台清理（此处不阻塞配置应用）。
        for name, old in list(self._notifiers.items()):
            if name not in new_notifiers:
                self._schedule_close(old, name)

        self._notifiers = new_notifiers
        logger.info(
            "通知配置已应用 (渠道: %s)", ", ".join(self._notifiers) if self._notifiers else "无"
        )

    def update_config(self, config: Any) -> None:
        """热更新通知配置（无需重启 worker）。"""
        self.apply_config(config)

    @staticmethod
    def _schedule_close(notifier: NotifierBase, name: str) -> None:
        """在事件循环可用时异步关闭旧通知器，否则忽略。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_safe_close(notifier, name))

    # ── 生命周期 ────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台发送 worker。"""
        if self._worker is not None and not self._worker.done():
            return
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._worker_loop())
        logger.info("通知管理器已启动 (渠道: %s)", ", ".join(self._notifiers) or "无")

    async def stop(self) -> None:
        """优雅停止：等待队列排空后关闭 worker 与通知器。"""
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            if self._queue is not None:
                try:
                    await self._queue.put(None)  # 哨兵：停止标记
                except Exception:
                    pass
            try:
                await asyncio.wait_for(worker, timeout=10)
            except asyncio.TimeoutError:
                logger.warning("通知 worker 未在超时内退出，强制取消")
                worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    pass
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("通知 worker 异常退出")
        self._queue = None

        for name, notifier in list(self._notifiers.items()):
            try:
                await notifier.close()
            except Exception:
                logger.debug("关闭通知器 %s 失败", name, exc_info=True)
        logger.info("通知管理器已停止")

    # ── 过滤规则 ────────────────────────────────────────────

    def _allowed_events(self) -> list[str]:
        """事件类型白名单（空 = 全部允许）。"""
        events = list(getattr(self._config, "notify_events", []) or [])
        return [str(e) for e in events]

    def _allowed_wallets(self) -> list[str]:
        """钱包白名单（空 = 不限制）。"""
        wallets = list(getattr(self._config, "notify_wallets", []) or [])
        return [str(w).strip() for w in wallets if str(w).strip()]

    def _cooldown_key(self, event_type: str, data: dict[str, Any] | None) -> str:
        """构造冷却期去重键。"""
        data = data or {}
        for key in ("dedup_key", "signal_id", "tx_hash", "order_id", "id"):
            val = data.get(key)
            if val:
                return f"{event_type}:{val}"
        wallet = data.get("wallet")
        market = data.get("market") or data.get("condition_id")
        if wallet or market:
            return f"{event_type}:{wallet}:{market}"
        return f"{event_type}:*"

    def should_notify(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        bypass_filters: bool = False,
    ) -> tuple[bool, str]:
        """判断事件是否应该发送通知。

        过滤规则：

        1. 事件类型白名单（``notify_events`` 为空 = 全部允许）
        2. 最小金额阈值（仅对**带金额**的事件生效；
           无法确定金额的事件如风控拦截 / 每日汇总不做阈值过滤）
        3. 钱包白名单（``notify_wallets`` 为空 = 不限制）
        4. 冷却期（同一事件在 ``cooldown_seconds`` 内不重复通知）

        Returns:
            ``(是否发送, 原因)``；允许时原因为空字符串。
        """
        if bypass_filters:
            return True, ""

        allowed = self._allowed_events()
        if allowed and event_type not in allowed:
            return False, f"事件类型 {event_type} 不在白名单"

        min_amount = float(getattr(self._config, "min_notify_amount", 0) or 0)
        amount = extract_amount(data)
        if min_amount > 0 and amount is not None and amount < min_amount:
            return False, f"金额 {amount:.4f} 低于阈值 {min_amount:.4f}"

        wallets = self._allowed_wallets()
        if wallets:
            wallet = str((data or {}).get("wallet", "") or "")
            if wallet and wallet.lower() not in {w.lower() for w in wallets}:
                return False, f"钱包 {wallet} 不在白名单"

        cooldown = float(getattr(self._config, "cooldown_seconds", 0) or 0)
        if cooldown > 0:
            key = self._cooldown_key(event_type, data)
            now = time.monotonic()
            last = self._cooldown_state.get(key)
            if last is not None and (now - last) < cooldown:
                return False, f"冷却期未过（{cooldown:.0f}s）"
            self._cooldown_state[key] = now

        return True, ""

    # ── 发送 ────────────────────────────────────────────────

    async def send(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
        channels: list[str] | None = None,
        bypass_filters: bool = False,
    ) -> dict[str, bool]:
        """发送通知（符合过滤规则时）。

        若后台 worker 已启动，则仅入队并立即返回（不阻塞主流程）；
        否则直接同步发送（便于独立使用 / 测试）。

        Returns:
            ``{channel: 是否成功}``；入队模式下返回空字典。
        """
        allowed, reason = self.should_notify(event_type, data, bypass_filters)
        if not allowed:
            self._counters["filtered"] += 1
            logger.debug("通知被过滤 (%s): %s", event_type, reason)
            return {}

        if channels is not None:
            selected = [c for c in channels if c in self._notifiers]
            if not selected:
                logger.warning("没有可用的通知渠道: %s", channels)
                return {}

        if self._queue is not None and self._worker is not None and not self._worker.done():
            await self._queue.put(_NotificationItem(event_type, title, message, data))
            self._counters["queued"] += 1
            return {}

        return await self._dispatch(event_type, title, message, data, channels)

    async def send_now(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
        channels: list[str] | None = None,
    ) -> dict[str, bool]:
        """立即发送通知（跳过队列与过滤规则），用于测试通道。"""
        return await self._dispatch(event_type, title, message, data, channels)

    async def _dispatch(
        self,
        event_type: str,
        title: str,
        message: str,
        data: dict[str, Any] | None = None,
        channels: list[str] | None = None,
    ) -> dict[str, bool]:
        """分发到各通知渠道并记录日志。"""
        targets = list(self._notifiers.keys())
        if channels is not None:
            targets = [c for c in channels if c in self._notifiers]

        results: dict[str, bool] = {}
        for name in targets:
            notifier = self._notifiers.get(name)
            if notifier is None:
                results[name] = False
                continue
            try:
                ok = await notifier.notify(event_type, title, message, data)
            except Exception as e:
                logger.warning("通知发送异常 (%s): %s", name, e)
                ok = False
            results[name] = ok
            if ok:
                self._counters["sent"] += 1
            else:
                self._counters["failed"] += 1
            await self._log(
                event_type=event_type,
                channel=name,
                status="success" if ok else "failed",
                error=None if ok else "发送失败",
                payload={"title": title, "message": message, "data": data},
            )
        return results

    async def _log(
        self,
        event_type: str,
        channel: str,
        status: str,
        error: str | None,
        payload: dict[str, Any] | None,
    ) -> None:
        """写入通知日志（DB）。"""
        store = self._store
        if store is None:
            return
        try:
            await store.log_notification(
                event_type=event_type,
                channel=channel,
                status=status,
                error=error or "",
                payload=payload or {},
            )
        except Exception:
            logger.debug("写通知日志失败", exc_info=True)

    # ── 后台 worker ─────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """后台批量发送 worker。"""
        queue = self._queue
        if queue is None:
            return

        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                break

            if item is None:  # 停止哨兵
                try:
                    queue.task_done()
                except Exception:
                    pass
                break

            batch = [item]
            stop = False
            while len(batch) < self._batch_size:
                try:
                    nxt = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    stop = True
                    try:
                        queue.task_done()
                    except Exception:
                        pass
                    break
                batch.append(nxt)

            for it in batch:
                try:
                    await self._dispatch(it.event_type, it.title, it.message, it.data)
                except Exception:
                    logger.exception("通知分发失败")
                finally:
                    try:
                        queue.task_done()
                    except Exception:
                        pass

            if stop:
                break

    async def flush(self, timeout: float = 5.0) -> None:
        """等待队列排空（关闭前调用）。"""
        queue = self._queue
        if queue is None:
            return
        try:
            await asyncio.wait_for(queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("通知队列未在超时内排空")
        except Exception:
            logger.debug("通知队列排空异常", exc_info=True)
