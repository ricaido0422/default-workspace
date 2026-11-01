#!/usr/bin/env python3
"""Polymarket 跟单与监测工具 — CLI 入口。

子命令:
  run        同时跑跟单+监测循环（默认）
  follow     只跑跟单循环
  monitor    只跑监测循环
  once       各跑一个 cycle 后退出（便于 cron/测试）
  bootstrap  从排行榜取 top N 钱包写入 config.json
  selfcheck  测试三个接口连通性
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
from typing import Optional

# 确保能导入 pmfollow 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows 控制台默认 GBK，遇到 ø/é 等字符会崩溃；强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pmfollow.config import Config, load_config, save_config
from pmfollow.httpclient import HttpClient, HttpClientError, NotFoundError
from pmfollow.dataapi import DataApi
from pmfollow.gamma import GammaApi
from pmfollow.clob import ClobApi
from pmfollow.store import StateStore
from pmfollow.alert import Alerter
from pmfollow.config import AlertsConfig
from pmfollow.tracker import CopyTracker
from pmfollow.monitor import MarketMonitor
from pmfollow.executor import CopyExecutor
from pmfollow import ws as ws_module


# 全局退出标志
_exit_flag = threading.Event()


def _setup_signal_handlers():
    """设置 SIGINT/SIGTERM 信号处理。"""
    def handler(signum, frame):
        print(f"\n[主程序] 收到信号 {signum}，准备退出...")
        _exit_flag.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _log(msg: str):
    """带时间戳的日志输出。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"{ts} {msg}")


def _build_components(config: Config, config_path: str):
    """构建所有组件，返回 (client, data_api, gamma_api, clob_api, store, alerter, state)。"""
    client = HttpClient(proxy=config.proxy)
    data_api = DataApi(client, base=config.api.get("data_api", ""))
    gamma_api = GammaApi(client, base=config.api.get("gamma_api", ""))
    clob_api = ClobApi(client, base=config.api.get("clob_api", ""))

    state_file = config.storage.get("state_file", "state.json")
    # 确保状态文件在项目目录下
    if not os.path.isabs(state_file):
        base_dir = os.path.dirname(os.path.abspath(config_path))
        state_file = os.path.join(base_dir, state_file)
    store = StateStore(path=state_file)
    state = store.load()

    alerter = Alerter(config.alerts, store, state)

    return client, data_api, gamma_api, clob_api, store, alerter, state


def _print_config_summary(config: Config, wallets_count: int = 0, markets_count: int = 0):
    """打印配置摘要。"""
    print("=" * 60)
    print("Polymarket 跟单与监测工具 v0.1")
    print("=" * 60)
    print(f"代理: {config.proxy or '直连'}")
    print(f"Data API: {config.api.get('data_api', '')}")
    print(f"Gamma API: {config.api.get('gamma_api', '')}")
    print(f"CLOB API:  {config.api.get('clob_api', '')}")
    print("-" * 60)
    print(f"跟单: 间隔={config.follow.poll_interval_secs}s "
          f"最小价值=${config.follow.min_position_value_usd} "
          f"仅活跃市场={config.follow.only_active_markets}")
    print(f"  手动钱包: {len(config.follow.wallets)} 个")
    fl = config.follow.from_leaderboard
    if fl and fl.get("enabled"):
        print(f"  排行榜: {fl.get('window', 'all')} top {fl.get('limit', 5)}")
    print(f"  解析钱包数: {wallets_count}")
    print("-" * 60)
    print(f"监测: 启用={config.monitor.enabled} "
          f"间隔={config.monitor.poll_interval_secs}s "
          f"价格变动阈值={config.monitor.price_move_alert_pct}% "
          f"价差阈值={config.monitor.spread_alert_max}")
    print(f"  手动市场: {len(config.monitor.markets)} 个")
    ftv = config.monitor.from_top_volume
    if ftv and ftv.get("enabled"):
        print(f"  热门市场: top {ftv.get('limit', 10)}")
    print(f"  解析市场数: {markets_count}")
    print(f"  WebSocket: {config.monitor.use_websocket}")
    print("-" * 60)
    print(f"告警: 控制台={config.alerts.console} "
          f"webhook={config.alerts.webhook_type if config.alerts.webhook_url or config.alerts.webhook_token else '无'} "
          f"去重窗口={config.alerts.dedupe_window_secs}s")
    print("-" * 60)
    ex = config.executor
    if ex.enabled:
        print(f"执行器: 启用 dry_run={ex.dry_run} "
              f"跟单比例={ex.follow_ratio*100:.0f}% "
              f"单笔上限=${ex.max_order_usd} 每日上限=${ex.max_daily_usd}")
        print(f"  钱包: {ex.wallet_address[:10]}..." if ex.wallet_address else "  钱包: 未配置")
    else:
        print("执行器: 未启用（只监测不下单）")
    print("=" * 60)


