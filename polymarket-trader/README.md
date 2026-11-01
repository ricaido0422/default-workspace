# Polymarket Trader

> 本次检查（2026-10-26）：已验证本机启动、模拟跟单和实盘接口桩测试；未提交真实资金订单。
> **已接入限价 FOK 实盘执行。默认保持 Paper；配置凭证并通过只读检查后，需在设置页手动切换 Live。**
> 双击 `1-start-service-bundled.bat`，打开 http://127.0.0.1:8765 即可操作。
> 设置页保存的监控、代理、风控参数会写入当前 YAML 配置文件（会重新格式化并去除注释）；环境变量仍优先。
> 流程回归：`Python312\python.exe -m unittest discover -s tests -v`，测试使用临时配置和数据库。

> 实时监测 Polymarket 钱包交易、多策略可组合跟单的 Python Web 应用

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

> 🚀 **不懂命令行？** 看 [零基础上手指南](零基础上手指南.md) —— 装一次 Python，之后双击 `1-start-service.bat`，浏览器打开 `http://localhost:8765` 即可使用。>
>
> 📦 **本机已内置 Python（免安装版）**：本目录下自带 `Python312\` 及其全部依赖，
> 无需自己装 Python、也不用联网装依赖——直接双击 `1-start-service-bundled.bat` 即可启动。
> 📱 **想在电脑、手机、平板上同时使用？** 见 [多端访问指南](docs/multi-device-access.md)。
>
> 📖 **怎么用？（从零配好自动跟单）** 见 [使用指南](使用指南.md) —— 五步配置、10 种策略组合配方、
> 一键链路自检、回测调优、FAQ，附模拟运行验证报告。

## 功能特性

- **钱包监控**：实时轮询 Polymarket 钱包交易活动，自动捕获新交易，支持多钱包并行监控
- **秒级实时**：高频轮询（默认 2s，最小 1s）+ WebSocket Market Channel 混合模式，实时缓存订单簿价格
- **多策略跟单**：10 种可组合策略，通过流水线串联定义复杂跟单逻辑
- **策略流水线**：拖拽式管道编排，支持策略的启用/禁用、参数调整、排序
- **策略自动调优**：按策略参数空间批量回测，多目标评分排序，一键写回最优组合
- **组合分散跟单**：多钱包权重分配（equal/manual/pnl）+ 分散上限 + 组合总敞口联动风控
- **回测与绩效**：历史交易回放回测、胜率/盈亏比/回撤等多维绩效分析
- **双模式执行**：Paper（模拟资金·实盘价结算）模式安全测试，Live（实盘）模式真实下单
- **虚拟持仓盯市**：模拟资金账户按 Polymarket 真实市价实时盯市，已实现/浮动盈亏均按实盘价结算
- **实时推送**：WebSocket 实时推送交易、信号、执行状态，前端即时更新
- **排行榜导入**：从 Polymarket 排行榜一键导入优质钱包（按 PnL 排序）
- **通知推送**：Telegram / 邮件，支持事件过滤、金额门槛、冷却与每日汇总
- **风险管理**：单笔金额上限、单市场仓位上限、日交易数限制、组合集中度约束
- **单页前端**：响应式 SPA 深色主题仪表盘，11 个面板，无构建步骤

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + Pydantic v2 + aiosqlite + httpx |
| 前端 | 原生 HTML/CSS/JS（无框架依赖，无构建步骤） |
| 数据源 | Polymarket Gamma API / Data API v2 / CLOB API |
| 实盘交易 | py-clob-client |
| 数据库 | SQLite（aiosqlite 异步驱动） |

## 快速开始

### ⚡ 零基础最快路径（推荐）

**完全不懂命令行？** 双击两个 `.bat` 文件就能跑起来，详细图文步骤见 [零基础上手指南](零基础上手指南.md)。

```
第 1 次  双击  0-install-deps.bat     （安装 Python 依赖，只做一次）
每次使用 双击  1-start-service.bat    （启动服务，弹出小黑窗）
用完     双击  2-stop-service.bat     （关闭服务）
```

启动后浏览器访问 `http://localhost:8765`。

> 💡 双击 `.bat` 之前，**必须先装好 Python**（安装时勾选 "Add Python to PATH"），
> 步骤见 [零基础上手指南](零基础上手指南.md) 第一步。

### 1. 安装依赖（命令行方式）

```bash
pip install -r requirements.txt
```

### 2. 启动服务

**方式一：标准启动（推荐）**

```bash
python run.py
```

首次运行会自动从 `config.example.yaml` 创建 `config.yaml`，并自动打开浏览器。

**方式二：降级启动（环境缺少 uvicorn 等 ASGI 依赖时）**

```bash
python run_minimal.py
```

当 `pip install` 因网络等原因无法完成时，`run_minimal.py` 用 Python 标准库自带的 HTTP 服务承载应用，**无需 uvicorn 即可运行**。

| 对比项 | `run.py` | `run_minimal.py` |
|---|---|---|
| 依赖 | uvicorn + 完整 requirements | 仅需 FastAPI/aiosqlite/httpx/websockets |
| WebSocket 实时推送 | ✅ 支持 | ❌ 不支持（升级请求返回 503，自动降级为轮询） |
| 其余功能 | 完整 | 完整（REST 全部可用） |
| 适用场景 | 正常环境 | 依赖装不上的应急环境 |

