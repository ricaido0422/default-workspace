/**
 * 本地网页控制台 —— 让监控和调参都在浏览器里做，不用碰黑窗口。
 *
 * 设计要点：
 *   - 只监听 127.0.0.1，外网访问不到，不开任何端口转发。
 *   - privateKey 永远不下发给前端，前端只拿到 hasPrivateKey 布尔值。
 *   - 改配置走 validateConfig 校验，校验不通过就拒绝写入（宁可拒绝也不写坏配置）。
 *   - 机器人跑在同一个进程里，不额外开子进程，避免代理环境变量丢失。
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ROOT, loadConfig, saveConfig, validateConfig, log, usd, LOG_RING, onLog } from './util.mjs';
import { fetchRecentTrades, fetchPositions, fetchUsdcBalance } from './api.mjs';
import { evaluateTrade } from './filter.mjs';
import { evaluateScope, discoverScopes } from './scope.mjs';
import { auditTrader, recommendSettings } from './stats.mjs';
import { buildLeaderboard, loadWatchlist, saveToWatchlist, removeFromWatchlist } from './leaderboard.mjs';
import { assertSafeConfig, assertSameOrigin, reportGuardStatus } from './guard.mjs';
import {
  hydrate, saveRuntime, evaluateRisk, addPaper, recordDailyLoss, blankRisk,
} from './runtime.mjs';
import { nextDelaySeconds, shouldAutoStop, nextRunAt } from './scheduler.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, '..', 'public');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

// ---------------- 机器人运行时状态 ----------------
const bot = {
  running: false,
  autoStopped: false,   // 因为连续失败自动停的（区别于用户手动停）
  startedAt: null,
  round: 0,
  lastPollAt: null,
  lastPollMs: null,
  lastError: null,
  consecutiveFail: 0,   // 连续失败次数，驱动退避与自动停机
  nextRunAt: null,      // 下一轮的时间点（前端倒计时）
  // 统计
  seen: 0,
  followed: 0,
  skipped: 0,
  invested: 0,
  // 最近事件（给前端表格）
  events: [],
  // 轮询定时器句柄
  timer: null,
  abort: false,
  state: {
    seen: new Set(),
    perMarket: new Map(),
    totalInvested: 0,
  },
  paper: [],   // dry-run 的纸面持仓，用来让止损/止盈在假跑阶段就真能触发
  risk: blankRisk(),
  cfgCache: null,   // 本轮生效的配置快照，用于检测"运行中改了参数"
  cfgRev: 0,        // 配置变更次数
};

const EVENT_MAX = 200;

/** 最近一次 /api/balance 的查询结果，供前端模式开关展示前置条件。 */
let lastBalance = null;

function pushEvent(ev) {
  bot.events.unshift({ ts: new Date().toISOString().slice(11, 19), ...ev });
  if (bot.events.length > EVENT_MAX) bot.events.pop();
}

function resetStatistics() {
  bot.round = 0;
  bot.followed = 0;
  bot.skipped = 0;
  bot.invested = 0;
  bot.events = [];
  bot.lastError = null;
  bot.consecutiveFail = 0;
}

/** 每次 status 变动都落盘，避免崩溃/断电丢掉仓位账。 */
function persistNow(cfg) {
  saveRuntime(bot.state, bot.risk, cfg?.targetWallet ?? bot.cfgCache?.targetWallet ?? '',
              { paper: bot.paper });
}

/** 把配置和状态打包成前端能直接渲染的安全快照。 */
function snapshot() {
  let cfg;
  try {
    cfg = loadConfig();
  } catch (e) {
    cfg = null;
  }
  const safe = cfg
    ? { ...cfg, privateKey: undefined }
    : null;
  return {
    bot: {
      running: bot.running,
      autoStopped: bot.autoStopped,
      startedAt: bot.startedAt,
      round: bot.round,
      lastPollAt: bot.lastPollAt,
      lastPollMs: bot.lastPollMs,
      lastError: bot.lastError,
      consecutiveFail: bot.consecutiveFail,
      nextRunAt: bot.nextRunAt,
      seen: bot.seen,
      followed: bot.followed,
      skipped: bot.skipped,
      invested: bot.invested,
      trackedMarkets: bot.state.perMarket.size,
      // 风险账本：前端用它画浮亏进度条和停机原因
      risk: {
        pnl: bot.risk.lastPnlUsd,
        pnlSource: bot.risk.lastPnlSource,
        checkedAt: bot.risk.lastRiskCheckAt,
        halted: bot.risk.halted,
        haltReason: bot.risk.haltReason,
        dailyLoss: bot.risk.dailyLoss,
      },
      cfgRev: bot.cfgRev,
    },
    config: safe,
    hasPrivateKey: Boolean(cfg?.privateKey),
    // 最近一次余额查询结果（/api/balance 写进来），供模式开关显示前置条件
    balance: lastBalance,
    events: bot.events.slice(0, 60),
    logs: LOG_RING.slice(-120),
  };
}

