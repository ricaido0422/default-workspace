#!/usr/bin/env python3
"""Polymarket 跟单与监测 — Web UI 控制台。

一个自包含的本地网页控制台：启动/停止跟单与监测循环、实时查看信号流、
调整风控参数、查看大佬持仓与市场行情。

设计要点：
    - 只用 Python 标准库（http.server / threading / json），无需额外安装。
    - 页面纯静态 HTML + 原生 JS，每 2 秒轮询 /api/state，无外部 CDN 依赖。
    - 后台线程跑跟单/监测循环，事件写入内存环形日志（最多 300 条）。
    - 配置改动通过 POST /api/config 写回 config.json。
    - 私钥永不通过网络返回，只返回"是否已配置"。

用法：
    python ui.py                # 默认 127.0.0.1:8791 并自动打开浏览器
    python ui.py --port 9000    # 指定端口
    python ui.py --no-browser   # 不自动打开浏览器

注意：UI 进程是 state.json 的唯一写入口，运行时不要再同时跑 main.py，
避免两个进程互相覆盖状态文件。
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# 路径与依赖加载（与 main.py 保持一致）
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

_DEPS_DIR = os.path.join(BASE_DIR, ".deps")
if os.path.isdir(_DEPS_DIR):
    sys.path.insert(0, _DEPS_DIR)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pmfollow.config import ExecutorConfig, load_config, save_config  # noqa: E402
from pmfollow.httpclient import HttpClient  # noqa: E402
from pmfollow.dataapi import DataApi  # noqa: E402
from pmfollow.gamma import GammaApi  # noqa: E402
from pmfollow.clob import ClobApi  # noqa: E402
from pmfollow.store import StateStore  # noqa: E402
from pmfollow.alert import Alerter  # noqa: E402
from pmfollow.tracker import CopyTracker  # noqa: E402
from pmfollow.monitor import MarketMonitor  # noqa: E402
from pmfollow.executor import CopyExecutor  # noqa: E402

MAX_EVENTS = 300
DEFAULT_PORT = 8791


def _fmt_money(val) -> str:
    """格式化美元金额，None → '-'。"""
    if val is None:
        return "-"
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return str(val)


def _short(addr: str, head: int = 8, tail: int = 6) -> str:
    """钱包地址脱敏显示。"""
    if not addr:
        return "-"
    if len(addr) <= head + tail:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"


def _esc(text: Any) -> str:
    """HTML 转义，防止市场标题里的引号破坏页面。"""
    s = "" if text is None else str(text)
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# 引擎：持有组件、跑循环、维护事件日志
# ---------------------------------------------------------------------------

class Engine:
    """跟单+监测引擎，UI 的唯一数据来源。"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = load_config(config_path)

        # 线程同步
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()
        self.threads: List[threading.Thread] = []
        self.running = False
        self.started_at: Optional[float] = None

        # 事件日志（最新在前）
        self.events: List[dict] = []
        # 计数器（跨日重置）
        self.counters = {"date": time.strftime("%Y-%m-%d"), "signals": 0,
                         "big": 0, "exec": 0, "reject": 0}
        # 最近一次自检结果
        self.selfcheck: Dict[str, Any] = {"ts": 0, "results": []}
        # 最近一轮市场信息（MarketInfo 列表，转 dict 用）
        self.last_markets: List[dict] = []
        # 最近一轮钱包快照（地址 -> 汇总）
        self.wallet_rows: List[dict] = []
        # 「我的钱包」数据（真实持仓/组合价值），带刷新节流
        self.my_wallet: Dict[str, Any] = {"ts": 0}
        self._my_wallet_ts: float = 0.0

        self._build()

    # ---- 组件构建 ----

    def _build(self):
        """构建 API 客户端、tracker、monitor、executor。"""
        cfg = self.config
        self.client = HttpClient(proxy=cfg.proxy)
        self.data_api = DataApi(self.client, base=cfg.api.get("data_api", ""))
        self.gamma_api = GammaApi(self.client, base=cfg.api.get("gamma_api", ""))
        self.clob_api = ClobApi(self.client, base=cfg.api.get("clob_api", ""))

        state_file = cfg.storage.get("state_file", "state.json")
        if not os.path.isabs(state_file):
            state_file = os.path.join(BASE_DIR, state_file)
        self.store = StateStore(path=state_file)
        self.state = self.store.load()

        self.alerter = Alerter(cfg.alerts, self.store, self.state)

        self.tracker = CopyTracker(
            self.data_api, self.gamma_api, cfg.follow, self.store,
            self.alerter, self.state,
        )
        self.monitor = MarketMonitor(
            self.clob_api, self.gamma_api, self.data_api, cfg.monitor,
            self.store, self.alerter, self.state,
        )

        self.executor = None
        if cfg.executor.enabled:
            self._rebuild_executor()

    def _rebuild_executor(self):
        """按当前配置重建执行器（可能为 None）。"""
        cfg = self.config
        if not cfg.executor.enabled:
            self.executor = None
            return
        exec_proxy = cfg.executor.proxy or cfg.proxy or ""
        exec_config = ExecutorConfig(
            enabled=cfg.executor.enabled,
            dry_run=cfg.executor.dry_run,
            private_key=cfg.executor.private_key,
            api_key=cfg.executor.api_key,
            api_secret=cfg.executor.api_secret,
            api_passphrase=cfg.executor.api_passphrase,
            wallet_address=cfg.executor.wallet_address,
            signature_type=cfg.executor.signature_type,
            max_order_usd=cfg.executor.max_order_usd,
            max_daily_usd=cfg.executor.max_daily_usd,
            follow_ratio=cfg.executor.follow_ratio,
            min_price=cfg.executor.min_price,
            max_price=cfg.executor.max_price,
            slippage_pct=cfg.executor.slippage_pct,
            proxy=exec_proxy,
        )
        self.executor = CopyExecutor(exec_config, alerter=self.alerter)

    # ---- 事件日志 ----

    def log(self, kind: str, title: str, detail: str = "", severity: str = "info"):
        """写入一条事件到环形日志。kind: signal/alert/exec/system/error"""
        ev = {
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S", time.localtime()),
            "kind": kind,
            "severity": severity,
            "title": title,
            "detail": detail,
        }
        with self.lock:
            self.events.insert(0, ev)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[:MAX_EVENTS]

    def _rollover_counters(self):
        today = time.strftime("%Y-%m-%d")
        if self.counters.get("date") != today:
            self.counters = {"date": today, "signals": 0, "big": 0,
                             "exec": 0, "reject": 0}

    # ---- 循环控制 ----

    def start(self) -> bool:
        """启动跟单+监测循环。"""
        with self.lock:
            if self.running:
                return False
            self.stop_evt.clear()
            self.running = True
            self.started_at = time.time()

        t_follow = threading.Thread(target=self._follow_loop, daemon=True,
                                    name="ui-follow")
        t_mon = threading.Thread(target=self._monitor_loop, daemon=True,
                                 name="ui-monitor")
        t_follow.start()
        t_mon.start()
        self.threads = [t_follow, t_mon]

        self.log("system", "引擎已启动",
                 f"跟单间隔 {self.config.follow.poll_interval_secs}s，"
                 f"监测间隔 {self.config.monitor.poll_interval_secs}s",
                 "success")
        return True

    def stop(self) -> bool:
        """停止循环。"""
        with self.lock:
            if not self.running:
                return False
            self.running = False
        self.stop_evt.set()
        self.log("system", "引擎已停止", "循环线程将在本轮结束后退出", "warning")
        return True

    def _follow_loop(self):
        interval = max(1, int(self.config.follow.poll_interval_secs))
        while not self.stop_evt.is_set():
            try:
                self.run_follow_cycle()
            except Exception as e:  # 单轮异常不致命
                self.log("error", "跟单循环异常", str(e), "error")
            self.stop_evt.wait(interval)

    def _monitor_loop(self):
        interval = max(5, int(self.config.monitor.poll_interval_secs))
        # 启动后先立即跑一轮监测，再进入周期
        while not self.stop_evt.is_set():
            try:
                self.run_monitor_cycle()
            except Exception as e:
                self.log("error", "监测循环异常", str(e), "error")
            self.stop_evt.wait(interval)

    # ---- 单轮执行 ----

    def run_follow_cycle(self):
        """跑一轮跟单：拉持仓+交易，产出信号，必要时交给执行器。"""
        cfg = self.config
        min_val = float(cfg.follow.min_position_value_usd or 0)
        wallets = self.tracker.resolve_wallets()

        for wallet in wallets:
            if self.stop_evt.is_set():
                return
            try:
                signals = self.tracker.poll_wallet(wallet)
            except Exception as e:
                self.log("error", f"拉取 {_short(wallet)} 失败", str(e), "error")
                continue

            for sig in signals:
                value = sig.value_usd
                is_small = (sig.kind in ("NEW_POSITION", "INCREASE")
                            and (value or 0) < min_val)
                self._record_signal(sig, is_small=is_small)
                # 小额信号只记录不告警不执行；大额信号照常告警+执行
                if not is_small:
                    self._alert_signal(sig)
                    self._maybe_execute(sig)

        # 刷新钱包汇总表
        self._refresh_wallet_rows(wallets)
        # 状态落盘
        try:
            self.store.save(self.state)
        except Exception as e:
            self.log("error", "状态保存失败", str(e), "error")

    def _record_signal(self, sig, is_small: bool):
        """把信号写入事件日志并计数。"""
        self._rollover_counters()
        self.counters["signals"] += 1
        if not is_small:
            self.counters["big"] += 1

        kind_label = {
            "NEW_POSITION": "新开仓", "INCREASE": "加仓",
            "DECREASE": "减仓", "CLOSED": "平仓", "NEW_TRADE": "新成交",
        }.get(sig.kind, sig.kind)
        side_label = {"BUY": "买入", "SELL": "卖出"}.get(sig.side, sig.side or "-")
        title = f"{kind_label} · {sig.title or sig.condition_id[:16]}"
        detail = (f"{side_label} {sig.outcome or '-'} | "
                  f"数量 {sig.size:.2f} | 价值 {_fmt_money(sig.value_usd)} | "
                  f"价格 {sig.price if sig.price is not None else '-'}")
        # 前缀带上用户名，方便一眼看出是哪个大户
        who = ""
        try:
            who = self.tracker.wallet_label(sig.wallet)
        except Exception:
            who = _short(sig.wallet)
        if who:
            title = f"[{who}] {title}"
        self.log("signal", title, detail,
                 "debug" if is_small else ("warning" if sig.kind in ("CLOSED", "DECREASE") else "info"))

    def _alert_signal(self, sig):
        """大额信号走 Alerter（webhook + 控制台 + 去重）。"""
        ts_min = int(time.time() / 60)
        key = f"{sig.wallet}:{sig.kind}:{sig.token_id}:{ts_min}"
        kind_label = {
            "NEW_POSITION": "新开仓", "INCREASE": "加仓",
            "DECREASE": "减仓", "CLOSED": "平仓", "NEW_TRADE": "新成交",
        }.get(sig.kind, sig.kind)
        title = f"[{self.tracker.wallet_label(sig.wallet)}] {kind_label}: {sig.title}"
        val = f"${sig.value_usd:.2f}" if sig.value_usd is not None else "N/A"
        body = (f"方向={sig.side} 数量={sig.size:.4f} "
                f"价格={sig.price} 价值={val} outcome={sig.outcome}")
        try:
            self.alerter.alert(key, title, body, severity="info")
        except Exception:
            pass

    def _maybe_execute(self, sig):
        """执行器跟单（若已启用）。"""
        if not self.executor:
            return
        try:
            result = self.executor.execute_signal(sig)
        except Exception as e:
            self.log("error", "执行异常", str(e), "error")
            return
        action = result.get("action", "")
        if result.get("success"):
            self.counters["exec"] += 1
            mode = "DRY-RUN" if action == "DRY_RUN" else "LIVE"
            self.log("exec", f"已跟单（{mode}）",
                     f"{sig.title} | {action} | {result.get('detail', '')}",
                     "success")
        else:
            if action == "REJECT":
                self.counters["reject"] += 1
                self.log("exec", "跟单被风控拒绝",
                         f"{sig.title} | {result.get('detail', '')}", "warning")
            # SKIP / FAIL 不单独刷屏，避免日志噪音

    def run_monitor_cycle(self):
        """跑一轮市场监测。"""
        if not self.config.monitor.enabled:
            return
        try:
            markets = self.monitor.resolve_markets()
        except Exception as e:
            self.log("error", "解析监测市场失败", str(e), "error")
            return

        rows = []
        for m in markets:
            if self.stop_evt.is_set():
                return
            try:
                alerts = self.monitor.poll_market(m)
            except Exception as e:
                self.log("error", f"监测 {m.condition_id[:12]}… 失败", str(e), "error")
                continue
            for a in alerts:
                self.log("alert", f"{a.kind}: {a.title}", a.detail, a.severity)
            rows.append(self._market_row(m))
        self.last_markets = rows
        try:
            self.store.save(self.state)
        except Exception:
            pass

    # ---- 数据汇总 ----

    def _market_row(self, m) -> dict:
        snap = self.state.get("markets", {}).get(m.condition_id, {})
        return {
            "condition_id": m.condition_id,
            "question": m.question,
            "outcomes": m.outcomes or [],
            "outcome_prices": [_safe_price(p) for p in (m.outcome_prices or [])],
            "last_price": snap.get("last_price", m.last_trade_price),
            "spread": snap.get("spread", m.spread),
            "volume24hr": m.volume24hr,
            "active": m.active,
            "accepting_orders": m.accepting_orders,
            "slug": m.slug,
        }

    def _refresh_wallet_rows(self, wallets: List[str]):
        """从 state 快照生成钱包汇总。

        每行带上**平台用户名**（tracker.wallet_label），这样一眼能看出
        每个地址是谁。state 里也存一份 wallet_names 供页面展示。
        """
        rows = []
        for w in wallets:
            snap = self.state.get("wallets", {}).get(w, {})
            positions = snap.get("positions", {}) or {}
            total = 0.0
            for info in positions.values():
                try:
                    total += float(info.get("value") or 0)
                except (TypeError, ValueError):
                    pass
            name = ""
            try:
                name = self.tracker.wallet_label(w)
            except Exception:
                name = _short(w)
            # 缓存用户名到 state，供「我的钱包」等其它地方复用
            if name and not name.startswith("0x"):
                self.state.setdefault("wallet_names", {})[w] = {
                    "name": name, "ts": int(time.time()),
                }
            rows.append({
                "wallet": w,
                "short": _short(w),
                "name": name,
                "positions": len(positions),
                "value": total,
                "ts": snap.get("ts", 0),
            })
        self.wallet_rows = rows

    # ---- 我的钱包（真实持仓）----

    def my_wallet_address(self) -> str:
        """返回「我的钱包」地址：优先执行器配置的地址。"""
        return (self.config.executor.wallet_address or "").strip()

    def refresh_my_wallet(self, force: bool = False):
        """拉取我的真实持仓与组合价值（15s 节流，除非 force）。"""
        now = time.time()
        if not force and (now - self._my_wallet_ts) < 15:
            return self.my_wallet
        self._my_wallet_ts = now

        me = self.my_wallet_address()
        if not me:
            self.my_wallet = {"ts": int(now), "configured": False,
                              "positions": [], "total_value": 0.0}
            return self.my_wallet

        rows: List[dict] = []
        total = 0.0
        err = ""
        try:
            positions, _ = self.data_api.positions(user=me, status="OPEN", limit=200)
        except Exception as e:
            positions = []
            err = str(e)[:120]

        for p in positions:
            try:
                val = float(p.current_value or 0)
            except (TypeError, ValueError):
                val = 0.0
            total += val
            rows.append({
                "condition_id": p.condition_id,
                "title": p.title,
                "outcome": p.outcome,
                "size": p.size,
                "avg_price": p.avg_price,
                "current_price": p.current_price,
                "value": val,
                "pnl": p.total_pnl,
                "slug": p.slug,
                "end_date": p.end_date,
            })
        rows.sort(key=lambda r: r.get("value") or 0, reverse=True)

        # 组合总价值（平台口径）
        try:
            v = self.data_api.value(me)
            if isinstance(v, dict):
                total = float(v.get("value") or total)
        except Exception:
            pass

        self.my_wallet = {
            "ts": int(now),
            "configured": True,
            "address": me,
            "short": _short(me),
            "positions": rows,
            "total_value": total,
            "error": err,
        }
        return self.my_wallet

    # ---- 跟单钱包管理 ----

    def add_follow_wallet(self, address: str, name: str = "") -> Dict[str, Any]:
        """新增一个跟单钱包（可带备注名）。"""
        addr = (address or "").strip()
        if not (addr.startswith("0x") and len(addr) == 42):
            return {"ok": False, "msg": "地址格式不对：应为 0x 开头的 42 位地址"}
        if addr.lower() in [w.lower() for w in self.config.follow.wallets]:
            return {"ok": False, "msg": "这个钱包已经在跟单列表里了"}
        self.config.follow.wallets.append(addr)
        self._save_and_log_wallets()
        return {"ok": True, "msg": f"已加入 {addr[:10]}…"}

    def remove_follow_wallet(self, address: str) -> Dict[str, Any]:
        """从跟单列表移除一个钱包（同时清掉它的备注名）。"""
        addr = (address or "").strip()
        before = len(self.config.follow.wallets)
        self.config.follow.wallets = [
            w for w in self.config.follow.wallets
            if w.lower() != addr.lower()
        ]
        if len(self.config.follow.wallets) == before:
            return {"ok": False, "msg": "列表里没有这个地址"}
        self.config.follow.wallet_labels.pop(addr, None)
        # 清掉 state 里该钱包的快照，避免残留旧持仓
        self.state.get("wallets", {}).pop(addr, None)
        self._save_and_log_wallets()
        return {"ok": True, "msg": f"已移除 {addr[:10]}…"}

    def rename_follow_wallet(self, address: str, name: str) -> Dict[str, Any]:
        """设置/清空一个钱包的备注名（留空则改回平台用户名）。"""
        addr = (address or "").strip()
        if addr not in self.config.follow.wallets:
            return {"ok": False, "msg": "列表里没有这个地址"}
        label = (name or "").strip()
        if label:
            self.config.follow.wallet_labels[addr] = label
        else:
            self.config.follow.wallet_labels.pop(addr, None)
        self._save_and_log_wallets()
        return {"ok": True,
                "msg": f"{addr[:10]}… 备注名已更新为「{label}」" if label
                       else f"{addr[:10]}… 已改回平台用户名"}

    def _save_and_log_wallets(self):
        try:
            save_config(self.config, self.config_path)
            self.log("system", "跟单钱包已更新",
                     "；".join(_short(w) for w in self.config.follow.wallets),
                     "success")
        except Exception as e:
            self.log("error", "保存钱包列表失败", str(e), "error")
            raise

    def executor_summary(self) -> dict:
        ex = self.config.executor
        out = {
            "enabled": bool(ex.enabled),
            "dry_run": bool(ex.dry_run),
            "wallet": _short(ex.wallet_address) if ex.wallet_address else "",
            "has_key": bool(ex.private_key),
            "max_order_usd": ex.max_order_usd,
            "max_daily_usd": ex.max_daily_usd,
            "follow_ratio": ex.follow_ratio,
            "daily_spent": 0.0,
        }
        if self.executor is not None:
            out["daily_spent"] = getattr(self.executor, "_daily_spent", 0.0)
            out["dry_run"] = bool(getattr(self.executor.config, "dry_run", ex.dry_run))
        return out

    def snapshot(self) -> dict:
        """给前端的一次性状态快照。"""
        cfg = self.config
        self._rollover_counters()
        uptime = int(time.time() - self.started_at) if self.started_at else 0
        return {
            "now": time.strftime("%Y-%m-%d %H:%M:%S"),
            "running": self.running,
            "uptime": uptime,
            "uptime_str": _fmt_duration(uptime),
            "proxy": cfg.proxy or "",
            "follow": {
                "wallets": len(cfg.follow.wallets),
                "interval": cfg.follow.poll_interval_secs,
                "min_value": cfg.follow.min_position_value_usd,
            },
            "monitor": {
                "enabled": cfg.monitor.enabled,
                "interval": cfg.monitor.poll_interval_secs,
                "markets": len(self.last_markets),
            },
            "alerts": {
                "webhook": (cfg.alerts.webhook_type
                            if (cfg.alerts.webhook_url or cfg.alerts.webhook_token)
                            else ""),
            },
            "executor": self.executor_summary(),
            "counters": dict(self.counters),
            "events": self.events[:120],
            "markets": self.last_markets,
            "wallets": self.wallet_rows,
            "my_wallet": self.my_wallet,
            "follow_cfg": {
                "only_open_positions": bool(cfg.follow.only_open_positions),
                "labels": dict(cfg.follow.wallet_labels or {}),
            },
            "selfcheck": self.selfcheck,
        }

    # ---- 配置更新 ----

    def update_config(self, payload: dict) -> Dict[str, Any]:
        """更新风控/过滤参数并写回 config.json。返回 {ok, msg}。"""
        allowed = {
            "min_position_value_usd": ("follow", float),
            "poll_interval_secs": ("follow", int),
            "monitor_interval": ("monitor", int),
            "max_order_usd": ("executor", float),
            "max_daily_usd": ("executor", float),
            "follow_ratio": ("executor", float),
            "min_price": ("executor", float),
            "max_price": ("executor", float),
            "slippage_pct": ("executor", float),
        }
        # 布尔型开关单独处理
        bool_keys = {"only_open_positions": "follow"}
        cfg = self.config
        changed = []
        for key, (section, caster) in allowed.items():
            if key not in payload or payload[key] in (None, ""):
                continue
            try:
                val = caster(payload[key])
            except (TypeError, ValueError):
                return {"ok": False, "msg": f"{key} 不是有效数字"}
            if section == "follow":
                setattr(cfg.follow, key, val)
            elif section == "monitor":
                # monitor_interval → poll_interval_secs
                setattr(cfg.monitor, "poll_interval_secs", val)
            else:
                setattr(cfg.executor, key, val)
            changed.append(f"{key}={val}")

        for key, section in bool_keys.items():
            if key not in payload:
                continue
            val = bool(payload[key])
            if section == "follow":
                setattr(cfg.follow, key, val)
            else:
                setattr(cfg.monitor, key, val)
            changed.append(f"{key}={val}")

        # 干跑开关
        if "dry_run" in payload:
            want = bool(payload["dry_run"])
            if cfg.executor.enabled:
                cfg.executor.dry_run = want
                self._rebuild_executor()
                changed.append(f"dry_run={want}")
            else:
                return {"ok": False,
                        "msg": "执行器未启用（executor.enabled=false），无法切换干跑模式。"
                               "请先在 config.json 中启用执行器。"}

        # 数值边界校正
        if cfg.executor.max_order_usd < 0:
            cfg.executor.max_order_usd = 0
        if cfg.executor.max_daily_usd < 0:
            cfg.executor.max_daily_usd = 0
        if not (0 < cfg.executor.follow_ratio <= 1):
            cfg.executor.follow_ratio = min(max(cfg.executor.follow_ratio, 0.0001), 1.0)

        try:
            save_config(cfg, self.config_path)
        except Exception as e:
            return {"ok": False, "msg": f"写回配置失败: {e}"}

        self.log("system", "配置已更新", "，".join(changed) or "无变化", "success")
        return {"ok": True, "msg": "已保存：" + ("，".join(changed) or "无变化")}

    # ---- 自检 ----

    def run_selfcheck(self) -> Dict[str, Any]:
        """测试三个 API 连通性（同步执行，结果缓存）。"""
        results = []
        client = HttpClient(proxy=self.config.proxy, timeout=15)

        checks = [
            ("Data API", "https://data-api.polymarket.com/v2/leaderboard",
             {"time_period": "ALL", "limit": 1}),
            ("Gamma API", "https://gamma-api.polymarket.com/events",
             {"active": "true", "limit": 1}),
            ("CLOB API", "https://clob.polymarket.com/time", None),
        ]
        for name, url, params in checks:
            t0 = time.time()
            try:
                resp = client.get_json(url, params)
                latency = (time.time() - t0) * 1000
                ok = resp is not None
                results.append({"name": name, "ok": ok,
                                "latency": round(latency),
                                "detail": "正常" if ok else "空响应"})
            except Exception as e:
                results.append({"name": name, "ok": False,
                                "latency": round((time.time() - t0) * 1000),
                                "detail": str(e)[:120]})

        self.selfcheck = {"ts": time.time(), "results": results}
        self.log("system", "自检完成",
                 "；".join(f"{r['name']} {'✓' if r['ok'] else '✗'}" for r in results),
                 "success" if all(r["ok"] for r in results) else "warning")
        return self.selfcheck


