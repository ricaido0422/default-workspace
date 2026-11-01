/**
 * 交易员排行榜 —— 我们自己从 Polymarket 官方数据算，不信任何平台的标称值。
 *
 * 为什么自己算：
 *   CopyOdds 展示 1575 名交易员并给出「胜率 84% / PF 11.02」这类指标，
 *   但我们实测同一个地址（ronaldk），用官方 /closed-positions 全量算出来是
 *   胜率 73.4% / PF 5.45 / 净 $44,702。差距来源不明（可能是去尘埃、时间窗口或口径不同）。
 *   与其猜它的算法，不如用能验证的官方数据自己算一遍。
 *
 * 数据来源：data-api.polymarket.com/closed-positions（已平仓，realizedPnl 为真战绩）
 */
import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from './util.mjs';
import { auditTrader } from './stats.mjs';

const WATCHLIST_FILE = path.join(ROOT, 'watchlist.json');

/** 读取关注名单。文件不存在时返回内置默认名单。 */
export function loadWatchlist() {
  try {
    const raw = JSON.parse(fs.readFileSync(WATCHLIST_FILE, 'utf8'));
    const list = Array.isArray(raw) ? raw : (raw.traders ?? []);
    return list
      .map((t) => (typeof t === 'string' ? { address: t } : t))
      .filter((t) => /^0x[0-9a-fA-F]{40}$/.test(t.address ?? ''))
      .map((t) => ({
        address: t.address.toLowerCase(),
        name: t.name ?? '',
        source: t.source ?? 'watchlist.json',
        addedAt: t.addedAt ?? null,
      }));
  } catch {
    return [];
  }
}

/** 追加/更新一个地址到关注名单。返回写入后的全量名单。 */
export function saveToWatchlist(address, name = '') {
  const list = loadWatchlist();
  const addr = String(address).trim().toLowerCase();
  if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) throw new Error('不是合法的钱包地址（0x + 40 位十六进制）');

  const i = list.findIndex((t) => t.address === addr);
  const entry = { address: addr, name: name.trim(), source: 'watchlist.json', addedAt: new Date().toISOString() };
  if (i >= 0) list[i] = { ...list[i], ...entry };
  else list.push(entry);

  fs.writeFileSync(WATCHLIST_FILE, JSON.stringify({ traders: list }, null, 2) + '\n', 'utf8');
  return list;
}

export function removeFromWatchlist(address) {
  const addr = String(address).trim().toLowerCase();
  const list = loadWatchlist().filter((t) => t.address !== addr);
  fs.writeFileSync(WATCHLIST_FILE, JSON.stringify({ traders: list }, null, 2) + '\n', 'utf8');
  return list;
}

/**
 * 刷新排行榜：逐个跑 auditTrader，然后按「该不该跟」排序。
 * @param {(msg:string)=>void} onProgress 进度回调（网页控制台用它实时显示）
 * @param {number} limit 最多算几个（每个都要翻页，太贵，默认 8）
 */
// ⚠️ 每个交易员的上限页数。之前用 200 页（10000 笔）：
//    实测 HomeRunHazard 有超过 20000 条已平仓记录，一个人就要连翻 200 页 / 283 秒，
//    把「刷新排行榜」变成一次几分钟的等待。
//    但 pages = 100（5000 笔）已经足以把正常量级的交易员翻到底
//    （实测 Smallcoins 1855 笔 / ColdBlooded 270 笔 / ronaldk 403 笔全部 exhausted=true）。
//    超量的人会被正确标记为 exhausted=false 并沉底 —— 这正是我们想要的判定，不必真去翻完。
const MAX_PAGES_PER_TRADER = 100;

export async function buildLeaderboard({ limit = 8, onProgress = () => {} } = {}) {
  const watchlist = loadWatchlist();
  if (watchlist.length === 0) {
    return { ok: false, error: '关注名单是空的。请在「关注名单」页添加地址，或手工编辑 watchlist.json', rows: [] };
  }

  const targets = watchlist.slice(0, limit);
  const rows = [];

  for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    onProgress(`正在分析 ${i + 1}/${targets.length}：${t.name || t.address.slice(0, 10) + '…'}`);
    try {
      const r = await auditTrader(t.address, { maxPages: MAX_PAGES_PER_TRADER });
      rows.push({
        address: t.address,
        name: t.name || '',
        ...r.raw,
        flags: r.flags,
        exhausted: r.exhausted,
        rowCount: r.rowCount,
        fetchError: r.fetchError,
      });
    } catch (e) {
      rows.push({ address: t.address, name: t.name || '', error: e.message, total: 0 });
    }
  }

  // 排序要看「能不能跟」，不是单纯看赚了多少。
  // ⚠️ 硬性前提：只有 exhausted（数据翻到底）的才算可信。
  //    被截断的人胜率必然虚高（接口按盈利降序），一律不参与"合格"评定，
  //    否则会把史上最差的交易员排到第一 —— 这正是修复前 HomeRunHazard 排榜首的原因。
  const isTrustworthy = (r) => r.exhausted === true && r.total >= 20;
  const qualified = rows.filter(
    (r) => isTrustworthy(r) && r.winRate >= 0.55 && r.profitFactor >= 1.5 && r.netPnl > 0,
  );
  qualified.sort((a, b) => b.netPnl - a.netPnl);

  // 不可信的一律沉底，并且在前端明确标注，绝不混在合格名单里
  const rest = rows.filter((r) => !qualified.includes(r));
  rest.sort((a, b) => {
    const ta = isTrustworthy(a) ? 1 : 0;
    const tb = isTrustworthy(b) ? 1 : 0;
    if (ta !== tb) return tb - ta; // 可信的排在不可信的前面
    return (b.total ?? 0) - (a.total ?? 0);
  });

  const ranked = [...qualified, ...rest];
  ranked.forEach((r, i) => { r.rank = i + 1; r.qualified = qualified.includes(r); });

  return {
    ok: true,
    scanned: targets.length,
    watchlistTotal: watchlist.length,
    rows: ranked,
    refreshedAt: new Date().toISOString(),
  };
}
