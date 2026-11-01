# 交接文档 — Polymarket 跟单机器人

> 给下一个接手的模型 / 未来的自己。
> 目标：**操作完善 + 自动执行**（用户 2026-10-25 原话：「完善跟单程序的操作以及自动执行，
> 具体跟单的对象到时候会重新设置调整，着重改良的点不要偏了」）

## 1. 总体目标（用户的原始诉求，优先级最高）

在 Polymarket 以 **30 USDC** 起步做「稳妥高胜率」跟单交易。

关键约束与历史结论：
- **过滤规则**：用户要求「只跟资金在一定范围内的订单」（双向上下限）。当前 `maxTradeUsd: 0`
  （不设上限）是因为跟单对象 ColdBlooded 是低频高信念型，大单往往是最强信号，设上限会砍掉最值钱的下注。
  **若换跟单对象需重新评估这一点。**
- **钱包**：充值地址（USDC on Base）`0x1279333686e1766Efc417acD55547E772EA7328`；
  **明确禁用** `0x6f555701754F44e1C015b32dB4aD1D406728DE7`（仅供 API 使用）。
- **预期管理已完成**：用户已接受「69% 已是稀缺水平，勿期待 80%+」。
- **用户核心痛点（本轮重点）**：此前我持续给参数表却不说明「在哪里填写」，
  且推荐的第三方平台设置页需登录、我无法预览或代操作。
  → 已转向**自托管方案**：所有功能做进本地网页控制台，用户双击 bat 即可自行操作、自行改参数，
  **不需要每次都来对话框问我**。

## 2. 项目位置与运行方式

`D:\Users\Administrator\Documents\deepseek-harness\default-workspace\.dsh-switchman\probe-npm\`

用户侧只需双击（按推荐顺序）：
| 文件 | 作用 |
|---|---|
| `1-检测网络.bat` | 只读探测，不下单 |
| `2-开始监控.bat` | 黑窗口 DRY-RUN 监控 |
| `3-打开控制台.bat` | 网页控制台 `http://127.0.0.1:8787`（日常用） |
| `4-运行测试.bat` | 43 个单元测试 |

命令行等价：`node scripts\run-all-tests.mjs`、`npm run dryrun`、`node src\console.mjs`

## 3. 已完成（可复用，别重做）