def _safe_price(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs} 秒"
    if secs < 3600:
        return f"{secs // 60} 分 {secs % 60} 秒"
    h, rem = divmod(secs, 3600)
    return f"{h} 小时 {rem // 60} 分"


# ---------------------------------------------------------------------------
# HTML 页面
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymarket 跟单控制台</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2330; --border:#2a3140;
  --text:#e6edf3; --muted:#8b949e; --accent:#3b82f6; --good:#22c55e;
  --warn:#f59e0b; --bad:#ef4444; --chip:#21262d;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}
.wrap{max-width:1280px;margin:0 auto;padding:18px 20px 60px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
header h1{font-size:19px;margin:0;font-weight:650}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;
  background:var(--chip);border:1px solid var(--border);font-size:12px;color:var(--muted)}
.pill b{color:var(--text);font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.dot.on{background:var(--good);box-shadow:0 0 8px var(--good)}
.dot.off{background:var(--bad)}
.dot.warn{background:var(--warn)}
.spacer{flex:1}
button{cursor:pointer;border:1px solid var(--border);background:var(--panel2);color:var(--text);
  padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;transition:.15s}
button:hover{border-color:var(--accent);color:#fff}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{filter:brightness(1.12)}
button.danger:hover{border-color:var(--bad);color:var(--bad)}
button:disabled{opacity:.45;cursor:not-allowed}
.grid{display:grid;gap:12px}
.stats{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:16px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.card .label{font-size:12px;color:var(--muted);margin-bottom:6px}
.card .value{font-size:22px;font-weight:700;letter-spacing:-.3px}
.card .sub{font-size:11px;color:var(--muted);margin-top:4px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;margin-bottom:16px;overflow:hidden}
.panel>h2{font-size:14px;margin:0;padding:12px 16px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;font-weight:650}
.panel>h2 .count{font-size:12px;color:var(--muted);font-weight:400}
.panel .body{padding:12px 16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:12px;padding:7px 10px;
  border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #1f2632;vertical-align:top}
tr:last-child td{border-bottom:none}
tr.new{animation:flash 2.2s ease-out}
@keyframes flash{0%{background:rgba(59,130,246,.28)}100%{background:transparent}}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;
  background:var(--chip);border:1px solid var(--border);white-space:nowrap}
.tag.buy{color:var(--good);border-color:rgba(34,197,94,.4);background:rgba(34,197,94,.1)}
.tag.sell{color:var(--bad);border-color:rgba(239,68,68,.4);background:rgba(239,68,68,.1)}
.tag.big{color:var(--warn);border-color:rgba(245,158,11,.4);background:rgba(245,158,11,.1)}
.tag.small{color:var(--muted)}
.tag.exec{color:#a78bfa;border-color:rgba(167,139,250,.4);background:rgba(167,139,250,.1)}
.tag.err{color:var(--bad);border-color:rgba(239,68,68,.4)}
.tag.sys{color:var(--accent);border-color:rgba(59,130,246,.4)}
.tag.alert{color:var(--warn);border-color:rgba(245,158,11,.4)}
.muted{color:var(--muted)}
.num{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.feed{max-height:420px;overflow-y:auto}
.feed .row{display:flex;gap:10px;padding:9px 16px;border-bottom:1px solid #1f2632;align-items:flex-start}
.feed .row:last-child{border-bottom:none}
.feed .time{color:var(--muted);font-size:12px;min-width:62px;font-variant-numeric:tabular-nums}
.feed .main{flex:1;min-width:0}
.feed .title{font-weight:600;font-size:13px;word-break:break-word}
.feed .detail{color:var(--muted);font-size:12px;margin-top:2px;word-break:break-word}
.feed .row.sev-error{background:rgba(239,68,68,.07)}
.feed .row.sev-warning{background:rgba(245,158,11,.06)}
.feed .row.sev-success{background:rgba(34,197,94,.06)}
.feed .row.sev-debug{opacity:.62}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}
.field input{width:100%;background:var(--panel2);border:1px solid var(--border);color:var(--text);
  padding:8px 10px;border-radius:8px;font-size:13px;font-variant-numeric:tabular-nums}
.field input:focus{outline:none;border-color:var(--accent)}
.switch{display:flex;align-items:center;gap:9px;padding-top:20px}
.switch input{width:16px;height:16px;accent-color:var(--accent)}
.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:var(--panel2);
  border:1px solid var(--accent);color:var(--text);padding:10px 20px;border-radius:10px;
  font-size:13px;opacity:0;transition:.25s;pointer-events:none;z-index:99;max-width:80vw}
.toast.show{opacity:1}
.empty{padding:26px;text-align:center;color:var(--muted);font-size:13px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
.hint{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.7}
kbd{background:var(--chip);border:1px solid var(--border);border-radius:4px;padding:1px 5px;font-size:11px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📈 Polymarket 跟单控制台</h1>
    <span class="pill"><span id="runDot" class="dot off"></span><b id="runText">未启动</b></span>
    <span class="pill">代理 <b id="proxyText">-</b></span>
    <span class="pill">执行 <b id="execText">-</b></span>
    <span class="spacer"></span>
    <span class="pill num" id="clock">-</span>
  </header>

  <div class="actions" style="margin:0 0 16px">
    <button id="btnStart" class="primary" onclick="act('start')">▶ 开始跟单监测</button>
    <button id="btnStop" class="danger" onclick="act('stop')">⏸ 停止</button>
    <button onclick="act('selfcheck')">🔍 立即自检</button>
    <button onclick="act('once')">⚡ 手动跑一轮</button>
    <span class="spacer"></span>
    <span class="muted" style="font-size:12px" id="uptimeText"></span>
  </div>

  <div class="grid stats" id="stats"></div>

  <div class="two">
    <div class="panel">
      <h2>📡 实时信号流 <span class="count" id="feedCount"></span></h2>
      <div class="feed" id="feed"><div class="empty">暂无信号，点击「开始跟单监测」</div></div>
    </div>
    <div>
      <div class="panel">
        <h2>👛 跟单钱包管理 <span class="count" id="walletCount"></span></h2>
        <div class="body">
          <div class="hint" style="margin:0 0 10px">
            地址 = 平台用户名，方便分辨是哪个大户。<b>只显示当前仍真实持仓的订单</b>，已平仓的不再出现。
          </div>
          <div style="overflow-x:auto"><table id="walletTable"></table></div>
          <div class="actions" style="margin-top:12px">
            <input id="wAddr" placeholder="0x… 跟单钱包地址" style="flex:1;min-width:260px;background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:8px;font-size:13px">
            <input id="wName" placeholder="备注名（可留空）" style="width:160px;background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:8px;font-size:13px">
            <button class="primary" onclick="addWallet()">＋ 加入跟单</button>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>👛 我的钱包（真实持仓） <span class="count" id="myWalletCount"></span></h2>
        <div class="body">
          <div class="hint" style="margin:0 0 10px" id="myWalletHint"></div>
          <div style="overflow-x:auto"><table id="myWalletTable"></table></div>
        </div>
      </div>
      <div class="panel">
        <h2>🌐 监测中的市场 <span class="count" id="marketCount"></span></h2>
        <div style="overflow-x:auto"><table id="marketTable"></table></div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>⚙️ 风控与过滤参数</h2>
    <div class="body">
      <div class="form">
        <div class="field"><label>最小跟单金额（USD，低于此值只记录不跟）</label>
          <input id="f_min_value" type="number" step="100" min="0"></div>
        <div class="field"><label>单笔上限（USD）</label>
          <input id="f_max_order" type="number" step="0.5" min="0"></div>
        <div class="field"><label>每日上限（USD）</label>
          <input id="f_max_daily" type="number" step="1" min="0"></div>
        <div class="field"><label>跟单比例（0.01 = 大佬的 1%）</label>
          <input id="f_ratio" type="number" step="0.005" min="0.0001" max="1"></div>
        <div class="field"><label>买入价下限</label>
          <input id="f_min_price" type="number" step="0.01" min="0.01" max="0.99"></div>
        <div class="field"><label>买入价上限</label>
          <input id="f_max_price" type="number" step="0.01" min="0.02" max="0.99"></div>
        <div class="field"><label>跟单轮询间隔（秒）</label>
          <input id="f_interval" type="number" step="1" min="1"></div>
        <div class="field"><label>滑点容忍（0.02 = 2%）</label>
          <input id="f_slippage" type="number" step="0.005" min="0.001" max="0.2"></div>
      </div>
      <div class="actions">
        <button class="primary" onclick="saveConfig()">💾 保存配置</button>
        <label class="switch" style="padding:0">
          <input type="checkbox" id="f_dryrun" onchange="toggleDry(this)">
          <span>干跑模式（只模拟下单，不动真钱）</span>
        </label>
        <label class="switch" style="padding:0">
          <input type="checkbox" id="f_only_open" onchange="toggleOnlyOpen(this)">
          <span>只显示仍持仓的订单（与平台实时对齐）</span>
        </label>
      </div>
      <div class="hint" id="execHint"></div>
    </div>
  </div>

  <div class="panel">
    <h2>🔍 最近自检结果 <span class="count" id="checkTime"></span></h2>
    <div style="overflow-x:auto"><table id="checkTable"></table></div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let lastEventId = null;
let seenIds = new Set();

const $ = id => document.getElementById(id);
const money = v => (v===null||v===undefined||v==='') ? '-' : '$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'),2600); }

async function act(name){
  const btns = document.querySelectorAll('button');
  try{
    const r = await fetch('/api/'+name, {method:'POST'});
    const j = await r.json();
    toast(j.msg || (j.ok ? '完成' : '失败'));
    refresh();
  }catch(e){ toast('请求失败: '+e); }
}

async function saveConfig(){
  const body = {
    min_position_value_usd: num('f_min_value'),
    max_order_usd: num('f_max_order'),
    max_daily_usd: num('f_max_daily'),
    follow_ratio: num('f_ratio'),
    min_price: num('f_min_price'),
    max_price: num('f_max_price'),
    poll_interval_secs: num('f_interval'),
    slippage_pct: num('f_slippage')
  };
  const r = await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j = await r.json();
  toast(j.msg);
  refresh();
}
const num = id => { const v=$(id).value; return v===''?null:Number(v); };

async function toggleDry(cb){
  const r = await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dry_run: cb.checked})});
  const j = await r.json();
  if(!j.ok){ cb.checked = !cb.checked; }
  toast(j.msg);
  refresh();
}

