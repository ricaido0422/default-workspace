# Polymarket Trader API 契约

> 版本: 0.1.0 | 更新: 2026-10-24

## 概述

前后端通过 REST API + WebSocket 通信。所有 REST 端点以 `/api` 为前缀，返回 JSON。WebSocket 端点为 `/ws`。

当前后端共注册 39 个 REST 端点，全部登记在下方「REST 端点」章节。

---

## REST 端点

### 钱包管理

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/wallets` | — | `list[WalletWatch]` | 获取所有监控钱包 |
| POST | `/api/wallets` | `{address, alias}` | `WalletWatch` | 添加监控钱包 |
| DELETE | `/api/wallets/{address}` | — | `{ok: true}` | 删除监控钱包 |
| POST | `/api/wallets/{address}/toggle` | — | `WalletWatch` | 切换钱包启用状态 |

### 排行榜

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/leaderboard?window=week&limit=20` | — | `list[LeaderboardEntry]` | 获取排行榜 (window: day/week/month/all) |
| POST | `/api/wallets/import-leaderboard` | `{wallets: list[str], aliases: list[str]}` | `{imported: int}` | 批量导入钱包 |

### 策略流水线

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/pipelines` | — | `list[StrategyPipeline]` | 获取所有流水线 |
| POST | `/api/pipelines` | `{name}` | `StrategyPipeline` | 创建流水线 |
| PUT | `/api/pipelines/{pipeline_id}` | `{strategies?, enabled?, target_wallets?}` | `StrategyPipeline` | 更新流水线 |
| DELETE | `/api/pipelines/{pipeline_id}` | — | `{ok: true}` | 删除流水线 |

### 策略类型

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/strategies/available` | — | `list[StrategyInfo]` | 获取可用策略类型及参数 schema |

**StrategyInfo 结构:**
```json
{
  "type": "PROPORTIONAL",
  "category": "TRANSFORM",
  "name": "比例跟单",
  "description": "按比例系数缩放交易数量",
  "params_schema": {"ratio": {"type": "float", "default": 1.0, "min": 0.01}}
}
```

### 交易与执行

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/trades?wallet=&limit=50` | — | `list[TradeEvent]` | 获取交易事件 |
| GET | `/api/executions?limit=50` | — | `list[ExecutionRecord]` | 获取执行记录 |

### 仪表盘

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/stats` | — | `DashboardStats` | 获取统计数据 |

### 配置管理

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/config` | — | `ConfigResponse` | 获取配置（脱敏） |
| PUT | `/api/config` | `{proxy?, risk?, monitor_interval?}` | `{ok: true}` | 更新配置 |

### 执行器

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/api/executor/mode` | `{mode: "paper"\|"live"}` | `{mode: string}` | 设置执行模式 |
| POST | `/api/executor/credentials` | `{api_key, api_secret, api_passphrase, wallet_address, private_key}` | `{ok: true}` | 设置凭据 |