> 前端 WebSocket 断线后会自动重连，`run_minimal.py` 下仍可通过轮询看到交易数据，只是实时性下降。

### 3. 访问应用

浏览器自动打开 `http://localhost:8765`，或手动访问。

### 开发模式

```bash
python run.py --reload    # 热重载
python run.py --port 9000 # 自定义端口
python run.py --no-browser # 不自动打开浏览器
```

`run_minimal.py` 同样支持 `--host` / `--port` / `--no-browser`。

> 📱 **想在电脑、手机、平板上同时使用？** 见 [多端访问指南](docs/multi-device-access.md)（局域网直连、防火墙放行、公网访问方案与安全提醒）。

## 配置说明

配置优先级：**环境变量 > YAML 文件 > 默认值**

### config.yaml

复制 `config.example.yaml` 为 `config.yaml` 并按需修改：

```yaml
# 代理（可选，用于访问 Polymarket API）
proxy:
  http: ""          # 例: "http://127.0.0.1:7890"
  https: ""

# Polymarket API 凭据（可选，真实跟单需要）
polymarket:
  api_key: ""
  api_secret: ""
  api_passphrase: ""
  wallet_address: ""    # 你的 Polygon 钱包地址
  private_key: ""       # 私钥（仅本地使用，切勿提交）

# 监控配置
monitor:
  interval: 15          # 轮询间隔（秒）

# 风险控制
risk:
  max_trade_amount: 100.0        # 单笔最大金额 (USDC)
  max_position_per_market: 500.0  # 单市场最大仓位 (USDC)
  max_daily_trades: 50           # 日最大交易数

# 执行器
executor:
  mode: paper           # paper | live

# 服务器
server:
  port: 8765
  host: "0.0.0.0"
```

### 环境变量

复制 `.env.example` 为 `.env`，或直接设置系统环境变量。所有配置项均可通过环境变量覆盖（见 `.env.example` 注释）。

### 代理设置

如果网络无法直接访问 Polymarket API，设置代理：

```bash
# 环境变量
export PROXY_HTTP=http://127.0.0.1:7890
export PROXY_HTTPS=http://127.0.0.1:7890

# 或在 config.yaml 中
proxy:
  http: "http://127.0.0.1:7890"
  https: "http://127.0.0.1:7890"
```

## 使用指南

### 添加监控钱包

1. 打开应用，进入「钱包」面板
2. 在输入框中填入钱包地址（0x...）和别名
3. 按 Enter 或点击「添加」按钮
4. 钱包立即开始监控，交易实时显示在「交易」面板

### 排行榜导入

1. 进入「钱包」面板，点击「排行榜导入」
2. 选择时间窗口（日/周/月/全部）和数量
3. 勾选要导入的钱包（按 PnL 排序）
4. 点击「批量导入」

### 策略组合

1. 进入「策略」面板
2. 左侧显示 10 种可用策略，拖拽到中间编排区
3. 调整策略顺序（拖拽排序）、启用/禁用、设置参数
4. 点击「保存管道」，输入管道名称
5. 可创建多个管道，同时运行

### 加载示例管道

项目提供 3 个示例管道配置（`config/pipelines.example.json`）：

- **保守跟单**：SizeFilter(>$500) → Proportional(0.5) → PositionCap → Mirror
- **激进跟单**：SizeFilter(>$100) → FixedAmount($200) → PriceOffset(0.01) → Mirror
- **反向跟单**：SizeFilter(>$1000) → Reverse → Proportional(0.3) → StopLoss(10%,20%)

通过 API 导入示例管道：

```bash
# 启动服务后，用脚本导入示例管道
python -c "
import httpx, json
pipes = json.load(open('config/pipelines.example.json'))
for p in pipes:
    r = httpx.post('http://localhost:8765/api/pipelines', json={'name': p['name']})
    pid = r.json()['id']
    httpx.put(f'http://localhost:8765/api/pipelines/{pid}', json={'strategies': p['strategies'], 'enabled': p['enabled']})
    print(f'已导入: {p[\"name\"]} → {pid}')
"
```

### 执行模式切换

1. 进入「设置」面板
2. 执行模式区域显示当前模式（paper/live）
3. 切换到 live 模式需要二次确认（弹窗警告）
4. live 模式需要配置 API 凭据

## 策略详解

### 策略类别

| 类别 | 说明 | 行为 |
|------|------|------|
| FILTER | 过滤 | 不符合条件的交易被丢弃，管道中止 |
| TRANSFORM | 变换 | 修改交易的方向/价格/数量，继续管道 |
| RISK | 风控 | 检查仓位/盈亏，超限则中止或生成平仓信号 |
| EXECUTION | 执行控制 | 控制执行时机和方式 |

### 10 种策略

#### 1. MIRROR — 镜像跟单

- **类别**：EXECUTION
- **参数**：无
- **用途**：完全复制源交易的方向、价格和数量
- **示例**：源交易 BUY 100@0.6 → 跟单 BUY 100@0.6

#### 2. PROPORTIONAL — 比例跟单

- **类别**：TRANSFORM
- **参数**：`ratio`（比例系数，0.01-10，默认 1.0）
- **用途**：按比例缩放交易数量
- **示例**：ratio=0.5，源交易 BUY 100@0.6 → 跟单 BUY 50@0.6

#### 3. FIXED_AMOUNT — 固定金额

