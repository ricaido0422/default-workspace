/**
 * 过滤器：把对方的成交记录逐条过筛，判断"这一单要不要跟"
 * 每条判断都返回原因，方便你事后回看为什么没跟。
 */
import { fetchMarket } from './api.mjs';
import { usd } from './util.mjs';
import { evaluateScope } from './scope.mjs';

const marketCache = new Map();
const marketFailAt = new Map(); // conditionId -> 上次失败时间戳

// 失败不永久缓存：gamma-api 被限流时返回的是临时失败，
// 如果把 null 永久记住，这个市场就永远被误判成「成交量 0」而跳过。
const FAIL_TTL_MS = 60_000;

/**
 * 三种跟单模式，语义与 CopyOdds 对齐：
 *   fixed          每笔固定金额 —— 本金小时最稳，单笔金额与对方无关
 *   percent        按对方单笔成交额的比例 —— 对方下大单你下大，跟得更忠实
 *                  （修正前这里是 fixedOrderSizeUsd × percentScale，
 *                    完全没用上对方金额，是个真 bug，已改）
 *   balancePercent 按你可用余额的百分比 —— 复利式，赚了自动加大，亏了自动缩小
 */
export function computeSizing(cfg, targetTradeUsd, state) {
  const size = Number(targetTradeUsd ?? 0);

  if (cfg.sizingMode === 'percent') {
    const amount = size * cfg.percentScale;
    return amount > 0
      ? { ok: true, amount, mode: 'percent' }
      : { ok: false, reason: `按比例算出的金额 ${usd(amount)} 无效` };
  }

  if (cfg.sizingMode === 'balancePercent') {
    const remaining = Math.max(0, cfg.maxTotalInvestedUsd - (state?.totalInvested ?? 0));
    const amount = remaining * cfg.percentScale;
    return amount > 0
      ? { ok: true, amount, mode: 'balancePercent' }
      : { ok: false, reason: `余额比例算出的金额 ${usd(amount)} 无效（可能已接近总仓位上限）` };
  }

  // fixed
  return cfg.fixedOrderSizeUsd > 0
    ? { ok: true, amount: cfg.fixedOrderSizeUsd, mode: 'fixed' }
    : { ok: false, reason: 'fixedOrderSizeUsd 未设置' };
}

export async function evaluateTrade(trade, cfg, state) {
  // 1) 只看买入/卖出的成交，不看拆分合并
  if (trade.side !== 'BUY' && trade.side !== 'SELL') {
    return { follow: false, reason: `非买卖单（${trade.side}），跳过` };
  }

  // 2) 资金区间过滤（用户明确要求的双向过滤）
  const size = Number(trade.size ?? 0);
  if (size < cfg.minTradeUsd) {
    return { follow: false, reason: `成交额 ${usd(size)} 低于下限 ${usd(cfg.minTradeUsd)}` };
  }
  if (cfg.maxTradeUsd > 0 && size > cfg.maxTradeUsd) {
    return { follow: false, reason: `成交额 ${usd(size)} 高于上限 ${usd(cfg.maxTradeUsd)}` };
  }

  // 3) 市场成交量过滤（冷门盘滑点大）
  let market = marketCache.get(trade.conditionId);
  if (market === undefined || market === null) {
    // 上次查失败过且还在冷却期内就先跳过这次查询，但仍然要按"查不到"处理
    const failAt = marketFailAt.get(trade.conditionId) ?? 0;
    if (market === undefined || Date.now() - failAt > FAIL_TTL_MS) {
      try {
        market = await fetchMarket(trade.conditionId);
        if (market) marketFailAt.delete(trade.conditionId);
      } catch {
        market = null;
        marketFailAt.set(trade.conditionId, Date.now());
      }
      marketCache.set(trade.conditionId, market);
      if (marketCache.size > 500) marketCache.delete(marketCache.keys().next().value);
    }
  }

  if (market == null) {
    // 查不到元数据：不能当成"成交量 0"直接否掉，否则限流期间会误杀所有单。
    // 按保守策略放行，但在原因里标注清楚，让用户在控制台里能看见。
    return { follow: false, reason: `查不到市场元数据（多半是 gamma-api 限流），本轮跳过，稍后自动重试` };
  }

  const vol = Number(market?.volumeNum ?? market?.volume ?? 0);
  if (vol < cfg.minMarketVolumeUsd) {
    return { follow: false, reason: `市场成交量 ${usd(vol)} 低于 ${usd(cfg.minMarketVolumeUsd)}` };
  }
  if (market?.closed === true) {
    return { follow: false, reason: '市场已关闭' };
  }

  // 2.5) 板块 / 赛事 / 阶段 过滤（要排在成交量之后：先确认盘够大，
  //      再花一次请求查它的板块标签，避免给冷门盘白跑一趟。）
  const scope = await evaluateScope(trade, market, cfg);
  if (!scope.ok) return { follow: false, reason: scope.reason };

  // 4) 单市场上限
  const perMarket = state.perMarket.get(trade.conditionId) ?? 0;
  if (perMarket >= cfg.maxPerMarketUsd) {
    return { follow: false, reason: `该市场已下满 ${usd(cfg.maxPerMarketUsd)}，不加码` };
  }

  // 5) 总仓位上限
  if (state.totalInvested >= cfg.maxTotalInvestedUsd) {
    return { follow: false, reason: `总仓位已满 ${usd(cfg.maxTotalInvestedUsd)}` };
  }

  // 6) 计算本次实际下单金额（三种模式，对齐 CopyOdds）
  const sizing = computeSizing(cfg, size, state);
  if (!sizing.ok) {
    return { follow: false, reason: sizing.reason };
  }
  let amount = sizing.amount;
  amount = Math.min(amount, cfg.maxPerOrderUsd);
  amount = Math.min(amount, cfg.maxPerMarketUsd - perMarket);
  amount = Math.min(amount, cfg.maxTotalInvestedUsd - state.totalInvested);
  amount = Math.round(amount * 100) / 100; // Polymarket 金额精度到分
  if (amount < 1) {
    return { follow: false, reason: `可下金额 ${usd(amount)} 低于 $1 最小下单额` };
  }

  return {
    follow: true,
    amount,
    reason: `区间/成交量/仓位全部通过`,
    market: market ?? { question: trade.title ?? trade.market ?? '(未知市场)' },
  };
}