### 通知推送

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/notifications/config` | — | `NotificationConfig` | 获取通知配置（脱敏） |
| PUT | `/api/notifications/config` | `NotificationConfigRequest` | `{ok: true, config: NotificationConfig}` | 更新通知配置（写 DB + 热更新） |
| POST | `/api/notifications/test` | `{channel: "telegram"\|"email"}` | `{ok: bool, channel: string, message: string}` | 发送测试通知 |
| GET | `/api/notifications/log?limit=50` | — | `list[NotificationLog]` | 最近通知发送记录 |

**NotificationConfig（脱敏后）:**
```json
{
  "telegram_enabled": true,
  "telegram_token": "1234...ALUE",
  "telegram_token_set": true,
  "telegram_chat_ids": ["111", "-100222"],
  "email_enabled": true,
  "email_host": "smtp.example.com",
  "email_port": 465,
  "email_user": "me@example.com",
  "email_password": "********",
  "email_password_set": true,
  "email_from": "bot@example.com",
  "email_to": ["a@example.com"],
  "email_use_ssl": true,
  "min_notify_amount": 250.0,
  "notify_events": ["trade_detected", "risk_blocked"],
  "notify_wallets": [],
  "cooldown_seconds": 30.0,
  "daily_summary_hour": 8,
  "daily_summary_minute": 0
}
```

> `telegram_token` 显示前 4 后 4，`email_password` 完全隐藏。
> 两者在 `PUT` 时传空字符串表示「保持原值不变」（前端不回显敏感字段）。

**NotificationLog:**
```json
{
  "id": 12,
  "created_at": "2026-01-01T12:00:02Z",
  "event_type": "trade_detected",
  "channel": "telegram",
  "status": "success",
  "error": "",
  "payload": {"title": "...", "message": "...", "data": {}}
}
```

### 通知事件类型

| 事件类型 | 说明 | 触发来源 |
|---------|------|---------|
| `trade_detected` | 大额交易 | `trade_update` |
| `signal_generated` | 跟单信号 | `signal_generated` |
| `execution_filled` | 跟单成交 | `execution_update`（FILLED） |
| `execution_failed` | 执行失败 | `execution_update`（FAILED，非风控） |
| `risk_blocked` | 风控拦截 | `execution_update`（FAILED，风控拒绝） |
| `daily_summary` | 每日汇总 | 定时任务（UTC `daily_summary_hour:minute`） |

### 策略回测

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/backtest/strategies` | — | `list[StrategyInfo]` | 可用于回测的策略类型及参数 schema（与 `/strategies/available` 同构） |
| POST | `/api/backtest/run` | `BacktestRunRequest` | `BacktestResult` | 对单个钱包的历史交易运行策略回测 |
| POST | `/api/backtest/compare` | `BacktestCompareRequest` | `{pipeline_id, pipeline_name, count, results, summary}` | 多钱包用同一流水线对比回测，按超额收益降序排名 |

**BacktestRunRequest / BacktestCompareRequest 结构:**
```json
{
  "wallet": "0x...",
  "pipeline_id": "pl_xyz789",
  "pipeline_config": null,
  "params": {
    "start_time": null,
    "end_time": null,
    "initial_capital": 1000.0,
    "slippage_ticks": 1,
    "min_amount": 0.0,
    "max_position_per_market": 500.0,
    "tick_size": 0.01
  }
}
```

> `wallet`（run）/ `wallets`（compare，非空）必填；`pipeline_id` 与 `pipeline_config` 二选一，
> 都不传时使用默认镜像跟单基线（`pl_default_mirror`）。
> `params` 可整体省略，缺省值即上表所示。

**BacktestResult 结构:**
```json
{
  "wallet": "0x...",
  "pipeline_id": "pl_xyz789",
  "pipeline_name": "巨鲸跟单流水线",
  "params": {"initial_capital": 1000.0, "slippage_ticks": 1, "tick_size": 0.01},
  "metrics": {
    "total_trades": 120,
    "total_signals": 90,
    "pass_rate": 0.75,
    "executed_trades": 90,
    "filtered_trades": 30,
    "win_rate": 0.6,
    "total_pnl": 234.56,
    "avg_pnl": 2.6,
    "max_win": 45.0,
    "max_loss": -18.5,
    "profit_factor": 1.8,
    "max_drawdown": 60.0,
    "initial_capital": 1000.0,
    "final_equity": 1234.56,
    "benchmark_pnl": 200.0,
    "benchmark_win_rate": 0.55,
    "benchmark_trades": 120,
    "excess_pnl": 34.56,
    "excess_return_pct": 3.46
  },
  "trades": [],
  "equity_curve": [0.0, 2.6, -1.2, 3.8],
  "filter_reasons": {"STOP_LOSS": 12, "SIZE_FILTER": 18},
  "filter_stats": [
    {
      "strategy_id": "st_abc123",
      "strategy_name": "数量过滤",
      "reason": "交易数量低于 min_size",
      "count": 18,
      "pct": 15.0
    }
  ],
  "data_source": "db"
}
```

> `metrics` 中 `benchmark_*` 为「直接镜像跟单」基线，`excess_*` = 策略 − 基准。
> `data_source` 取值为 `db` 或 `api`，表示历史交易数据的来源。

**`/api/backtest/compare` 的 `summary` 结构:**
```json
{
  "best_wallet": "0x...",
  "best_excess_pnl": 34.56,
  "avg_excess_pnl": 21.3,
  "total_pnl": 400.0,
  "avg_pass_rate": 0.72,
  "avg_win_rate": 0.58
}
```

### 绩效分析

