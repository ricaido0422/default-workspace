/**
 * 单元测试：guard.mjs 的私钥保护 + 配置校验。
 * 私钥保护是钱包安全的底线，必须每次改 guard 都跑一遍。
 */
import { maskSecrets, assertSafeConfig, assertSameOrigin, reportGuardStatus } from '../src/guard.mjs';

/** 兼容别名：guard 里叫 maskSecrets，测试和 README 里习惯叫 maskPrivateKey */
const maskPrivateKey = maskSecrets;

let pass = 0, fail = 0;
const T = (name, fn) => { try { fn(); console.log('  PASS  ' + name); pass++; }
  catch (e) { console.log('  FAIL  ' + name + ' -> ' + e.message); fail++; } };

console.log('\n=== guard.mjs 单元测试 ===\n');

T('64 位私钥被打码', () => {
  const k = '0x' + 'a'.repeat(64);
  const m = maskPrivateKey(k);
  if (m !== '0x****(私钥已隐藏)****') throw new Error('打码结果不对：' + m);
  if (m.includes('a'.repeat(8))) throw new Error('私钥内容泄漏了');
});

T('大写 0X 私钥也打码', () => {
  const k = '0X' + 'A'.repeat(64);
  if (maskPrivateKey(k) !== '0x****(私钥已隐藏)****') throw new Error('未打码');
});

T('40 位地址不被误伤', () => {
  const a = '0xbcc321b6336117432f4e9d2d28b20d8b6795ac9c';
  if (maskPrivateKey(a) !== a) throw new Error('地址不该被当成私钥');
});

T('日志文本里的私钥被抹掉', () => {
  const k = '0x' + 'f'.repeat(64);
  const line = `出错了 privateKey=${k} 请检查`;
  const m = maskPrivateKey(line);
  if (m.includes(k)) throw new Error('日志里私钥还在');
  if (!m.includes('(私钥已隐藏)')) throw new Error('没替换成提示');
});

T('多个私钥同时出现也全部抹掉', () => {
  const k1 = '0x' + '1'.repeat(64);
  const k2 = '0x' + '2'.repeat(64);
  const m = maskPrivateKey(`a=${k1} b=${k2}`);
  if (m.includes(k1) || m.includes(k2)) throw new Error('有私钥漏网');
  if ((m.match(/私钥已隐藏/g) || []).length !== 2) throw new Error('应替换两处');
});

T('非私钥字符串原样返回', () => {
  const s = '正常日志，没有秘密';
  if (maskPrivateKey(s) !== s) throw new Error('不该改动普通文本');
});

T('空/null 不炸', () => {
  maskPrivateKey(''); maskPrivateKey(null); maskPrivateKey(undefined);
});

T('assertSafeConfig：dry-run 无风险配置通过', () => {
  const r = assertSafeConfig({
    mode: 'dry-run',
    targetWallet: '0x' + 'a'.repeat(40),
    maxPerOrderUsd: 3,
    maxTotalInvestedUsd: 15,
    sizingMode: 'fixed',
  });
  if (r.length !== 0) throw new Error('不该报错：' + JSON.stringify(r));
});

T('assertSafeConfig：自跟单被拒', () => {
  const r = assertSafeConfig({
    mode: 'live',
    targetWallet: '0x' + 'a'.repeat(40),
    funderAddress: '0x' + 'A'.repeat(40),
    maxPerOrderUsd: 3, maxTotalInvestedUsd: 15,
  });
  if (!r.some(e => /跟单自己/.test(e))) throw new Error('应拒绝自跟单');
});

T('assertSafeConfig：单笔超过总仓位一半被拒', () => {
  const r = assertSafeConfig({
    mode: 'dry-run', targetWallet: '0x' + 'a'.repeat(40),
    maxPerOrderUsd: 10, maxTotalInvestedUsd: 15,
  });
  if (!r.some(e => /单笔风险过大/.test(e))) throw new Error('应拒绝单笔过大');
});

T('assertSafeConfig：live 无私钥被拒', () => {
  const r = assertSafeConfig({ mode: 'live', targetWallet: '0x' + 'a'.repeat(40) });
  if (r.ok) throw new Error('live 无私钥必须被拒');
});

T('assertSafeConfig：私钥格式错误被拒', () => {
  const r = assertSafeConfig({ mode: 'live', targetWallet: '0x' + 'a'.repeat(40), privateKey: 'not-a-key' });
  if (r.ok) throw new Error('坏私钥必须被拒');
});

T('assertSafeConfig：跟单对象格式错误被拒', () => {
  const r = assertSafeConfig({ mode: 'dry-run', targetWallet: 'garbage' });
  if (r.ok) throw new Error('坏地址必须被拒');
});

T('assertSameOrigin：Origin 匹配 + json 放行', () => {
  const r = assertSameOrigin({
    headers: { origin: 'http://127.0.0.1:8787', 'content-type': 'application/json' },
  });
  if (!r.ok) throw new Error('同源应放行：' + r.reason);
});

T('assertSameOrigin：Origin 匹配但非 json 仍拒', () => {
  // Origin 对了不代表内容类型安全——表单 POST 不触发预检，照样能打进来
  const r = assertSameOrigin({ headers: { origin: 'http://127.0.0.1:8787', 'content-type': 'text/plain' } });
  if (r.ok) throw new Error('非 json 必须被拒');
});

T('assertSameOrigin：跨站 Origin 被拒', () => {
  const r = assertSameOrigin({ headers: { origin: 'http://evil.example.com' } });
  if (r.ok) throw new Error('跨站必须被拒');
});

T('assertSameOrigin：无 Origin 时按 content-type 判定', () => {
  // 浏览器同源请求不带 Origin，此时只要求 content-type 是 json
  const r1 = assertSameOrigin({ headers: { 'content-type': 'application/json' } });
  if (!r1.ok) throw new Error('无 Origin + json 应放行：' + r1.reason);
  const r2 = assertSameOrigin({ headers: { 'content-type': 'text/plain' } });
  if (r2.ok) throw new Error('无 Origin + 非 json 必须被拒');
});

T('assertSameOrigin：无 Origin 的 json 请求放行（命令行/调试脚本）', () => {
  // 浏览器同源请求不带 Origin；命令行 curl 也不带。两者都只要求 content-type 是 json。
  // 注意：控制台只监听 127.0.0.1，能连上来的本来就只有本机进程，不存在"外网伪造"通道。
  const r = assertSameOrigin({ headers: { 'content-type': 'application/json' } });
  if (!r.ok) throw new Error('无 Origin + json 应放行：' + r.reason);
});

T('reportGuardStatus 不抛异常（只往日志打状态）', () => {
  reportGuardStatus(); // 返回 undefined，只负责打日志
});

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
if (fail) process.exitCode = 1;
