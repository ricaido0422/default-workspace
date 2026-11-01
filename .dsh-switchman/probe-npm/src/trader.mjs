/**
 * 真实下单模块（只有 config.json 里 mode 改成 "live" 才会被加载）
 *
 * 安全设计：
 *  - 私钥只从本地 config.json 读，不联网、不上传
 *  - 每次下单前重新校验金额上限（双保险）
 *  - 任何异常都返回 { ok:false, error }，绝不静默吞掉
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { log, usd, ROOT } from './util.mjs';
import { fetchPositions } from './api.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const CLOB_HOST = 'https://clob.polymarket.com';
const CHAIN_ID = 137;

let ClobClient, OrderType;
let client = null;
let cfgRef = null;

/** 初始化 CLOB 客户端（懒加载，避免 dry-run 也要装依赖） */
async function getClient(cfg) {
  if (client) return client;
  try {
    ({ ClobClient, OrderType } = await import('@polymarket/clob-client'));
    const { Wallet } = await import('ethers');
  } catch {
    throw new Error(
      '缺少依赖。请先在项目目录运行：\n' +
      '    npm install @polymarket/clob-client ethers\n' +
      '（ethers v6 与 clob-client v5 配套，不要装 ethers@5）',
    );
  }

  const { Wallet } = await import('ethers');
  const signer = new Wallet(cfg.privateKey);

  client = new ClobClient(
    CLOB_HOST,
    CHAIN_ID,
    signer,
    undefined,
    cfg.signatureType ?? 0,
    cfg.funderAddress,
  );

  // 派生/获取 API 凭证（L2 签名）
  const creds = await client.createOrDeriveApiKey();
  client = new ClobClient(
    CLOB_HOST,
    CHAIN_ID,
    signer,
    creds,
    cfg.signatureType ?? 0,
    cfg.funderAddress,
  );
  log.ok('CLOB 客户端已初始化（API 凭证派生成功）');
  return client;
}

const ORDER_TYPES = {
  FOK: () => OrderType.FOK,
  FAK: () => OrderType.FAK,
  GTC: () => OrderType.GTC,
  GTD: () => OrderType.GTD,
};

export async function placeOrder(trade, verdict) {
  try {
    cfgRef = cfgRef ?? {};
    const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
    cfgRef = cfg;

    // 双保险：即使 filter 算错，这里也再压一次
    if (verdict.amount > cfg.maxPerOrderUsd) {
      return { ok: false, error: `金额 ${usd(verdict.amount)} 超过单笔上限 ${usd(cfg.maxPerOrderUsd)}，已拦截` };
    }

    const c = await getClient(cfg);

    // 从成交记录里取 token id
    const tokenId = trade.asset ?? trade.tokenId;
    if (!tokenId) {
      return { ok: false, error: '成交记录里没有 asset/tokenId 字段，无法定位合约' };
    }

    const side = trade.side === 'BUY' ? 'BUY' : 'SELL';
    // 注意：跟单要买的是"对方买的那个 outcome"
    const marketOrderArgs = {
      tokenId: String(tokenId),
      side,
      amount: verdict.amount, // USDC 金额
    };

    const resp = await c.createAndPostMarketOrder(
      marketOrderArgs,
      ORDER_TYPES[cfg.orderType]?.() ?? OrderType.FOK,
    );

    if (resp?.success === false || resp?.errorMsg) {
      return { ok: false, error: resp.errorMsg ?? JSON.stringify(resp) };
    }
    return { ok: true, orderId: resp.orderID ?? resp.id ?? '(无 id)' };
  } catch (err) {
    return { ok: false, error: err?.message ?? String(err) };
  }
}

/**
 * 紧急平仓：把所有持仓按市价卖掉。
 *
 * ⚠️ 默认不启用（config.autoFlattenOnStop 为 false）。理由：
 *    预测市场的冷门盘买卖价差极大，市价全抛的滑点可能吃掉 10% 以上，
 *    有时比"放着等它自己结算"亏得更多。所以它必须是用户显式打开的开关，
 *    而不是止损的默认行为。
 *
 * 即便启用，也带三重保护：
 *   1. 低于 minPriceFloor 的价格不卖（白送价，卖了等于认亏到底）
 *   2. 余额为 0 / 数量过小的持仓跳过
 *   3. 每笔独立 try/catch，一笔失败不影响其余
 */
export async function flattenAll() {
  const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
  const floor = Number(cfg.minSellPrice ?? 0.02); // 低于 2 分不卖
  const c = await getClient(cfg);

  let positions = [];
  try {
    positions = await fetchPositions(cfg.funderAddress);
  } catch (e) {
    return { ok: false, closed: 0, skipped: 0, errors: [`读不到持仓：${e.message}`] };
  }

  const results = { closed: 0, skipped: 0, errors: [] };
  for (const p of positions) {
    const size = Number(p.size ?? 0);
    const price = Number(p.curPrice ?? 0);
    const tokenId = p.asset ?? p.oppositeAsset;
    try {
      if (!(size > 0)) { results.skipped++; continue; }
      if (price < floor) {
        results.skipped++;
        results.errors.push(`${p.title ?? p.asset}：现价 ${usd(price)} 低于 ${usd(floor)} 地板价，不贱卖`);
        continue;
      }
      const resp = await c.createAndPostMarketOrder({
        tokenId: String(tokenId),
        side: 'SELL',
        amount: size, // SELL 时 amount 是份数
      }, ORDER_TYPES[cfg.orderType]?.() ?? OrderType.FOK);
      if (resp?.success === false || resp?.errorMsg) {
        results.errors.push(`${p.title ?? p.asset}：${resp.errorMsg ?? '下单失败'}`);
      } else {
        results.closed++;
        log.ok(`平仓成功 | ${p.title ?? p.asset} | ${size} 份`);
      }
    } catch (e) {
      results.errors.push(`${p.title ?? p.asset}：${e.message}`);
    }
  }
  return { ok: true, ...results };
}

export default { placeOrder, flattenAll };
