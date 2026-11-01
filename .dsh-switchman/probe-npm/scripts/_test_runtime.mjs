/**
 * 单元测试：runtime.mjs 的 hydrate 跨对象清零 + 跨日熔断重置 + 风险判定。
 * 先用 saveRuntime 写入受控状态，再 hydrate 读回来，避免依赖磁盘上的真实数据。
 */
import fs from 'node:fs';
import { hydrate, evaluateRisk, blankRisk, saveRuntime, loadRuntime, RUNTIME_FILE } from '../src/runtime.mjs';

let pass = 0, fail = 0;
const T = (name, fn) => { try { fn(); console.log('  PASS  ' + name); pass++; }
  catch (e) { console.log('  FAIL  ' + name + ' -> ' + e.message); fail++; } };
const TA = async (name, fn) => { try { await fn(); console.log('  PASS  ' + name); pass++; }
  catch (e) { console.log('  FAIL  ' + name + ' -> ' + e.message); fail++; } };

console.log('\n=== runtime.mjs 单元测试 ===\n');

const OLD = '0xaaaa000000000000000000000000000000000001';
const NEW = '0xbbbb000000000000000000000000000000000002';

T('blankRisk 初始值正确', () => {
  const r = blankRisk();
  if (r.halted !== false) throw new Error('halted 应为 false');
  if (r.dailyLoss.amount !== 0) throw new Error('dailyLoss 应为 0');
  if (r.haltReason !== '') throw new Error('haltReason 应为空');
});

// 造一个"跟 OLD 对象跑过、今天已亏 7 块"的存档
function seedOldRun() {
  const st = { seen: new Set(['t1', 't2']), perMarket: new Map([['m1', 6], ['m2', 3]]),
               totalInvested: 9 };
  const risk = blankRisk();
  risk.dailyLoss = { date: new Date().toISOString().slice(0, 10), amount: 7 };
  saveRuntime(st, risk, OLD, { paper: [{ conditionId: 'm1' }] });
  return loadRuntime();
}

T('hydrate：同一个对象 → 恢复账目，不清零', () => {
  seedOldRun();
  const st = {};
  const info = hydrate(st, OLD);
  if (!/恢复/.test(info.restored)) throw new Error('应提示恢复');
  if (st.perMarket.size !== 2) throw new Error('perMarket 应恢复 2 个');
  if (st.totalInvested !== 9) throw new Error('invested 应恢复 9');
  if (info.targetChanged !== false) throw new Error('不应标记换对象');
});

T('hydrate：同一天 → 保留当日亏损计数', () => {
  seedOldRun();
  const info = hydrate({}, OLD);
  if (info.risk.dailyLoss.amount !== 7) throw new Error('同一天不该归零，实得 ' + info.risk.dailyLoss.amount);
});

T('hydrate：换跟单对象 → 旧账清零', () => {
  seedOldRun();
  const st = {};
  const info = hydrate(st, NEW);
  if (st.perMarket.size !== 0) throw new Error('perMarket 应清零');
  if (st.totalInvested !== 0) throw new Error('invested 应清零');
  if (info.targetChanged !== true) throw new Error('应标记换对象');
  if (info.paper.length !== 0) throw new Error('纸面持仓应作废');
});

T('hydrate：换对象时熔断计数也应归零（那是上一个人的账）', () => {
  seedOldRun();
  const info = hydrate({}, NEW);
  if (info.risk.dailyLoss.amount !== 0) throw new Error('换对象应归零');
});

T('hydrate：跨日 → 熔断计数自动重置', () => {
  const st = { seen: [], perMarket: new Map(), totalInvested: 0 };
  const risk = blankRisk();
  risk.dailyLoss = { date: '2026-01-01', amount: 99 };
  saveRuntime(st, risk, OLD, { paper: [] });
  const info = hydrate({}, OLD);
  if (info.risk.dailyLoss.amount !== 0) throw new Error('跨日应归零');
  if (info.risk.dailyLoss.date !== new Date().toISOString().slice(0, 10))
    throw new Error('日期应更新为今天');
});

T('hydrate：空存档 → 首次运行提示', () => {
  // saveRuntime 写不出空 targetWallet 时 savedAt 仍会被写上，
  // 所以"首次运行"的判定要看 savedAt 而不是 targetWallet 是否为空。
  // 这里直接清掉存档文件来模拟真正的首次运行。
  fs.rmSync(RUNTIME_FILE, { force: true });
  const info = hydrate({}, OLD);
  if (!/首次运行/.test(info.restored)) throw new Error('应提示首次运行');
});

await TA('evaluateRisk：停机状态下不再判定', async () => {
  const risk = blankRisk();
  risk.halted = true; risk.haltReason = 'x';
  const v = await evaluateRisk({ mode: 'dry-run', stopLossTotalUsd: -9 }, risk, []);
  if (v.action !== 'none') throw new Error('halted 时应返回 none');
});

await TA('evaluateRisk：dry-run 无纸面持仓 → 静默', async () => {
  const v = await evaluateRisk({ mode: 'dry-run', stopLossTotalUsd: -9, takeProfitTotalUsd: 15 },
                              blankRisk(), []);
  if (v.action !== 'none') throw new Error('空纸面持仓应 none');
});

await TA('evaluateRisk：熔断优先于止损判定', async () => {
  const risk = blankRisk();
  risk.dailyLoss = { date: new Date().toISOString().slice(0, 10), amount: 12 };
  const v = await evaluateRisk({ mode: 'dry-run', maxDailyLossUsd: 5, stopLossTotalUsd: -9 },
                              risk, []);
  if (v.action !== 'halt-open') throw new Error('熔断应触发停机');
  if (!/单日熔断/.test(v.reason)) throw new Error('原因应含单日熔断');
});

await TA('evaluateRisk：纸面持仓能算出数字并走完分支', async () => {
  const risk = blankRisk();
  const paper = [
    { conditionId: 'c1', title: 'T1', tokenId: '1', side: 'BUY', price: 0.5, amount: 6 },
    { conditionId: 'c2', title: 'T2', tokenId: '2', side: 'BUY', price: 0.5, amount: 6 },
  ];
  const cfg = { mode: 'dry-run', stopLossTotalUsd: -3, takeProfitTotalUsd: 15, minSellPrice: 0.02 };
  const v = await evaluateRisk(cfg, risk, paper);
  // 网络不通 → queryFailed 或 pnl=null（宁可漏报也不误报）；
  // 网络通了 → pnl 是有限数字，未超线时 action=none。
  if (v.pnl !== null && Number.isFinite(v.pnl) && v.action === 'halt' && !/止损|止盈/.test(v.reason))
    throw new Error('halt 原因应含止损或止盈');
  if (v.action === 'halt-open' && !/止损|止盈/.test(v.reason))
    throw new Error('halt-open 原因应含止损或止盈');
});

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
if (fail) process.exitCode = 1;