# ---------------------------------------------------------------------------
# 循环函数
# ---------------------------------------------------------------------------

def _follow_loop(tracker: CopyTracker, interval: int, executor=None):
    """跟单循环。可选传入 executor 执行真实跟单。"""
    _log("[跟单] 循环启动")
    while not _exit_flag.is_set():
        try:
            count = tracker.run_cycle()
            if count > 0:
                _log(f"[跟单] 本轮产生 {count} 个信号")
                # 执行器：把信号传给 executor 下单
                if executor and tracker.last_signals:
                    exec_count = executor.run_cycle(tracker.last_signals)
                    _log(f"[执行] 本轮执行 {exec_count} 个信号")
            else:
                _log("[跟单] 本轮无变化")
        except Exception as e:
            _log(f"[跟单] 循环异常: {e}")
        _exit_flag.wait(interval)
    _log("[跟单] 循环退出")


def _monitor_loop(monitor: MarketMonitor, interval: int):
    """监测循环。"""
    _log("[监测] 循环启动")
    while not _exit_flag.is_set():
        try:
            count = monitor.run_cycle()
            if count > 0:
                _log(f"[监测] 本轮产生 {count} 个告警")
            else:
                _log("[监测] 本轮无异常")
        except Exception as e:
            _log(f"[监测] 循环异常: {e}")
        _exit_flag.wait(interval)
    _log("[监测] 循环退出")


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_run(config: Config, config_path: str):
    """同时跑跟单 + 监测循环。"""
    client, data_api, gamma_api, clob_api, store, alerter, state = _build_components(config, config_path)

    tracker = CopyTracker(
        data_api, gamma_api, config.follow, store, alerter, state
    )
    monitor = MarketMonitor(
        clob_api, gamma_api, data_api, config.monitor, store, alerter, state
    )

    # 执行器（可选）
    executor = None
    if config.executor.enabled:
        exec_proxy = config.executor.proxy or config.proxy or ""
        from pmfollow.config import ExecutorConfig
        exec_config = ExecutorConfig(
            enabled=config.executor.enabled,
            dry_run=config.executor.dry_run,
            private_key=config.executor.private_key,
            api_key=config.executor.api_key,
            api_secret=config.executor.api_secret,
            api_passphrase=config.executor.api_passphrase,
            wallet_address=config.executor.wallet_address,
            signature_type=config.executor.signature_type,
            max_order_usd=config.executor.max_order_usd,
            max_daily_usd=config.executor.max_daily_usd,
            follow_ratio=config.executor.follow_ratio,
            min_price=config.executor.min_price,
            max_price=config.executor.max_price,
            slippage_pct=config.executor.slippage_pct,
            proxy=exec_proxy,
        )
        executor = CopyExecutor(exec_config, alerter=alerter)
        _log(f"[执行] 执行器已启动 (dry_run={config.executor.dry_run})")

    # 预解析钱包和市场数
    try:
        wallets = tracker.resolve_wallets()
    except Exception:
        wallets = config.follow.wallets
    try:
        markets = monitor.resolve_markets()
    except Exception:
        markets = []

    _print_config_summary(config, wallets_count=len(wallets), markets_count=len(markets))

    threads = []
    ws_monitor = None

    if config.monitor.enabled:
        # WebSocket 实时模式
        if config.monitor.use_websocket and ws_module.WsMonitor is not None:
            # 收集所有 token_ids
            asset_ids = []
            for m in markets:
                for tid in m.clob_token_ids:
                    if tid and tid not in asset_ids:
                        asset_ids.append(tid)
            if asset_ids:
                ws_monitor = ws_module.WsMonitor(
                    config.monitor, alerter, state, proxy=config.proxy
                )
                ws_monitor.subscribe(asset_ids)
                _log(f"[监测] WebSocket 模式，已订阅 {len(asset_ids)} 个资产")
            else:
                _log("[监测] WebSocket 模式但无可用 token_id，降级为轮询")
                t_mon = threading.Thread(
                    target=_monitor_loop,
                    args=(monitor, config.monitor.poll_interval_secs),
                    daemon=True,
                )
                threads.append(t_mon)
        elif config.monitor.use_websocket and ws_module.WsMonitor is None:
            _log("[监测] use_websocket=True 但未安装 websocket-client，降级为轮询")
            t_mon = threading.Thread(
                target=_monitor_loop,
                args=(monitor, config.monitor.poll_interval_secs),
                daemon=True,
            )
            threads.append(t_mon)
        else:
            t_mon = threading.Thread(
                target=_monitor_loop,
                args=(monitor, config.monitor.poll_interval_secs),
                daemon=True,
            )
            threads.append(t_mon)

    t_follow = threading.Thread(
        target=_follow_loop,
        args=(tracker, config.follow.poll_interval_secs, executor),
        daemon=True,
    )
    threads.append(t_follow)

    for t in threads:
        t.start()

    _log("[主程序] 按 Ctrl-C 退出")
    while not _exit_flag.is_set():
        _exit_flag.wait(1)

    # 停止 WebSocket
    if ws_monitor:
        ws_monitor.stop()

    for t in threads:
        t.join(timeout=5)

    store.save(state)
    _log("[主程序] 状态已保存，退出")


