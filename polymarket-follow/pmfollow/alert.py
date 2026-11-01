"""告警模块。

支持控制台彩色输出和可选 webhook 推送，带去重窗口。
webhook 支持多平台：generic（通用 JSON POST）、telegram（Bot）、discord、lark（飞书）。
代理通过环境变量 HTTPS_PROXY / http_proxy 自动生效（urllib 原生支持，无需硬编码）。
"""

import json
import sys
import time
import urllib.request
from typing import Optional

from .config import AlertsConfig
from .store import StateStore


# ANSI 颜色码
_COLORS = {
    "info": "\033[92m",      # 绿色
    "warning": "\033[93m",   # 黄色
    "critical": "\033[91m",  # 红色
    "debug": "\033[90m",     # 灰色
}
_RESET = "\033[0m"

# severity 标签
_LABELS = {
    "info": "[信息]",
    "warning": "[警告]",
    "critical": "[严重]",
    "debug": "[调试]",
}

# severity → emoji 映射，用于 webhook 消息格式化
_EMOJIS = {
    "info": "✅",
    "warning": "⚠️",
    "critical": "🚨",
}

# Discord 单条消息内容上限
_DISCORD_MAX_LEN = 2000


def _enable_vt100() -> None:
    """Windows 下尝试启用 VT100 转义序列支持。"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


class Alerter:
    """告警器，支持控制台输出和多平台 webhook 推送。"""

    def __init__(self, config: AlertsConfig, store: StateStore, state: dict):
        self.config = config
        self.store = store
        self.state = state
        _enable_vt100()

    def alert(
        self,
        key: str,
        title: str,
        body: str,
        severity: str = "info",
    ) -> bool:
        """触发告警。

        先按 dedupe_window 去重；通过控制台输出和/或 webhook 推送。
        返回是否实际触发（被去重则返回 False）。
        """
        # debug 级别不去重，直接打印
        if severity != "debug":
            if not self.store.is_alert_due(self.state, key, self.config.dedupe_window_secs):
                return False

        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # 控制台输出
        if self.config.console:
            color = _COLORS.get(severity, "")
            label = _LABELS.get(severity, f"[{severity}]")
            if color:
                print(f"{color}{ts_str} {label} {title}{_RESET}")
                print(f"{color}  {body}{_RESET}")
            else:
                print(f"{ts_str} {label} {title}")
                print(f"  {body}")

        # webhook 推送（按平台类型判断是否已配置）
        if severity != "debug" and self._webhook_enabled():
            self._send_webhook(title, body, severity, ts_str)

        # 标记已触发
        if severity != "debug":
            self.store.mark_alert(self.state, key)

        return True

    # ------------------------------------------------------------------
    # webhook 配置判断
    # ------------------------------------------------------------------
    def _webhook_type(self) -> str:
        """读取 webhook 平台类型，缺省回退为 generic。"""
        return getattr(self.config, "webhook_type", "generic") or "generic"

    def _webhook_enabled(self) -> bool:
        """判断 webhook 是否已配置必要字段。

        telegram 依靠 token + chat_id，其余平台依靠 webhook_url。
        """
        wtype = self._webhook_type()
        if wtype == "telegram":
            token = getattr(self.config, "webhook_token", "") or ""
            chat_id = getattr(self.config, "webhook_chat_id", "") or ""
            return bool(token) and bool(chat_id)
        return bool(self.config.webhook_url)

    # ------------------------------------------------------------------
    # webhook 发送总入口
    # ------------------------------------------------------------------
    def _send_webhook(self, title: str, body: str, severity: str, ts_str: str) -> None:
        """按 webhook_type 分派到对应平台发送，异常吞掉只打印警告。"""
        try:
            wtype = self._webhook_type()
            if wtype == "telegram":
                self._send_telegram(title, body, severity, ts_str)
            elif wtype == "discord":
                self._send_discord(title, body, severity, ts_str)
            elif wtype == "lark":
                self._send_lark(title, body, severity, ts_str)
            else:
                self._send_generic(title, body, severity, ts_str)
        except Exception as e:
            print(f"[告警] webhook 推送失败（不影响主循环）: {e}")

    def _post_json(self, url: str, payload: dict) -> None:
        """通用 JSON POST。

        代理由环境变量 HTTPS_PROXY / http_proxy 自动生效
        （urllib 默认 opener 内置 ProxyHandler，无需额外处理）。
        """
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)

    # ------------------------------------------------------------------
    # 各平台发送实现
    # ------------------------------------------------------------------
    def _send_generic(self, title: str, body: str, severity: str, ts_str: str) -> None:
        """通用 JSON POST：{title, body, severity, ts}。"""
        payload = {
            "title": title,
            "body": body,
            "severity": severity,
            "ts": ts_str,
        }
        self._post_json(self.config.webhook_url, payload)

    def _send_telegram(self, title: str, body: str, severity: str, ts_str: str) -> None:
        """Telegram Bot sendMessage。

        URL 由 bot token 拼接，使用 HTML 解析模式。
        text 含 severity emoji + title + body（附带时间戳）。
        """
        token = getattr(self.config, "webhook_token", "") or ""
        chat_id = getattr(self.config, "webhook_chat_id", "") or ""
        if not token or not chat_id:
            raise ValueError("Telegram 推送缺少 webhook_token 或 webhook_chat_id")

        emoji = _EMOJIS.get(severity, "ℹ️")
        text = f"{emoji} <b>{title}</b>\n{body}\n<code>{ts_str}</code>"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        self._post_json(url, payload)

    def _send_discord(self, title: str, body: str, severity: str, ts_str: str) -> None:
        """Discord Webhook：{content: msg}，超 2000 字截断。"""
        emoji = _EMOJIS.get(severity, "ℹ️")
        msg = f"{emoji} **{severity.upper()}** {title}\n{body}\n`{ts_str}`"
        if len(msg) > _DISCORD_MAX_LEN:
            msg = msg[: _DISCORD_MAX_LEN - 3] + "..."
        payload = {"content": msg}
        self._post_json(self.config.webhook_url, payload)

    def _send_lark(self, title: str, body: str, severity: str, ts_str: str) -> None:
        """飞书(Lark) Webhook：text 类型消息 {msg_type, content: {text}}。"""
        emoji = _EMOJIS.get(severity, "ℹ️")
        msg = f"{emoji} [{severity}] {title}\n{body}\n{ts_str}"
        payload = {"msg_type": "text", "content": {"text": msg}}
        self._post_json(self.config.webhook_url, payload)
