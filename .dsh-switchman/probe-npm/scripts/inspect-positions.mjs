/** 看 /positions 到底返回哪些字段，哪些是真「已实现」。 */
import { fetchPositions } from '../src/api.mjs';

const pos = await fetchPositions('0x09e27c2cbcd98c7e821ef66b309506701dcf29de');
console.log('返回条数:', pos.length);
if (pos.length) {
  console.log('字段名:', Object.keys(pos[0]).join(', '));
  console.log('\n前 3 条完整内容:');
  for (const p of pos.slice(0, 3)) console.log(JSON.stringify(p, null, 2));
}

// 对比：看有几个明确的状态标记
const byFlag = {};
for (const p of pos) {
  const k = `redeemable=${p.redeemable} curQty=${Number(p.size ?? p.currentQty ?? 0) > 0}`;
  byFlag[k] = (byFlag[k] ?? 0) + 1;
}
console.log('\n状态分布:', JSON.stringify(byFlag));