// ---------------- 轮询核心 ----------------
const RISK_CHECK_EVERY_ROUNDS_DEFAULT = 5; // 默认每 5 轮查一次浮亏，别每轮都去问（API 有成本）
/** 风险检查频率：能被 config.riskCheckEveryRounds 覆盖（config.json 里可改）。 */
function riskEvery(cfg) {
  const n = Number(cfg?.riskCheckEveryRounds);
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : RISK_CHECK_EVERY_ROUNDS_DEFAULT;
}

/** 把 Set/Map 之外的东西也安全地喂给 evaluateTrade（它只需要这几个字段）。 */
async function riskCheck(cfg) {
  const verdict = await evaluateRisk(cfg, bot.risk, bot.paper);

  if (verdict.pnl !== null && Number.isFinite(verdict.pnl)) {
    log.info(`风险检查：当前浮盈亏 ${usd(verdict.pnl)}（${verdict.source}）`);
  } else if (verdict.queryFailed) {
    log.warn('风险检查：本轮查不到浮亏（API 抖动），沿用上次结果');
  }

  if (verdict.action === 'none') return { tripped: false };

  bot.risk.halted = true;
  bot.risk.haltReason = verdict.reason;
  bot.risk.haltedAt = new Date().toISOString();

  // autoFlattenOnStop 打开且是 live 模式，才真的去市价清仓
  if (verdict.action === 'flatten' && cfg.mode === 'live') {
    log.err(`${verdict.reason} → 正在市价清仓（autoFlattenOnStop 已开启）`);
    try {
      const trader = await import('./trader.mjs');
      const r = await trader.flattenAll();
      pushEvent({
        kind: 'error', side: '-', size: 0,
        title: '止损清仓', reason: `平掉 ${r.closed} 个，跳过 ${r.skipped} 个${r.errors?.length ? '；' + r.errors.slice(0, 3).join('；') : ''}`,
      });
    } catch (e) {
      log.err(`清仓失败：${e.message}（持仓仍在，请手动处理）`);
      pushEvent({ kind: 'error', title: '清仓失败', reason: e.message });
    }
  } else if (verdict.action === 'flatten') {
    log.warn(`${verdict.reason}（dry-run 模式，纸面持仓已冻结，无真实仓位需要清理）`);
  }

  bot.running = false;
  pushEvent({ kind: 'error', side: '-', size: 0,
              title: '风控停机', reason: verdict.reason });
  persistNow(cfg);
  return { tripped: true, reason: verdict.reason };
}

async function pollOnce() {
  const cfg = loadConfig();
  bot.round++;
  bot.lastPollAt = new Date().toISOString();
  const t0 = Date.now();

  const trades = await fetchRecentTrades(cfg.targetWallet, 100);
  const fresh = trades.filter(
    (t) => !bot.state.seen.has(String(t.id ?? t.transactionHash ?? t.timestamp + t.side)),
  );

  // 首轮只做"认领"：把历史成交全部标记为已见，绝不跟单。
  // 否则一启动就会把对方过去几十笔历史单全部复制一遍，这不是跟单、是追高。
  const firstRound = bot.round === 1;
  if (firstRound) {
    log.info(`首次扫描到 ${trades.length} 条历史成交，只记录不下单（避免一启动就把历史单全跟一遍）`);
  }

  for (const t of fresh) {
    const id = String(t.id ?? t.transactionHash ?? t.timestamp + t.side);
    bot.state.seen.add(id);
    bot.seen++;

    if (firstRound) {
      // 首轮仍然展示在表格里，但标明是历史单，方便用户核对过滤器
      pushEvent({
        kind: 'history',
        side: t.side,
        size: Number(t.size ?? 0),
        title: t.title ?? t.market ?? '(未知市场)',
        reason: '历史单，仅记录不跟单',
      });
      continue;
    }

    const verdict = await evaluateTrade(t, cfg, bot.state);
    const size = Number(t.size ?? 0);
    const title = t.title ?? t.market ?? '(未知市场)';

    if (!verdict.follow) {
      bot.skipped++;
      pushEvent({ kind: 'skip', side: t.side, size, title, reason: verdict.reason });
      continue;
    }

    if (cfg.mode === 'live') {
      // live 下单需要 trader.mjs（依赖 ethers）。没装依赖就明确报错，绝不静默跳过。
      try {
        const trader = await import('./trader.mjs');
        const r = await trader.placeOrder(t, verdict);
        if (r.ok) {
          bot.state.perMarket.set(t.conditionId, (bot.state.perMarket.get(t.conditionId) ?? 0) + verdict.amount);
          bot.state.totalInvested += verdict.amount;
          bot.invested += verdict.amount;
          bot.followed++;
          pushEvent({ kind: 'follow', side: t.side, size, amount: verdict.amount, title, reason: r.orderId });
          log.ok(`已跟单 ${usd(verdict.amount)} | ${title} | orderId=${r.orderId}`);
        } else {
          pushEvent({ kind: 'error', side: t.side, size, title, reason: r.error });
          log.err(`跟单失败 | ${title} | ${r.error}`);
        }
      } catch (e) {
        bot.lastError = `live 下单模块不可用：${e.message}（请先 npm install）`;
        log.err(bot.lastError);
        pushEvent({ kind: 'error', title, reason: bot.lastError });
      }
    } else {
      bot.state.perMarket.set(t.conditionId, (bot.state.perMarket.get(t.conditionId) ?? 0) + verdict.amount);
      bot.state.totalInvested += verdict.amount;
      bot.invested += verdict.amount;
      bot.followed++;
      addPaper(bot.paper, t, verdict.amount);
      pushEvent({ kind: 'follow', side: t.side, size, amount: verdict.amount, title, reason: 'DRY-RUN 模拟，未真实下单' });
      log.ok(`[模拟跟单] ${usd(verdict.amount)} ${t.side} @ ${t.price ?? '?'} | ${title}`);
    }
  }

  bot.lastPollMs = Date.now() - t0;
  persistNow(cfg);
}

