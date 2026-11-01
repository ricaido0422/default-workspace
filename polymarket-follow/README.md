# Polymarket 跟单与监测工具

纯 Python 标准库实现的 Polymarket 跟单（copy-trading）与平台市场监测程序。

## 特性

- **纯标准库**：仅使用 Python 内置模块（urllib/json/threading/signal/argparse 等），零必选依赖
- **可选 WebSocket**：安装 `websocket-client` 后可启用实时行情推送，未安装时自动降级为纯轮询
- **代理支持**：本机直连 Polymarket 不通时，通过 HTTP 代理访问
- **跟单追踪**：监控目标钱包的持仓变化（新增/加仓/减仓/平仓）和新交易
- **市场监测**：监控市场价格变动、价差、流动性、未平仓量和解析状态
- **告警去重**：基于时间窗口的告警去重，支持控制台彩色输出和多平台 webhook 推送
- **状态持久化**：JSON 文件原子写入，重启后恢复上次快照
- **Webhook 推送**：支持 Telegram / Discord / 飞书(Lark) / 通用 JSON POST 四种告警推送
- **WebSocket 实时**：安装 websocket-client 后秒级实时行情推送，替代 30s 轮询
- **真实下单**：可选跟单执行器，收到信号后自动下单复制大户操作，含风控和 dry-run 模式

## 快速开始

### 环境要求

- Python 3.8+
- 本机直连 Polymarket 不通时需要 HTTP 代理（默认 `http://127.0.0.1:7897`）

### 安装

```bash
# 无需安装必选依赖
# 可选：安装 WebSocket 支持
pip install -r requirements.txt
```

### 配置

编辑 `config.json`：

```json
{
  "proxy": "http://127.0.0.1:7897",
  "follow": {
    "wallets": [],
    "from_leaderboard": { "enabled": true, "window": "all", "limit": 5 }
  },
  "monitor": {
    "enabled": true,
    "from_top_volume": { "enabled": true, "limit": 10 }
  }
}
```

### 使用

```bash
# 自检：测试三个 API 接口连通性
python main.py selfcheck

# 从排行榜取 top 5 钱包写入配置
python main.py bootstrap -n 5

# 同时跑跟单 + 监测（前台循环，Ctrl-C 退出）
python main.py run

# 只跑跟单
python main.py follow

# 只跑监测
python main.py monitor

# 各跑一个 cycle 后退出（适合 cron）
python main.py once

# 跟单 + 真实下单执行（需配置 executor）
python main.py execute

# OKX Wallet 配置向导
python main.py setup

# 查看自己的钱包状态
python main.py mywallet
```

## Webhook 告警推送

在 `config.json` 的 `alerts` 段配置：

### Telegram

```json
"alerts": {
  "console": true,
  "webhook_type": "telegram",
  "webhook_token": "你的Bot Token",
  "webhook_chat_id": "你的Chat ID"
}
```

### Discord

```json
"alerts": {
  "console": true,
  "webhook_type": "discord",
  "webhook_url": "https://discord.com/api/webhooks/xxx/yyy"
}
```

### 飞书 (Lark)

```json
"alerts": {
  "console": true,
  "webhook_type": "lark",
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
}
```

## WebSocket 实时行情

```bash
pip install websocket-client
```

在 `config.json` 中启用：

```json
"monitor": {
  "use_websocket": true,
  "ws_ping_interval": 10
}
```

启用后监测从 30s 轮询降为秒级实时推送，自动重连（最多 3 次指数退避）。

## 真实下单执行器

> ⚠️ **涉及真实资金**，请充分理解风险后使用。

### 安装 SDK

```bash
pip install py-clob-client
```

### 配置

在 `config.json` 中添加 `executor` 段：

```json
"executor": {
  "enabled": true,
  "dry_run": true,
  "private_key": "你的私钥",
  "wallet_address": "你的代理钱包地址",
  "signature_type": 2,
  "max_order_usd": 50,
  "max_daily_usd": 500,
  "follow_ratio": 0.1
}
```

### 安全机制

- **dry_run 默认开启**：只打印不下真单，确认无误后改为 `false`
- **enabled 默认关闭**：必须显式启用
- **单笔/每日限额**：超限自动拒绝
- **价格区间过滤**：低于 min_price 或高于 max_price 不买入
- **跟单比例**：按 `follow_ratio` 缩放大户操作规模（默认 10%）
- **私钥不打印**：日志中永不输出私钥

