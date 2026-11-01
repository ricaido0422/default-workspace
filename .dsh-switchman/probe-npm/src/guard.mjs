/**
 * 钱包安全护栏 ——「保护钱包安全」的落地点。
 *
 * 守的四类风险：
 *  1. 私钥泄漏：任何写进日志/网页的文字，只要长得像私钥就打码
 *  2. 跨站请求伪造（CSRF）：你浏览的恶意网页可以向 127.0.0.1 的控制台发请求，
 *     所以所有写操作强制要求 application/json 且拒绝带外站 Origin 的请求
 *  3. 自己跟自己：funderAddress 和 targetWallet 相同会白付手续费
 *  4. 亏损失控：单日亏损熔断 + 止损/止盈停机（判定逻辑在 runtime.mjs）
 */
import { log } from './util.mjs';

/** 私钥长这样：0x + 64 位十六进制。地址是 40 位，不会被误伤。0X 大写也接受。 */
const PRIVATE_KEY_RE = /0[xX][0-9a-fA-F]{64}/g;

/** 把文本里所有疑似私钥的串打码。日志落盘前、推给网页前都必须过这一道。 */
export function maskSecrets(text) {
  return String(text ?? '').replace(PRIVATE_KEY_RE, '0x****(私钥已隐藏)****');
}

/**
 * 启动前/保存后的安全检查。返回错误列表，空数组表示通过。
 * 与 validateConfig 的区别：validateConfig 管"格式对不对"，这里管"会不会出事"。
 */
export function assertSafeConfig(cfg) {
  const errors = [];

  // 自己跟自己：不会赚钱，只会白白付两边的手续费
  if (
    cfg.targetWallet &&
    cfg.funderAddress &&
    String(cfg.targetWallet).toLowerCase() === String(cfg.funderAddress).toLowerCase()
  ) {
    errors.push('targetWallet 和 funderAddress 是同一个地址 —— 你在跟单自己，只会白付手续费');
  }

  // 单笔仓位不能吃掉总仓位的一大半，否则一笔判断失误就满盘皆输
  if (cfg.maxPerOrderUsd > cfg.maxTotalInvestedUsd * 0.5) {
    errors.push(
      `maxPerOrderUsd(${cfg.maxPerOrderUsd}) 超过 maxTotalInvestedUsd(${cfg.maxTotalInvestedUsd}) 的一半，` +
      '单笔风险过大，等于把命运押在一笔上',
    );
  }

  // 下限不能比单笔金额还高，否则永远跟不到（不是安全问题，但用户会一头雾水）
  if (cfg.sizingMode === 'percent' && cfg.percentScale > 0 && cfg.minTradeUsd > 0) {
    const smallest = cfg.minTradeUsd * cfg.percentScale;
    if (smallest < 1) {
      errors.push(
        `percent 模式下，对方按下限 ${cfg.minTradeUsd} 下单时你只能跟 ` +
        `$${smallest.toFixed(2)}，低于 Polymarket 的 $1 最小下单额 —— 这类单会全部失败。` +
        '请把 percentScale 调大，或改用 fixed 模式。',
      );
    }
  }

  return errors;
}

/**
 * 已实现亏损 / 单日熔断现在统一由 runtime.mjs 负责（那边管着 risk 账本），
 * 这里保留跳转说明，避免两份实现各记一套账导致口径不一致。
 *
 * @see runtime.mjs 的 recordDailyLoss / checkDailyLoss
 */

/**
 * CSRF 防护：浏览器里打开的恶意网页可以偷偷向 http://127.0.0.1:8787 发请求。
 * 表单形式（text/plain）的 POST 不会触发 CORS 预检，能直接打到本机端口，
 * 所以必须自己校验：① 必须是 application/json ② Origin 必须是本机。
 */
export function assertSameOrigin(req) {
  const origin = req.headers?.origin;
  // Origin 是浏览器强制带、无法被 JS 伪造的头，是这里唯一可信的判据。
  // Sec-Fetch-Site: none 只用于放行"用户直接发起"的请求（命令行 curl、调试脚本），
  // 但即使它显示 none，只要带了外站 Origin 就仍然拒——伪造头很容易，Origin 才是硬证据。
  if (origin) {
    const ok = /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(origin.trim());
    if (!ok) return { ok: false, reason: `拒绝来自 ${origin} 的跨站请求` };
  }
  const ct = String(req.headers?.['content-type'] ?? '');
  if (!ct.toLowerCase().includes('application/json')) {
    return { ok: false, reason: '写操作必须是 application/json 请求（防跨站伪造）' };
  }
  return { ok: true };
}

/** 启动时自检，把结论打到日志里，让用户一眼看到"护栏是开着的"。 */
export function reportGuardStatus() {
  log.ok('钱包护栏已启用：私钥打码 / 跨站请求拦截 / 自跟单拦截 / 单日亏损熔断');
}