function scheduleNext(seconds) {
  if (bot.abort) return;
  bot.timer = setTimeout(tick, seconds * 1000);
}

/**
 * 一次完整的 tick：轮询 → 风险检查 → 排下一次。
 *
 * 三处与之前的行为差异，都是为了让「自动执行」真的可信：
 *   1. 失败退避 + 连续失败自动停机（原来：固定间隔无限重试）
 *   2. 风险判定每 N 轮跑一次（原来：只在 index.mjs 打印一行，从不判定）
 *   3. 每次变更后立刻落盘（原来：只在内存，重启丢账）
 */
async function tick() {
  if (bot.abort || !bot.running) return;

  try {
    await pollOnce();
    if (bot.consecutiveFail > 0) {
      log.ok(`网络恢复，已连续失败 ${bot.consecutiveFail} 次`);
      bot.consecutiveFail = 0;
    }
    bot.lastError = null;
  } catch (err) {
    bot.consecutiveFail++;
    bot.lastError = err.message;
    log.err(`轮询失败（连续第 ${bot.consecutiveFail} 次）：${err.message}`);
  }

  // 连续失败到阈值就停机：一直失败却显示"运行中"是最危险的状态
  if (shouldAutoStop(bot.consecutiveFail)) {
    bot.running = false;
    bot.autoStopped = true;
    bot.abort = true;
    if (bot.timer) clearTimeout(bot.timer);
    bot.timer = null;
    log.err(`连续失败 ${bot.consecutiveFail} 次，已自动停机。检查网络/代理后手动点「启动」`);
    pushEvent({
      kind: 'error', side: '-', size: 0, title: '连续失败自动停机',
      reason: `${bot.consecutiveFail} 次轮询全部失败：${bot.lastError ?? '未知错误'}`,
    });
    return;
  }

  let cfg;
  try {
    cfg = loadConfig();
  } catch (e) {
    bot.lastError = `配置读取失败：${e.message}`;
    log.err(bot.lastError);
    scheduleNext(60);
    return;
  }

  // 运行时改了仓位/钱包相关参数就记一笔。
  detectHotChange(cfg);

  // 风险判定（止损/止盈/熔断）。halted 之后不再重复触发。
  // 注意：riskCheck 内部已经置 running=false 并落盘，这里只负责掐掉定时器。
  if (bot.round % riskEvery(cfg) === 0 && !bot.risk.halted) {
    try {
      const r = await riskCheck(cfg);
      if (r.tripped) {
        bot.abort = true;
        if (bot.timer) clearTimeout(bot.timer);
        bot.timer = null;
        log.err(`${r.reason} → 机器人已停机（仅停止开新仓，持仓未动）`);
        return;
      }
    } catch (e) {
      log.warn(`风险判定异常：${e.message}（不停机）`);
    }
  }

  const delay = nextDelaySeconds(bot.consecutiveFail, cfg.pollIntervalSeconds);
  bot.nextRunAt = nextRunAt(delay);
  if (bot.consecutiveFail > 0) {
    log.info(`退避等待 ${delay} 秒后重试（第 ${bot.consecutiveFail + 1} 次尝试）`);
  }
  scheduleNext(delay);
}

/**
 * 运行中如果改了 those affecting 仓位/钱包的字段，明确记一笔。
 * 不是禁止改 —— 收紧限额是正当需求 —— 而是让用户回头看日志时知道
 * "第 30 轮之后的参数和之前不一样"。
 */