### 运行

```bash
# dry-run 模式（安全验证）
python main.py execute

# 确认无误后，改 dry_run=false 再运行
python main.py execute
```

## OKX Wallet 对接指南

### 背景

OKX Wallet 浏览器插件登录 Polymarket 的链路：

```
OKX Wallet (EOA 签名) → Polymarket 创建 Gnosis Safe 代理钱包 → API 交易
```

- **EOA 私钥**：OKX Wallet 里的账户私钥，用于签名
- **代理钱包地址**：Polymarket 为你创建的 Gnosis Safe 地址，持有持仓和资金
- **signature_type = 2**：Gnosis Safe 类型（已设为默认）
- **L2 API 凭据**：无需手动获取，程序从私钥自动派生

### 快速配置

```bash
# 方式一：交互式向导（推荐）
python main.py setup
# → 按提示粘贴私钥和钱包地址
# → 自动派生 L2 凭据 + 测试连接 + 写入 config.json

# 方式二：非交互模式
python main.py setup --private-key 0x... --wallet 0x...
```

### 获取私钥和钱包地址

1. **导出 OKX Wallet 私钥**：
   - 打开 OKX Wallet 浏览器插件
   - 点击设置 → 选择账户 → 导出私钥
   - 输入密码确认 → 复制私钥（0x 开头 64 位 hex）

2. **获取 Polymarket 代理钱包地址**：
   - 打开 [polymarket.com](https://polymarket.com) 并用 OKX Wallet 登录
   - 点击右上角头像 → Profile
   - 复制钱包地址（0x 开头）

### 查看自己的钱包状态

```bash
# 查看你的持仓、交易、PnL
python main.py mywallet

# 指定其他钱包
python main.py mywallet --wallet 0x...
```

### 安全提示

- ⚠️ 私钥仅存储在本地 `config.json`，**切勿提交到 git**
- 建议在 `.gitignore` 中添加 `config.json`
- `setup` 命令会自动设置 `dry_run=true`，确认无误后再改为 `false`
- 程序日志永不打印完整私钥，只显示前 6 + 后 4 字符

## 项目结构

```
polymarket-follow/
├── main.py              # CLI 入口
├── config.json          # 配置文件
├── pmfollow/
│   ├── config.py        # 配置加载
│   ├── httpclient.py    # HTTP 客户端（代理/限流/重试）
│   ├── models.py        # 数据模型与解析
│   ├── dataapi.py       # Data API v2 客户端
│   ├── gamma.py         # Gamma API 客户端
│   ├── clob.py          # CLOB API 客户端
│   ├── store.py         # 状态持久化
│   ├── alert.py         # 告警（控制台/webhook/去重）
│   ├── tracker.py       # 跟单核心（持仓/交易 diff）
│   ├── monitor.py       # 市场监测
│   ├── executor.py      # 真实下单执行器（可选，需 py-clob-client）
│   └── ws.py            # 可选 WebSocket 实时行情
└── tests/
    ├── run_tests.py     # 测试启动器
    ├── test_parse.py    # 解析测试
    ├── test_diff.py     # diff 逻辑测试
    ├── test_gamma.py    # Gamma 解析测试
    └── fixtures/        # 离线样本数据
```

## API 说明

程序访问三个 Polymarket API（均无鉴权）：

| API | Base URL | 用途 |
|-----|----------|------|
| Data API v2 | `https://data-api.polymarket.com` | 持仓、交易、排行榜、持有者、OI |
| Gamma | `https://gamma-api.polymarket.com` | 事件、市场元信息、搜索 |
| CLOB | `https://clob.polymarket.com` | 订单簿、价格、价差 |

## 测试

```bash
python tests/run_tests.py
```

## 限流

各 API 有不同的限流策略，程序默认每请求最小间隔 0.1s，失败时指数退避重试 3 次。

| 接口 | 限流 |
|------|------|
| Data API /positions | 150/10s |
| Data API /trades | 200/10s |
| Gamma /events | 500/10s |
| CLOB /book | 1500/10s |
| CLOB /price | 1500/10s |
