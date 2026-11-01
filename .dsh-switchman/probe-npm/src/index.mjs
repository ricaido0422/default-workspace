/**
 * 主循环：轮询目标地址的新成交 → 过滤 → (dry-run 打印 / live 下单)
 *
 * ⚠️ 这是**旧入口**（黑窗口 `2-开始监控.bat`），保留它只是为了在没有浏览器时兜底。
 * 自动执行三件套——失败退避、单日熔断、状态落盘到 console-state.json——
 * 都不在这条线上，只有 `3-打开控制台.bat`（src/console.mjs → server.mjs）才有。
 * 它自己的止损检查也只是每 30 轮打一行参考值，不会真停机。
 */
import fs from 'node:fs';
import path from 'node:path';
import { loadConfig, log, usd, shortAddr, sleep, initLogger } from './util.mjs';
import { fetchRecentTrades, fetchPositions, fetchUsdcBalance } from './api.mjs';
import { evaluateTrade } from './filter.mjs';
import { ensureProxy } from './proxy.mjs';
import { ROOT } from './util.mjs';

// Node.js 默认不走 Windows 系统代理，而「梯子」是系统级的（浏览器能用、Node 用不了）。
// 必须先确保走代理，否则所有请求都会超时。
// 如果检测到代理，这里会自动用带代理的新进程重启本程序，然后退出当前进程。
await ensureProxy(log);

const cfg = loadConfig();
initLogger(cfg);

const STATE_FILE = path.join(ROOT, 'logs', 'state.json');
const state = {
  seen: new Set(),           // 已处理过的成交 id，防重复下单
  perMarket: new Map(),      // 每个市场已投入
  totalInvested: 0,          // 总投入
  realized: 0,               // 已实现盈亏
  halted: false,             // 是否因止损/止盈停机
  haltReason: '',
};

// 恢复上次运行的状态（重启不重复跟单）
try {
  const saved = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  state.seen = new Set(saved.seen ?? []);
  state.perMarket = new Map(saved.perMarket ?? []);
  state.totalInvested = saved.totalInvested ?? 0;
  state.realized = saved.realized ?? 0;
  state.halted = saved.halted ?? false;
  state.haltReason = saved.haltReason ?? '';
  log.ok(`已恢复上次状态：见 ${Object.keys(state.perMarket).length} 个市场，总投入 ${usd(state.totalInvested)}`);
} catch {
  log.info('首次运行，无历史状态');
}

function saveState() {
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify({
    seen: [...state.seen].slice(-500),
    perMarket: [...state.perMarket],
    totalInvested: state.totalInvested,
    realized: state.realized,
    halted: state.halted,
    haltReason: state.haltReason,
  }, null, 2));
}

const MODE_LABEL = cfg.mode === 'live'
  ? '\x1b[41m LIVE 真实下单 \x1b[0m'
  : '\x1b[42m DRY-RUN 只观察不下单 \x1b[0m';

async function printBanner() {
  log.info('========================================');
  log.info(`Polymarket 跟单机器人启动  ${MODE_LABEL}`);
  log.info(`跟单对象：${shortAddr(cfg.targetWallet)}`);
  log.info(`下单模式：${cfg.sizingMode}${cfg.sizingMode === 'fixed' ? ` 每笔 ${usd(cfg.fixedOrderSizeUsd)}` : ` 比例 ${(cfg.percentScale * 100).toFixed(1)}%`}`);
  log.info(`资金区间：下限 ${usd(cfg.minTradeUsd)}${cfg.maxTradeUsd ? ` / 上限 ${usd(cfg.maxTradeUsd)}` : ' / 不设上限'}`);
  log.info(`仓位上限：单市场 ${usd(cfg.maxPerMarketUsd)} / 总计 ${usd(cfg.maxTotalInvestedUsd)} / 单笔 ${usd(cfg.maxPerOrderUsd)}`);
  log.info(`止损 ${usd(cfg.stopLossTotalUsd)} / 止盈 ${usd(cfg.takeProfitTotalUsd)}`);
  if (cfg.mode === 'live') {
    try {
      const bal = await fetchUsdcBalance(cfg.rpcUrl, cfg.funderAddress);
      log.info(`钱包 USDC 余额：${usd(bal)}`);
      if (bal < 10) log.warn('余额不足 $10，live 下单很可能因资金不足失败');
    } catch (e) {
      log.warn(`读不到钱包余额（${e.message}），不影响跟单，请自行确认充值到账`);
    }
  }
  log.info('========================================');
}