### 3.1 自动执行内核（本轮新增，全部实测通过）
- **`src/runtime.mjs`**：状态落盘 `logs\console-state.json`（seen 限 2000 条 / paper 限 200 条）；
  `hydrate()` 换跟单对象时自动清零 perMarket/totalInvested/paper/**当日亏损计数**；
  跨日自动重置 dailyLoss；`computeUnrealizedPnl()` live 用 `fetchPositions().cashPnl` 求和、
  dry-run 用纸面持仓查 `clob.polymarket.com/midpoint` 重估（最近 20 个）；
  `evaluateRisk()` 判定顺序：**熔断 → 止损 → 止盈**，返回 `none/halt-open/flatten`。
- **`src/scheduler.mjs`**：`nextDelaySeconds()` 失败 1 次不惩罚、2 次 2x、3 次 4x、4 次 8x、
  ≥5 次封顶 900s；`shouldAutoStop()` 连续 6 次失败自动停机；`nextRunAt()` 供前端倒计时。
- **`src/trader.mjs`**：新增 `flattenAll()`，低于 `minSellPrice`(0.02) 不贱卖，每笔独立 try/catch。
- **`src/guard.mjs`**：`PRIVATE_KEY_RE` 已改为 `/0[xX][0-9a-fA-F]{64}/g`（**大写 0X 之前漏了，已修**）；
  删掉 checkDailyLoss/recordDailyLoss 实体，统一交给 runtime.mjs（避免两套口径）。
- **`src/server.mjs`**：新增 `persistNow()`、`riskCheck()`、`detectHotChange()`、`unhaltBot()`、
  `resetDailyLoss()`；API 新增 `POST /api/unhalt`、`POST /api/reset-daily-loss`；
  `riskEvery(cfg)` 可由 `config.riskCheckEveryRounds` 覆盖默认 5；
  **halted 时拒绝启动并要求先 unhalt**（返回 `needUnhalt:true`）。
- **`config.json` 第 6 节重写**：新增 `maxDailyLossUsd:5`、`autoFlattenOnStop:false`、
  `minSellPrice:0.02`、`riskCheckEveryRounds:5`，每个字段带中文注释。
- **前端**：`public/index.html` 风控区新增 3 个输入框 + 解除停机按钮 + `#healthPanel`；
  `public/app.js` 新增 `renderHealth()/row()/pnlBar()/countdown()`，FIELDS 增 3 项；
  `public/style.css` 新增 `.hrow/.hpnl-bar/.savebar.tight/.row.check`。
- **测试**：`scripts/_test_guard.mjs`(18) + `_test_runtime.mjs`(11) + `_test_scheduler.mjs`(14)
  = **43 全绿**；`scripts/run-all-tests.mjs` 用**主进程 import** 而非 spawnSync
  （Windows 沙箱下 spawnSync(node) 报 EPERM，具名管道被禁）。
- **README 第 9 节**：三条线的区别、触发后做什么/不做什么、解除停机的设计理由、
  退避表、健康面板说明、断电保命说明、测试覆盖说明。文件树已同步。

### 3.2 之前已完成（详见 git / 早期 block）
- CopyOdds 代码学习 + stats / leaderboard / guard 三模块
- 网页控制台（参数/日志/跟单记录表/参数体检/测试网络）
- 交易员分析中心：排行榜、单人深度体检、参数推荐、板块过滤、关注名单
- **板块过滤**（三级粒度：板块 / 赛事 / 阶段，whitelist/blacklist/all，短于 2 字符拒绝保存）

## 4. 踩过的坑（务必避开）

1. **跨轮/跨模型后 read 记录失效**：`edit` 前必须重新 `read`，否则报
   `file has not been read — read the file, then retry`。本轮踩了 index.html 和 run-all-tests.mjs 两次。
2. **`/closed-positions` 按 realizedPnl 降序**：只读前几页会严重高估胜率
   （HomeRunHazard 只读前 100 页显示 100% 胜率 / 净赚 6336 万，实际人工核实仅 51%）。
   必须翻到底，`exhausted=true` 才参与合格评定。
3. **板块标签取数路径**：`/markets?condition_ids=X` 只有 seriesSlug，
   **必须再查 `/events?slug=Y`** 才拿得到 tags。
4. **测试断言写错比代码错更常见**：本轮 4 次 FAIL 全是我测试代码的问题
   （依赖磁盘真实状态、给 hydrate 传不存在的第三参、退避倍数记错一格、
   拿返回值当对象）。**改测试前先读被测源码。**
5. **纯按比例跟单在 30 本金下会全败**：单笔低于 $1 最小下单额，故用 fixed $3 + Max per order $3。
6. **不要用以太坊主网桥接**：gas 可能 $5-20，吃掉本金 17%-67%，须交易所直提 Base。

## 5. 待完成（按优先级）

1. **用户尚未充值 30 USDC** 到 Base 地址；到账后需协助确认。
2. **换跟单对象**：用户明说「到时候会重新设置调整」。
   现有数据：Smallcoins `0x482acc0c3c6268a16482953be000b2b46cccc85d`（79.0% / PF 5.76 / 1855 笔，
   翻到底）统计上优于 ColdBlooded（68.5% / PF 1.75 / 270 笔）。goldmyname 仅 191 单样本不足。
   **换人后必须重新评估是否该设 `maxTradeUsd` 上限**（撒网型高频玩家适合区间过滤）。
3. 可选：开机自启（当前只有启动/停止 bat，没做计划任务）；自动平仓实测（需 live 模式）。

## 6. 当前配置快照（config.json）

```json
mode: dry-run
targetWallet: 0xbcc321b6336117432f4e9d2d28b20d8b6795ac9c   ← ColdBlooded
sizingMode: fixed, fixedOrderSizeUsd: 3, percentScale: 0.02
minTradeUsd: 500, maxTradeUsd: 0
scopeMode: all, scopeKeywords: []
maxPerOrderUsd: 3, maxPerMarketUsd: 6, maxTotalInvestedUsd: 15
minMarketVolumeUsd: 50000
stopLossTotalUsd: -9, takeProfitTotalUsd: 15
maxDailyLossUsd: 5, autoFlattenOnStop: false, minSellPrice: 0.02, riskCheckEveryRounds: 5
pollIntervalSeconds: 60, orderType: FOK
funderAddress: 0x1279333686e1766Efc417acD55547E772EA7328
signatureType: 0, rpcUrl: https://mainnet.base.org
```

## 7. 环境注意事项

- Node v26.12.0 + npm 11.20.0 已装；git 2.56.0 已装。
- **本机直连 polymarket.com / data-api / gamma-api / clob.polymarket.com 超时**，
  必须走代理 `http://127.0.0.1:7897`（`HTTPS_PROXY` + `HTTP_PROXY` + `NODE_USE_ENV_PROXY=1`）。
  实测走代理后 `/api/probe` 两项全通（546ms / 658ms）。
- npm registry 可用，能装包。
- 技能目录已大幅缩减，**browser-skill / office-* / univer-* / evolve-* 都不可用了**，
  抓网页只能用 `read_page`（经 Firecrawl）或 web_search / advanced_search。
- 当前 Web GUI URL：`http://127.0.0.1:3456`（此前是 10603，还会变，用每次注入的最新值）。
- `polymarket_*` MCP 工具在本机全部 fetch failed，别依赖它们做实时数据。