def cmd_follow(config: Config, config_path: str):
    """只跑跟单循环。"""
    client, data_api, gamma_api, clob_api, store, alerter, state = _build_components(config, config_path)
    tracker = CopyTracker(data_api, gamma_api, config.follow, store, alerter, state)

    # 执行器（可选）
    executor = None
    if config.executor.enabled:
        exec_proxy = config.executor.proxy or config.proxy or ""
        from pmfollow.config import ExecutorConfig
        exec_config = ExecutorConfig(
            enabled=config.executor.enabled, dry_run=config.executor.dry_run,
            private_key=config.executor.private_key, api_key=config.executor.api_key,
            api_secret=config.executor.api_secret, api_passphrase=config.executor.api_passphrase,
            wallet_address=config.executor.wallet_address, signature_type=config.executor.signature_type,
            max_order_usd=config.executor.max_order_usd, max_daily_usd=config.executor.max_daily_usd,
            follow_ratio=config.executor.follow_ratio, min_price=config.executor.min_price,
            max_price=config.executor.max_price, slippage_pct=config.executor.slippage_pct,
            proxy=exec_proxy,
        )
        executor = CopyExecutor(exec_config, alerter=alerter)
        _log(f"[执行] 执行器已启动 (dry_run={config.executor.dry_run})")

    try:
        wallets = tracker.resolve_wallets()
    except Exception:
        wallets = config.follow.wallets
    _print_config_summary(config, wallets_count=len(wallets), markets_count=0)

    _follow_loop(tracker, config.follow.poll_interval_secs, executor=executor)
    store.save(state)


def cmd_monitor(config: Config, config_path: str):
    """只跑监测循环。"""
    client, data_api, gamma_api, clob_api, store, alerter, state = _build_components(config, config_path)
    monitor = MarketMonitor(clob_api, gamma_api, data_api, config.monitor, store, alerter, state)

    try:
        markets = monitor.resolve_markets()
    except Exception:
        markets = []
    _print_config_summary(config, wallets_count=0, markets_count=len(markets))

    # WebSocket 模式
    if config.monitor.use_websocket and ws_module.WsMonitor is not None:
        asset_ids = []
        for m in markets:
            for tid in m.clob_token_ids:
                if tid and tid not in asset_ids:
                    asset_ids.append(tid)
        if asset_ids:
            ws_monitor = ws_module.WsMonitor(config.monitor, alerter, state, proxy=config.proxy)
            ws_monitor.subscribe(asset_ids)
            _log(f"[监测] WebSocket 模式，已订阅 {len(asset_ids)} 个资产")
            _log("[主程序] 按 Ctrl-C 退出")
            while not _exit_flag.is_set():
                _exit_flag.wait(1)
            ws_monitor.stop()
            store.save(state)
            return
        else:
            _log("[监测] WebSocket 模式但无可用 token_id，降级为轮询")
    elif config.monitor.use_websocket and ws_module.WsMonitor is None:
        _log("[监测] use_websocket=True 但未安装 websocket-client，降级为轮询")

    _monitor_loop(monitor, config.monitor.poll_interval_secs)
    store.save(state)


