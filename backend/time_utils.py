"""
Unified UTC timestamp formatting and parsing helpers for multi-timezone clients.

Architecture:
- Backend persistence, risk-control calendar boundaries, order numbers, and exchange
  API authentication signatures strictly use standard UTC and Unix epoch timestamps
  (`time.time()`, `wall_ms()`, `ack_ts_ms`, `created_at_ts`, `vip_expires_at`, etc.).
- Every API / WebSocket payload includes canonical epoch timestamps (`ts_ms` / `*_ts`)
  or unambiguous UTC timestamps so each online client (Hong Kong, Beijing, London,
  New York, Tokyo, etc.) independently renders times in its own device timezone without
  any cross-user timezone conflict.
"""
from datetime import datetime, timezone
from typing import Optional

TZ_UTC = timezone.utc
TZ_LABEL = "UTC"


def now_utc() -> datetime:
    """Returns the current timezone-aware datetime in UTC."""
    return datetime.now(TZ_UTC)


def today_utc_str() -> str:
    """Returns the current calendar date 'YYYY-MM-DD' in UTC."""
    return now_utc().strftime("%Y-%m-%d")


def now_utc_str() -> str:
    """Returns the current timestamp 'YYYY-MM-DD HH:MM:SS' in UTC."""
    return now_utc().strftime("%Y-%m-%d %H:%M:%S")


def now_utc_iso_str() -> str:
    """Returns the current ISO-8601 UTC timestamp 'YYYY-MM-DDTHH:MM:SSZ'."""
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc_compact_str() -> str:
    """Returns the current compact UTC timestamp 'YYYYMMDDHHMMSS' (for order IDs)."""
    return now_utc().strftime("%Y%m%d%H%M%S")


def now_utc_time_str() -> str:
    """Returns the current time 'HH:MM:SS' in UTC."""
    return now_utc().strftime("%H:%M:%S")


def now_utc_time_ms_str() -> str:
    """Returns the current millisecond time 'HH:MM:SS.mmm' in UTC."""
    dt = now_utc()
    return dt.strftime("%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"


def format_ts_utc(ts_sec: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Formats a Unix epoch timestamp in seconds as a UTC string."""
    return datetime.fromtimestamp(float(ts_sec), tz=TZ_UTC).strftime(fmt)


def format_ms_utc(ts_ms: float, fmt: str = "%H:%M:%S.%f") -> str:
    """Formats a Unix epoch timestamp in milliseconds as a UTC string.

    When `fmt == '%H:%M:%S.%f'` (default), truncates microseconds to 3-digit
    milliseconds (`HH:MM:SS.mmm`).
    """
    dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=TZ_UTC)
    if fmt == "%H:%M:%S.%f":
        return dt.strftime("%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"
    return dt.strftime(fmt)


def parse_db_timestamp_ms(ts_text: Optional[str]) -> int:
    """Parses a UTC SQLite ('YYYY-MM-DD HH:MM:SS') or ISO-8601 ('...Z') string into epoch milliseconds."""
    if not ts_text:
        return 0
    s = str(ts_text).strip()
    if len(s) < 19:
        return 0
    try:
        if "T" in s:
            norm = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(norm)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_UTC)
            return int(dt.timestamp() * 1000)
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_UTC)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0