async function toggleOnlyOpen(cb){
  const r = await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({only_open_positions: cb.checked})});
  const j = await r.json();
  if(!j.ok){ cb.checked = !cb.checked; }
  toast(j.msg);
  refresh();
}

function evKey(e){ return e.ts+':'+e.kind+':'+e.title; }

function renderStats(s){
  const c = s.counters;
  const cards = [
    {label:'今日信号', value:c.signals, sub:'含小额（灰色）'},
    {label:'重仓信号 ≥'+money(s.follow.min_value).replace('.00',''), value:c.big, sub:'达到跟单门槛'},
    {label:'已跟单', value:c.exec, sub:s.executor.dry_run?'干跑模拟':'真实下单'},
    {label:'风控拒绝', value:c.reject, sub:'超限额/价格不符'},
    {label:'跟单大佬', value:s.follow.wallets, sub:'钱包地址数'},
    {label:'监测市场', value:s.monitor.markets, sub:'价格异动监控'},
    {label:'今日已用额度', value:money(s.executor.daily_spent), sub:'上限 '+money(s.executor.max_daily_usd)},
    {label:'运行时长', value:s.uptime_str||'-', sub:s.running?'循环运行中':'已停止'}
  ];
  $('stats').innerHTML = cards.map(c=>
    `<div class="card"><div class="label">${esc(c.label)}</div>
     <div class="value num">${esc(c.value)}</div>
     <div class="sub">${esc(c.sub)}</div></div>`).join('');
}

