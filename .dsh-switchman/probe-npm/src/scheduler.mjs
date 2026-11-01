/**
 * 轮询调度：什么时候该等、等多久、连续失败怎么办。
 *
 * 之前的做法：无论成功失败都固定 sleep(pollIntervalSeconds)。
 * 问题：代理断掉 / gamma 限流时，程序会按 60 秒节奏无限撞墙，
 *       日志刷屏但一行有用的都没有，用户以为"在跑"，其实一直在失败。
 *
 * 现在：
 *   - 失败按 1x → 2x → 4x → …→ 上限 退避，给网络恢复的时间
 *   - 连续失败到阈值就自动停机，而不是无限重试
 *   - 成功一次立刻回到正常节奏
 */

/** 默认退避参数。 */
export const BACKOFF = {
  baseSeconds: 60,
  maxSeconds: 900,     // 最长等 15 分钟
  maxConsecutiveFail: 6, // 连续失败 6 次（约 30 分钟内）就停机
};

/**
 * 计算下一次轮询的等待秒数。
 * @param {number} consecutiveFail 当前连续失败次数
 * @param {number} pollIntervalSeconds 正常节奏
 */
export function nextDelaySeconds(consecutiveFail, pollIntervalSeconds, backoff = BACKOFF) {
  const base = Math.max(1, Number(pollIntervalSeconds ?? 60));
  if (!(consecutiveFail > 0)) return base;
  const mult = 2 ** Math.min(consecutiveFail - 1, 6); // 最多 64 倍
  return Math.min(base * mult, Number(backoff.maxSeconds ?? 900));
}

/**
 * 是否应该因为连续失败而停机。
 * 停机而不是无限重试的理由：跟单错过了最好的入场窗口比错过单本身更糟，
 * 一直失败却显示"运行中"会让用户以为一切正常。
 */
export function shouldAutoStop(consecutiveFail, backoff = BACKOFF) {
  return consecutiveFail >= Number(backoff.maxConsecutiveFail ?? 6);
}

/** 给人看的倒计时用：下一次轮询的时间点。 */
export function nextRunAt(seconds) {
  return new Date(Date.now() + Number(seconds ?? 0) * 1000).toISOString();
}