所有分析端点共用查询参数 `days`（默认 7，范围 0–3650；0 表示全部历史），
`/api/analytics/daily` 例外，其默认 30。

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/analytics/summary?days=7` | — | `PerformanceSummary` | 跟单绩效总览（成交率、盈亏、胜率、延迟分布、执行质量） |
| GET | `/api/analytics/wallets?days=7` | — | `list[WalletPerformance]` | 按钱包分组的绩效统计 |
| GET | `/api/analytics/strategies?days=7` | — | `list[StrategyPerformance]` | 按策略分组的绩效统计（策略归因） |
| GET | `/api/analytics/markets?days=7` | — | `list[MarketPerformance]` | 按市场分组的绩效统计（盈利/亏损 Top10 + 无盈亏） |
| GET | `/api/analytics/daily?days=30` | — | `list[DailyPerformance]` | 每日绩效时间序列，按日期升序 |

**PerformanceSummary 结构:**
```json
{
  "period_days": 7,
  "start_time": "2026-01-01T00:00:00Z",
  "end_time": "2026-01-08T00:00:00Z",
  "total_signals": 150,
  "total_executions": 120,
  "filled_executions": 100,
  "fill_rate": 0.83,
  "total_pnl": 234.56,
  "win_rate": 0.6,
  "avg_pnl": 2.35,
  "avg_hold_seconds": 3600.0,
  "avg_latency_ms": 820.5,
  "total_wallets": 5,
  "total_markets": 42,
  "risk_blocked_count": 8,
  "failed_count": 4,
  "cancelled_count": 2,
  "latency_p50_ms": 700.0,
  "latency_p90_ms": 1500.0,
  "latency_p95_ms": 2100.0,
  "latency_max_ms": 4800.0,
  "has_data": true
}
```

> `start_time` / `end_time` 在区间内无数据时为 `null`；`has_data` 表示区间内是否存在统计数据。

**WalletPerformance 结构:**
```json
{
  "wallet": "0x...",
  "alias": "巨鲸A",
  "signal_count": 30,
  "execution_count": 25,
  "filled_count": 20,
  "fill_rate": 0.8,
  "total_pnl": 120.5,
  "win_rate": 0.55,
  "avg_pnl": 6.03,
  "contribution_pct": 51.4,
  "avg_latency_ms": 780.0,
  "last_active": "2026-01-07T18:30:00Z"
}
```

**StrategyPerformance 结构:**
```json
{
  "strategy_id": "st_abc123",
  "strategy_type": "SIZE_FILTER",
  "name": "数量过滤",
  "category": "FILTER",
  "pipeline_id": "pl_xyz789",
  "pipeline_name": "巨鲸跟单流水线",
  "position": 1,
  "triggered_count": 30,
  "passed_count": 22,
  "pass_rate": 0.73,
  "contributed_pnl": 120.5,
  "contribution_pct": 51.4,
  "win_rate": 0.55
}
```

**MarketPerformance 结构:**
```json
{
  "market_key": "0xcond...",
  "condition_id": "0x...",
  "token_id": "12345",
  "title": "BTC 能否突破 10 万美元？",
  "slug": "will-btc-hit-100k",
  "trade_count": 40,
  "execution_count": 32,
  "filled_count": 26,
  "total_pnl": 88.5,
  "win_rate": 0.62,
  "avg_pnl": 3.4,
  "best_pnl": 45.0,
  "worst_pnl": -18.5,
  "bucket": "profit",
  "rank": 1
}
```

> `bucket` 取值为 `profit`（盈利 Top10）/ `loss`（亏损 Top10）/ `flat`（无盈亏）。

**DailyPerformance 结构:**
```json
{
  "date": "2026-01-07",
  "pnl": 45.2,
  "signal_count": 22,
  "execution_count": 18,
  "filled_count": 15,
  "win_rate": 0.6,
  "cumulative_pnl": 234.56
}
```

### 组合跟单

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/portfolio` | — | `PortfolioAllocation` | 获取组合跟单配置（未配置时返回默认空权重列表） |
| PUT | `/api/portfolio` | `PortfolioAllocation` | `PortfolioAllocation` | 保存组合配置，立即生效并持久化到 DB |
| GET | `/api/portfolio/exposure` | — | `{total_exposure, total_limit, utilization, wallets}` | 组合当前敞口快照 |
| GET | `/api/portfolio/weights` | — | `dict[str, float]` | 归一化后的权重表 `{wallet: share}`，share 之和为 1 |
| POST | `/api/portfolio/check?wallet=&amount=` | — | `AllocationCheckResult` | 试算一笔信号的分配结果（不实际下单），供前端预览 |