def cmd_execute(config: Config, config_path: str):
    """跟单 + 真实下单执行循环。"""
    if not config.executor.enabled:
        _log("[执行] executor.enabled=false，请先在 config.json 中启用执行器")
        _log("[执行] 提示：设置 executor.enabled=true 并填写凭据")
        return

    _log("=" * 60)
    if config.executor.dry_run:
        _log("[执行] ⚠️ DRY-RUN 模式：只打印不下真单")
    else:
        _log("[执行] ⚠️⚠️ 真实下单模式：将用你的钱包实际下单！")
        _log(f"[执行] 钱包: {config.executor.wallet_address[:10]}...")
        _log(f"[执行] 单笔上限: ${config.executor.max_order_usd}  每日上限: ${config.executor.max_daily_usd}")
        _log(f"[执行] 跟单比例: {config.executor.follow_ratio*100:.0f}%")
    _log("=" * 60)

    cmd_follow(config, config_path)


def cmd_once(config: Config, config_path: str):
    """各跑一个 cycle 后退出。"""
    client, data_api, gamma_api, clob_api, store, alerter, state = _build_components(config, config_path)

    tracker = CopyTracker(data_api, gamma_api, config.follow, store, alerter, state)
    monitor = MarketMonitor(clob_api, gamma_api, data_api, config.monitor, store, alerter, state)

    # 跟单
    _log("[once] === 跟单 ===")
    try:
        wallets = tracker.resolve_wallets()
        _log(f"[once] 解析到 {len(wallets)} 个钱包")
        for w in wallets:
            _log(f"[once]   {w}")
        sig_count = tracker.run_cycle()
        _log(f"[once] 跟单信号数: {sig_count}")
    except Exception as e:
        _log(f"[once] 跟单失败: {e}")

    # 监测
    if config.monitor.enabled:
        _log("[once] === 监测 ===")
        try:
            markets = monitor.resolve_markets()
            _log(f"[once] 解析到 {len(markets)} 个市场")
            for m in markets[:5]:
                _log(f"[once]   {m.condition_id[:20]}... {m.question[:50]}")
            if len(markets) > 5:
                _log(f"[once]   ... 及其余 {len(markets) - 5} 个")
            alert_count = monitor.run_cycle()
            _log(f"[once] 监测告警数: {alert_count}")
        except Exception as e:
            _log(f"[once] 监测失败: {e}")

    store.save(state)
    _log("[once] 状态已保存，退出")


def cmd_bootstrap(config: Config, config_path: str, top_n: int = 5):
    """从排行榜取 top N 钱包写入 config.json。"""
    client = HttpClient(proxy=config.proxy)
    data_api = DataApi(client, base=config.api.get("data_api", ""))

    _log(f"[bootstrap] 从排行榜取 top {top_n} 钱包...")
    try:
        data, _ = data_api.leaderboard(window="all", limit=top_n)
    except Exception as e:
        _log(f"[bootstrap] 获取排行榜失败: {e}")
        return

    wallets = []
    print(f"\n排行榜 top {len(data)}:")
    print("-" * 60)
    for row in data:
        uid = row.get("user_id", "")
        name = row.get("user_name", "")
        pnl = row.get("pnl", 0)
        rank = row.get("rank", "?")
        print(f"  #{rank} {uid}")
        print(f"       名称: {name}  PnL: ${pnl:,.2f}")
        if uid:
            wallets.append(uid)
    print("-" * 60)

    # 合并到现有 wallets（去重）
    existing = set(config.follow.wallets)
    new_wallets = list(config.follow.wallets)
    for w in wallets:
        if w not in existing:
            new_wallets.append(w)
            existing.add(w)

    config.follow.wallets = new_wallets
    save_config(config, config_path)
    _log(f"[bootstrap] 已写入 {len(new_wallets)} 个钱包到 {config_path}")


