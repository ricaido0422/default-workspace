/**
 * 交易员战绩统计 —— 用官方 API 自己算，不信任何平台的标称值。
 *
 * 核心发现（实测）：
 *   /positions          → 返回【当前持仓】，cashPnl 是未实现浮亏，不能算战绩
 *   /closed-positions   → 返回【已平仓】，realizedPnl 是已落袋的钱，这才是战绩
 *   limit 参数硬上限 50，必须用 offset 翻页才能拿全量
 *
 * 所以「胜率 / 盈亏比 / 累计盈亏」只能从 /closed-positions 算。
 */
import { getJson } from './util.mjs';

const DATA_API = 'https://data-api.polymarket.com';

/** 单次请求最多返回多少条（实测 limit=100/200/500 都被压回 50）。 */
const HARD_LIMIT = 50;

/**
 * 拉取某个地址的全部已平仓记录。
 *
 * ⚠️ 关键陷阱（实测确认，务必牢记）：
 *   /closed-positions 是按 realizedPnl **降序**返回的，亏损单全排在最后面。
 *   因此「只读前 N 页」会严重高估胜率 —— 实测 HomeRunHazard 前 3000 笔全是赢单，
 *   只看前 600 笔会算出「胜率 100%、净赚 $2143 万」这种彻头彻尾的假象。
 *   结论：必须一直翻到数据穷尽（某页返回 < 50 条）才能算战绩，中途截断的结果一律不可用。
 *
 * @param {string} address 钱包地址
 * @param {number} maxPages 最多翻几页（默认 200 页 = 10000 笔）
 * @returns {{rows:Array, exhausted:boolean, error:string|null}}
 *   exhausted=true 表示已翻到最后一页，统计可信；false 表示被截断，统计偏向乐观、不可用。
 */
export async function fetchClosedPositions(address, maxPages = 200) {
  const all = [];
  const seen = new Set();
  let exhausted = false;
  let error = null;

  for (let page = 0; page < maxPages; page++) {
    const offset = page * HARD_LIMIT;
    const url =
      `${DATA_API}/closed-positions?user=${encodeURIComponent(address)}` +
      `&limit=${HARD_LIMIT}&offset=${offset}`;

    // 单个钱包要连翻上百页，中途一次抖动（429/超时）就会把结果判成"数据不完整"，
    // 让本来可信的交易员沉底。这里对同一页重试几次再放弃。
    let batch = null;
    let lastErr = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        batch = await getJson(url);
        lastErr = null;
        break;
      } catch (e) {
        lastErr = e;
        await new Promise((r) => setTimeout(r, 600 * 2 ** attempt)); // 0.6s → 1.2s
      }
    }
    if (lastErr) {
      error = lastErr.message;
      break; // 连续三次都失败才认定中断；已拿到的数据照样返回，但标记未穷尽
    }
    if (!Array.isArray(batch) || batch.length === 0) { exhausted = true; break; }

    let added = 0;
    for (const p of batch) {
      // 同一个 position 可能因为成交多笔被拆成多行，按 市场+结果 去重
      const key = `${p.conditionId}|${p.outcome}`;
      if (seen.has(key)) continue;
      seen.add(key);
      all.push(p);
      added++;
    }
    // 返回不满一页 = 后面没有了
    if (batch.length < HARD_LIMIT) { exhausted = true; break; }
    if (added === 0) { exhausted = true; break; } // 整页都是重复，说明到底了
  }

  return { rows: all, exhausted, error };
}

/**
 * 从一个地址的已平仓记录算出全部战绩指标。
 * dustFilterUsd：小于这个投入金额的仓位不计入（默认 0 = 全算）。
 * 之所以提供这个开关：极小的仓位会拉低胜率，但也可能确实是策略的一部分，让用户自己决定。
 */