function renderFeed(s){
  const evs = s.events;
  $('feedCount').textContent = evs.length ? '（最近 '+evs.length+' 条）' : '';
  if(!evs.length){ $('feed').innerHTML='<div class="empty">暂无信号</div>'; return; }
  $('feed').innerHTML = evs.map(e=>{
    const k = evKey(e);
    const isNew = !seenIds.has(k);
    seenIds.add(k);
    let tag='';
    if(e.kind==='signal') tag = e.severity==='debug' ? '<span class="tag small">小额</span>' : '<span class="tag big">重仓</span>';
    else if(e.kind==='exec') tag='<span class="tag exec">跟单</span>';
    else if(e.kind==='alert') tag='<span class="tag alert">监测</span>';
    else if(e.kind==='error') tag='<span class="tag err">错误</span>';
    else tag='<span class="tag sys">系统</span>';
    return `<div class="row sev-${esc(e.severity)} ${isNew?'new':''}">
      <span class="time">${esc(e.time)}</span>
      <span>${tag}</span>
      <div class="main"><div class="title">${esc(e.title)}</div>
      ${e.detail?`<div class="detail">${esc(e.detail)}</div>`:''}</div></div>`;
  }).join('');
  // 控制 seenIds 体积
  if(seenIds.size > 600) seenIds = new Set(evs.map(evKey));
}

