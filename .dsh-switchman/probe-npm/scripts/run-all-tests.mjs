/**
 * 一条命令跑完所有单元测试。
 * 用法：node scripts\run-all-tests.mjs
 * 故意不用外部测试框架 —— 用户那边只装了 Node，多一个依赖就多一个跑不起来的风险。
 *
 * 注意：这里用「主进程里逐个 import」而不是 spawnSync 起子进程。
 * 原因：本仓库跑在 Windows 沙箱里，spawnSync(node) 会拿到 EPERM（具名管道被禁）。
 * 顺序执行还顺带保证输出按模块顺序排列，不会被并行刷乱。
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const files = fs.readdirSync(dir)
  .filter(f => f.startsWith('_test_') && f.endsWith('.mjs'))
  .sort();

let totalPass = 0, totalFail = 0;
const failures = [];

console.log(`\n=== 跟单机器人单元测试（${files.length} 个文件） ===\n`);

for (const f of files) {
  // 截住每个测试文件自己的 console 输出，先攒着，失败时原样打出来
  const lines = [];
  const orig = { log: console.log, warn: console.warn, error: console.error };
  const capture = (...a) => lines.push(a.map(x => String(x ?? '')).join(' '));
  console.log = capture;
  console.warn = capture;
  console.error = capture;

  let status = 0;
  try {
    await import(pathToFileURL(path.join(dir, f)).href);
  } catch (e) {
    status = 1;
    capture('抛异常：' + (e && e.stack ? e.stack : e));
  } finally {
    console.log = orig.log;
    console.warn = orig.warn;
    console.error = orig.error;
  }

  const out = lines.join('\n');
  const m = out.match(/结果：(\d+) 通过 \/ (\d+) 失败/);
  const p1 = m ? Number(m[1]) : 0;
  const f1 = m ? Number(m[2]) : 0;
  totalPass += p1; totalFail += f1;
  const bad = f1 > 0 || status !== 0;
  console.log(`${bad ? 'FAIL' : ' ok '}  ${f.padEnd(22)} ${p1} 通过 / ${f1} 失败`);
  if (bad) {
    failures.push(f);
    console.log(out.split('\n').map(l => '      ' + l).join('\n'));
  }
}

console.log(`\n总计：${totalPass} 通过 / ${totalFail} 失败`);
if (failures.length) {
  console.log(`失败文件：${failures.join(', ')}`);
  process.exitCode = 1;
} else {
  console.log('全部通过。');
}
