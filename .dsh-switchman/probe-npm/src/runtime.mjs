/**
 * 运行时状态 + 风险判定 —— 这是「自动执行」真正落地的地方。
 *
 * 之前的问题（本模块要解决的）：
 *   1. 控制台的 bot.state 只在内存里，进程一重启，perMarket / totalInvested 归零。
 *      → 仓位上限被绕过：重启 5 次就能在 30 本金上投出 75 刀。
 *   2. stopLossTotalUsd / takeProfitTotalUsd 只被打印出来，从未被判定。
 *   3. recordDailyLoss 定义了但全项目零调用，maxDailyLossUsd 都不在 config 里。
 *   4. 轮询失败没有退避，代理断了会按固定间隔无限撞墙。
 *
 * 设计原则：
 *   - 状态每次变更后立刻落盘，宁可多写一次文件也不能丢仓位账。
 *   - 风险判定只负责"判断要不要停"，具体停机动作由调用方执行。
 *   - dry-run 下没有真实持仓，用"纸面仓位"模拟同等语义的浮亏，
 *     这样止损逻辑在真金白银之前就能被验证一遍。
 */
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, log, usd } from './util.mjs';
import { fetchPositions } from './api.mjs';

const RUNTIME_FILE = path.join(ROOT, 'logs', 'console-state.json');

/** 空的风险账本。dailyLoss 记录"当天已实现亏损"，换日自动清零。 */
export function blankRisk() {
  return {
    realized: 0,
    dailyLoss: { date: todayStr(), amount: 0 },
    halted: false,
    haltReason: '',
    haltedAt: null,
    lastRiskCheckAt: null,
    lastPnlUsd: null,
    lastPnlSource: '',
  };
}

export function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function ensureDir() {
  fs.mkdirSync(path.dirname(RUNTIME_FILE), { recursive: true });
}

/** 读取上次运行的持仓/去重状态。读不到就返回空结构，绝不抛异常到调用方。 */
export function loadRuntime() {
  try {
    const raw = JSON.parse(fs.readFileSync(RUNTIME_FILE, 'utf8'));
    return {
      seen: Array.isArray(raw.seen) ? raw.seen : [],
      perMarket: Array.isArray(raw.perMarket) ? raw.perMarket : [],
      totalInvested: Number(raw.totalInvested ?? 0) || 0,
      paper: Array.isArray(raw.paper) ? raw.paper : [],
      targetWallet: raw.targetWallet ?? '',
      risk: { ...blankRisk(), ...(raw.risk ?? {}) },
      savedAt: raw.savedAt ?? null,
    };
  } catch {
    return {
      seen: [], perMarket: [], totalInvested: 0, paper: [],
      targetWallet: '', risk: blankRisk(), savedAt: null,
    };
  }
}

/** 落盘。ctx 传入 bot.state 与 risk 账本。 */
export function saveRuntime(botState, risk, targetWallet, extra = {}) {
  try {
    ensureDir();
    fs.writeFileSync(RUNTIME_FILE, JSON.stringify({
      savedAt: new Date().toISOString(),
      targetWallet,
      // seen 只保留最近 2000 条：够覆盖去重需求，又不至于让文件无限膨胀
      seen: [...(botState.seen ?? [])].slice(-2000),
      perMarket: [...(botState.perMarket ?? [])],
      totalInvested: Number(botState.totalInvested ?? 0) || 0,
      paper: (extra.paper ?? []).slice(-200),
      risk,
    }, null, 2));
  } catch (e) {
    // 落盘失败必须让用户知道：这意味着重启后可能重复跟单
    log.err(`运行时状态保存失败：${e.message}（重启后可能重复跟单！）`);
  }
}

/**
 * 启动时恢复状态。
 * 关键：如果换了跟单对象，旧的 perMarket / totalInvested / paper 必须清零 ——
 * 那是上一个人的仓位账，照着算会让新对象一上来就被"仓位已满"卡死，
 * 或者反过来让上限失效。
 */
export function hydrate(botState, targetWallet) {
  const saved = loadRuntime();
  botState.seen = new Set(saved.seen);
  const sameTarget =
    saved.targetWallet && String(saved.targetWallet).toLowerCase() === String(targetWallet).toLowerCase();

  if (sameTarget) {
    botState.perMarket = new Map(saved.perMarket);
    botState.totalInvested = saved.totalInvested;
  } else {
    botState.perMarket = new Map();
    botState.totalInvested = 0;
    if (saved.targetWallet) {
      log.warn(
        `跟单对象已从 ${saved.targetWallet} 换成 ${targetWallet}，` +
        '旧对象的仓位账已清零，纸面持仓一并作废',
      );
    }
    // 熔断计数也是上一个人的账：新对象的第一天不该背着别人的亏损被锁死
    saved.risk = { ...(saved.risk ?? blankRisk()), dailyLoss: { date: todayStr(), amount: 0 } };
  }

  // 跨日重置当日亏损计数
  const risk = saved.risk ?? blankRisk();
  if (risk.dailyLoss?.date !== todayStr()) {
    risk.dailyLoss = { date: todayStr(), amount: 0 };
  }

  return {
    restored: saved.savedAt
      ? `已恢复上次运行：${botState.perMarket.size} 个市场在册，累计投入 ${usd(botState.totalInvested)}`
      : '首次运行，无历史持仓',
    targetChanged: Boolean(saved.targetWallet) && !sameTarget,
    paper: sameTarget ? (saved.paper ?? []) : [],
    risk,
  };
}

