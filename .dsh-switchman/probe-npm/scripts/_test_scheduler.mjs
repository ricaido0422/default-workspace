/**
 * 单元测试：scheduler.mjs 的退避与自动停机。
 */
import { nextDelaySeconds, shouldAutoStop, nextRunAt, BACKOFF } from '../src/scheduler.mjs';

let pass = 0, fail = 0;
const T = (name, fn) => { try { fn(); console.log('  PASS  ' + name); pass++; }
  catch (e) { console.log('  FAIL  ' + name + ' -> ' + e.message); fail++; } };

console.log('\n=== scheduler.mjs 单元测试 ===\n');

T('0 次失败 → 按正常间隔', () => {
  if (nextDelaySeconds(0, 60) !== 60) throw new Error('应为 60');
});

T('1 次失败 → 1 倍（第一次立刻重试，不惩罚用户）', () => {
  if (nextDelaySeconds(1, 60) !== 60) throw new Error('应为 60');
});

T('2 次失败 → 2 倍', () => {
  if (nextDelaySeconds(2, 60) !== 120) throw new Error('应为 120');
});

T('3 次失败 → 4 倍', () => {
  if (nextDelaySeconds(3, 60) !== 240) throw new Error('应为 240');
});

T('4 次失败 → 8 倍 = 480', () => {
  if (nextDelaySeconds(4, 60) !== 480) throw new Error('应为 480');
});

T('5 次失败 → 16 倍 = 960，但封顶 900', () => {
  if (nextDelaySeconds(5, 60) !== 900) throw new Error('应封顶 900');
});

T('10 次失败 → 仍封顶 900', () => {
  if (nextDelaySeconds(10, 60) !== 900) throw new Error('应封顶 900');
});

T('退避是单调不减的', () => {
  let prev = 0;
  for (let i = 0; i <= 12; i++) {
    const d = nextDelaySeconds(i, 60);
    if (d < prev) throw new Error(`第 ${i} 次退到了 ${d}，比上次 ${prev} 还小`);
    prev = d;
  }
});

T('不同正常间隔也按同样倍数', () => {
  if (nextDelaySeconds(1, 30) !== 30) throw new Error('30s 间隔 1 次失败应 30');
  if (nextDelaySeconds(2, 120) !== 240) throw new Error('120s 间隔 2 次失败应 240');
});

T('未达上限不自动停机', () => {
  for (let i = 0; i < BACKOFF.maxConsecutiveFail - 1; i++) {
    if (shouldAutoStop(i)) throw new Error(`第 ${i} 次失败就停机了，太早`);
  }
});

T('达到上限自动停机', () => {
  if (!shouldAutoStop(BACKOFF.maxConsecutiveFail)) throw new Error('达到上限应停机');
  if (!shouldAutoStop(99)) throw new Error('超过上限也应停机');
});

T('nextRunAt 返回未来时间点', () => {
  const iso = nextRunAt(60);
  const t = new Date(iso).getTime();
  if (!(t > Date.now())) throw new Error('应在未来');
  if (t > Date.now() + 61000) throw new Error('超出预期太多');
});

T('nextRunAt(0) 是现在或稍后', () => {
  const t = new Date(nextRunAt(0)).getTime();
  if (t < Date.now() - 1000) throw new Error('不该在过去');
});

T('退避上限 >= 自动停机阈值所需的最后一段等待', () => {
  // 第 maxConsecutiveFail 次失败时的等待时长，必须 <= 封顶值
  const last = nextDelaySeconds(BACKOFF.maxConsecutiveFail - 1, 60);
  if (last > BACKOFF.maxSeconds) throw new Error('最后一次等待超过封顶值');
});

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
if (fail) process.exitCode = 1;
