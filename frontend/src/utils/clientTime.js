/**
 * Client-local timezone formatting helpers.
 *
 * All timestamps from the backend are canonical UTC (Unix epoch ms/seconds,
 * ISO-8601 `...Z`, or UTC SQL `YYYY-MM-DD HH:MM:SS`). These helpers convert
 * them into the viewer's own device/browser timezone (e.g., Asia/Hong_Kong,
 * Asia/Shanghai, Europe/London with automatic DST, America/New_York, Asia/Kolkata),
 * allowing global users in different timezones to be online simultaneously without
 * any timezone interference.
 */

const pad2 = (v) => String(v).padStart(2, '0');
const pad3 = (v) => String(v).padStart(3, '0');

const BARE_TIME_RE = /^\d{2}:\d{2}:\d{2}(\.\d{1,3})?$/;
const SQL_UTC_DATETIME_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseToDate(input) {
  if (input === null || input === undefined || input === '') return null;
  if (input instanceof Date) {
    return Number.isNaN(input.getTime()) ? null : input;
  }
  if (typeof input === 'number') {
    if (!Number.isFinite(input) || input <= 0) return null;
    const ms = input >= 1e11 ? input : input * 1000;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const str = String(input).trim();
  if (!str) return null;
  if (/^\d+(\.\d+)?$/.test(str)) {
    const n = Number(str);
    if (!Number.isFinite(n) || n <= 0) return null;
    const ms = n >= 1e11 ? n : n * 1000;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (SQL_UTC_DATETIME_RE.test(str)) {
    const iso = str.replace(' ', 'T') + 'Z';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(str);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Returns the browser's current UTC offset label, e.g. 'UTC+8', 'UTC+0', 'UTC+1', 'UTC-5', 'UTC+5:30'.
 */
export function getClientTimezoneOffsetLabel(date = new Date()) {
  const d = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date();
  const offsetMin = -d.getTimezoneOffset();
  if (offsetMin === 0) return 'UTC+0';
  const sign = offsetMin > 0 ? '+' : '-';
  const absMin = Math.abs(offsetMin);
  const hh = Math.floor(absMin / 60);
  const mm = absMin % 60;
  return mm === 0 ? `UTC${sign}${hh}` : `UTC${sign}${hh}:${pad2(mm)}`;
}

/**
 * Returns the browser's IANA timezone name, e.g. 'Asia/Hong_Kong', 'Asia/Shanghai', 'Europe/London'.
 */
export function getClientTimezoneName() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

export function getClientTimezoneTooltip() {
  const tzName = getClientTimezoneName();
  const offsetLabel = getClientTimezoneOffsetLabel();
  return tzName ? `跟随本机时区: ${tzName} (${offsetLabel})` : `跟随本机时区 (${offsetLabel})`;
}

/**
 * Formats an epoch ms / UTC input as local 'HH:MM:SS.mmm'.
 */
export function formatLocalTimeMs(input, fallback = '') {
  const d = parseToDate(input);
  if (!d) return fallback || '';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.${pad3(d.getMilliseconds())}`;
}

/**
 * Formats an epoch / ISO UTC input as local 'HH:MM:SS'.
 * Legacy bare 'HH:MM:SS' strings are preserved as-is.
 */
export function formatLocalTime(input, fallback = '') {
  if (typeof input === 'string' && BARE_TIME_RE.test(input.trim())) {
    return input.trim();
  }
  const d = parseToDate(input);
  if (!d) return fallback || '';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/**
 * Formats an epoch / UTC input as local 'MM-DD HH:MM:SS'.
 */
export function formatLocalMonthDayTime(input, fallback = '') {
  const d = parseToDate(input);
  if (!d) return fallback || '';
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/**
 * Formats an epoch / UTC input as local 'YYYY-MM-DD HH:MM:SS' (or 'YYYY-MM-DD HH:MM').
 */
export function formatLocalDateTime(input, includeSeconds = true, fallback = '') {
  const d = parseToDate(input);
  if (!d) return fallback || (typeof input === 'string' ? input : '');
  const base = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return includeSeconds ? `${base}:${pad2(d.getSeconds())}` : base;
}

/**
 * Formats an hourly chart timestamp as local 'MM-DD HH:mm' or 'HH:mm'.
 * Uses actual local minutes so half-hour timezones (e.g. UTC+5:30) display accurately.
 */
export function formatLocalHourLabel(tsMs, includeDate = true) {
  const d = parseToDate(tsMs);
  if (!d) return '--';
  const mm = pad2(d.getMonth() + 1);
  const dd = pad2(d.getDate());
  const hh = pad2(d.getHours());
  const mi = pad2(d.getMinutes());
  return includeDate ? `${mm}-${dd} ${hh}:${mi}` : `${hh}:${mi}`;
}

/**
 * Formats a user's VIP expiration timestamp (`vip_expires_at` epoch seconds) in client local time.
 */
export function formatLocalVipExpiry(user) {
  if (!user) return '未开通';
  if (user.role === 'admin') return '永久有效';
  const expSec = Number(user.vip_expires_at || 0);
  if (!Number.isFinite(expSec) || expSec <= 0) return '未开通';
  const nowSec = Math.floor(Date.now() / 1000);
  if (expSec > nowSec + 3650 * 86400) return '永久有效';
  const includeSeconds = expSec > nowSec && expSec - nowSec < 86400;
  return formatLocalDateTime(expSec * 1000, includeSeconds, user.vip_expires_str || '');
}