- **类别**：TRANSFORM
- **参数**：`amount`（USDC 金额，1-10000，默认 100）
- **用途**：使用固定金额下单，size = amount / price
- **示例**：amount=200，源交易 BUY 100@0.5 → 跟单 BUY 400@0.5（200/0.5=400）

#### 4. PRICE_OFFSET — 价格偏移

- **类别**：TRANSFORM
- **参数**：`offset`（偏移量，0-0.5，默认 0.01）、`direction`（buy_up/sell_down/auto，默认 auto）
- **用途**：在源价格基础上偏移，提高成交概率
- **示例**：offset=0.01，BUY 100@0.6 → BUY 100@0.61

#### 5. DELAYED — 延迟执行

- **类别**：EXECUTION
- **参数**：`delay_seconds`（延迟秒数，≥0，默认 0）
- **用途**：延迟一定时间后执行跟单
- **示例**：delay_seconds=60，交易延迟 60 秒后执行

#### 6. MARKET_FILTER — 市场过滤

- **类别**：FILTER
- **参数**：`mode`（whitelist/blacklist）、`slugs`（市场 slug 列表）、`tags`（标签列表）、`min_volume`（最小交易量）
- **用途**：按市场条件过滤，只跟单特定市场的交易
- **示例**：mode=whitelist, slugs=["will-bitcoin-hit-100k"]，只跟单该市场

#### 7. SIZE_FILTER — 数量过滤

- **类别**：FILTER
- **参数**：`threshold`（最小金额 USDC，0-100000，默认 50）
- **用途**：只跟单金额（size × price）大于阈值的交易
- **示例**：threshold=500，交易 100@0.6（$60）→ 过滤掉；交易 1000@0.6（$600）→ 通过

#### 8. REVERSE — 反向跟单

- **类别**：TRANSFORM
- **参数**：无
- **用途**：翻转交易方向（BUY → SELL，SELL → BUY）
- **示例**：源交易 BUY 100@0.6 → 跟单 SELL 100@0.6

#### 9. POSITION_CAP — 仓位上限

- **类别**：RISK
- **参数**：`max_per_market`（单市场上限，默认 1000）、`max_total`（总上限，默认 10000）
- **用途**：检查当前仓位，超限则不跟单
- **示例**：max_per_market=500，当前仓位 $400 + 新交易 $200 → 超限，过滤

#### 10. STOP_LOSS — 止盈止损

- **类别**：RISK
- **参数**：`stop_loss_pct`（止损比例，0-1，默认 0.1）、`take_profit_pct`（止盈比例，0-1，默认 0.2）
- **用途**：检查仓位盈亏，触发止损/止盈时生成平仓信号
- **示例**：stop_loss_pct=0.1，持仓 BUY 入场价 0.6，当前价 0.54（亏损 10%）→ 生成 SELL 平仓信号

### 组合示例

| 场景 | 管道 | 说明 |
|------|------|------|
| 保守跟单 | SizeFilter(>$500) → Proportional(0.5) → PositionCap → Mirror | 只跟大额交易，减半规模，仓位控制 |
| 激进跟单 | SizeFilter(>$100) → FixedAmount($200) → PriceOffset(0.01) → Mirror | 小额也跟，固定金额，加价成交 |
| 反向跟单 | SizeFilter(>$1000) → Reverse → Proportional(0.3) → StopLoss(10%,20%) | 跟大额反向，缩量，止盈止损 |

## 风险提示

### ⚠️ 真实跟单风险

- **Live 模式会使用真实资金下单**，请充分理解风险后再启用
- 建议先用 Paper 模式验证策略效果，再切换到 Live
- 设置合理的风控参数（单笔上限、仓位上限、日交易数）
- 跟单不保证盈利，市场波动可能导致亏损
- Polymarket 市场为预测市场，结果不确定

### 🔒 API 凭证安全

- 私钥和 API 凭证仅存储在本地配置文件中，不会上传到任何服务器
- 切勿将含凭据的 `config.yaml` 或 `.env` 提交到版本控制
- 建议在 `.gitignore` 中添加 `config.yaml`、`.env`、`data/`
- 后端 API 的 `/api/config` 端点对凭据脱敏，不返回私钥
- 前端设置页面的密码框不回显已保存的凭据

## 架构说明

### 数据流

```
Polymarket API
      │
      ▼
WalletMonitor (轮询)
      │ on_new_trade
      ▼
EventBus ──→ WebSocket ──→ 前端实时更新
      │
      ▼
StrategyPipeline (策略链)
      │ process(trade) → signal
      ▼
EventBus ──→ WebSocket ──→ 前端实时更新
      │
      ▼
OrderExecutor (执行)
      │ execute(signal) → record
      ▼
EventBus ──→ WebSocket ──→ 前端实时更新
```

### 目录结构

