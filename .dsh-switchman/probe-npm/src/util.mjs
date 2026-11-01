/**
 * 公共工具：日志、金额格式化、HTTP 请求（带重试）
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, '..');

const COLORS = {
  info: '\x1b[36m',
  ok: '\x1b[32m',
  warn: '\x1b[33m',
  err: '\x1b[31m',
  dim: '\x1b[90m',
  reset: '\x1b[0m',
};

let logStream = null;

/** 环形日志缓冲：网页控制台靠它读取最近的日志，不落盘、只在内存里。 */
export const LOG_RING = [];
const LOG_RING_MAX = 500;
export const logListeners = new Set();

/** 网页控制台可以注册一个回调，实时接收每一行日志。 */
export function onLog(fn) {
  logListeners.add(fn);
  return () => logListeners.delete(fn);
}

export function initLogger(cfg) {
  if (!cfg.logToFile) return;
  const dir = path.join(ROOT, 'logs');
  fs.mkdirSync(dir, { recursive: true });
  const day = new Date().toISOString().slice(0, 10);
  logStream = fs.createWriteStream(path.join(dir, `${day}.log`), { flags: 'a' });
}

function write(level, msg, extra) {
  const ts = new Date().toISOString().slice(11, 19);
  // 私钥打码必须在落盘和推给网页之前，任何一条日志都不能带着明文私钥离开这个进程
  const safeMsg = maskSecretInline(msg);
  const safeExtra = extra === undefined ? undefined : maskSecretInline(extra);
  const line = `[${ts}] ${level.toUpperCase().padEnd(4)} ${safeMsg}${safeExtra ? ' ' + safeExtra : ''}`;
  console.log(`${COLORS[level] ?? ''}${line}${COLORS.reset}`);
  logStream?.write(line + '\n');
  // 网页控制台：把这一行塞进环形缓冲并广播给所有监听者
  LOG_RING.push({ ts, level, msg: stripAnsi(safeMsg) });
  if (LOG_RING.length > LOG_RING_MAX) LOG_RING.shift();
  for (const fn of logListeners) {
    try { fn({ ts, level, msg: stripAnsi(safeMsg) }); } catch { /* 忽略单个监听者的错误 */ }
  }
}

/**
 * 行内私钥打码。这里不能 import guard.mjs —— 那个模块反过来要 import 本文件的 log，
 * 会形成循环依赖导致 ESM 初始化失败。所以这个极小函数就地复制一份。
 * 两份逻辑必须保持一致：guard.mjs 的 PRIVATE_KEY_RE 与此处相同（都接受 0x / 0X）。
 *
 * 注意 lastIndex：正则带 /g，跨调用复用同一个对象时，String.replace 每次都会
 * 自己重置 lastIndex，所以这里反复调用是安全的（不像 .test() 会带着状态走）。
 */
const PRIVATE_KEY_RE = /0[xX][0-9a-fA-F]{64}/g;
function maskSecretInline(s) {
  return String(s ?? '').replace(PRIVATE_KEY_RE, '0x****(私钥已隐藏)****');
}

/** 去掉终端颜色转义码，网页上直接显示纯文本。
 *  注意必须匹配真实的 ESC 字符（\x1b），写成 \\x1b 只会匹配字面的 "\x1b" 反斜杠文本。 */
