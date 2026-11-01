/**
 * 用官方已平仓数据复核第三方平台（如 CopyOdds）给的战绩指标。
 * 用法：node scripts\reconcile-copyodds.mjs <地址> [平台标称胜率%] [平台标称PF] [平台标称净盈亏]
 *
 * ⚠️ 前提：/closed-positions 按 realizedPnl 降序返回，必须翻到数据穷尽才准。
 *    本脚本会一直翻到底；若被上限截断会明确告诉你结果不可信。
 */
import { auditTrader } from '../src/stats.mjs';

const addr = process.argv[2];
if (!addr || !/^0x[0-9a-fA-F]{40}$/.test(addr)) {
  console.log('用法：node scripts\\reconcile-copyodds.mjs <0x地址> [标称胜率%] [标称PF] [标称净盈亏]');
  process.exit(1);
}

const claimedWin = process.argv[3] ? Number(process.argv[3]) : null;
const claimedPf = process.argv[4] ? Number(process.argv[4]) : null;
const claimedNet = process.argv[5] ? Number(process.argv[5]) : null;

console.log(`正在翻页拉取 ${addr} 的全部已平仓记录（接口按盈利降序，必须翻到底才准）…\n`);

const r = await auditTrader(addr, { maxPages: 200 });
const s = r.raw;

if (!s.total) {
  console.log('没有已平仓记录，无法复核');
  process.exit(0);
}

console.log('┌─ 我们自己算的（官方 /closed-positions 全量）');
console.log(`│  已平仓 ${s.total} 笔   胜 ${s.wins} / 负 ${s.losses}`);
console.log(`│  胜率    ${(s.winRate * 100).toFixed(1)}%`);
console.log(`│  盈亏比  ${s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)}`);
console.log(`│  净盈亏  $${s.netPnl.toFixed(2)}`);
console.log(`│  最大回撤 $${s.maxDrawdownUsd.toFixed(2)}`);
console.log(`│  样本覆盖 ${s.sampleDays} 天`);
console.log(`└  数据完整性：${r.exhausted ? '已翻到底，可信' : `被截断（${s.total} 笔），胜率虚高不可用`}`);

if (claimedWin != null || claimedPf != null || claimedNet != null) {
  const dWin = claimedWin != null ? (s.winRate * 100 - claimedWin) : null;
  const dPf = claimedPf != null ? (s.profitFactor - claimedPf) : null;
  const dNet = claimedNet != null ? (s.netPnl - claimedNet) : null;
  console.log('\n┌─ 与平台标称值对比（正数=我们算得更高）');
  if (dWin != null) console.log(`│  胜率    标称 ${claimedWin}%   实测 ${(s.winRate * 100).toFixed(1)}%   差 ${dWin > 0 ? '+' : ''}${dWin.toFixed(1)}`);
  if (dPf != null) console.log(`│  盈亏比  标称 ${claimedPf}    实测 ${s.profitFactor.toFixed(2)}    差 ${dPf > 0 ? '+' : ''}${dPf.toFixed(2)}`);
  if (dNet != null) console.log(`│  净盈亏  标称 $${claimedNet}  实测 $${s.netPnl.toFixed(0)}  差 ${dNet > 0 ? '+' : ''}$${dNet.toFixed(0)}`);
  console.log('└');
}

console.log('\n体检结论：');
r.flags.forEach((f) => console.log(`  [${f.level}] ${f.text}`));

if (r.exhausted) {
  console.log('\n判断：数据完整，以上实测值可直接作为选人依据。');
} else {
  console.log('\n判断：数据未翻到底，以上实测值偏高，不能用于选人。');
}