function detectHotChange(cfg) {
  const prev = bot.cfgCache;
  const hot = [
    'targetWallet', 'maxPerOrderUsd', 'maxPerMarketUsd',
    'maxTotalInvestedUsd', 'mode', 'privateKey', 'funderAddress',
    'sizingMode', 'fixedOrderSizeUsd', 'percentScale',
  ];
  if (!prev) { bot.cfgCache = cfg; return; }
  const changed = hot.filter((k) => String(prev[k]) !== String(cfg[k]));
  if (changed.length) {
    bot.cfgRev++;
    log.info(`检测到参数变更（第 ${bot.cfgRev} 次）：${changed.join(', ')} → 下一轮立即生效`);
    pushEvent({
      kind: 'info', side: '-', size: 0, title: '参数已更新',
      reason: `${changed.join(', ')} 变更，已在下一轮生效（无需重启）`,
    });
    // 换了跟单对象：旧对象的仓位账作废，按新对象重开
    if (changed.includes('targetWallet')) {
      bot.state.perMarket = new Map();
      bot.state.totalInvested = 0;
      bot.paper = [];
      bot.invested = 0;
      log.warn('跟单对象已更换，旧仓位账与纸面持仓已清零');
    }
  }
  bot.cfgCache = cfg;
}

function startBot() {
  if (bot.running) return { ok: false, error: '已经在运行了' };
  let cfg;
  try {
    cfg = loadConfig(); // 配置有错会直接抛，前端能拿到提示
  } catch (e) {
    return { ok: false, error: `配置读取失败：${e.message}` };
  }

  // 安全护栏：这是启动真金白银之前的最后一道闸
  const safeErrors = assertSafeConfig(cfg);
  if (safeErrors.length) {
    return { ok: false, error: '安全检查未通过，拒绝启动', errors: safeErrors };
  }

  // 上一次是踩到止损/止盈停的话，重启必须先让原因被人看见一次，
  // 否则用户点了启动却发现什么都不跟，会以为是坏了。
  if (bot.risk.halted) {
    return {
      ok: false,
      error: `上次因【${bot.risk.haltReason}】停机。请先点「解除停机」确认你已了解原因，再启动。`,
      needUnhalt: true,
    };
  }

  // 每次启动都重新 hydrate：换过跟单对象的话会自动清零旧账
  const info = hydrate(bot.state, cfg.targetWallet);
  log.info(info.restored);
  bot.risk = info.risk;
  bot.paper = info.paper ?? [];

  bot.abort = false;
  bot.running = true;
  bot.autoStopped = false;
  bot.startedAt = new Date().toISOString();
  bot.cfgCache = cfg;
  resetStatistics();
  log.info(`控制台启动机器人 | 模式=${cfg.mode} | 每 ${cfg.pollIntervalSeconds} 秒轮询`);
  log.info(
    `自动守卫：止损 ${usd(cfg.stopLossTotalUsd)} / 止盈 ${usd(cfg.takeProfitTotalUsd)} / ` +
    `单日熔断 ${cfg.maxDailyLossUsd > 0 ? usd(cfg.maxDailyLossUsd) : '未启用'} / ` +
    `失败自动停机 ${'已启用'}`,
  );
  tick();
  return { ok: true, restored: info.restored };
}

/** 解除停机：把上次的风险停机原因记入事件流后清空，允许用户重新跑。 */
function unhaltBot() {
  if (!bot.risk.halted) return { ok: false, error: '当前不在停机状态' };
  const reason = bot.risk.haltReason;
  pushEvent({
    kind: 'info', side: '-', size: 0, title: '已解除停机',
    reason: `原因已确认：${reason}。请确认你已经处理过持仓，再点启动。`,
  });
  log.warn(`已解除停机（上次原因：${reason}）`);
  bot.risk.halted = false;
  bot.risk.haltReason = '';
  bot.risk.haltedAt = null;
  // 不清空 perMarket/seen —— 那是真金白银的账，解除停机不该把仓位账洗掉
  persistNow(loadConfig());
  return { ok: true, reason };
}

/** 重置当日亏损计数器（比如你手动平掉了亏损仓位，想让熔断器归零）。 */
function resetDailyLoss() {
  bot.risk.dailyLoss = { date: new Date().toISOString().slice(0, 10), amount: 0 };
  log.info('当日亏损计数已清零');
  persistNow(loadConfig());
  return { ok: true };
}

function stopBot() {
  if (!bot.running) return { ok: false, error: '没有在运行' };
  bot.running = false;
  bot.abort = true;
  if (bot.timer) clearTimeout(bot.timer);
  bot.timer = null;
  bot.startedAt = null;
  bot.nextRunAt = null;
  log.info('控制台停止机器人');
  const cfg = loadConfig();
  persistNow(cfg);
  return { ok: true };
}