export function computeStats(rows, { dustFilterUsd = 0 } = {}) {
  const list = rows.filter(
    (p) => Number.isFinite(Number(p.realizedPnl)) && Number(p.totalBought ?? 0) >= dustFilterUsd,
  );

  if (list.length === 0) {
    return {
      total: 0, wins: 0, losses: 0, even: 0,
      winRate: 0, profitFactor: 0, netPnl: 0,
      grossProfit: 0, grossLoss: 0, avgReturn: 0,
      avgHoldHours: 0, maxDrawdownUsd: 0,
      bestTrade: 0, worstTrade: 0,
      note: '没有已平仓记录，无法评估',
    };
  }

  let wins = 0, losses = 0, even = 0;
  let grossProfit = 0, grossLoss = 0;
  let bestTrade = -Infinity, worstTrade = Infinity;

  for (const p of list) {
    const pnl = Number(p.realizedPnl);
    if (pnl > 0) { wins++; grossProfit += pnl; bestTrade = Math.max(bestTrade, pnl); }
    else if (pnl < 0) { losses++; grossLoss += -pnl; worstTrade = Math.min(worstTrade, pnl); }
    else even++;
  }

  const netPnl = grossProfit - grossLoss;

  // 平均盈利率：按每笔的投入金额加权前的简单平均 return（realizedPnl / totalBought）
  let retSum = 0, retCount = 0;
  for (const p of list) {
    const cost = Number(p.totalBought ?? 0);
    if (cost > 0) { retSum += Number(p.realizedPnl) / cost; retCount++; }
  }
  const avgReturn = retCount > 0 ? retSum / retCount : 0;

  // 平均持仓时长：平仓时间戳 - 首次成交时间戳（closed-positions 没有开仓时间，
  // 用 avgPrice→1 的价格变化无法推算，所以退而求其次：用时间戳跨度估计不可靠。
  // 这里改为输出"最早/最晚平仓时间"，让用户知道样本覆盖的时间范围，比假装有数据更诚实。）
  const ts = list.map((p) => Number(p.timestamp ?? 0)).filter((t) => t > 0);
  const firstClose = ts.length ? Math.min(...ts) : 0;
  const lastClose = ts.length ? Math.max(...ts) : 0;

  // 最大回撤：按平仓时间排序，累加 realizedPnl，看资金曲线从高点到低点的最大跌幅
  const sorted = [...list]
    .filter((p) => Number(p.timestamp ?? 0) > 0)
    .sort((a, b) => Number(a.timestamp) - Number(b.timestamp));
  let equity = 0, peak = 0, maxDrawdownUsd = 0;
  for (const p of sorted) {
    equity += Number(p.realizedPnl);
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDrawdownUsd) maxDrawdownUsd = dd;
  }

  return {
    total: list.length,
    wins, losses, even,
    winRate: wins / list.length,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? Infinity : 0),
    netPnl,
    grossProfit,
    grossLoss,
    avgReturn,
    maxDrawdownUsd,
    bestTrade: bestTrade === -Infinity ? 0 : bestTrade,
    worstTrade: worstTrade === Infinity ? 0 : worstTrade,
    firstCloseTs: firstClose,
    lastCloseTs: lastClose,
    sampleDays: firstClose && lastClose ? Math.round((lastClose - firstClose) / 86400) : 0,
    note: null,
  };
}

/**
 * 一键体检某个交易员。返回原始统计 + 若干个「该不该跟」的客观判据。
 * 判据带 level（ok/warn/err），前端直接按等级上色，用户能看到结论的依据。
 */
