// src/utils/messageTime.ts
// [v0.38] 消息时间分隔线 & 时间格式化
//
// Item 现在带 `ts?: number`（毫秒，来自 state.ts 的 eventMillis(ev)）——回放历史事件时
// 也会带上事件自己的 ts，所以隔夜分隔线能用真实时间，而不只是「本次接收时刻」。

import i18n from '../i18n';

/** 相邻消息间隔 ≥ 4 分钟（240s）才插入时间分隔线 */
export const DIVIDER_THRESHOLD_MS = 4 * 60 * 1000;

/** 归一到毫秒。无法解析 → null。 */
export function toMs(ts: number | string | undefined | null): number | null {
  if (ts == null) return null;
  if (typeof ts === 'number') return Number.isFinite(ts) ? ts : null;
  const n = Date.parse(ts);
  return Number.isNaN(n) ? null : n;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function pad2(n: number): string { return n < 10 ? `0${n}` : String(n); }

/**
 * 是否在 cur 前插入分隔线。**只在两条消息之间**——最顶上不画（prevMs==null → 不画），
 * 免得历史第一条顶一条「现在」的时间。
 */
export function shouldShowDivider(prevMs: number | null, curMs: number | null): boolean {
  if (curMs == null || prevMs == null) return false;
  return curMs - prevMs >= DIVIDER_THRESHOLD_MS;
}

/**
 * 分隔线文字：
 *   今日 → `上午/下午 h:mm`（12 小时制）
 *   隔夜 → `YYYY-MM-DD HH:mm`（24 小时制）
 */
export function formatDividerLabel(curMs: number, now: Date = new Date()): string {
  const d = new Date(curMs);
  if (sameDay(d, now)) {
    const h24 = d.getHours();
    const meridiem = h24 >= 12 ? i18n.t('message.time.02') : i18n.t('message.time.01');
    let h12 = h24 % 12;
    if (h12 === 0) h12 = 12;
    return `${meridiem} ${h12}:${pad2(d.getMinutes())}`;
  }
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} `
    + `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** 记录列表用的完整时间戳：yyyy-MM-dd HH:mm:ss */
export function formatFullTimestamp(ts: number | string | null | undefined): string {
  const ms = toMs(ts);
  if (ms == null) return '';
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} `
    + `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 截断长文本（记录摘要用） */
export function truncate(text: string, max = 80): string {
  const t = (text ?? '').replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max)}…` : t;
}