```
polymarket-trader/
├── backend/
│   ├── main.py                  # FastAPI 应用入口 + 生命周期管理
│   ├── config.py                # 配置管理 (Pydantic Settings)
│   ├── api/
│   │   ├── routes.py            # REST API 路由（39 个端点）
│   │   ├── ws.py                # WebSocket 端点 + 连接管理
│   │   └── events.py            # EventBus 事件总线
│   ├── core/
│   │   ├── polymarket_client.py # Polymarket API 客户端
│   │   ├── wallet_monitor.py    # 钱包交易监控引擎
│   │   ├── leaderboard.py       # 排行榜客户端
│   │   ├── ws_client.py         # CLOB Market Channel WS 客户端
│   │   ├── order_executor.py    # 订单执行器 (Paper/Live) + 组合风控
│   │   ├── portfolio.py         # 多钱包权重分配与敞口管理
│   │   ├── backtest.py          # 历史交易回测引擎
│   │   ├── analytics.py         # 绩效分析引擎
│   │   └── notifier.py          # Telegram / 邮件通知
│   ├── strategies/
│   │   ├── base.py              # 策略基类 + 注册表
│   │   ├── pipeline.py          # 策略流水线运行器
│   │   ├── mirror.py            # 镜像跟单
│   │   ├── proportional.py      # 比例跟单
│   │   ├── fixed_amount.py      # 固定金额
│   │   ├── price_offset.py      # 价格偏移
│   │   ├── delayed.py           # 延迟执行
│   │   ├── market_filter.py     # 市场过滤
│   │   ├── size_filter.py       # 数量过滤
│   │   ├── reverse.py           # 反向跟单
│   │   ├── position_cap.py      # 仓位上限
│   │   └── stop_loss.py         # 止盈止损
│   ├── models/
│   │   └── schemas.py           # Pydantic v2 数据模型
│   └── store/
│       └── state.py             # SQLite 异步状态存储
├── frontend/
│   ├── index.html               # 单页应用骨架
│   ├── css/style.css            # 深色主题样式
│   └── js/
│       ├── app.js               # 主应用 + 路由 + Toast
│       ├── ws.js                # WebSocket 客户端
│       ├── api.js               # REST API 封装
│       └── panels/              # 11 个面板模块
│           ├── dashboard.js     # 仪表盘
│           ├── wallets.js       # 钱包管理
│           ├── trades.js        # 交易列表
│           ├── strategies.js    # 策略编排
│           ├── execution.js     # 执行监控
│           ├── backtest.js      # 回测
│           ├── analytics.js     # 绩效分析
│           ├── optimizer.js     # 策略自动调优
│           ├── portfolio.js     # 组合分散跟单
│           └── settings.js      # 系统设置
├── config/
│   └── pipelines.example.json   # 示例策略管道配置
├── docs/
│   ├── api-contract.md          # API 契约文档（39 个端点）
│   └── multi-device-access.md   # 多端访问指南（PC / 手机 / 平板）
├── data/                        # SQLite 数据目录（运行时生成，未打包）
├── requirements.txt
├── config.example.yaml
├── .env.example
├── run.py                       # 一键启动脚本（需完整依赖）
├── run_minimal.py               # 降级启动器（无 uvicorn 时可用，不支持 WS）
├── Python312/                   # 内置 Python 3.12.10 运行环境 + 全部依赖（约 48 MB）
├── 1-start-service-bundled.bat  # ⭐ 推荐：用内置 Python 启动，零安装零配置
├── 0-install-deps.bat           # 备用：给系统 Python 安装依赖（仅一次）
├── 1-start-service.bat          # 备用：用系统 Python 启动服务
├── 2-stop-service.bat           # 一键停止服务
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.docker.example
├── 零基础上手指南.md            # 不懂命令行用户的图文步骤
└── README.md
```

> 🚀 **免安装版**：目录内的 `Python312\` 已包含 Python 3.12.10 与全部第三方依赖，
> 直接双击 `1-start-service-bundled.bat` 即可运行，无需安装 Python、无需联网拉依赖。
> 整个文件夹可直接拷贝到任意 Windows 电脑（含 U 盘）上双击使用。

### REST API

详见 [docs/api-contract.md](docs/api-contract.md)。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/wallets` | 获取监控钱包列表 |
| POST | `/api/wallets` | 添加监控钱包 |
| DELETE | `/api/wallets/{address}` | 删除钱包 |
| POST | `/api/wallets/{address}/toggle` | 切换钱包状态 |
| GET | `/api/leaderboard` | 获取排行榜 |
| POST | `/api/wallets/import-leaderboard` | 批量导入钱包 |
| GET | `/api/pipelines` | 获取策略流水线 |
| POST | `/api/pipelines` | 创建流水线 |
| PUT | `/api/pipelines/{id}` | 更新流水线 |
| DELETE | `/api/pipelines/{id}` | 删除流水线 |
| GET | `/api/strategies/available` | 获取可用策略 |
| GET | `/api/trades` | 获取交易列表 |
| GET | `/api/executions` | 获取执行记录 |
| GET | `/api/stats` | 获取统计数据 |
| GET | `/api/config` | 获取配置（脱敏） |
| PUT | `/api/config` | 更新配置 |
| POST | `/api/executor/mode` | 设置执行模式 |
| POST | `/api/executor/credentials` | 设置凭据 |
| GET | `/api/ws/status` | 查询上游 WS 连接状态 |
| POST | `/api/ws/subscribe` | 订阅/退订 token 价格频道 |
| GET | `/api/ws/price/{token_id}` | 查询缓存的订单簿价格 |
| GET | `/api/backtest/strategies` | 获取可回测策略及参数空间 |
| POST | `/api/backtest/run` | 运行单组策略回测 |
| POST | `/api/backtest/compare` | 多组策略对比回测 |
| GET | `/api/analytics/summary` | 绩效总览 |
| GET | `/api/analytics/wallets` | 按钱包绩效 |
| GET | `/api/analytics/strategies` | 按策略绩效 |
| GET | `/api/analytics/markets` | 按市场绩效 |
| GET | `/api/analytics/daily` | 按日绩效曲线 |
| GET | `/api/portfolio` | 获取组合跟单配置 |
| PUT | `/api/portfolio` | 更新组合跟单配置 |
| GET | `/api/portfolio/exposure` | 查询组合敞口快照 |
| GET | `/api/portfolio/weights` | 查询归一化权重 |
| POST | `/api/portfolio/check` | 试算某笔金额的分配结果 |
| GET | `/api/portfolio/positions` | 虚拟持仓盯市快照（支持 `?wallet=`） |
| POST | `/api/portfolio/positions/reset` | 清空虚拟持仓（支持 `?wallet=`） |
| GET | `/api/health` | 健康检查（容器 HEALTHCHECK 用） |

