/**
 * 板块 / 赛事 / 阶段 过滤器
 *
 * 为什么单独一个文件：Polymarket 的板块标签不在市场接口里。
 * 实测：
 *   /markets?condition_ids=X  -> 只有 seriesSlug（如 league-of-legends），拿不到 tags
 *   /events?slug=Y            -> 才返回完整 tags：Esports / league of legends /
 *                                Sports / LoL Worlds 2026 / Swiss Stage / Swiss Round 2
 * 这三个层级正好对应「板块 → 赛事 → 阶段（区间）」，所以必须多查一次 event。
 *
 * 匹配用 tag 的 slug（不是 label）：slug 是稳定的英文短名，label 有大小写和空格。
 */
import { fetchEvent } from './api.mjs';

const eventCache = new Map();  // slug -> { tags, seriesSlug, title, at }
const failAt = new Map();      // slug -> 上次失败时间戳
const OK_TTL_MS = 10 * 60_000; // 板块标签几乎不变，10 分钟足够
const FAIL_TTL_MS = 60_000;    // 限流失败只冷却 1 分钟，跟 filter.mjs 同一套策略
const MAX_CACHE = 300;

function evict(map) {
  if (map.size > MAX_CACHE) map.delete(map.keys().next().value);
}

/** 取一个事件的板块标签。失败不永久缓存，否则限流期间会误杀。 */
export async function loadEventScope(slug) {
  if (!slug) return null;
  const hit = eventCache.get(slug);
  if (hit && Date.now() - hit.at < OK_TTL_MS) return hit;

  const failedAt = failAt.get(slug) ?? 0;
  if (!hit && Date.now() - failedAt < FAIL_TTL_MS) return null;

  try {
    const ev = await fetchEvent(slug);
    if (!ev) throw new Error('事件不存在');
    const tags = (ev.tags ?? [])
      .map((t) => ({ slug: String(t?.slug ?? '').toLowerCase(), label: String(t?.label ?? '') }))
      .filter((t) => t.slug);
    const rec = { tags, seriesSlug: String(ev.seriesSlug ?? '').toLowerCase(), title: ev.title ?? '', at: Date.now() };
    eventCache.set(slug, rec);
    failAt.delete(slug);
    evict(eventCache);
    return rec;
  } catch {
    failAt.set(slug, Date.now());
    evict(failAt);
    return null;
  }
}

/**
 * 把一笔成交能代表的所有「板块词」收集出来。
 * 顺序：赛事阶段 tag → 赛事 tag → 板块 tag → seriesSlug → 标题（兜底）
 */
export function collectTokens(trade, market, evScope) {
  const set = new Set();
  const push = (v) => {
    const s = String(v ?? '').trim().toLowerCase();
    if (s) set.add(s);
  };
  for (const t of evScope?.tags ?? []) { push(t.slug); push(t.label); }
  push(evScope?.seriesSlug);
  push(market?.events?.[0]?.seriesSlug);
  push(market?.slug);
  push(trade?.eventSlug);
  // 标题兜底：有些盘没有 tag，只能靠问题文字认（如 "LoL: A vs B"）
  push(trade?.title);
  push(market?.question);
  // 归一化变体：把 "-" 和 "_" 也换成空格存一份。
  // 用户印象里写的是 "LoL Worlds 2026"（带空格），而 tag slug 是
  // "lol-worlds-2026"（带连字符）。两边形态不同会直接漏配。
  for (const raw of [...set]) {
    const spaced = raw.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (spaced && spaced !== raw) set.add(spaced);
  }
  return [...set];
}

function normalizeKeywords(cfg) {
  const raw = cfg.scopeKeywords;
  const arr = Array.isArray(raw)
    ? raw
    : String(raw ?? '').split(',');
  return arr
    .map((k) => String(k ?? '').trim().toLowerCase())
    .filter(Boolean);
}

/** 单个关键词是否命中这组 token。子串匹配：填 "league" 能命中 "league-of-legends"。 */
function hit(tokens, kw) {
  return tokens.some((t) => t.includes(kw));
}