/** 记录一笔已实现亏损到当日熔断计数器。只在真的亏了钱时才调用。 */
export function recordDailyLoss(risk, amountUsd) {
  const loss = Math.abs(Number(amountUsd ?? 0));
  if (!(loss > 0)) return risk.dailyLoss;
  if (risk.dailyLoss?.date !== todayStr()) {
    risk.dailyLoss = { date: todayStr(), amount: 0 };
  }
  risk.dailyLoss.amount += loss;
  return risk.dailyLoss;
}

/** 单日亏损熔断判定。 */
export function checkDailyLoss(cfg, risk) {
  const limit = Number(cfg.maxDailyLossUsd ?? 0);
  if (!(limit > 0)) return { tripped: false };
  const counter = risk?.dailyLoss;
  if (!counter || counter.date !== todayStr()) return { tripped: false };
  if (counter.amount >= limit) {
    return {
      tripped: true,
      reason: `今日累计亏损 ${usd(counter.amount)}，触及熔断线 ${usd(limit)}`,
    };
  }
  return { tripped: false };
}

/**
 * 计算当前浮亏。
 *
 * live：读我们自己钱包的真实持仓，cashPnl 之和就是未实现盈亏。
 * dry-run：我们没有真实持仓，改用纸面仓位按当前价重估 ——
 *          这样止损/止盈在真钱进场之前就能被实际跑通一次。
 */
export async function computeUnrealizedPnl(cfg, paper) {
  if (cfg.mode === 'live' && cfg.funderAddress) {
    const positions = await fetchPositions(cfg.funderAddress);
    const pnl = positions.reduce((s, p) => s + Number(p.cashPnl ?? 0), 0);
    return { pnl, source: 'live 真实持仓', positions };
  }

  if (!paper?.length) return { pnl: 0, source: 'dry-run 无纸面持仓', positions: [] };

  // 纸面重估：拿每个持仓的 token 去问当前中间价。
  // 只查最近 20 个，避免刷爆 gamma/clob —— 我们本来也不允许仓位超过 maxTotalInvestedUsd。
  const recent = paper.slice(-20);
  let pnl = 0;
  let resolved = 0;
  for (const pos of recent) {
    try {
      const url =
        `https://clob.polymarket.com/midpoint?token_id=${encodeURIComponent(pos.tokenId)}`;
      const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
      if (!res.ok) continue;
      const j = await res.json();
      const mid = Number(j.mid ?? j.midpoint ?? NaN);
      if (!Number.isFinite(mid)) continue;
      resolved++;
      const shares = pos.amount / (pos.price || 1);
      pnl += pos.side === 'SELL'
        ? shares * (pos.price - mid)
        : shares * (mid - pos.price);
    } catch { /* 单个查价失败不影响整体，按 0 贡献跳过 */ }
  }
  return {
    pnl,
    source: `dry-run 纸面重估（${resolved}/${recent.length} 个取到价）`,
    positions: [],
  };
}

/**
 * 风险判定总入口。返回动作建议，由调用方执行。
 *
 * action:
 *   'none'      继续跑
 *   'halt-open' 停止开新仓（止损/止盈/熔断达成，但不清仓）
 *   'flatten'   停止并清仓（只有用户显式开了 autoFlattenOnStop 才会走到这里）
 */
export async function evaluateRisk(cfg, risk, paper) {
  const trip = checkDailyLoss(cfg, risk);
  if (trip.tripped) {
    return { action: 'halt-open', reason: `【单日熔断】${trip.reason}`, pnl: null, source: '熔断' };
  }

  let pnl = null;
  let source = '';
  try {
    const r = await computeUnrealizedPnl(cfg, paper);
    pnl = r.pnl;
    source = r.source;
  } catch (e) {
    // 查不到浮亏不能假装没事 —— 但也不能因此停机（可能只是 API 抖动）。
    // 这里返回 null，让调用方保留上一次的值，并在界面上标注"本次查价失败"。
    log.warn(`浮亏查询失败：${e.message}（沿用上次数值，不停机）`);
    return { action: 'none', pnl: null, source: '查询失败', queryFailed: true };
  }

  if (pnl === null || !Number.isFinite(pnl)) {
    return { action: 'none', pnl: null, source };
  }
  risk.lastPnlUsd = pnl;
  risk.lastPnlSource = source;
  risk.lastRiskCheckAt = new Date().toISOString();

  const stop = Number(cfg.stopLossTotalUsd ?? 0);   // 负数，如 -9
  const take = Number(cfg.takeProfitTotalUsd ?? 0); // 正数，如 15

  if (stop < 0 && pnl <= stop) {
    return {
      action: cfg.autoFlattenOnStop ? 'flatten' : 'halt-open',
      reason: `【止损】当前浮亏 ${usd(pnl)}，已达止损线 ${usd(stop)}`,
      pnl, source,
    };
  }
  if (take > 0 && pnl >= take) {
    return {
      action: cfg.autoFlattenOnStop ? 'flatten' : 'halt-open',
      reason: `【止盈】当前浮盈 ${usd(pnl)}，已达止盈线 ${usd(take)}`,
      pnl, source,
    };
  }
  return { action: 'none', pnl, source };
}

/** 纸面持仓入账（dry-run 用；live 由 chain 记账，这里只做镜像留痕）。 */
export function addPaper(paper, trade, amount) {
  const price = Number(trade.price ?? 0);
  if (!(price > 0)) return;
  paper.push({
    conditionId: trade.conditionId,
    tokenId: String(trade.asset ?? trade.tokenId ?? ''),
    side: trade.side,
    price,
    amount,
    ts: new Date().toISOString(),
  });
}

export { RUNTIME_FILE };