完整请求/响应契约见 [docs/api-contract.md](docs/api-contract.md)。

### WebSocket

连接 `/ws`，支持订阅频道：

```json
{"type": "subscribe", "channels": ["trade_update", "signal_generated"]}
```

推送消息类型：`trade_update`、`signal_generated`、`execution_update`、`wallet_status`、`stats_update`

## FAQ

### Q: 启动后看不到交易数据？

A: 需要先添加监控钱包。进入「钱包」面板，输入 Polymarket 钱包地址（0x...），添加后系统开始轮询该钱包的交易活动。

### Q: Paper 模式和 Live 模式有什么区别？

A: **Paper（模拟资金）模式**接入的是真实市场数据，但投入的是虚拟资金：

| 维度 | Paper 模式 | Live 模式 |
| --- | --- | --- |
| 信号来源 | Polymarket 真实钱包交易 | 同左 |
| 成交价 | CLOB 真实盘口价（买入取 ask，卖出取 bid） | 真实成交价 |
| 投入资金 | **虚拟资金**（不下单、不动真实 USDC） | 真实 USDC |
| 盈亏结算 | **按实盘价盯市结算**（见下） | 真实账户盈亏 |

Live 模式使用 py-clob-client 实际下单到 Polymarket，需要配置 API 凭据，涉及真实资金。

### Q: 模拟盘的盈亏是怎么算的？是"假"的吗？

A: 不是假估算，而是**实盘数据 + 虚拟资金 + 实盘价结算**：

1. **建仓**：按 CLOB 真实盘口价成交，记录虚拟持仓（移动加权平均成本）
2. **持有**：按 Polymarket 真实市价实时盯市，浮动盈亏随之变化
   - 多头按买价 `bid` 估值，空头按卖价 `ask` 估值（取不到时退用中间价）
3. **平仓**：按真实成交价与加权均价配对，计算**已实现盈亏**

在「💼 虚拟持仓」面板可查看：未平仓数、已实现盈亏、浮动盈亏、总盈亏，
以及每个持仓的均价 / 现价 / 市值 / 计价来源。目标面板每 15 秒自动拉取一次真实价。

### Q: 如何减少 API 请求频率？

A: 增大监控轮询间隔（`monitor.interval`），或在「设置」面板调整。默认 15 秒，建议 10-60 秒。

### Q: 策略管道的执行顺序是什么？

A: 按 `display_order` 从小到大依次执行。前一个策略返回 None 则管道中止（该交易不跟单）。可以在「策略」面板拖拽调整顺序。

### Q: 支持同时运行多个管道吗？

A: 支持。每条新交易会依次经过所有启用的管道，各自独立处理和执行。可在管道配置中设置 `target_wallets` 限定只跟单特定钱包。

### Q: 数据存储在哪里？

A: SQLite 数据库，默认路径 `data/trader.db`。包含钱包、交易、信号、执行记录、管道配置等表。

### Q: 如何访问被墙的 Polymarket API？

A: 在 `config.yaml` 或环境变量中设置代理（`proxy.http` / `PROXY_HTTP`）。

### Q: 前端需要构建吗？

A: 不需要。前端是纯 HTML/CSS/JS，由 FastAPI 直接提供静态文件服务，无构建步骤。

### Q: `run.py` 和 `run_minimal.py` 该用哪个？

A: 能正常 `pip install -r requirements.txt` 就用 `run.py`（uvicorn，完整支持 WebSocket 实时推送）。只有当依赖装不上时用 `run_minimal.py`——它用标准库 HTTP 服务承载同样的应用，REST 接口与全部业务功能一致，但上游价格 WS 推送不可用，实时性退化为轮询。

### Q: 手机上能用吗？

A: 能。这是纯 Web 应用，手机/平板与电脑在同一 Wi-Fi 下时，浏览器直接打开 `http://<电脑局域网IP>:8765` 即可，无需安装任何 App，前端已做响应式适配。详细步骤（含防火墙放行、外出访问方案、安全提醒）见 [多端访问指南](docs/multi-device-access.md)。

## Docker 部署

### 前置条件

- Docker 20.10+
- Docker Compose v2（`docker compose version` 验证，需为 v2.x；Compose v1 的 `docker-compose` 命令不支持本文语法）

### 启动（三步）

```bash
# 1. 复制配置文件（首次启动也会自动生成，建议显式准备以便修改）
cp config.example.yaml config.yaml

# 2. 构建并启动容器
docker compose up -d --build

# 3. 访问应用
# 浏览器打开 http://localhost:8765
```

### 数据持久化