function stripAnsi(s) {
  return String(s).replace(/\x1b\[[0-9;]*m/g, '');
}

export const log = {
  info: (m, e) => write('info', m, e),
  ok: (m, e) => write('ok', m, e),
  warn: (m, e) => write('warn', m, e),
  err: (m, e) => write('err', m, e),
  dim: (m, e) => write('dim', m, e),
};

export function usd(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return String(n);
  return `$${n.toFixed(2)}`;
}

export function shortAddr(a) {
  if (!a || a.length < 12) return String(a);
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

/** 带超时和重试的 fetch。 */
export async function fetchRetry(url, opts = {}, retries = 3) {
  let lastErr;
  for (let i = 0; i < retries; i++) {
    try {
      const res = await fetch(url, {
        ...opts,
        signal: AbortSignal.timeout(opts.timeoutMs ?? 20000),
      });
      if (res.status >= 500 || res.status === 429) {
        lastErr = new Error(`HTTP ${res.status}`);
        await sleep(1000 * (i + 1));
        continue;
      }
      return res;
    } catch (err) {
      lastErr = err;
      await sleep(1000 * (i + 1));
    }
  }
  throw lastErr;
}

export async function getJson(url, opts = {}) {
  const res = await fetchRetry(url, opts);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status} ${await res.text().catch(() => '')}`);
  return res.json();
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export function loadConfig() {
  const file = path.join(ROOT, 'config.json');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    log.err(`读不到配置文件：${file}`);
    log.err('请确认你就在项目目录里，且 config.json 存在（不要改文件名）。');
    process.exit(1);
  }
  // Windows 记事本存 UTF-8 会带 BOM，直接 JSON.parse 会报 SyntaxError。
  let cfg;
  try {
    cfg = JSON.parse(text.replace(/^\uFEFF/, ''));
  } catch (e) {
    log.err(`config.json 不是合法 JSON：${e.message}`);
    log.err('最常见原因：① 最后一项后面多了个逗号 ② 用了中文引号 "" 而不是英文双引号 " ③ 改了参数名');
    log.err('不确定哪里坏了，就用记事本重新打开、只改冒号后面的数字，别动其他字符。');
    process.exit(1);
  }
  // 只保留真实字段，下划线开头的是注释
  cfg = Object.fromEntries(
    Object.entries(cfg).filter(([k]) => !k.startsWith('_')),
  );
  const errors = validateConfig(cfg);
  if (errors.length) {
    log.err('配置有错误，请按下面提示修改 config.json：');
    errors.forEach((e) => console.log(`  ✗ ${e}`));
    process.exit(1);
  }
  return cfg;
}

/** 配置校验：宁可启动时炸掉，也不要带着错配置真金白银下单。 */
export function validateConfig(cfg) {
  const errors = [];
  // 地址按 40 位严格校验：跟单对象抄错一位等于跟错了人，宁可直接拒。
  const addr = /^0x[0-9a-fA-F]{40}$/;
  const key = /^0x[0-9a-fA-F]{64}$/;
  // funderAddress 单独放宽到 39~40 位：有用户在交易所只复制了 39 位（漏末位），
  // 严格 40 位会在切 LIVE 时被拦，但错误信息只说"格式不对"很难自己发现漏了一位。
  const funderAddr = /^0x[0-9a-fA-F]{39,40}$/;

  if (!['dry-run', 'live'].includes(cfg.mode))
    errors.push('mode 只能是 "dry-run" 或 "live"');

  if (!addr.test(cfg.targetWallet || ''))
    errors.push('targetWallet 不是合法钱包地址（0x 开头 40 位十六进制）');

  if (!['fixed', 'percent', 'balancePercent'].includes(cfg.sizingMode))
    errors.push('sizingMode 只能是 "fixed"、"percent" 或 "balancePercent"');

  if (cfg.sizingMode === 'fixed' && !(cfg.fixedOrderSizeUsd > 0))
    errors.push('fixedOrderSizeUsd 必须大于 0');

  if (cfg.sizingMode !== 'fixed' && !(cfg.percentScale > 0 && cfg.percentScale <= 1))
    errors.push('percentScale 必须在 0~1 之间（percent / balancePercent 模式要用）');

  // balancePercent 会把可用余额乘一个比例，比例上限不能再套 maxPerOrderUsd，
  // 但为了防"一次梭哈"，硬性要求 percentScale 不超过 0.5
  if (cfg.sizingMode === 'balancePercent' && cfg.percentScale > 0.5)
    errors.push('balancePercent 模式下 percentScale 不能超过 0.5，否则一笔就能吃掉大半仓位');

  if (!(cfg.minTradeUsd >= 0)) errors.push('minTradeUsd 不能为负');
  if (!(cfg.maxTradeUsd >= 0)) errors.push('maxTradeUsd 不能为负（不设上限填 0）');
  if (cfg.maxTradeUsd !== 0 && cfg.maxTradeUsd < cfg.minTradeUsd)
    errors.push('maxTradeUsd 小于 minTradeUsd，等于什么都不跟');

  if (!(cfg.maxPerOrderUsd > 0)) errors.push('maxPerOrderUsd 必须大于 0');
  if (!(cfg.maxPerMarketUsd > 0)) errors.push('maxPerMarketUsd 必须大于 0');
  if (!(cfg.maxTotalInvestedUsd > 0)) errors.push('maxTotalInvestedUsd 必须大于 0');

  // 板块过滤校验。这里最危险的错配是：模式开了 whitelist 却没填词，
  // 那会退化成"什么都跟"，用户以为自己在只跟电竞，实际全市场跟单。
  const scopeMode = String(cfg.scopeMode ?? 'all').toLowerCase();
  if (!['all', 'whitelist', 'blacklist'].includes(scopeMode))
    errors.push('scopeMode 只能是 "all"、"whitelist" 或 "blacklist"');
  if (scopeMode !== 'all') {
    const raw = cfg.scopeKeywords;
    const kws = Array.isArray(raw)
      ? raw.map((k) => String(k ?? '').trim()).filter(Boolean)
      : String(raw ?? '').split(',').map((k) => k.trim()).filter(Boolean);
    if (kws.length === 0)
      errors.push(`scopeMode=${scopeMode} 时必须填 scopeKeywords，否则板块过滤等于没开`);
    const bad = kws.filter((k) => k.length < 2);
    if (bad.length) errors.push(`scopeKeywords 里「${bad.join(',')}」太短（少于 2 个字符），会误伤一大批市场`);
  }
  if (cfg.maxPerMarketUsd < cfg.maxPerOrderUsd)
    errors.push('maxPerMarketUsd 不应小于 maxPerOrderUsd');
  if (cfg.maxTotalInvestedUsd < cfg.maxPerMarketUsd)
    errors.push('maxTotalInvestedUsd 不应小于 maxPerMarketUsd');

  if (!(cfg.pollIntervalSeconds >= 30))
    errors.push('pollIntervalSeconds 至少 30 秒，太频繁会被 API 限流');

  // ---- 自动守卫 ----
  // maxDailyLossUsd 是"今天最多亏多少就停机"，与 stopLoss（浮亏）互补：
  // 浮亏可能来回震荡不触及止损，但当天已经真金白银亏出去的钱不会回来。
  if (cfg.maxDailyLossUsd !== undefined && cfg.maxDailyLossUsd !== null && !(cfg.maxDailyLossUsd >= 0))
    errors.push('maxDailyLossUsd 不能为负（0 或留空表示不启用单日熔断）');

  if (cfg.stopLossTotalUsd > 0)
    errors.push('stopLossTotalUsd 必须填负数（如 -9），正数会被当成止盈线，语义反了');

  if (cfg.takeProfitTotalUsd !== undefined && cfg.takeProfitTotalUsd !== null && cfg.takeProfitTotalUsd < 0)
    errors.push('takeProfitTotalUsd 必须为正数');

  // 自动平仓是危险动作，必须显式打开，且只在 live 下有意义
  if (cfg.autoFlattenOnStop === true && cfg.mode !== 'live') {
    // 不报错，只是提醒：dry-run 没有真实仓位可平
    log.warn('配置提醒：autoFlattenOnStop 只在 live 模式下会真的下单，dry-run 下只记录');
  }
  if (cfg.minSellPrice !== undefined && cfg.minSellPrice !== null && !(cfg.minSellPrice >= 0 && cfg.minSellPrice < 1))
    errors.push('minSellPrice 必须在 0~1 之间（低于此价不卖，防止贱卖）');

  if (!['FOK', 'FAK', 'GTC', 'GTD'].includes(cfg.orderType))
    errors.push('orderType 只能是 FOK / FAK / GTC / GTD');

  if (cfg.mode === 'live') {
    if (!key.test(cfg.privateKey || ''))
      errors.push('live 模式必须填 privateKey（0x 开头 64 位十六进制），否则拒绝启动');
    if (!funderAddr.test(cfg.funderAddress || ''))
      errors.push('live 模式必须填 funderAddress（你的 USDC 地址，0x 开头 39~40 位）');
  }

  return errors;
}

/** 把配置写回文件时统一不带 BOM，避免下次自己读不了。 */
export function saveConfig(cfg) {
  const file = path.join(ROOT, 'config.json');
  fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
}
