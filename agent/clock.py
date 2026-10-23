"""A clock corrected against internet time (NTP), for "at 10:59:30 sharp" jobs.

The PC clock can drift by seconds; the Tatkal window does not. `sync()` measures the offset
once (and again whenever asked); `now()` applies it. If NTP is unreachable the offset stays
at its last value (0 at first) and the reason is kept for the logs.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

SERVERS = ("time.google.com", "pool.ntp.org", "time.windows.com", "in.pool.ntp.org")

_offset = 0.0
_synced_at: float | None = None
_source = "local clock (not synced)"


def sync(timeout: float = 3.0) -> str:
    """Measure the PC clock's error. Returns a one-line description."""
    global _offset, _synced_at, _source
    try:
        import ntplib
    except ImportError:
        _source = "local clock (ntplib not installed)"
        return _source
    client = ntplib.NTPClient()
    for server in SERVERS:
        try:
            resp = client.request(server, version=3, timeout=timeout)
        except Exception:
            continue
        _offset, _synced_at = resp.offset, time.time()
        _source = f"NTP {server}: PC clock is {abs(resp.offset):.3f}s {'behind' if resp.offset > 0 else 'ahead'}"
        return _source
    _source = f"local clock (NTP unreachable; keeping offset {_offset:+.3f}s)"
    return _source


def status() -> str:
    return _source


def offset() -> float:
    return _offset


def now_ts() -> float:
    return time.time() + _offset


def now() -> datetime:
    return datetime.fromtimestamp(now_ts())


def parse_when(text: str, base: datetime | None = None) -> datetime:
    """'10:59:30', '10:59', 'tomorrow 10:45', '2026-10-25 10:59:30', 'in 5m' / 'in 90s' / 'in 2h'.

    A bare time means its next occurrence (today if still ahead, else tomorrow).
    """
    base = base or now()
    t = text.strip().lower()
    if t.startswith("in "):
        amount, unit = t[3:].strip()[:-1], t.strip()[-1]
        secs = float(amount) * {"s": 1, "m": 60, "h": 3600}[unit]
        return base + timedelta(seconds=secs)
    day = None
    if t.startswith("tomorrow "):
        day, t = (base + timedelta(days=1)).date(), t[len("tomorrow "):]
    elif t.startswith("today "):
        day, t = base.date(), t[len("today "):]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            pass
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(t, fmt).time()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"Cannot read time {text!r}. Use 10:59:30, tomorrow 10:45, 2026-10-25 10:59:30 or in 5m.")
    if day is not None:
        return datetime.combine(day, clock)
    candidate = datetime.combine(base.date(), clock)
    return candidate if candidate > base else candidate + timedelta(days=1)


def sleep_until(target: datetime, cancelled=None) -> bool:
    """Sleep until `target` on the corrected clock, finishing with a tight spin for accuracy.
    Returns False if `cancelled()` became true first."""
    end = target.timestamp()
    while True:
        if cancelled and cancelled():
            return False
        left = end - now_ts()
        if left <= 0:
            return True
        time.sleep(min(left - 0.02, 1.0) if left > 0.05 else 0.001)
