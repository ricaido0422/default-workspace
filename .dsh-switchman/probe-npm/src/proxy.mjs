/**
 * 代理适配层 —— 本项目的联网关键。
 *
 * 背景（重要，别删）：
 *   Windows 的「梯子」代理是系统级的，浏览器会自动走它，
 *   但 Node.js 的 fetch 默认完全忽略系统代理，所以浏览器能开
 *   polymarket.com、命令行脚本却连不上。这不是网络坏，是 Node 不走代理。
 *
 * 实测结论（Node v26.12.0，2026-10-24）：
 *   - HTTPS_PROXY + NODE_USE_ENV_PROXY=1 → 可用（HTTP 200）
 *   - 只设 HTTPS_PROXY 不加 NODE_USE_ENV_PROXY → ECONNRESET，不生效
 *   - 关键坑：这两个变量必须在**进程启动时**就存在于环境里。
 *     在 JS 代码运行后再 process.env.HTTPS_PROXY = ... 写入是无效的，
 *     Node 的 fetch 只在初始化时读一次，之后再改它不认。
 *   - 所以本模块探测到代理后，用 spawn 重新启动自己（spawnSync + detached），
 *     让子进程带着正确的环境变量起来，父进程退出。
 *     这就是为什么探测脚本第一次运行会打印「已重启」再出结果。
 *
 * 不能在子进程里调 reg.exe 读注册表（DSH 沙箱会 EPERM），
 * 所以用「连接本地代理端口 + CONNECT 探测」反推端口，不依赖 reg。
 */

import net from 'node:net';
import { spawnSync } from 'node:child_process';

const CANDIDATE_PORTS = [7897, 7890, 10809, 10808, 1080, 8080, 7891, 2080, 33210];

/**
 * 探测某个本地端口是否是 HTTP 代理：对它发一个 CONNECT，代理会回 200。
 * 这是只读探测，不访问任何外部业务接口。
 */
function isHttpProxy(port) {
  return new Promise((resolve) => {
    const sock = net.connect(port, '127.0.0.1');
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch {}
      resolve(ok);
    };
    sock.setTimeout(1200);
    sock.on('connect', () => {
      sock.write('CONNECT data-api.polymarket.com:443 HTTP/1.1\r\nHost: data-api.polymarket.com:443\r\n\r\n');
    });
    sock.on('data', (chunk) => {
      const s = chunk.toString('latin1');
      // 只有真的 HTTP 代理才会用 CONNECT 回 200；普通服务不会
      finish(/^HTTP\/1\.[01] 200/.test(s));
    });
    sock.on('timeout', () => finish(false));
    sock.on('error', () => finish(false));
  });
}

/**
 * 确保本进程带着代理环境变量运行。
 *
 * 因为 Node 的 fetch 只在进程初始化时读一次代理配置，代码运行中再改
 * process.env 无效，所以发现代理后必须用 spawn 重启自己。
 *
 * @param {((m:string)=>void)|{info:(m:string)=>void}} [logger] 可选的日志函数或 log 对象
 * @returns {boolean} true = 本进程已带代理，可以继续；false = 已重启，应该退出
 */
export async function ensureProxy(logger) {
  const say = typeof logger === 'function'
    ? logger
    : (typeof logger?.info === 'function' ? (m) => logger.info(m) : () => {});

  // 已经带对了，什么都不做
  const envOk = (process.env.HTTPS_PROXY || process.env.HTTP_PROXY)
    && process.env.NODE_USE_ENV_PROXY === '1'
    && process.env.__PROXY_REEXEC !== '1';
  const alreadyReexec = process.env.__PROXY_REEXEC === '1';

  if (envOk || alreadyReexec) {
    if (alreadyReexec) say(`代理已就绪：${process.env.HTTPS_PROXY || process.env.HTTP_PROXY}`);
    return true;
  }

  // 1) 环境变量里已有代理地址，只是缺 NODE_USE_ENV_PROXY → 重启
  const given = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.ALL_PROXY;
  if (given) {
    return reexec(say, given);
  }

  // 2) 扫描本地常见梯子端口
  for (const port of CANDIDATE_PORTS) {
    // eslint-disable-next-line no-await-in-loop
    const ok = await isHttpProxy(port);
    if (ok) {
      return reexec(say, `http://127.0.0.1:${port}`);
    }
  }

  say('未检测到本机代理，将尝试直连（如果一直超时，说明必须挂代理）');
  return true;
}

/** 用带代理环境变量的新进程替换当前进程。 */
function reexec(say, proxyUrl) {
  say(`检测到本机代理：${proxyUrl}`);
  say('正在用代理重启本程序（这是必需的一步，不是出错）...');
  const child = spawnSync(process.execPath, process.argv.slice(1), {
    stdio: 'inherit',
    env: {
      ...process.env,
      HTTP_PROXY: proxyUrl,
      HTTPS_PROXY: proxyUrl,
      NODE_USE_ENV_PROXY: '1',
      __PROXY_REEXEC: '1',
    },
  });
  process.exit(child.status ?? 0);
}