export async function auditTrader(address, opts = {}) {
  const { rows, exhausted, error } = await fetchClosedPositions(address, opts.maxPages ?? 200);
  const raw = computeStats(rows, { dustFilterUsd: opts.dustFilterUsd ?? 0 });

  const flags = [];
  if (raw.total === 0) {
    flags.push({ level: 'err', text: '没有已平仓战绩，无法验证。可能是纯持仓型或新号' });
  } else {
    // 最重要的一条：数据没翻到底时，胜率必然虚高（因为接口按 realizedPnl 降序），
    // 此时下面的胜率/PF 全部不可信，必须明确警告而不是照样展示。
    if (!exhausted) {
      flags.push({
        level: 'err',
        text: `只取到 ${raw.total} 笔就被上限截断（接口按盈利降序，亏损单还排在后面）。` +
              `这个胜率是虚高的，不能用来选人 —— 建议先别跟他，等后续翻完整再评估`,
      });
    }
    if (raw.total < 30) flags.push({ level: 'warn', text: `样本只有 ${raw.total} 笔，统计意义弱` });
    if (error) flags.push({ level: 'warn', text: `抓取中断：${error}` });

    if (exhausted) {
      // 只有数据完整时才做正面/负面评价。数据截断时这些指标都是假的，
      // 上面已经打过 err 标记了，这里再给结论只会让人误判。
      flags.push({ level: 'ok', text: `已翻完全部 ${raw.total} 笔已平仓记录，统计完整可信` });
      if (raw.winRate < 0.5) flags.push({ level: 'warn', text: `胜率 ${(raw.winRate * 100).toFixed(1)}% 低于抛硬币水平` });
      if (raw.profitFactor < 1.2) flags.push({ level: 'warn', text: `盈亏比 ${raw.profitFactor.toFixed(2)} 偏低，赚的不比亏的多多少` });
      if (raw.profitFactor >= 2 && raw.winRate >= 0.6) flags.push({ level: 'ok', text: '胜率与盈亏比双高，属于稀缺的优质战绩' });
      if (raw.netPnl <= 0) flags.push({ level: 'err', text: `累计盈亏 $${raw.netPnl.toFixed(0)}，整体是亏的` });
      if (raw.maxDrawdownUsd > Math.abs(raw.netPnl) * 3 && raw.netPnl > 0) {
        flags.push({ level: 'warn', text: `最大回撤 $${raw.maxDrawdownUsd.toFixed(0)} 远大于净利润，过程会很颠簸` });
      }
    }
  }

  return { address, raw, flags, rowCount: rows.length, exhausted, fetchError: error };
}

/**
 * 给当前 Target 推荐跟单参数。
 * 逻辑对齐 CopyOdds 的「应用推荐」：用你的资金 + 对方的交易节奏，
 * 推出一个"不会被一两笔大单吃光"的设置。
 */
export async function recommendSettings(address, capitalUsd) {
  const { rows } = await fetchClosedPositions(address, 8);
  if (rows.length === 0) return null;

  // 对方的仓位规模分布：用中位数代表他的典型下注额
  const sizes = rows.map((p) => Number(p.totalBought ?? 0)).filter((n) => n > 0).sort((a, b) => a - b);
  const median = Math.floor(sizes.length / 2);
  const typicalSize = sizes[median] ?? sizes[sizes.length - 1] ?? 100;
  const p90 = sizes[Math.floor(sizes.length * 0.9)] ?? typicalSize * 3;

  // 本金能承受的笔数：目标至少能下 12 笔（每笔约占本金 8%），再多就太散
  const targetTrades = Math.min(20, Math.max(6, Math.floor(capitalUsd / 3)));
  let perOrder = Math.max(1, capitalUsd / targetTrades);
  if (perOrder < 1) perOrder = 1; // Polymarket 最小 $1

  // 规模区间下限：卡在对方中位仓位的 30%，过滤掉他的试水小单
  const minTrade = Math.max(50, Math.round(typicalSize * 0.3));
  // 上限：卡在 p90 的 1.5 倍，超出的是异常大单
  const maxTrade = Math.round(p90 * 1.5);

  return {
    capitalUsd,
    typicalSize,
    p90,
    suggested: {
      sizingMode: 'fixed',
      fixedOrderSizeUsd: Math.round(perOrder * 100) / 100,
      minTradeUsd: minTrade,
      maxTradeUsd: maxTrade,
      maxPerOrderUsd: Math.round(perOrder * 100) / 100,
      maxPerMarketUsd: Math.round(perOrder * 2 * 100) / 100,
      maxTotalInvestedUsd: Math.round(capitalUsd * 0.5 * 100) / 100,
    },
    rationale: [
      `对方典型单笔约 $${typicalSize.toFixed(0)}（中位），90 分位约 $${p90.toFixed(0)}`,
      `本金 $${capitalUsd} 建议拆成约 ${targetTrades} 笔，每笔 $${perOrder.toFixed(2)}`,
      `规模区间 $${minTrade}–$${maxTrade}：滤掉小额试水单，同时挡住异常大单`,
      `总仓位上限 $${(capitalUsd * 0.5).toFixed(2)}（本金 50%），留一半现金应对机会`,
    ],
  };
}