function renderWallets(s){
  const rows = s.wallets;
  $('walletCount').textContent = rows.length ? '（'+rows.length+' 个）' : '';
  if(!rows.length){ $('walletTable').innerHTML='<tr><td class="empty">暂无数据</td></tr>'; return; }
  $('walletTable').innerHTML = '<tr><th>用户名</th><th>地址</th><th>持仓数</th><th>持仓价值</th><th>操作</th></tr>' +
    rows.map(w=>`<tr>
      <td><b>${esc(w.name||'-')}</b></td>
      <td class="num" title="${esc(w.wallet)}">${esc(w.short)}</td>
      <td class="num">${w.positions}</td>
      <td class="num">${money(w.value)}</td>
      <td>
        <button onclick="renameWallet('${esc(w.wallet)}')">改名</button>
        <button class="danger" onclick="removeWallet('${esc(w.wallet)}')">移除</button>
      </td></tr>`).join('');
}

async function addWallet(){
  const addr = $('wAddr').value.trim();
  const name = $('wName').value.trim();
  if(!addr){ toast('请先填钱包地址'); return; }
  const r = await fetch('/api/wallets/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({address:addr, name:name})});
  const j = await r.json();
  toast(j.msg);
  if(j.ok){ $('wAddr').value=''; $('wName').value=''; }
  refresh();
}

async function removeWallet(addr){
  if(!confirm('确定不再跟单这个钱包？\n'+addr)) return;
  const r = await fetch('/api/wallets/remove',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({address:addr})});
  toast((await r.json()).msg);
  refresh();
}

