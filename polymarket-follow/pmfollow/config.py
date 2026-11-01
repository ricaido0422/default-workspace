"""配置加载模块。

从 JSON 文件加载配置，缺失字段用默认值填充，不报错。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FollowConfig:
    """跟单配置。"""

    wallets: List[str] = field(default_factory=list)
    # 钱包地址 -> 自定义备注名（UI 显示用，如 "RN1"）
    wallet_labels: Dict[str, str] = field(default_factory=dict)
    from_leaderboard: Dict[str, Any] = field(
        default_factory=lambda: {"enabled": True, "window": "all", "limit": 5}
    )
    poll_interval_secs: int = 30
    min_position_value_usd: float = 5.0
    only_active_markets: bool = True
    max_market_age_days: int = 365
    # 只显示「当前仍真实持仓」的信号（过滤已平仓/已结束的市场）
    only_open_positions: bool = True


@dataclass
class MonitorConfig:
    """市场监测配置。"""

    enabled: bool = True
    markets: List[Any] = field(default_factory=list)
    from_top_volume: Dict[str, Any] = field(
        default_factory=lambda: {"enabled": True, "limit": 10}
    )
    poll_interval_secs: int = 20
    price_move_alert_pct: float = 5.0
    spread_alert_max: float = 0.05
    use_websocket: bool = False
    ws_ping_interval: int = 10


@dataclass
class AlertsConfig:
    """告警配置。"""

    console: bool = True
    webhook_url: str = ""
    webhook_type: str = "generic"  # generic | telegram | discord | lark
    webhook_token: str = ""        # Telegram bot token
    webhook_chat_id: str = ""      # Telegram chat_id
    dedupe_window_secs: int = 300


@dataclass
class ExecutorConfig:
    """跟单执行器配置（真实下单）。"""

    enabled: bool = False          # 默认关闭，安全优先
    dry_run: bool = True           # 默认 dry-run，不下真单
    private_key: str = ""          # 私钥（不打印）
    api_key: str = ""              # L2 API key
    api_secret: str = ""           # L2 API secret
    api_passphrase: str = ""       # L2 passphrase
    wallet_address: str = ""       # 代理钱包地址（funder）
    signature_type: int = 2        # 2=GNOSIS_SAFE
    # 风控参数
    max_order_usd: float = 50.0    # 单笔最大 USD
    max_daily_usd: float = 500.0   # 每日最大 USD
    follow_ratio: float = 0.1      # 跟单比例：复制大户操作的 10%
    min_price: float = 0.02        # 最低买入价
    max_price: float = 0.98        # 最高买入价
    slippage_pct: float = 0.02     # 滑点容忍 2%
    # 代理
    proxy: str = ""                # HTTP 代理（留空则继承顶层 proxy）


@dataclass
class Config:
    """顶层配置，聚合各子配置。"""

    proxy: Optional[str] = None
    api: Dict[str, str] = field(
        default_factory=lambda: {
            "data_api": "https://data-api.polymarket.com",
            "gamma_api": "https://gamma-api.polymarket.com",
            "clob_api": "https://clob.polymarket.com",
        }
    )
    follow: FollowConfig = field(default_factory=FollowConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)
    storage: Dict[str, Any] = field(
        default_factory=lambda: {"state_file": "state.json"}
    )


def _deep_merge(base: dict, overlay: dict) -> dict:
    """递归合并 overlay 到 base，overlay 的值优先。"""
    result = dict(base)
    for key, val in overlay.items():
        if key.startswith("_comment"):
            continue
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _default_dict() -> dict:
    """返回完整的默认配置字典。"""
    return {
        "proxy": None,
        "api": {
            "data_api": "https://data-api.polymarket.com",
            "gamma_api": "https://gamma-api.polymarket.com",
            "clob_api": "https://clob.polymarket.com",
        },
        "follow": {
            "wallets": [],
            "wallet_labels": {},
            "from_leaderboard": {"enabled": True, "window": "all", "limit": 5},
            "poll_interval_secs": 30,
            "min_position_value_usd": 5,
            "only_active_markets": True,
            "max_market_age_days": 365,
            "only_open_positions": True,
        },
        "monitor": {
            "enabled": True,
            "markets": [],
            "from_top_volume": {"enabled": True, "limit": 10},
            "poll_interval_secs": 20,
            "price_move_alert_pct": 5,
            "spread_alert_max": 0.05,
            "use_websocket": False,
            "ws_ping_interval": 10,
        },
        "alerts": {
            "console": True,
            "webhook_url": "",
            "webhook_type": "generic",
            "webhook_token": "",
            "webhook_chat_id": "",
            "dedupe_window_secs": 300,
        },
        "executor": {
            "enabled": False,
            "dry_run": True,
            "private_key": "",
            "api_key": "",
            "api_secret": "",
            "api_passphrase": "",
            "wallet_address": "",
            "signature_type": 2,
            "max_order_usd": 50,
            "max_daily_usd": 500,
            "follow_ratio": 0.1,
            "min_price": 0.02,
            "max_price": 0.98,
            "slippage_pct": 0.02,
            "proxy": "",
        },
        "storage": {"state_file": "state.json"},
    }


def load_config(path: str = "config.json") -> Config:
    """从 JSON 文件加载配置。

    缺失字段用默认值填充，文件不存在时返回全默认配置。
    """
    default = _default_dict()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        merged = _deep_merge(default, raw)
    else:
        merged = default

    follow = FollowConfig(
        wallets=merged["follow"].get("wallets", []),
        wallet_labels=merged["follow"].get("wallet_labels", {}) or {},
        from_leaderboard=merged["follow"].get("from_leaderboard", {}),
        poll_interval_secs=merged["follow"].get("poll_interval_secs", 30),
        min_position_value_usd=merged["follow"].get("min_position_value_usd", 5),
        only_active_markets=merged["follow"].get("only_active_markets", True),
        max_market_age_days=merged["follow"].get("max_market_age_days", 365),
        only_open_positions=merged["follow"].get("only_open_positions", True),
    )

    monitor = MonitorConfig(
        enabled=merged["monitor"].get("enabled", True),
        markets=merged["monitor"].get("markets", []),
        from_top_volume=merged["monitor"].get("from_top_volume", {}),
        poll_interval_secs=merged["monitor"].get("poll_interval_secs", 20),
        price_move_alert_pct=merged["monitor"].get("price_move_alert_pct", 5),
        spread_alert_max=merged["monitor"].get("spread_alert_max", 0.05),
        use_websocket=merged["monitor"].get("use_websocket", False),
        ws_ping_interval=merged["monitor"].get("ws_ping_interval", 10),
    )

    alerts = AlertsConfig(
        console=merged["alerts"].get("console", True),
        webhook_url=merged["alerts"].get("webhook_url", ""),
        webhook_type=merged["alerts"].get("webhook_type", "generic"),
        webhook_token=merged["alerts"].get("webhook_token", ""),
        webhook_chat_id=merged["alerts"].get("webhook_chat_id", ""),
        dedupe_window_secs=merged["alerts"].get("dedupe_window_secs", 300),
    )

    ex = merged.get("executor", {})
    executor = ExecutorConfig(
        enabled=ex.get("enabled", False),
        dry_run=ex.get("dry_run", True),
        private_key=ex.get("private_key", ""),
        api_key=ex.get("api_key", ""),
        api_secret=ex.get("api_secret", ""),
        api_passphrase=ex.get("api_passphrase", ""),
        wallet_address=ex.get("wallet_address", ""),
        signature_type=ex.get("signature_type", 2),
        max_order_usd=ex.get("max_order_usd", 50),
        max_daily_usd=ex.get("max_daily_usd", 500),
        follow_ratio=ex.get("follow_ratio", 0.1),
        min_price=ex.get("min_price", 0.02),
        max_price=ex.get("max_price", 0.98),
        slippage_pct=ex.get("slippage_pct", 0.02),
        proxy=ex.get("proxy", ""),
    )

    return Config(
        proxy=merged.get("proxy"),
        api=merged.get("api", default["api"]),
        follow=follow,
        monitor=monitor,
        alerts=alerts,
        executor=executor,
        storage=merged.get("storage", {"state_file": "state.json"}),
    )


def save_config(config: Config, path: str = "config.json") -> None:
    """将配置写回 JSON 文件（用于 bootstrap 命令更新 wallets）。"""
    data = {
        "proxy": config.proxy,
        "api": config.api,
        "follow": {
            "wallets": config.follow.wallets,
            "wallet_labels": config.follow.wallet_labels,
            "from_leaderboard": config.follow.from_leaderboard,
            "poll_interval_secs": config.follow.poll_interval_secs,
            "min_position_value_usd": config.follow.min_position_value_usd,
            "only_active_markets": config.follow.only_active_markets,
            "max_market_age_days": config.follow.max_market_age_days,
            "only_open_positions": config.follow.only_open_positions,
        },
        "monitor": {
            "enabled": config.monitor.enabled,
            "markets": config.monitor.markets,
            "from_top_volume": config.monitor.from_top_volume,
            "poll_interval_secs": config.monitor.poll_interval_secs,
            "price_move_alert_pct": config.monitor.price_move_alert_pct,
            "spread_alert_max": config.monitor.spread_alert_max,
            "use_websocket": config.monitor.use_websocket,
            "ws_ping_interval": config.monitor.ws_ping_interval,
        },
        "alerts": {
            "console": config.alerts.console,
            "webhook_url": config.alerts.webhook_url,
            "webhook_type": config.alerts.webhook_type,
            "webhook_token": config.alerts.webhook_token,
            "webhook_chat_id": config.alerts.webhook_chat_id,
            "dedupe_window_secs": config.alerts.dedupe_window_secs,
        },
        "executor": {
            "enabled": config.executor.enabled,
            "dry_run": config.executor.dry_run,
            "private_key": config.executor.private_key,
            "api_key": config.executor.api_key,
            "api_secret": config.executor.api_secret,
            "api_passphrase": config.executor.api_passphrase,
            "wallet_address": config.executor.wallet_address,
            "signature_type": config.executor.signature_type,
            "max_order_usd": config.executor.max_order_usd,
            "max_daily_usd": config.executor.max_daily_usd,
            "follow_ratio": config.executor.follow_ratio,
            "min_price": config.executor.min_price,
            "max_price": config.executor.max_price,
            "slippage_pct": config.executor.slippage_pct,
            "proxy": config.executor.proxy,
        },
        "storage": config.storage,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
