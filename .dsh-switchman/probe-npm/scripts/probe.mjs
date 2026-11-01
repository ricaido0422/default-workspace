#!/usr/bin/env node
/**
 * 独立探测脚本：确认本机能连上 Polymarket，并看一眼目标地址最近在干什么。
 * 用法：node scripts/probe.mjs
 * 这个脚本不需要任何依赖、不需要私钥、绝对不会下单。
 */
import { DATA_API, GAMMA_API, CLOB_API, fetchRecentTrades } from '../src/api.mjs';
import { loadConfig, log, usd, shortAddr } from '../src/util.mjs';
import { ensureProxy } from '../src/proxy.mjs';

// 必须先确保走代理，否则后面所有请求都会超时。
// 如果检测到代理，这里会自动用带代理的新进程重启本程序，然后退出当前进程。
await ensureProxy(log);

const cfg = loadConfig();
log.info(`探测目标：${shortAddr(cfg.targetWallet)}`);
log.info('');

const checks = [
  ['data-api /trades', `${DATA_API}/trades?limit=1`],
  ['gamma-api /markets', `${GAMMA_API}/markets?limit=1`],
  ['clob /ok', `${CLOB_API}/ok`],
];

for (const [name, url] of checks) {
  const started = Date.now();
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
    const text = await res.text();
    log.ok(`${name}: HTTP ${res.status} (${Date.now() - started}ms, ${text.length} bytes)`);
  } catch (err) {
    log.err(`${name}: 连不上 (${Date.now() - started}ms) -> ${err?.cause?.code || err.name}: ${err.message}`);
  }
}
log.info('');
log.info('--- 目标地址最近 10 笔成交 ---');try {
  const trades = await fetchRecentTrades(cfg.targetWallet, 10);
  if (!trades.length) {
    log.warn('拉到了 0 条记录。可能是该地址近期无成交，或接口暂时不可用。');
  }
  for (const t of trades) {
    const size = Number(t.size ?? 0);
    const tag = size >= 500 ? 'OK 会跟' : '-- 太小';
    console.log(
      `  ${tag} | ${usd(size).padStart(10)} | ${String(t.side ?? '?').padEnd(4)} | ` +
      `${String(t.outcome ?? '?').padEnd(3)} @ ${String(t.price ?? '?').padEnd(6)} | ` +
      `${String(t.title ?? t.market ?? '').slice(0, 50)}`,
    );
  }
  log.info('');
  log.info(`说明：以上 ${trades.filter((t) => Number(t.size ?? 0) >= 500).length} 笔会被当前配置跟单。`);
} catch (err) {
  log.err(`读取成交记录失败：${err.message}`);
  log.warn('如果全是连接超时，说明本机网络到 polymarket.com 不通（见 README「网络不通」一节）。');
}