// ---------------- HTTP 服务器 ----------------
function sendJson(res, code, body) {
  const buf = Buffer.from(JSON.stringify(body), 'utf8');
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': buf.length,
    'cache-control': 'no-store',
  });
  res.end(buf);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let n = 0;
    const chunks = [];
    req.on('data', (c) => {
      n += c.length;
      if (n > 1_000_000) { reject(new Error('请求体过大')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

/**
 * 合并配置：只接受已知字段，忽略未知字段，
 * 这样前端误传或被篡改都写不进脏数据。
 */
const ALLOWED_KEYS = [
  'mode', 'targetWallet', 'sizingMode', 'fixedOrderSizeUsd', 'percentScale',
  'minTradeUsd', 'maxTradeUsd', 'maxPerOrderUsd', 'maxPerMarketUsd',
  'maxTotalInvestedUsd', 'minMarketVolumeUsd', 'stopLossTotalUsd',
  'takeProfitTotalUsd', 'pollIntervalSeconds', 'orderType',
  'privateKey', 'funderAddress', 'rpcUrl', 'signatureType', 'verbose', 'logToFile',
  'scopeMode', 'scopeKeywords',
  'maxDailyLossUsd', 'autoFlattenOnStop', 'minSellPrice', 'riskCheckEveryRounds',
];
const ARRAY_KEYS = new Set(['scopeKeywords']);
const NUM_KEYS = new Set([
  'fixedOrderSizeUsd', 'percentScale', 'minTradeUsd', 'maxTradeUsd',
  'maxPerOrderUsd', 'maxPerMarketUsd', 'maxTotalInvestedUsd',
  'minMarketVolumeUsd', 'stopLossTotalUsd', 'takeProfitTotalUsd',
  'pollIntervalSeconds', 'signatureType', 'maxDailyLossUsd', 'minSellPrice',
  'riskCheckEveryRounds',
]);
const BOOL_KEYS = new Set(['verbose', 'logToFile', 'autoFlattenOnStop']);

/**
 * 落盘配置。保留原文件里下划线开头的注释字段，
 * 否则每次保存都会把用户自己写的中文说明冲掉。
 */
function persistConfig(next) {
  const rawFile = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
  const merged = { ...rawFile };
  for (const k of ALLOWED_KEYS) if (k in next) merged[k] = next[k];
  saveConfig(merged);
  return merged;
}

function mergeConfig(patch) {
  const current = loadConfig();
  const next = { ...current };
  for (const [k, v] of Object.entries(patch ?? {})) {
    if (!ALLOWED_KEYS.includes(k)) continue;
    if (k === 'privateKey') {
      // 前端发空串表示「不修改」，避免每次保存都把私钥清空
      if (typeof v === 'string' && v !== '') next.privateKey = v.trim();
      continue;
    }
    if (NUM_KEYS.has(k)) {
      const n = Number(v);
      if (!Number.isFinite(n)) throw new Error(`${k} 必须是数字，收到「${v}」`);
      next[k] = n;
      continue;
    }
    if (ARRAY_KEYS.has(k)) {
      // 允许前端传数组，也允许传 "esports, dota-2" 这种逗号串（手改 config 更常见）
      const arr = Array.isArray(v)
        ? v.map((x) => String(x ?? '').trim()).filter(Boolean)
        : String(v ?? '').split(/[,，]/).map((x) => x.trim()).filter(Boolean);
      next[k] = arr;
      continue;
    }
    if (BOOL_KEYS.has(k)) { next[k] = Boolean(v); continue; }
    next[k] = typeof v === 'string' ? v.trim() : v;
  }
  return next;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host ?? '127.0.0.1'}`);
  const p = url.pathname;

  try {
    // ---- API ----
    if (p === '/api/state' && req.method === 'GET') {
      return sendJson(res, 200, snapshot());
    }

    if (p === '/api/start' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const r = startBot();
      return sendJson(res, r.ok ? 200 : 400, r);
    }

    if (p === '/api/stop' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const r = stopBot();
      return sendJson(res, r.ok ? 200 : 400, r);
    }

    if (p === '/api/unhalt' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const r = unhaltBot();
      return sendJson(res, r.ok ? 200 : 400, r);
    }

    if (p === '/api/reset-daily-loss' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      return sendJson(res, 200, resetDailyLoss());
    }

    if (p === '/api/config' && req.method === 'GET') {
      const cfg = loadConfig();
      return sendJson(res, 200, { ...cfg, privateKey: undefined, hasPrivateKey: Boolean(cfg.privateKey) });
    }

    if (p === '/api/config' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const raw = await readBody(req);
      let patch;
      try {
        patch = JSON.parse(raw);
      } catch {
        return sendJson(res, 400, { ok: false, error: '提交的不是合法 JSON' });
      }
      let next;
      try {
        next = mergeConfig(patch);
      } catch (e) {
        return sendJson(res, 400, { ok: false, error: e.message });
      }
      const errors = [...validateConfig(next), ...assertSafeConfig(next)];
      if (errors.length) {
        return sendJson(res, 400, { ok: false, error: '配置校验未通过', errors });
      }
      persistConfig(next);
      bot.cfgCache = loadConfig(); // 立刻刷新缓存，避免下一轮误报"参数变更"
      log.info('控制台更新了配置');
      return sendJson(res, 200, { ok: true, message: '已保存，下一轮轮询自动生效（无需重启）。' });
    }

    // 运行模式专用端点：只改 mode 一个字段，其他参数一律不动。
    // 刻意不复用 /api/config：那个入口会把整个表单提交上来，
    // 模式开关只该改模式，不该顺手把别处正在编辑但还没保存的值也写进去。
    // 这里是切 LIVE 的最后一道硬闸门——网页上的前置条件清单只负责提醒，这里才真的拦住。
    if (p === '/api/mode' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const raw = await readBody(req);
      let patch;
      try {
        patch = JSON.parse(raw);
      } catch {
        return sendJson(res, 400, { ok: false, error: '提交的不是合法 JSON' });
      }
      const mode = patch?.mode;
      if (mode !== 'dry-run' && mode !== 'live') {
        return sendJson(res, 400, { ok: false, error: "mode 只能是 'dry-run' 或 'live'" });
      }
      const current = loadConfig();
      if (mode === 'live') {
        const errors = [];
        // 与网页上前置条件清单一一对应；这里才是真的拦住的那道闸门
        if (!/^0[xX][0-9a-fA-F]{64}$/.test(String(current.privateKey || '')))
          errors.push('还没填私钥：先到下方「钱包」填 0x 开头的 64 位十六进制私钥');
        if (!/^0x[0-9a-fA-F]{39,40}$/.test(String(current.funderAddress || '')))
          errors.push('充值地址格式不对：需要 0x 开头的 39~40 位十六进制（USDC on Base 地址）');
        const bal = await fetchUsdcBalance(current.rpcUrl, current.funderAddress).catch(() => null);
        if (bal === null)
          errors.push('查不到 USDC 余额：先点「测试网络」确认能连上，并确认地址已充值');
        else if (!(bal > 0))
          errors.push('钱包 USDC 余额为 0：先往 ' + current.funderAddress + ' 充值');
        if (errors.length) {
          lastBalance = { ok: false, error: errors[0] };
          return sendJson(res, 400, { ok: false, error: '还不能切到 LIVE', errors });
        }
        lastBalance = { ok: true, balance: bal };
      }
      const next = mergeConfig({ mode });
      const errors = [...validateConfig(next), ...assertSafeConfig(next)];
      if (errors.length) return sendJson(res, 400, { ok: false, error: '配置校验未通过', errors });
      persistConfig(next);
      bot.cfgCache = loadConfig();
      log.info(mode === 'live'
        ? '⚠️ 已切换到 LIVE 模式，之后将用真钱下单'
        : '已切回 DRY-RUN 模式，只观察不下单');
      return sendJson(res, 200, {
        ok: true,
        message: mode === 'live' ? '已切到 LIVE，正在用真钱下单。' : '已切回 DRY-RUN，从现在起只观察不下单。',
      });
    }

    if (p === '/api/probe' && req.method === 'GET') {
      // 连通性自检，前端「测试网络」按钮
      const cfg = loadConfig();
      const out = [];
      const t0 = Date.now();
      try {
        const trades = await fetchRecentTrades(cfg.targetWallet, 5);
        out.push({ name: 'data-api /trades', ok: true, ms: Date.now() - t0, note: `${trades.length} 条` });
      } catch (e) {
        out.push({ name: 'data-api /trades', ok: false, ms: Date.now() - t0, note: e.message });
      }
      const t1 = Date.now();
      try {
        await fetchPositions(cfg.targetWallet);
        out.push({ name: 'data-api /positions', ok: true, ms: Date.now() - t1, note: 'OK' });
      } catch (e) {
        out.push({ name: 'data-api /positions', ok: false, ms: Date.now() - t1, note: e.message });
      }
      return sendJson(res, 200, { ok: out.every((x) => x.ok), items: out });
    }

    if (p === '/api/balance' && req.method === 'GET') {
      const cfg = loadConfig();
      try {
        const bal = await fetchUsdcBalance(cfg.rpcUrl, cfg.funderAddress);
        lastBalance = { ok: true, balance: bal };
        return sendJson(res, 200, { ok: true, balance: bal });
      } catch (e) {
        lastBalance = { ok: false, error: e.message };
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    /** 板块发现：对方最近都在哪些板块下单。解决"不知道该填什么关键词"的问题。 */
    if (p === '/api/scopes' && req.method === 'GET') {
      const cfg = loadConfig();
      const limit = Math.min(Number(url.searchParams.get('limit') ?? 60) || 60, 100);
      try {
        const trades = await fetchRecentTrades(cfg.targetWallet, limit);
        const scopes = await discoverScopes(trades, null, limit);
        return sendJson(res, 200, { ok: true, trades: trades.length, scopes });
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    /** 板块体检：当前 板块模式+关键词 下，最近 N 笔会跟几笔、跳过原因是什么。
     *  与 /api/simulate 的区别：这里只跑板块这一层，用来单独调关键词，
     *  不受金额/仓位参数干扰（否则参数一变就分不清是板块挡的还是金额挡的）。 */
    if (p === '/api/scopes/simulate' && req.method === 'GET') {
      const cfg = loadConfig();
      const limit = Math.min(Number(url.searchParams.get('limit') ?? 40) || 40, 60);
      try {
        const trades = await fetchRecentTrades(cfg.targetWallet, limit);
        const rows = [];
        const reasons = new Map();
        let pass = 0;
        for (const t of trades) {
          // eslint-disable-next-line no-await-in-loop
          const r = await evaluateScope(t, { question: t.title }, cfg);
          if (r.ok) {
            pass++;
            rows.push({ pass: true, title: t.title, label: r.tagLabel ?? '', usd: Number(t.size ?? 0) });
          } else {
            rows.push({ pass: false, title: t.title, label: r.tagLabel ?? '(未知)', reason: r.reason, usd: Number(t.size ?? 0) });
            const key = r.reason.replace(/^[「『][^」』]*[」』]/, '板块').replace(/\d+/g, 'N');
            reasons.set(key, (reasons.get(key) ?? 0) + 1);
          }
        }
        return sendJson(res, 200, {
          ok: true,
          total: trades.length,
          pass,
          rows: rows.slice(0, 40),
          reasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([reason, count]) => ({ reason, count })),
        });
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    if (p === '/api/simulate' && req.method === 'GET') {
      // 参数体检：拿最近的真实成交跑一遍过滤器，告诉用户"当前参数能跟到几笔"
      const cfg = loadConfig();
      const trades = await fetchRecentTrades(cfg.targetWallet, 50);
      const probe = { seen: new Set(), perMarket: new Map(), totalInvested: 0 };
      const reasons = new Map();
      let pass = 0;
      let passAmount = 0;
      for (const t of trades.slice(0, 50)) {
        // eslint-disable-next-line no-await-in-loop
        const v = await evaluateTrade(t, cfg, probe);
        if (v.follow) {
          pass++;
          passAmount += v.amount;
          probe.perMarket.set(t.conditionId, (probe.perMarket.get(t.conditionId) ?? 0) + v.amount);
          probe.totalInvested += v.amount;
        } else {
          // 把具体数字抹掉，只留规则名，好归类统计
          const key = v.reason.replace(/\$[\d,.]+/g, '$X').replace(/\d+/g, 'N');
          reasons.set(key, (reasons.get(key) ?? 0) + 1);
        }
      }
      return sendJson(res, 200, {
        ok: true,
        total: trades.length,
        pass,
        passAmount,
        reasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([reason, count]) => ({ reason, count })),
      });
    }

    if (p === '/api/logs' && req.method === 'GET') {
      return sendJson(res, 200, { logs: LOG_RING.slice(-200) });
    }

    // ============ CopyOdds 对标功能 ============

    /** 交易员体检：用官方已平仓数据自己算战绩，不信任何平台标称值 */
    if (p === '/api/audit' && req.method === 'GET') {
      const addr = url.searchParams.get('address') || loadConfig().targetWallet;
      if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) {
        return sendJson(res, 400, { ok: false, error: '地址格式不对（需要 0x 开头 40 位）' });
      }
      // ⚠️ 必须翻到底（maxPages 给足）。/closed-positions 按盈利降序返回，
      //    只读前几页会把史上最差的交易员算成 100% 胜率 —— 我们自己文档里就是这么警告的。
      //    上限 100 页（5000 笔）足够正常量级的交易员翻到底
      //    （实测 Smallcoins 1855 / ColdBlooded 270 / ronaldk 403 全部 exhausted=true）；
      //    超量的人会被正确标为 exhausted=false 并沉底，不必真去翻完
      //    （HomeRunHazard 有 20000+ 条，翻完要 283 秒，不值得）。
      try {
        const r = await auditTrader(addr, { maxPages: 100 });
        return sendJson(res, 200, { ok: true, ...r });
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    /** 应用推荐：按本金 + 对方交易节奏，给出一套能直接用的一参数 */
    if (p === '/api/recommend' && req.method === 'GET') {
      const capital = Number(url.searchParams.get('capital') ?? 30);
      if (!Number.isFinite(capital) || capital <= 0) {
        return sendJson(res, 400, { ok: false, error: '本金必须是正数' });
      }
      try {
        const r = await recommendSettings(loadConfig().targetWallet, capital);
        if (!r) return sendJson(res, 200, { ok: false, error: '拉取不到对方的交易样本' });
        return sendJson(res, 200, { ok: true, ...r });
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    /** 一键套用推荐参数到 config.json */
    if (p === '/api/apply-recommend' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      let body = {};
      try { body = JSON.parse((await readBody(req)) || '{}'); } catch { /* 空 body 也允许 */ }
      const capital = Number(body.capital ?? 30);
      try {
        const rec = await recommendSettings(loadConfig().targetWallet, capital);
        if (!rec) return sendJson(res, 200, { ok: false, error: '拉取不到对方的交易样本' });
        const next = mergeConfig(rec.suggested);
        const errs = [...validateConfig(next), ...assertSafeConfig(next)];
        if (errs.length) return sendJson(res, 400, { ok: false, error: '推荐参数未通过校验', errors: errs });
        persistConfig(next);
        log.info(`已套用推荐参数：每笔 $${rec.suggested.fixedOrderSizeUsd}，区间 $${rec.suggested.minTradeUsd}~$${rec.suggested.maxTradeUsd}`);
        return sendJson(res, 200, { ok: true, applied: rec.suggested, rationale: rec.rationale });
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message });
      }
    }

    /** 排行榜：批量体检关注名单里的地址 */
    if (p === '/api/leaderboard' && req.method === 'GET') {
      try {
        const r = await buildLeaderboard({ limit: Number(url.searchParams.get('limit') ?? 8) });
        return sendJson(res, 200, r);
      } catch (e) {
        return sendJson(res, 200, { ok: false, error: e.message, rows: [] });
      }
    }

    /** 关注名单读写 */
    if (p === '/api/watchlist' && req.method === 'GET') {
      return sendJson(res, 200, { ok: true, traders: loadWatchlist() });
    }
    if (p === '/api/watchlist' && req.method === 'POST') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      let body;
      try { body = JSON.parse(await readBody(req)); } catch { return sendJson(res, 400, { ok: false, error: '不是合法 JSON' }); }
      try {
        const traders = saveToWatchlist(body.address, body.name ?? '');
        return sendJson(res, 200, { ok: true, traders });
      } catch (e) {
        return sendJson(res, 400, { ok: false, error: e.message });
      }
    }
    if (p === '/api/watchlist' && req.method === 'DELETE') {
      const same = assertSameOrigin(req);
      if (!same.ok) return sendJson(res, 403, { ok: false, error: same.reason });
      const addr = url.searchParams.get('address');
      if (!addr) return sendJson(res, 400, { ok: false, error: '缺少 address 参数' });
      const traders = removeFromWatchlist(addr);
      return sendJson(res, 200, { ok: true, traders });
    }

    // ---- 静态文件 ----
    let file = p === '/' ? 'index.html' : decodeURIComponent(p).replace(/^\/+/, '');
    // 防目录穿越
    const target = path.resolve(PUBLIC_DIR, file);
    if (!target.startsWith(PUBLIC_DIR)) {
      res.writeHead(403); return res.end('forbidden');
    }
    if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      return res.end('404');
    }
    const ext = path.extname(target).toLowerCase();
    const body = fs.readFileSync(target);
    res.writeHead(200, {
      'content-type': MIME[ext] ?? 'application/octet-stream',
      'content-length': body.length,
      'cache-control': 'no-cache',
    });
    return res.end(body);
  } catch (err) {
    log.err(`控制台请求出错：${err.message}`);
    return sendJson(res, 500, { ok: false, error: err.message });
  }
});

export function startServer(port = 8787) {
  reportGuardStatus();
  return new Promise((resolve) => {
    // 端口被占用就自动往后找，避免用户看到 EADDRINUSE 不知道怎么办
    let tryPort = port;
    const onError = (e) => {
      if (e.code === 'EADDRINUSE' && tryPort < port + 20) {
        tryPort++;
        server.listen(tryPort, '127.0.0.1');
      } else {
        log.err(`网页控制台启动失败：${e.message}`);
      }
    };
    server.once('error', onError);
    server.on('error', onError);
    server.listen(tryPort, '127.0.0.1', () => {
      server.removeListener('error', onError);
      resolve({ port: tryPort, url: `http://127.0.0.1:${tryPort}` });
    });
  });
}

export { bot };