let trader = null; // live 模式才加载下单模块

async function handleTrade(trade) {
  const id = String(trade.id ?? trade.transactionHash ?? trade.timestamp + trade.side);
  if (state.seen.has(id)) return;
  state.seen.add(id);

  const verdict = await evaluateTrade(trade, cfg, state);

  if (!verdict.follow) {
    if (cfg.verbose) {
      log.dim(`跳过 | ${usd(Number(trade.size ?? 0))} ${trade.side} | ${trade.title ?? trade.market ?? ''} | ${verdict.reason}`);
    }
    return;
  }

  const q = verdict.market.question ?? '(未知市场)';
  if (cfg.mode === 'live') {
    const result = await trader.placeOrder(trade, verdict);
    if (result.ok) {
      state.perMarket.set(trade.conditionId, (state.perMarket.get(trade.conditionId) ?? 0) + verdict.amount);
      state.totalInvested += verdict.amount;
      log.ok(`已跟单 | ${usd(verdict.amount)} | ${trade.side} | ${q} | orderId=${result.orderId}`);
    } else {
      log.err(`跟单失败 | ${q} | ${result.error}`);
    }
  } else {
    log.ok(`[模拟跟单] 将下 ${usd(verdict.amount)} ${trade.side} @ ${trade.price ?? '?'} | ${q}`);
    log.dim(`          对方实际成交 ${usd(Number(trade.size ?? 0))}，本笔理由：${verdict.reason}`);
  }
}

async function checkStop() {
  // dry-run 下只能根据对方持仓估算，live 下可以查自己真实持仓
  try {
    const positions = await fetchPositions(cfg.targetWallet);
    const pnl = positions.reduce((s, p) => s + Number(p.cashPnl ?? 0), 0);
    log.info(`对方当前总盈亏：${usd(pnl)}（参考）`);
  } catch { /* 忽略，非关键 */ }
}

async function loop() {
  await printBanner();
  if (state.halted) {
    log.warn(`当前处于停机状态（${state.haltReason}）。想恢复就编辑 config.json 或删掉 logs/state.json 后重启。`);
    return;
  }

  let round = 0;
  for (;;) {
    round++;
    try {
      const trades = await fetchRecentTrades(cfg.targetWallet, 100);
      const fresh = trades.filter((t) => !state.seen.has(String(t.id ?? t.transactionHash ?? t.timestamp + t.side)));

      if (round === 1) {
        log.info(`首次扫描到 ${trades.length} 条历史成交，只记录不下单（避免一启动就把历史单全跟一遍）`);
        trades.forEach((t) => state.seen.add(String(t.id ?? t.transactionHash ?? t.timestamp + t.side)));
        saveState();
      } else if (fresh.length) {
        log.info(`发现 ${fresh.length} 条新成交`);
        for (const t of fresh.reverse()) await handleTrade(t);
        saveState();
      } else {
        log.dim(`本轮无新成交（第 ${round} 轮）`);
      }

      if (round % 30 === 0) { await checkStop(); saveState(); }
    } catch (err) {
      log.err(`轮询失败：${err.message}（${cfg.pollIntervalSeconds} 秒后重试）`);
    }
    await sleep(cfg.pollIntervalSeconds * 1000);
  }
}

if (cfg.mode === 'live') {
  try {
    trader = await import('./trader.mjs');
  } catch (e) {
    log.err('live 模式需要下单模块，但 trader.mjs 加载失败：' + e.message);
    log.err('请先确认已运行 npm install 安装依赖。');
    process.exit(1);
  }
}

process.on('SIGINT', () => {
  saveState();
  log.info('已停止，状态已保存。按 Ctrl+C 可随时安全退出。');
  process.exit(0);
});

loop();