- 数据库文件通过卷 `./data:/app/data` 持久化到宿主机 `./data` 目录
- 容器重建（`docker compose up -d --build`）不会丢失钱包、交易、执行记录与管道配置
- 配置文件以只读方式挂载（`./config.yaml:/app/config.yaml:ro`），需在宿主机修改后重启容器生效

### 环境变量

在 `docker-compose.yml` 同级目录创建 `.env.docker`（可参考 `.env.docker.example`），通过 `${VAR:-default}` 透传：

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `HTTP_PROXY` | 空 | HTTP 代理地址，留空表示直连 |
| `HTTPS_PROXY` | 空 | HTTPS 代理地址，留空表示直连 |
| `NO_PROXY` | `localhost,127.0.0.1` | 不走代理的地址列表 |
| `MONITOR_INTERVAL` | `2` | 监控轮询间隔（秒） |
| `MONITOR_MIN_INTERVAL` | `1` | 允许的最小轮询间隔（秒） |
| `MONITOR_WS_ENABLED` | `true` | 是否启用 WebSocket 价格推送 |
| `EXECUTOR__MODE` | `paper` | 执行模式：`paper` 模拟 / `live` 实盘 |
| `RISK__MAX_TRADE_AMOUNT` | `100.0` | 单笔最大金额（USDC） |
| `RISK__MAX_POSITION_PER_MARKET` | `500.0` | 单市场最大仓位（USDC） |
| `RISK__MAX_DAILY_TRADES` | `50` | 每日最大交易数 |

> 嵌套配置使用 `__` 双下划线分隔，例如 `RISK__MAX_TRADE_AMOUNT` 对应 `risk.max_trade_amount`。

### 健康检查

容器内置 HEALTHCHECK，每 30 秒探测一次 `http://127.0.0.1:8765/api/health`（超时 5s，重试 3 次，启动宽限期 20s），探活接口返回 `{"status": "ok"}`。

```bash
docker ps                                  # 查看 STATUS 列，healthy 表示正常
docker inspect --format '{{.State.Health.Status}}' polymarket-trader
```

### 更新与停止

```bash
docker compose up -d --build               # 代码更新后重新构建并启动
docker compose logs -f                     # 查看实时日志
docker compose down                        # 停止并删除容器（数据卷保留）
docker compose stop                        # 仅停止容器
docker compose restart                     # 重启容器
```

### 常见问题

**Q: 端口冲突怎么办？**
A: 修改 `docker-compose.yml` 中 `ports` 的左侧宿主机端口，例如 `"8766:8765"`，然后访问 `http://localhost:8766`。

**Q: 无法访问 Polymarket API / 拉取数据失败？**
A: 检查代理配置。在 `.env.docker` 中设置 `HTTP_PROXY` / `HTTPS_PROXY`（注意容器内访问宿主机代理需用 `host.docker.internal`，Linux 上需额外加 `extra_hosts: ["host.docker.internal:host-gateway"]`），或改用 `config.yaml` 的 `proxy.http` / `proxy.https`，修改后执行 `docker compose up -d` 重建生效。

**Q: 想重置数据重新开始？**
A: 先停止容器（`docker compose down`），确认服务已停后删除宿主机 `data` 目录，再重新 `docker compose up -d --build`。切勿在容器运行时直接删除 `data` 目录。

## License

MIT


## 实盘使用与钱包资料（2026-10-26）

1. 在本机打开 `http://127.0.0.1:8765`，先保持 Paper 验证钱包和策略。
2. 设置 → 填写 API Key、Secret、Passphrase、签名私钥；EOA 选 0，代理钱包选 1 或 2 并填写正确的 Funder。签名钱包与资金钱包可能不同，请按实际钱包类型配置。
3. 凭证表单只保存在本次进程内存，留空保留已有秘密值，不会回显。需要重启后保留时，自行在本机 `.env` 设置 `POLYMARKET_*`，该文件为明文，不要分享。SDK 已内置安装。
4. 点击「实盘只读检查」，检查认证、抵押品余额和授权。资金充值与授权在 Polymarket 官方页面完成。
5. 设置单笔/单市场/每日限额，点击 Live 应用并确认，才会开始后续真实跟单。限价为策略输出价；BUY 向下、SELL 向上对齐 tick，数量向下保留两位小数。只用 FOK，不产生常驻挂单；无法按限价立即全部成交会拒绝。
6. 交易源时间超过默认 60 秒拒绝追单；可在 YAML `executor.max_signal_age_seconds` 调整。切回 Paper 或点击「停止后续实盘下单」可停止新单，已经提交的订单仍需核对。
7. 提交超时不会自动重发，执行记录显示 PENDING 并阻止后续实盘；有订单号时点击「核对订单」。无订单号时必须在交易所人工核对，当前版本不会自动解除此暂停。实盘限额按买入目标金额保守预占，拒单或未知结果也保留额度；本交易台已确认卖出后按已记录成本释放相应额度。这是工具内预算，不等同于交易所全账户净持仓。尚不提供自动链上授权、结算赎回和实盘账户完整持仓/盈亏同步。
8. 资金和配置写操作仅允许本机同源访问；局域网设备可以只读查看，不能远程启用实盘或填写凭证。

钱包监控 →「获取用户名」从公开资料同步；「编辑资料」保存备注名、用户名、擅长板块与观察备注。交易流与仪表盘优先展示备注名/用户名，地址仍可查看。排行榜使用官方 v1 接口，导入时保留用户名。

