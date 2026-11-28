"""
Unified UTC+8 (Asia/Shanghai / Beijing Time) formatting helpers for the platform.

IMPORTANT:
- Internal Unix epoch timestamps (`time.time()`, `wall_ms()`, `ack_ts_ms`, `vip_expires_at`, etc.)
  ALWAYS remain standard UTC Unix epoch seconds / milliseconds.
- External exchange API authentication signatures (e.g., OKX `OK-ACCESS-TIMESTAMP` ISO-8601 UTC `Z`,
  Binance `timestamp`) ALWAYS remain strict UTC and MUST NOT use UTC+8.
- All user-facing formatted timestamps, daily PnL calendar date boundaries, order numbers,
  and persisted human-readable timestamp strings use UTC+8 (`TZ_UTC8`).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

TZ_UTC8 = timezone(timedelta(hours=8))
TZ_LABEL = "UTC+8"


def now_utc8() -> datetime:
    """Returns the current timezone-aware datetime in UTC+8."""
    return datetime.now(TZ_UTC8)


def today_utc8_str() -> str:
    """Returns the current calendar date 'YYYY-MM-DD' in UTC+8."""
    return now_utc8().strftime("%Y-%m-%d")


def now_utc8_str() -> str:
    """Returns the current timestamp 'YYYY-MM-DD HH:MM:SS' in UTC+8."""
    return now_utc8().strftime("%Y-%m-%d %H:%M:%S")


def now_utc8_compact_str() -> str:
    """Returns the current compact timestamp 'YYYYMMDDHHMMSS' in UTC+8."""
    return now_utc8().strftime("%Y%m%d%H%M%S")


def now_utc8_time_str() -> str:
    """Returns the current time 'HH:MM:SS' in UTC+8."""
    return now_utc8().strftime("%H:%M:%S")


def now_utc8_time_ms_str() -> str:
    """Returns the current millisecond time 'HH:MM:SS.mmm' in UTC+8."""
    dt = now_utc8()
    return dt.strftime("%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"


def format_ts_utc8(ts_sec: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Formats a Unix epoch timestamp in seconds as a UTC+8 string."""
    return datetime.fromtimestamp(float(ts_sec), tz=TZ_UTC8).strftime(fmt)


def format_ms_utc8(ts_ms: float, fmt: str = "%H:%M:%S.%f") -> str:
    """Formats a Unix epoch timestamp in milliseconds as a UTC+8 string.

    When `fmt == '%H:%M:%S.%f'` (default), truncates microseconds to 3-digit
    milliseconds (`HH:MM:SS.mmm`).
    """
    dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=TZ_UTC8)
    if fmt == "%H:%M:%S.%f":
        return dt.strftime("%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"
    return dt.strftime(fmt)


def parse_db_timestamp_ms(ts_text: Optional[str], assume_utc8: bool = True) -> int:
    """Parses a 'YYYY-MM-DD HH:MM:SS' SQLite timestamp string into epoch milliseconds."""
    if not ts_text:
        return 0
    s = str(ts_text).strip()[:19]
    if len(s) < 19:
        return 0
    try:
        tz = TZ_UTC8 if assume_utc8 else timezone.utc
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0
