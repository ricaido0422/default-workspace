/**
 * 网页控制台启动入口 —— 双击「3-打开控制台.bat」实际跑的就是这里。
 *
 * 顺序很重要：ensureProxy 必须在最前面。
 * 它会检测本机代理并用带代理的新进程重启自己，
 * 如果放在 import 服务器之后，服务器会先起来、然后进程被重启，
 * 端口就被占住了，用户会看到 EADDRINUSE。
 */
import { log, loadConfig, usd, shortAddr } from './util.mjs';
import { ensureProxy } from './proxy.mjs';

await ensureProxy(log);

const { startServer } = await import('./server.mjs');

const cfg = loadConfig();
const { url, port } = await startServer(Number(process.env.PORT) || 8787);

log.info('========================================');
log.info('  Polymarket 跟单控制台已启动');
log.info(`  在浏览器打开：${url}`);
log.info(`  当前模式：${cfg.mode === 'live' ? 'LIVE 真实下单' : 'DRY-RUN 只观察不下单'}`);
log.info(`  跟单对象：${shortAddr(cfg.targetWallet)}`);
log.info(`  每笔金额：${usd(cfg.fixedOrderSizeUsd)}  下限 ${usd(cfg.minTradeUsd)}`);
log.info('========================================');
log.info('  挂着这个黑窗口别关，关了控制台就停了。');
log.info('  要停止：在这个窗口按 Ctrl+C。');
log.info('========================================');

// 顺手帮用户把浏览器打开，省一步操作
try {
  const { exec } = await import('node:child_process');
  // Windows 用 start，macOS 用 open，Linux 用 xdg-open
  const cmd = process.platform === 'win32' ? `start "" "${url}"`
            : process.platform === 'darwin' ? `open "${url}"`
            : `xdg-open "${url}"`;
  exec(cmd, () => {});
  log.info('  已尝试自动打开浏览器；没弹出来就手动复制上面的地址。');
} catch {
  log.info('  自动打开浏览器失败，请手动复制上面的地址到浏览器。');
}