async function renameWallet(addr){
  const cur = $('wName') && $('wName').value || '';
  const name = prompt('给这个钱包起个备注名（留空 = 用平台用户名）：', cur);
  if(name === null) return;
  const r = await fetch('/api/wallets/rename',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({address:addr, name:name})});
  toast((await r.json()).msg);
  refresh();
}

function renderMyWallet(s){
  const mw = s.my_wallet || {};
  const rows = mw.positions || [];
  $('myWalletCount').textContent = rows.length ? '（'+rows.length+' 个持仓）' : '';
  if(!mw.configured){
    $('myWalletHint').innerHTML = '尚未配置我的钱包。要真跟单，请在 config.json 里把 <kbd>executor.enabled</kbd> 改为 <kbd>true</kbd>，并填入 OKX 钱包私钥与 Polymarket 代理钱包地址。';
    $('myWalletTable').innerHTML = '<tr><td class="empty">未配置钱包地址</td></tr>';
    return;
  }
  $('myWalletHint').innerHTML =
    `钱包 <span class="num">${esc(mw.short||'')}</span> · 组合总价值 <b class="num">${money(mw.total_value)}</b>` +
    (mw.error ? ' · <span style="color:var(--bad)">拉取失败：'+esc(mw.error)+'</span>' : '') +
    ' · <a href="javascript:act(\'my-wallet-refresh\')">刷新</a>';
  if(!rows.length){ $('myWalletTable').innerHTML='<tr><td class="empty">当前无持仓</td></tr>'; return; }
  $('myWalletTable').innerHTML = '<tr><th>市场</th><th>方向</th><th>数量</th><th>均价</th><th>现价</th><th>价值</th><th>盈亏</th></tr>' +
    rows.map(p=>{
      const pnl = (p.pnl==null) ? '-' : '<span class="num" style="color:'+(p.pnl>=0?'var(--good)':'var(--bad)')+'">'+money(p.pnl)+'</span>';
      return `<tr><td title="${esc(p.condition_id)}">${esc(p.title||'')}</td>
        <td><span class="tag">${esc(p.outcome||'-')}</span></td>
        <td class="num">${Number(p.size||0).toFixed(2)}</td>
        <td class="num">${p.avg_price==null?'-':Number(p.avg_price).toFixed(3)}</td>
        <td class="num">${p.current_price==null?'-':Number(p.current_price).toFixed(3)}</td>
        <td class="num">${money(p.value)}</td><td>${pnl}</td></tr>`;
    }).join('');
}

