"""后端配置管理模块。

使用 Pydantic Settings 从 YAML 文件 + 环境变量加载配置。
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── 子配置模型 ──────────────────────────────────────────────

class ProxyConfig(BaseModel):
    """HTTP/HTTPS 代理配置。"""
    http: str = ""
    https: str = ""


class PolymarketCredentials(BaseModel):
    """Polymarket API 凭据（可选，真实跟单用）。"""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    wallet_address: str = ""
    private_key: str = ""
    signature_type: int = Field(default=0, ge=0, le=2)
    funder: str = ""


class MonitorConfig(BaseModel):
    """监控轮询配置。"""
    interval: int = Field(default=2, ge=1, description="轮询间隔（秒），秒级实时建议 2s")
    ws_enabled: bool = Field(default=True, description="是否启用 WebSocket Market Channel 实时价格")
    min_interval: int = Field(default=1, ge=1, description="最小轮询间隔（秒）")
    # ── 订单金额监测范围 ──
    # 只把「成交金额 = 价格 × 数量」落在此闭区间内的交易送入监测与跟单链路；
    # min_amount = 0 表示不设下限，max_amount = 0 表示不设上限。
    min_amount: float = Field(
        default=0.0, ge=0, description="只监测金额 >= 此值的交易 (USDC)，0 = 不设下限"
    )
    max_amount: float = Field(
        default=0.0, ge=0, description="只监测金额 <= 此值的交易 (USDC)，0 = 不设上限"
    )


class RiskConfig(BaseModel):
    """风险控制参数。"""
    max_trade_amount: float = Field(default=100.0, gt=0, description="单笔最大金额 (USDC)")
    max_position_per_market: float = Field(default=500.0, gt=0, description="单市场最大仓位 (USDC)")
    max_daily_trades: int = Field(default=50, ge=1, description="日最大交易数")


class ExecutorConfig(BaseModel):
    """执行器配置。"""
    mode: str = Field(default="paper", pattern="^(paper|live)$", description="执行模式")
    max_signal_age_seconds: int = Field(default=60, ge=1, le=3600)


class RealtimeConfig(BaseModel):
    enabled: bool = True
    rest_interval: int = Field(default=2, ge=1, le=60)
    settlement_interval: int = Field(default=15, ge=5, le=300)


class NotificationConfig(BaseModel):
    """通知推送配置（Telegram + 邮件）。"""

    # ── Telegram ──
    telegram_enabled: bool = Field(default=False, description="是否启用 Telegram 通知")
    telegram_token: str = Field(default="", description="Telegram Bot Token")
    telegram_chat_ids: list[str] = Field(
        default_factory=list, description="Telegram 接收方 chat_id 列表（群组 / 用户）"
    )

    # ── 邮件 ──
    email_enabled: bool = Field(default=False, description="是否启用邮件通知")
    email_host: str = Field(default="", description="SMTP 服务器地址")
    email_port: int = Field(default=587, ge=1, le=65535, description="SMTP 端口（465=SSL，587=STARTTLS）")
    email_user: str = Field(default="", description="SMTP 用户名")
    email_password: str = Field(default="", description="SMTP 密码 / 授权码")
    email_from: str = Field(default="", description="发件人地址（留空则使用 email_user）")
    email_to: list[str] = Field(default_factory=list, description="收件人列表")
    email_use_ssl: bool = Field(default=False, description="是否使用隐式 SSL（465）；否则用 STARTTLS（587）")

    # ── 过滤规则 ──
    min_notify_amount: float = Field(default=0.0, ge=0, description="最小通知金额阈值 (USDC)，0 = 不过滤")
    notify_events: list[str] = Field(
        default_factory=list,
        description="要通知的事件类型白名单，空 = 全部通知",
    )
    notify_wallets: list[str] = Field(
        default_factory=list, description="仅通知这些钱包（源交易钱包），空 = 不限制"
    )
    cooldown_seconds: float = Field(default=0.0, ge=0, description="同一事件冷却期（秒），0 = 不限制")

    # ── 每日汇总 ──
    daily_summary_hour: int = Field(
        default=8, ge=0, le=23, description="每日汇总发送时间（UTC 小时）"
    )
    daily_summary_minute: int = Field(
        default=0, ge=0, le=59, description="每日汇总发送时间（UTC 分钟）"
    )


class ServerConfig(BaseModel):
    """服务器配置。"""
    port: int = Field(default=8765, ge=1, le=65535)
    host: str = "0.0.0.0"


# ── 顶层 Settings ───────────────────────────────────────────

class Settings(BaseSettings):
    """应用全局配置。

    优先级: 环境变量 > YAML 文件 > 默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    polymarket: PolymarketCredentials = Field(default_factory=PolymarketCredentials)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    realtime: RealtimeConfig = Field(default_factory=RealtimeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)

    # 数据库路径
    db_path: str = "data/trader.db"

    # 前端静态文件目录
    frontend_dir: str = "frontend"