**PortfolioAllocation 结构:**
```json
{
  "allocations": [
    {
      "wallet": "0xAAA...",
      "weight": 1.0,
      "max_amount": null,
      "enabled": true
    }
  ],
  "max_total_exposure": 1000.0,
  "max_per_wallet_share": 0.6,
  "weight_mode": "manual"
}
```

> `weight_mode` 取值为 `equal`（等权）/ `manual`（手动权重）/ `pnl`（按历史盈亏加权）。
> `allocations` 中的 `weight` 为相对值，系统按总量归一化；`max_amount` 为 `null` 时回退到全局风控上限。

**AllocationCheckResult 结构（`/api/portfolio/check` 响应）:**
```json
{
  "allowed": true,
  "wallet": "0xAAA...",
  "weight": 0.5,
  "allocated_amount": 250.0,
  "reason": "权重 50.0%，分配 250.00 USDC"
}
```

> `allowed=false` 时 `reason` 说明拒绝原因（钱包不在组合中、总敞口已达上限、分配后金额为 0 等）。

**`/api/portfolio/exposure` 响应结构:**
```json
{
  "total_exposure": 600.0,
  "total_limit": 1000.0,
  "utilization": 0.6,
  "wallets": [
    {"wallet": "0xAAA...", "weight": 0.6, "exposure": 400.0, "share": 0.67}
  ]
}
```

> 执行器未初始化或未配置组合跟单时，`exposure` / `weights` 返回降级空值，`check` 视为未配置权重全额执行。

### 健康检查

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/health` | — | `{status: "ok"}` | 探活端点，供 Docker `HEALTHCHECK` 与外部监控使用 |

### 链路自检（模拟交易注入）

> 用于在不等待真实交易的情况下验证「检测 → 策略 → 风控 → 分配 → 执行」全链路是否配置正确。

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/api/simulate/trade` | `SimulateTradeRequest` | `TradeEvent` | 注入一笔模拟交易，走与真实捕获完全相同的回调链路 |
| GET | `/api/simulate/status` | — | 自检状态对象 | 返回前置条件检查结果，供前端展示「还差哪一步」 |

**SimulateTradeRequest 结构:**

```json
{
  "wallet": "",              // 源钱包地址；留空则取第一个监控钱包
  "market_title": "模拟市场-自检",
  "market_slug": "sim-market",
  "side": "BUY",             // BUY | SELL
  "price": 0.55,             // 0-1
  "size": 200.0              // >0，源交易数量
}
```

**`/api/simulate/status` 响应:**

```json
{
  "ready": true,
  "mode": "paper",
  "wallets": 2,
  "pipelines": 1,
  "enabled_pipelines": 1,
  "max_trade_amount": 100.0,
  "checks": [
    {"name": "监控钱包", "ok": true, "detail": "2 个已添加"},
    {"name": "启用中的策略管道", "ok": true, "detail": "1/1 条启用"},
    {"name": "模拟模式", "ok": true, "detail": "当前 paper 模式"}
  ]
}
```

**安全约束**: `executor.mode != "paper"` 时 `POST /api/simulate/trade` 返回 **403**，
避免在实盘模式下误触发真实下单。钱包留空且无监控钱包时返回 **400**，
监控器未初始化时返回 **503**。

> 注入的 `condition_id` / `token_id` / `tx_hash` 均以 `0xsim` / `sim_token_` 前缀生成，
> 便于在交易流中辨识数据来源。

---

## WebSocket `/ws`

### 客户端 → 服务端

```json
{"type": "subscribe", "channels": ["trades", "signals", "executions", "stats", "wallets"]}
```

### 服务端 → 客户端

| type | data | 说明 |
|------|------|------|
| `trade_update` | `TradeEvent` | 新交易捕获 |
| `signal_generated` | `FollowSignal` | 跟单信号生成 |
| `execution_update` | `ExecutionRecord` | 执行状态更新 |
| `wallet_status` | `{address, status, last_checked, trade_count}` | 钱包状态更新 |
| `stats_update` | `DashboardStats` | 统计数据更新 |
| `subscribed` | `{channels: list[str]}` | 订阅确认 |
| `error` | `{message: string}` | 错误消息 |

### 实时行情（Market Channel）