/**
 * 板块过滤主入口。
 * scopeMode: all（不过滤）/ whitelist（只跟名单内）/ blacklist（不跟名单内）
 * 返回 follow=false 时 reason 会原样显示在控制台，方便你事后回看。
 */
export async function evaluateScope(trade, market, cfg) {
  const mode = String(cfg.scopeMode ?? 'all').toLowerCase();
  if (mode === 'all') return { ok: true };

  const keywords = normalizeKeywords(cfg);
  if (keywords.length === 0) {
    // 开了名单模式却一个词都没填：按"不过滤"处理并明确提示，
    // 否则用户会以为过滤器坏了（实际上会静默跟所有单，更危险）。
    return { ok: true, warn: `scopeMode=${mode} 但 scopeKeywords 为空，板块过滤未生效` };
  }

  const slug = trade?.eventSlug || market?.events?.[0]?.slug || market?.slug;
  const evScope = await loadEventScope(slug);
  if (!evScope) {
    return { ok: false, reason: `查不到板块标签（gamma-api 限流），本轮跳过，稍后自动重试` };
  }

  const tokens = collectTokens(trade, market, evScope);
  const matched = keywords.filter((k) => hit(tokens, k));
  const tagLabel = evScope.tags.map((t) => t.label).filter(Boolean).join(' / ') || evScope.seriesSlug || '(无标签)';

  if (mode === 'whitelist') {
    return matched.length
      ? { ok: true, matched, tagLabel }
      : { ok: false, reason: `板块「${tagLabel}」不在白名单内，跳过`, tagLabel };
  }
  // blacklist
  return matched.length
    ? { ok: false, reason: `板块「${tagLabel}」命中黑名单词「${matched.join(',')}」，跳过`, tagLabel }
    : { ok: true, tagLabel };
}

/**
 * 板块发现：扫描目标最近的成交，统计他都在哪些板块下注。
 * 存在的意义：用户根本不知道该填什么关键词（"esports" 还是 "Esports"？），
 * 与其让他猜，不如把他真实打过的板块列出来给他点。
 */
export async function discoverScopes(trades, marketOf, limit = 60) {
  const agg = new Map(); // slug -> {slug,label,count,amount,series}
  const list = trades.slice(0, limit);

  for (const t of list) {
    const slug = t.eventSlug || t.slug;
    if (!slug) continue;
    const evScope = await loadEventScope(slug);
    const tags = evScope?.tags ?? [];
    const amount = Number(t.size ?? 0);

    // 只统计"板块级"标签（前几个通常是大类：Esports / Sports / Politics…），
    // 阶段级（Swiss Round 2）太细，会碎成一地。
    const broad = tags.filter((x) =>
      ['esports', 'sports', 'politics', 'crypto', 'culture', 'economy', 'weather',
       'fintech', 'tech', 'science', 'business', 'world', 'games', 'nba', 'nfl',
       'soccer', 'tennis', 'mma', 'csgo', 'dota-2', 'league-of-legends', 'valorant',
       'counter-strike', 'football', 'basketball', 'baseball', 'hockey'].includes(x.slug));
    const use = broad.length ? broad : tags.slice(0, 2);

    for (const tag of use) {
      const cur = agg.get(tag.slug) ?? { slug: tag.slug, label: tag.label || tag.slug, count: 0, amount: 0 };
      cur.count += 1;
      cur.amount += amount;
      agg.set(tag.slug, cur);
    }
    if (!tags.length && evScope?.seriesSlug) {
      const cur = agg.get(evScope.seriesSlug) ?? { slug: evScope.seriesSlug, label: evScope.seriesSlug, count: 0, amount: 0 };
      cur.count += 1;
      cur.amount += amount;
      agg.set(evScope.seriesSlug, cur);
    }
  }

  return [...agg.values()]
    .map((x) => ({ ...x, amount: Math.round(x.amount * 100) / 100 }))
    .sort((a, b) => b.count - a.count || b.amount - a.amount);
}

export function clearScopeCache() {
  eventCache.clear();
  failAt.clear();
}