function renderMarkets(s){
  const rows = s.markets;
  $('marketCount').textContent = rows.length ? '（'+rows.length+' 个）' : '';
  if(!rows.length){ $('marketTable').innerHTML='<tr><td class="empty">暂无数据</td></tr>'; return; }
  $('marketTable').innerHTML = '<tr><th>市场</th><th>最新价</th><th>价差</th><th>24h 量</th></tr>' +
    rows.slice(0,20).map(m=>{
      const price = (m.last_price!=null) ? Number(m.last_price).toFixed(3) : '-';
      const spread = (m.spread!=null) ? Number(m.spread).toFixed(3) : '-';
      const vol = (m.volume24hr!=null) ? '$'+Number(m.volume24hr).toLocaleString('en-US',{maximumFractionDigits:0}) : '-';
      return `<tr><td title="${esc(m.condition_id)}">${esc(m.question||m.condition_id.slice(0,16)+'…')}</td>
        <td class="num">${price}</td><td class="num">${spread}</td><td class="num">${vol}</td></tr>`;
    }).join('');
}

function renderCheck(s){
  const sc = s.selfcheck;
  if(!sc || !sc.ts){ $('checkTable').innerHTML='<tr><td class="empty">尚未自检，点「立即自检」</td></tr>'; return; }
  $('checkTime').textContent = new Date(sc.ts*1000).toLocaleTimeString();
  $('checkTable').innerHTML = '<tr><th>接口</th><th>状态</th><th>延迟</th><th>说明</th></tr>' +
    sc.results.map(r=>`<tr><td>${esc(r.name)}</td>
      <td>${r.ok?'<span class="tag buy">✓ 通</span>':'<span class="tag err">✗ 断</span>'}</td>
      <td class="num">${r.latency} ms</td><td class="muted">${esc(r.detail)}</td></tr>`).join('');
}