def _load_yaml(path: str | Path) -> dict[str, Any]:
    """从 YAML 文件加载配置字典。"""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _split_csv(value: str) -> list[str]:
    """把逗号分隔的字符串拆成字符串列表（去除空项）。"""
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _to_bool(value: str) -> bool:
    """把字符串转成布尔值。"""
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """将环境变量映射覆盖到配置字典中。"""
    # (section, field) 或 (section, field, 转换函数)
    env_map: dict[str, tuple[str, str] | tuple[str, str, Any]] = {
        "PROXY_HTTP": ("proxy", "http"),
        "PROXY_HTTPS": ("proxy", "https"),
        "POLYMARKET_API_KEY": ("polymarket", "api_key"),
        "POLYMARKET_API_SECRET": ("polymarket", "api_secret"),
        "POLYMARKET_API_PASSPHRASE": ("polymarket", "api_passphrase"),
        "POLYMARKET_WALLET_ADDRESS": ("polymarket", "wallet_address"),
        "POLYMARKET_PRIVATE_KEY": ("polymarket", "private_key"),
        "POLYMARKET_SIGNATURE_TYPE": ("polymarket", "signature_type", int),
        "POLYMARKET_FUNDER": ("polymarket", "funder"),
        "MONITOR_INTERVAL": ("monitor", "interval"),
        "MONITOR_WS_ENABLED": ("monitor", "ws_enabled"),
        "MONITOR_MIN_INTERVAL": ("monitor", "min_interval"),
        "MONITOR_MIN_AMOUNT": ("monitor", "min_amount", float),
        "MONITOR_MAX_AMOUNT": ("monitor", "max_amount", float),
        "RISK_MAX_TRADE_AMOUNT": ("risk", "max_trade_amount"),
        "RISK_MAX_POSITION_PER_MARKET": ("risk", "max_position_per_market"),
        "RISK_MAX_DAILY_TRADES": ("risk", "max_daily_trades"),
        "EXECUTOR_MODE": ("executor", "mode"),
        "SERVER_PORT": ("server", "port"),
        "SERVER_HOST": ("server", "host"),

        # ── 通知设置 ──
        "TELEGRAM_ENABLED": ("notification", "telegram_enabled", _to_bool),
        "TELEGRAM_TOKEN": ("notification", "telegram_token"),
        "TELEGRAM_CHAT_IDS": ("notification", "telegram_chat_ids", _split_csv),
        "EMAIL_ENABLED": ("notification", "email_enabled", _to_bool),
        "EMAIL_HOST": ("notification", "email_host"),
        "EMAIL_PORT": ("notification", "email_port"),
        "EMAIL_USER": ("notification", "email_user"),
        "EMAIL_PASSWORD": ("notification", "email_password"),
        "EMAIL_FROM": ("notification", "email_from"),
        "EMAIL_TO": ("notification", "email_to", _split_csv),
        "EMAIL_USE_SSL": ("notification", "email_use_ssl", _to_bool),
        "NOTIFY_MIN_AMOUNT": ("notification", "min_notify_amount"),
        "NOTIFY_EVENTS": ("notification", "notify_events", _split_csv),
        "NOTIFY_WALLETS": ("notification", "notify_wallets", _split_csv),
        "NOTIFY_COOLDOWN": ("notification", "cooldown_seconds"),
        "NOTIFY_SUMMARY_HOUR": ("notification", "daily_summary_hour"),
        "NOTIFY_SUMMARY_MINUTE": ("notification", "daily_summary_minute"),
    }
    for env_key, entry in env_map.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        section, field = entry[0], entry[1]
        section_dict = data.setdefault(section, {})
        if len(entry) == 3:
            # 显式转换函数（布尔 / 列表等）
            section_dict[field] = entry[2](val)
            continue
        # 尝试类型转换
        current = section_dict.get(field)
        if isinstance(current, bool):
            # 布尔值：支持 true/false/1/0
            section_dict[field] = _to_bool(val)
        elif isinstance(current, int):
            section_dict[field] = int(val)
        elif isinstance(current, float):
            section_dict[field] = float(val)
        else:
            section_dict[field] = val
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    从 CONFIG_FILE 环境变量或默认 config.yaml 加载 YAML，
    然后叠加环境变量覆盖。
    """
    from dotenv import dotenv_values
    for key, value in dotenv_values(PROJECT_ROOT / ".env").items():
        if value is not None:
            os.environ.setdefault(key, value)
    config_file = os.environ.get("CONFIG_FILE", str(PROJECT_ROOT / "config.yaml"))
    yaml_data = _load_yaml(config_file)
    yaml_data = _apply_env_overrides(yaml_data)
    settings = Settings(**yaml_data)
    for field in ("db_path", "frontend_dir"):
        path = Path(getattr(settings, field))
        if not path.is_absolute():
            setattr(settings, field, str(PROJECT_ROOT / path))
    return settings


def save_runtime_config(changes: dict[str, Any]) -> None:
    """原子保存界面设置，保留配置中的其他字段。"""
    path = Path(os.environ.get("CONFIG_FILE", str(PROJECT_ROOT / "config.yaml")))
    data = _load_yaml(path)
    for section, values in changes.items():
        data.setdefault(section, {}).update(values)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False, suffix=".yaml") as output:
            temp_path = output.name
            yaml.safe_dump(data, output, allow_unicode=True, sort_keys=False)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def reload_settings() -> Settings:
    """强制重新加载配置（清除缓存）。"""
    get_settings.cache_clear()
    return get_settings()