def cmd_selfcheck(config: Config, config_path: str):
    """测试三个接口连通性。"""
    client = HttpClient(proxy=config.proxy, timeout=15)

    print("=" * 60)
    print("自检：测试三个 API 接口连通性")
    print(f"代理: {config.proxy or '直连'}")
    print("=" * 60)

    results = []

    # Data API
    print("\n1. Data API (data-api.polymarket.com)")
    try:
        t0 = time.time()
        resp = client.get_json("https://data-api.polymarket.com/v2/leaderboard", {"time_period": "ALL", "limit": 1})
        latency = (time.time() - t0) * 1000
        if resp and isinstance(resp, dict) and resp.get("data"):
            print(f"   [OK] 延迟 {latency:.0f}ms，返回 {len(resp['data'])} 条")
            results.append(True)
        else:
            print(f"   [FAIL] 延迟 {latency:.0f}ms，响应异常")
            results.append(False)
    except Exception as e:
        print(f"   [FAIL] {e}")
        results.append(False)

    # Gamma API
    print("\n2. Gamma API (gamma-api.polymarket.com)")
    try:
        t0 = time.time()
        resp = client.get_json("https://gamma-api.polymarket.com/events", {"active": "true", "limit": 1})
        latency = (time.time() - t0) * 1000
        if resp and isinstance(resp, list):
            print(f"   [OK] 延迟 {latency:.0f}ms，返回 {len(resp)} 个事件")
            results.append(True)
        else:
            print(f"   [FAIL] 延迟 {latency:.0f}ms，响应异常")
            results.append(False)
    except Exception as e:
        print(f"   [FAIL] {e}")
        results.append(False)

    # CLOB API
    print("\n3. CLOB API (clob.polymarket.com)")
    try:
        t0 = time.time()
        resp = client.get_json("https://clob.polymarket.com/time")
        latency = (time.time() - t0) * 1000
        print(f"   [OK] 延迟 {latency:.0f}ms，响应: {str(resp)[:80]}")
        results.append(True)
    except Exception as e:
        print(f"   [FAIL] {e}")
        results.append(False)

    # 排行榜调用
    print("\n4. 排行榜调用测试")
    try:
        data_api = DataApi(client)
        t0 = time.time()
        data, _ = data_api.leaderboard(window="all", limit=3)
        latency = (time.time() - t0) * 1000
        if data:
            print(f"   [OK] 延迟 {latency:.0f}ms，top1: {data[0].get('user_name', '?')} "
                  f"({data[0].get('user_id', '')[:16]}...)")
            results.append(True)
        else:
            print(f"   [FAIL] 延迟 {latency:.0f}ms，无数据")
            results.append(False)
    except Exception as e:
        print(f"   [FAIL] {e}")
        results.append(False)

    # 结论
    print("\n" + "=" * 60)
    ok_count = sum(results)
    total = len(results)
    if ok_count == total:
        print(f"结论: 全部通过 ({ok_count}/{total})，代理生效，接口可用。")
    elif ok_count > 0:
        print(f"结论: 部分通过 ({ok_count}/{total})，代理可能生效但部分接口异常。")
    else:
        print(f"结论: 全部失败 ({ok_count}/{total})，代理可能未生效或网络不通。")
    print("=" * 60)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(
        description="Polymarket 跟单与监测工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", default="config.json",
        help="配置文件路径（默认: config.json）",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    sub.add_parser("run", help="同时跑跟单+监测循环（默认）")
    sub.add_parser("follow", help="只跑跟单循环")
    sub.add_parser("monitor", help="只跑监测循环")
    sub.add_parser("once", help="各跑一个 cycle 后退出")
    sub.add_parser("execute", help="跟单+真实下单执行（需配置 executor）")

    bp = sub.add_parser("bootstrap", help="从排行榜取 top N 钱包写入 config.json")
    bp.add_argument("-n", "--top", type=int, default=5, help="取 top N（默认 5）")

    sub.add_parser("selfcheck", help="测试三个接口连通性")

    args = parser.parse_args()

    # 加载配置
    config_path = args.config
    if not os.path.isabs(config_path):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, config_path)

    config = load_config(config_path)

    _setup_signal_handlers()

    command = args.command or "run"

    if command == "run":
        cmd_run(config, config_path)
    elif command == "follow":
        cmd_follow(config, config_path)
    elif command == "monitor":
        cmd_monitor(config, config_path)
    elif command == "once":
        cmd_once(config, config_path)
    elif command == "execute":
        cmd_execute(config, config_path)
    elif command == "bootstrap":
        cmd_bootstrap(config, config_path, top_n=args.top)
    elif command == "selfcheck":
        cmd_selfcheck(config, config_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