function fillForm(s){
  const f=s.follow, e=s.executor;
  if(document.activeElement===null || document.activeElement.tagName!=='INPUT'){
    $('f_min_value').value=f.min_value;
    $('f_max_order').value=e.max_order_usd;
    $('f_max_daily').value=e.max_daily_usd;
    $('f_ratio').value=e.follow_ratio;
    $('f_min_price').value=0.02; $('f_max_price').value=0.98;
    $('f_interval').value=f.interval;
    $('f_slippage').value=0.02;
  }
  $('f_dryrun').checked = !!e.dry_run;
  $('f_dryrun').disabled = !e.enabled;
  const oo = (s.follow_cfg && s.follow_cfg.only_open_positions);
  $('f_only_open').checked = !!oo;
  $('execHint').innerHTML = e.enabled
    ? (e.has_key
        ? `执行器已启用 · 本地钱包 <span class="num">${esc(e.wallet||'未配置')}</span> · `+
          (e.dry_run?'<b style="color:var(--warn)">干跑模式：只模拟不真下单</b>'
                    :'<b style="color:var(--bad)">真实下单模式：会用真钱！</b>')
        : '执行器已启用但未配置私钥，请运行 <kbd>python main.py setup</kbd> 或编辑 config.json')
    : '执行器未启用（只监测不下单）。要真跟单需在 config.json 里把 <kbd>executor.enabled</kbd> 改为 <kbd>true</kbd> 并配置 OKX 钱包私钥。';
}

function renderHeader(s){
  $('runDot').className = 'dot '+(s.running?'on':'off');
  $('runText').textContent = s.running?'运行中':'已停止';
  $('btnStart').disabled = s.running;
  $('btnStop').disabled = !s.running;
  $('proxyText').textContent = s.proxy ? s.proxy.replace(/^https?:\/\//,'') : '直连';
  $('execText').textContent = s.executor.enabled ? (s.executor.dry_run?'干跑':'真实下单') : '未启用';
  $('clock').textContent = s.now;
  $('uptimeText').textContent = s.running ? '已运行 '+s.uptime_str : '';
}

async function refresh(){
  try{
    const r = await fetch('/api/state');
    const s = await r.json();
    renderHeader(s); renderStats(s); renderFeed(s);
    renderWallets(s); renderMyWallet(s); renderMarkets(s); renderCheck(s); fillForm(s);
  }catch(e){ /* 网络抖动时静默，下一轮再试 */ }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class UiHandler(BaseHTTPRequestHandler):
    """REST + 静态页面。engine 由 server 实例注入。"""

    engine: Engine = None  # type: ignore
    server_version = "PMFollowUI/1.0"

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    # ---- 工具 ----

    def _send_json(self, obj: Any, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 1_000_000:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    # ---- 路由 ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html(PAGE_HTML)
        elif path == "/api/state":
            self._send_json(self.engine.snapshot())
        elif path == "/api/health":
            self._send_json({"ok": True, "running": self.engine.running})
        else:
            self._send_json({"ok": False, "msg": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        eng = self.engine
        try:
            if path == "/api/start":
                ok = eng.start()
                self._send_json({"ok": True,
                                 "msg": "已启动跟单+监测" if ok else "已在运行中"})
            elif path == "/api/stop":
                ok = eng.stop()
                self._send_json({"ok": True,
                                 "msg": "已停止" if ok else "当前未在运行"})
            elif path == "/api/selfcheck":
                self._send_json({"ok": True, "msg": "自检完成",
                                 "result": eng.run_selfcheck()})
            elif path == "/api/once":
                # 不依赖运行状态，手动跑一轮跟单+监测
                threading.Thread(target=self._once, daemon=True).start()
                self._send_json({"ok": True, "msg": "已触发一轮，请查看信号流"})
            elif path == "/api/config":
                self._send_json(eng.update_config(self._read_body()))
            elif path == "/api/wallets/add":
                body = self._read_body()
                self._send_json(eng.add_follow_wallet(
                    body.get("address", ""), body.get("name", "")))
            elif path == "/api/wallets/remove":
                body = self._read_body()
                self._send_json(eng.remove_follow_wallet(body.get("address", "")))
            elif path == "/api/wallets/rename":
                body = self._read_body()
                self._send_json(eng.rename_follow_wallet(
                    body.get("address", ""), body.get("name", "")))
            elif path == "/api/my-wallet/refresh":
                threading.Thread(target=self._refresh_my_wallet,
                                 daemon=True).start()
                self._send_json({"ok": True, "msg": "正在刷新我的持仓…"})
            else:
                self._send_json({"ok": False, "msg": "not found"}, 404)
        except Exception as e:
            self._send_json({"ok": False, "msg": f"服务器错误: {e}"}, 500)

    def _once(self):
        try:
            eng = self.engine
            eng.log("system", "手动执行一轮", "正在拉取大佬持仓与市场行情…")
            eng.run_follow_cycle()
            eng.run_monitor_cycle()
            eng.refresh_my_wallet(force=True)
            eng.log("system", "手动执行完成", "信号已刷新")
        except Exception as e:
            self.engine.log("error", "手动执行失败", str(e), "error")

    def _refresh_my_wallet(self):
        try:
            self.engine.refresh_my_wallet(force=True)
        except Exception as e:
            self.engine.log("error", "刷新我的持仓失败", str(e), "error")


def main():
    parser = argparse.ArgumentParser(description="Polymarket 跟单 Web UI")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--autostart", action="store_true", help="启动后立即开始跟单")
    args = parser.parse_args()

    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(BASE_DIR, config_path)

    engine = Engine(config_path)
    UiHandler.engine = engine

    server = ThreadingHTTPServer((args.host, args.port), UiHandler)
    url = f"http://{args.host}:{args.port}"

    print("=" * 62)
    print("Polymarket 跟单控制台")
    print("=" * 62)
    print(f"  地址:     {url}")
    print(f"  代理:     {engine.config.proxy or '直连'}")
    print(f"  跟单钱包: {len(engine.config.follow.wallets)} 个")
    print(f"  执行器:   {'启用' if engine.config.executor.enabled else '未启用'}"
          f"（dry_run={engine.config.executor.dry_run}）")
    print(f"  最小跟单: ${engine.config.follow.min_position_value_usd}")
    print("=" * 62)
    print("提示: 运行时不要再同时运行 main.py，避免两个进程抢写 state.json")
    print("按 Ctrl-C 停止服务")
    print()

    if args.autostart:
        engine.start()

    if not args.no_browser:
        try:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        engine.stop()
        server.server_close()
        print("已退出。")


if __name__ == "__main__":
    main()