板块标签是人工判断，不是盈利认证。建议按板块统计样本量、已实现盈亏、回撤、持有时长，并与跟单后的真实净收益比较，再决定是否跟随。

测试：`Python312\python.exe -m unittest discover -s tests -v`。实盘测试使用模拟 SDK，不会下单。


## 交易台：自动、半自动与买卖（2026-10-26）

侧栏「交易台 · 买卖审核」提供完整操作入口。默认账户为 Paper，执行方式为半自动审核。

- **仅观察**：记录新策略信号，不自动执行；手动创建的委托仍可单独审批。
- **半自动**：钱包策略产生的 BUY/SELL 进入待审核队列。点击审核后重新校验当前盘口、信号时间、价差、深度、余额和额度；超时不能执行，可复制为新委托后重新定价。
- **自动**：只对保存规则之后的新监控钱包策略信号和已启用的止盈止损信号自动执行。手动买卖委托始终需要逐笔审批。要用真实资金还需在设置页配置凭证、通过实盘检查并启用 Live。

### 手动买入、卖出和减仓

1. 在平台最近成交中点击买入或卖出，或直接填写该结果的 CLOB Token ID。
2. 读取当前买一卖一，设置限价和份额（不是 USDC 金额），再预览。
3. 创建待审核委托，检查账户模式、方向、Token、价格和数量后点击审核执行。
4. 持仓中点击卖出/平仓，可按当前买一与单笔金额上限填入减仓委托。大仓位分批处理；最小份额或两位小数以下的尘埃仓位可能无法卖出。
5. 卖出不允许裸卖。Paper 检查交易台持仓，Live 每次签名前读取条件代币余额并由交易所校验授权与实际可用份额。账户在其他地方操作后，可能出现余额变化导致拒单。

### 风控和退出

- 不利价格偏离按绝对价格差计算（0.02 = 2 美分），不是 2%。BUY 不高于设定限价，SELL 不低于设定限价；FOK 不能在限价内全部成交时拒绝。
- 可设置有效时间、盘口价差、限价内最小深度、同一事件合计投入、每日已实现净损失、已实现收益回撤。买入熔断后仍允许符合余额与单笔限额的卖出减仓。
- 事件归组优先使用 Gamma 返回的事件 ID，取不到事件信息时退回市场 condition ID，因此不能覆盖所有跨市场相关性。
- 「仅买入钱包已标记擅长板块」会把交易的推断板块与钱包资料中的标签精确匹配。当前推断类别为体育、政治、加密货币、经济、未分类。人工标签不是盈利认证。
- 止盈止损默认关闭；勾选后每轮间隔 5 秒检查本交易台持仓，网络请求耗时另计。按当前 bid 与持仓均价比较，自动模式下尝试卖出，半自动模式下创建卖出审核信号。每个 Token 有 60 秒冷却；盘口不足、跳空、断网或信号过期都会影响成交，止损不保证按触发价卖出。
- 手续费参数只用于模拟净收益估算，不改变交易所实际手续费。每日与统计日期按 UTC。
- 订单与审核绑定 Paper/Live 模式，切换模式后不能把旧队列直接执行到另一个账户。提交结果不明继续暂停新实盘委托，绝不自动重复下单。

### 数据与收益口径

平台成交通过官方 RTDS activity/trades 实时推送，默认每 2 秒用 REST 最近 100 笔补拉；持仓盘口使用 Market WebSocket，缓存超过 3 秒退回 CLOB REST。实时成交进入持久化去重后推送界面，并交给已监控钱包的策略；钱包轮询继续补拉。断网或极大流量下，REST 有限窗口不能保证补齐所有缺口。自动跟单只作用于已添加的钱包及启用的策略，不会自动跟随整个平台。

新交易台台账只统计经交易台执行且已确认成交价/数量的订单，采用 FIFO 配对。提供源价与成交价差、手续费估算、已实现毛盈亏、估算净盈亏、同份额源价格基准、持有时间及板块归因。手动委托以自设限价作为基准。没有成本依据的外部持仓卖出会单独标记，不能当作零成本盈利。

源钱包板块表只使用最近最多 10000 笔已监测样本，板块是标题关键词推断；不包含完整历史成本、链上赎回或完整钱包收益。未实现亏损和交易台外交易不纳入本版熔断，旧版本虚拟持仓不会自动迁入交易台。买入实际收益可能受资金占用、手续费、滑点、持有期和结算影响，工具不保证盈利。

对应 API：`/api/desk/state`、`/policy`、`/orders`、`/preview`、`/queue/{id}/approve`、`/queue/{id}/reject`、`/book/{token}`、`/platform-trades`、`/source-matrix`、`/account-positions`（均以 `/api/desk` 为前缀）。

已知订单号的待核对实盘委托，可在「执行记录」点击「请求撤单」。程序撤单后继续核对成交明细，只有交易所确认未成交才标记取消；已成交部分不会因撤单而消失。无订单号的提交超时仍需到交易所人工核对。


## 实时同步与模拟结算

交易台直接切换 PAPER / LIVE。默认 PAPER、半自动审核；LIVE 切换先做账户认证、资金和授权只读检查。两种模式独立记账，旧委托不能跨模式执行。

