/**
 * Polymarket API 客户端（只读部分：交易记录 + 市场元数据）
 */
import { getJson } from './util.mjs';

const DATA_API = 'https://data-api.polymarket.com';
const GAMMA_API = 'https://gamma-api.polymarket.com';
const CLOB_API = 'https://clob.polymarket.com';

/** 拉取目标地址最近的成交记录（TRADE 类型，已成交） */
export async function fetchRecentTrades(address, limit = 100) {
  const url =
    `${DATA_API}/trades?user=${encodeURIComponent(address)}` +
    `&limit=${limit}&takerOnly=false&filterType=CASH&filterAmount=0`;
  const data = await getJson(url);
  return Array.isArray(data) ? data : (data?.trades ?? []);
}

/** 拉取目标地址的持仓 */
export async function fetchPositions(address) {
  const url = `${DATA_API}/positions?user=${encodeURIComponent(address)}`;
  const data = await getJson(url);
  return Array.isArray(data) ? data : [];
}

/** 查询市场元数据（成交量、是否关闭等） */
export async function fetchMarket(conditionId) {
  if (!conditionId) return null;
  const url = `${GAMMA_API}/markets?condition_ids=${encodeURIComponent(conditionId)}`;
  const data = await getJson(url);
  return Array.isArray(data) ? (data[0] ?? null) : null;
}

/** 按事件 slug 查事件详情。板块标签（Esports / LoL Worlds 2026 等）
 *  只在 /events 里返回，/markets 拿不到，所以必须单独查一次。 */
export async function fetchEvent(slug) {
  if (!slug) return null;
  const url = `${GAMMA_API}/events?slug=${encodeURIComponent(slug)}`;
  const data = await getJson(url);
  return Array.isArray(data) ? (data[0] ?? null) : (data ?? null);
}

/** 查询当前盘口，用来估算滑点 */
export async function fetchBook(tokenId) {
  const url = `${CLOB_API}/book?token_id=${encodeURIComponent(tokenId)}`;
  return getJson(url);
}

/** 查询 USDC 余额（链上 RPC） */
export async function fetchUsdcBalance(rpcUrl, address) {
  // USDC on Base: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  const usdc = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
  const padded = address.toLowerCase().replace('0x', '').padStart(64, '0');
  const body = {
    jsonrpc: '2.0', id: 1, method: 'eth_call',
    params: [{ to: usdc, data: `0x70a08231${padded}` }, 'latest'],
  };
  const res = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const json = await res.json();
  if (json.error) throw new Error(json.error.message);
  return Number(BigInt(json.result)) / 1e6;
}

export { DATA_API, GAMMA_API, CLOB_API };
