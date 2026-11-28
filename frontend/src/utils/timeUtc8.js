/**
 * Unified UTC+8 (Asia/Shanghai / Beijing Time) formatting helpers.
 * Always displays UTC+8 regardless of the user's browser or OS timezone.
 */
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;

const pad2 = (v) => String(v).padStart(2, '0');

function toUtc8Date(tsMs) {
  const n = Number(tsMs);
  if (!Number.isFinite(n) || n <= 0) return null;
  const d = new Date(n + UTC8_OFFSET_MS);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatUtc8MonthDayTime(tsMs) {
  const d = toUtc8Date(tsMs);
  if (!d) return '';
  return `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

export function formatUtc8DateTime(tsMs, includeSeconds = true) {
  const d = toUtc8Date(tsMs);
  if (!d) return '';
  const base = `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
  return includeSeconds ? `${base}:${pad2(d.getUTCSeconds())}` : base;
}

export function formatUtc8HourLabel(tsMs, includeDate = true) {
  const d = toUtc8Date(tsMs);
  if (!d) return '--';
  const mm = pad2(d.getUTCMonth() + 1);
  const dd = pad2(d.getUTCDate());
  const hh = pad2(d.getUTCHours());
  return includeDate ? `${mm}-${dd} ${hh}:00` : `${hh}:00`;
}