PAPER 默认初始虚拟资金 10000 USDC，首次模拟成交前可在交易台修改。买入扣除成交成本与估算费用，卖出收回虚拟资金；资金不足会拒单。模拟买卖按真实盘口深度测算 FOK 成交，不提交交易所订单。页面展示可用资金、持仓市值与浮动盈亏；缺失行情时显示未知。

默认每 15 秒同步持仓市场最终结果；Market WebSocket 的 market_resolved 通知也会触发查询。只有 CLOB 明确关闭且标记最终胜方，或 Gamma 明确 resolved 并给出支付比例，才按平台支付比例关闭模拟仓位、入账虚拟资金。输方按 0 结算，赢方按 1 结算；仅停止交易或价格接近 1 不会结算。结算持久化且幂等，重启不重复入账。切到 LIVE 后仍继续同步已有 PAPER 持仓结算，不动用真实资金。实盘链上赎回仍需在官方平台完成。

实时配置（YAML）：
```yaml
realtime:
  enabled: true
  rest_interval: 2
  settlement_interval: 15
```

实时成交历史保留最近约 10000 笔；界面显示最近 100 笔。交易台显示实时连接、最近成交、补拉和结果同步时间及错误。资金估值仅覆盖本工具持仓，手续费为估算。止盈止损需勾选启用并保存；半自动产生审批信号，自动模式尝试执行，不能保证成交或盈利。


## 发现交易员与快捷跟单（参考 CopyOdds 的公开功能）

侧栏新增「发现交易员 · 我的跟单」：官方日/周/月/全部时间榜单与本地观察钱包合并，可搜索用户名、备注、地址，按人工板块标签或已观察状态筛选。区间盈亏、成交量来自官方榜单；本地观测成交数另列，缺少完整成本时不推断胜率。榜单不可用时仍保留本地观察钱包。

选钱包后在右侧保存快捷规则：固定金额或按源交易份额比例、单笔金额上限、源成交最小金额、买入价格区间、是否跟随卖出。规则会自动加入钱包监控，只处理创建/恢复之后的新成交，沿用交易台全局仅观察/半自动/自动方式；创建规则不会切换账户或启用自动执行。规则限价为源成交价，盘口偏离时可能不成交；进一步定价可使用高级策略。

规则绑定创建时的 PAPER 或 LIVE；切换账户后另一模式规则等待，不会跨模式跟单。编辑/暂停会作废旧的待审核委托，已提交订单仍需查看执行记录。恢复后不追赶暂停期间的成交。规则的跟随卖出不超过该规则净买入份额和工具总可卖持仓；仍需盘口深度与全局风控通过。同模式资金、止盈止损和风险额度共享，持仓盈亏仍采用工具全局 FIFO，不是各规则独立资金账户。

已有高级策略仍会独立处理同一钱包。使用快捷规则前检查高级策略的目标钱包，避免重复买入；旧高级策略没有因这次升级被自动修改。钱包板块资料继续在「钱包监控」编辑。参考站的托管、聊天、邀请奖励与移动 App 不在此次实现范围。

设计预览：`docs/design/copy-workbench-preview.png`（示意数据）；实际页面以平台实时数据为准。


## 实盘配置与连接状态

交易台、跟单工作台、设置页使用同一实盘状态控件：配置实盘 → 保存凭证并检测 → 启动实盘；随时可切回模拟盘。配置表单支持只修改签名类型/Funder，秘密字段留空保留，保存后清空秘密输入框。

检测逐项展示凭证完整性、CLOB 网络、受认证的账户余额读取、抵押品余额和授权。只读检测不创建、不签署、不提交订单。绿色表示前置检查通过，不表示真实成交已被验证。未配置、检查异常、结果超过60秒、存在未核对实盘订单时不能启动。真正启动会重新检测；LIVE 时后台每30秒检查，页面每10秒读取状态。

凭证/代理变更会使旧检测失效。检测期间停止实盘或修改配置，旧结果不能重新启用账户。切回模拟盘会阻止尚未提交的已签名订单发送；已发往交易所的订单需核对。凭证不回显，SDK异常原文不返回页面。实盘实际成交仍受平台可用性、账户权限、余额、每市场授权及盘口限制。链上充值/授权和结算赎回在官方平台完成。

新增 API：GET `/api/executor/status`（读取检测状态），POST `/api/executor/check`（只读实盘检查），POST `/api/executor/mode`（重新检查后启动）。


## 工作台界面（2026-10-26）

默认进入「发现交易员」。左侧常用入口为发现交易员、我的跟单、市场与买卖、订单审核、持仓与结算、观察钱包；回测、调优、旧版台账等收进「分析与高级工具」，现有配置保留。

顶部始终显示当前模拟/实盘资金模式。点击模式状态打开账户检测侧栏；「配置实盘」直接进入凭证设置；启动实盘继续沿用原有检查与确认。跟单配置改为侧栏，点选交易员自动带入地址与名称，固定/比例只展示对应金额输入。

交易台按市场下单、审核、持仓、风控、归因拆分标签页。选成交可填入右侧委托，创建后进入审核页；从持仓点卖出会回到下单页。止盈止损在「风控设置」，交易执行机制未改变。审核角标只计尚未过期的待审核记录。

支持窄屏布局、键盘切换交易台标签、Escape关闭配置侧栏。直接入口：`/#copy-hub/discover`、`/#copy-hub/rules`、`/#desk/trade`、`/#desk/queue`、`/#desk/positions`、`/#desk/risk`。