Polymarket Market Channel WebSocket 客户端的连接状态、订阅关系与缓存价格通过以下 REST 端点读取；
前端无需自行维护 WebSocket 连接即可获得实时 mid / best_bid / best_ask。

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/api/ws/status` | — | `{connected, subscribed_markets, price_cache_size, uptime, enabled}` | Market Channel 客户端状态 |
| POST | `/api/ws/subscribe` | `{token_ids: list[str]}` | `{ok, subscribed, total_subscribed}` | 手动订阅额外市场 token_id |
| GET | `/api/ws/price/{token_id}` | — | `{token_id, cached, mid, best_bid, best_ask, last_trade_price, timestamp}` | 获取缓存的实时价格 |

**`/api/ws/status` 响应结构:**
```json
{
  "connected": true,
  "subscribed_markets": ["12345", "67890"],
  "price_cache_size": 12,
  "uptime": 3600.5,
  "enabled": true
}
```

> `enabled=false` 表示后端未启用 Market Channel 客户端，此时其余字段为降级空值。
> `subscribed_markets` 为该客户端当前订阅的 asset_id 列表，`price_cache_size` 为缓存条目数。

**`/api/ws/price/{token_id}` 响应结构（有缓存）:**
```json
{
  "token_id": "12345",
  "cached": true,
  "mid": 0.65,
  "best_bid": 0.64,
  "best_ask": 0.66,
  "last_trade_price": 0.65,
  "timestamp": "2026-01-01T12:00:00Z"
}
```

> 该 token_id 无缓存时返回 `{"token_id": "...", "cached": false}`（不含价格字段）。
> 客户端未启用时返回 `503 WebSocket 客户端未启用`。

---

## 数据模型

### WalletWatch
```json
{
  "address": "0x...",
  "alias": "巨鲸A",
  "enabled": true,
  "added_at": "2026-01-01T00:00:00Z",
  "last_checked": "2026-01-01T12:00:00Z",
  "trade_count": 42
}
```

### TradeEvent
```json
{
  "wallet": "0x...",
  "condition_id": "0x...",
  "token_id": "12345",
  "market_slug": "will-btc-hit-100k",
  "market_title": "BTC 能否突破 10 万美元？",
  "side": "BUY",
  "price": 0.65,
  "size": 100.0,
  "timestamp": "2026-01-01T12:00:00Z",
  "tx_hash": "0x...",
  "asset_id": "12345"
}
```

### StrategyType (enum)
`MIRROR` | `PROPORTIONAL` | `FIXED_AMOUNT` | `PRICE_OFFSET` | `DELAYED` | `MARKET_FILTER` | `SIZE_FILTER` | `REVERSE` | `POSITION_CAP` | `STOP_LOSS`

### StrategyCategory (enum)
`FILTER` | `TRANSFORM` | `RISK` | `EXECUTION`

### StrategyConfig
```json
{
  "id": "st_abc123",
  "type": "PROPORTIONAL",
  "category": "TRANSFORM",
  "name": "半比例跟单",
  "description": "0.5 倍比例",
  "enabled": true,
  "params": {"ratio": 0.5},
  "display_order": 1
}
```

### StrategyPipeline
```json
{
  "id": "pl_xyz789",
  "name": "巨鲸跟单流水线",
  "strategies": [StrategyConfig, ...],
  "enabled": true,
  "target_wallets": ["0xAAA...", "0xBBB..."]
}
```

### FollowSignal
```json
{
  "source_trade": TradeEvent,
  "pipeline_id": "pl_xyz789",
  "market": {"condition_id": "0x...", "token_id": "12345", "slug": "...", "title": "..."},
  "side": "BUY",
  "price": 0.65,
  "size": 50.0,
  "strategy_chain": ["st_abc123", "st_def456"],
  "created_at": "2026-01-01T12:00:01Z",
  "signal_id": "sig_..."
}
```

### ExecutionRecord
```json
{
  "signal_id": "sig_...",
  "status": "FILLED",
  "order_id": "paper_sig_...",
  "fill_price": 0.65,
  "fill_size": 50.0,
  "pnl": 0.0,
  "mode": "PAPER",
  "timestamp": "2026-01-01T12:00:02Z",
  "error": ""
}
```

### LeaderboardEntry
```json
{
  "rank": 1,
  "wallet": "0x...",
  "pnl": 12345.67,
  "volume": 98765.43,
  "markets_traded": 120
}
```

### DashboardStats
```json
{
  "watched_wallets": 5,
  "today_trades": 23,
  "today_signals": 15,
  "today_executions": 12,
  "total_pnl": 234.56
}
```

### BacktestParams
```json
{
  "start_time": null,
  "end_time": null,
  "initial_capital": 1000.0,
  "slippage_ticks": 1,
  "min_amount": 0.0,
  "max_position_per_market": 500.0,
  "tick_size": 0.01
}
```

> `start_time` / `end_time` 可空，省略时回测该钱包全部历史交易。
> `slippage_ticks` 范围 0–100，滑点 = `tick_size` × `slippage_ticks`。

### BacktestMetrics
```json
{
  "total_trades": 120,
  "total_signals": 90,
  "pass_rate": 0.75,
  "executed_trades": 90,
  "filtered_trades": 30,
  "win_rate": 0.6,
  "total_pnl": 234.56,
  "avg_pnl": 2.6,
  "max_win": 45.0,
  "max_loss": -18.5,
  "profit_factor": 1.8,
  "max_drawdown": 60.0,
  "initial_capital": 1000.0,
  "final_equity": 1234.56,
  "benchmark_pnl": 200.0,
  "benchmark_win_rate": 0.55,
  "benchmark_trades": 120,
  "excess_pnl": 34.56,
  "excess_return_pct": 3.46
}
```

> `benchmark_*` 为镜像跟单基线指标；`excess_pnl` = `total_pnl` − `benchmark_pnl`。

### BacktestTradeDetail
```json
{
  "source_trade": TradeEvent,
  "passed": true,
  "executed": true,
  "reason": "SIZE_FILTER：交易数量满足 min_size",
  "filter_strategy_id": "",
  "filter_strategy_name": "",
  "strategy_chain": ["st_abc123"],
  "side": "BUY",
  "fill_price": 0.65,
  "fill_size": 50.0,
  "exit_price": 0.68,
  "pnl": 1.5,
  "timestamp": "2026-01-01T12:00:00Z"
}
```

> `side` / `fill_price` / `fill_size` / `exit_price` / `pnl` 在未成交时为 `null`。
> 未通过策略时 `passed=false`，`filter_strategy_id` / `filter_strategy_name` 指向触发过滤的策略。

### BacktestFilterStat
```json
{
  "strategy_id": "st_abc123",
  "strategy_name": "数量过滤",
  "reason": "交易数量低于 min_size",
  "count": 18,
  "pct": 15.0
}
```

### BacktestResult
```json
{
  "wallet": "0x...",
  "pipeline_id": "pl_xyz789",
  "pipeline_name": "巨鲸跟单流水线",
  "params": BacktestParams,
  "metrics": BacktestMetrics,
  "trades": [BacktestTradeDetail, ...],
  "equity_curve": [0.0, 2.6, -1.2, 3.8],
  "filter_reasons": {"STOP_LOSS": 12, "SIZE_FILTER": 18},
  "filter_stats": [BacktestFilterStat, ...],
  "data_source": "db"
}
```

> `equity_curve` 为逐笔累计盈亏曲线；`filter_reasons` 为策略名 → 过滤次数的聚合。
> `data_source` 取值为 `db` 或 `api`。

### PerformanceSummary
```json
{
  "period_days": 7,
  "start_time": "2026-01-01T00:00:00Z",
  "end_time": "2026-01-08T00:00:00Z",
  "total_signals": 150,
  "total_executions": 120,
  "filled_executions": 100,
  "fill_rate": 0.83,
  "total_pnl": 234.56,
  "win_rate": 0.6,
  "avg_pnl": 2.35,
  "avg_hold_seconds": 3600.0,
  "avg_latency_ms": 820.5,
  "total_wallets": 5,
  "total_markets": 42,
  "risk_blocked_count": 8,
  "failed_count": 4,
  "cancelled_count": 2,
  "latency_p50_ms": 700.0,
  "latency_p90_ms": 1500.0,
  "latency_p95_ms": 2100.0,
  "latency_max_ms": 4800.0,
  "has_data": true
}
```

> `start_time` / `end_time` 可空；`avg_hold_seconds` 为近似值，`latency_*` 单位为毫秒。

### WalletPerformance
```json
{
  "wallet": "0x...",
  "alias": "巨鲸A",
  "signal_count": 30,
  "execution_count": 25,
  "filled_count": 20,
  "fill_rate": 0.8,
  "total_pnl": 120.5,
  "win_rate": 0.55,
  "avg_pnl": 6.03,
  "contribution_pct": 51.4,
  "avg_latency_ms": 780.0,
  "last_active": "2026-01-07T18:30:00Z"
}
```

> `last_active` 可空（区间内无活动时为 `null`）。

### StrategyPerformance
```json
{
  "strategy_id": "st_abc123",
  "strategy_type": "SIZE_FILTER",
  "name": "数量过滤",
  "category": "FILTER",
  "pipeline_id": "pl_xyz789",
  "pipeline_name": "巨鲸跟单流水线",
  "position": 1,
  "triggered_count": 30,
  "passed_count": 22,
  "pass_rate": 0.73,
  "contributed_pnl": 120.5,
  "contribution_pct": 51.4,
  "win_rate": 0.55
}
```

> `strategy_type` / `category` 为字符串而非枚举，取自策略注册表；`position` 从 1 起。

### MarketPerformance
```json
{
  "market_key": "0xcond...",
  "condition_id": "0x...",
  "token_id": "12345",
  "title": "BTC 能否突破 10 万美元？",
  "slug": "will-btc-hit-100k",
  "trade_count": 40,
  "execution_count": 32,
  "filled_count": 26,
  "total_pnl": 88.5,
  "win_rate": 0.62,
  "avg_pnl": 3.4,
  "best_pnl": 45.0,
  "worst_pnl": -18.5,
  "bucket": "profit",
  "rank": 1
}
```

> `bucket` 取值为 `profit` / `loss` / `flat`；`rank` 为分组内排名（0 表示未上榜）。

### DailyPerformance
```json
{
  "date": "2026-01-07",
  "pnl": 45.2,
  "signal_count": 22,
  "execution_count": 18,
  "filled_count": 15,
  "win_rate": 0.6,
  "cumulative_pnl": 234.56
}
```

> `date` 为 UTC 日期字符串 `YYYY-MM-DD`。

### WalletAllocation
```json
{
  "wallet": "0xAAA...",
  "weight": 1.0,
  "max_amount": null,
  "enabled": true
}
```

> `weight` 为相对值（>0，≤100），系统按总量归一化；`max_amount` 可空，
> 为 `null` 时回退到全局风控上限 `RiskConfig.max_trade_amount`。

### PortfolioAllocation
```json
{
  "allocations": [WalletAllocation, ...],
  "max_total_exposure": 1000.0,
  "max_per_wallet_share": 0.6,
  "weight_mode": "manual"
}
```

> `max_total_exposure` 为组合总敞口上限（>0），超出则拒绝新信号；
> `max_per_wallet_share` 范围 (0, 1]，用于强制分散，1 表示不限制；
> `weight_mode` 取值为 `equal` / `manual` / `pnl`。

### AllocationCheckResult
```json
{
  "allowed": true,
  "wallet": "0xAAA...",
  "weight": 0.5,
  "allocated_amount": 250.0,
  "reason": "权重 50.0%，分配 250.00 USDC"
}
```

### PortfolioExposure
```json
{
  "total_exposure": 600.0,
  "total_limit": 1000.0,
  "utilization": 0.6,
  "wallets": [{"wallet": "0xAAA...", "weight": 0.6, "exposure": 400.0, "share": 0.67}]
}
```

---

## 策略类型详解

| 类型 | 类别 | 说明 | 参数 |
|------|------|------|------|
| MIRROR | EXECUTION | 镜像跟单，完全复制 | — |
| PROPORTIONAL | TRANSFORM | 比例缩放 | `ratio: float` |
| FIXED_AMOUNT | TRANSFORM | 固定金额 | `amount: float` |
| PRICE_OFFSET | TRANSFORM | 价格偏移 | `offset_pct: float` |
| DELAYED | EXECUTION | 延迟执行 | `delay_seconds: int` |
| MARKET_FILTER | FILTER | 市场过滤 | `slugs: list[str]`, `mode: "allow"\|"block"` |
| SIZE_FILTER | FILTER | 数量过滤 | `min_size: float`, `max_size: float` |
| REVERSE | TRANSFORM | 反向跟单 | — |
| POSITION_CAP | RISK | 仓位上限 | `max_position: float` |
| STOP_LOSS | RISK | 止损 | `loss_threshold: float` |
