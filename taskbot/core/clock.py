"""Single source of "now". Freeze it in tests with ``set_now``.

All stored timestamps are UTC ISO-8601 strings with offset, e.g.
``2026-11-20T06:30:00+00:00``. Because every value uses the same offset,
plain string comparison orders them correctly in SQL.
"""

from __future__ import annotations

from datetime import datetime, timezone

_frozen: datetime | None = None


def now() -> datetime:
    return _frozen if _frozen is not None else datetime.now(timezone.utc)


def now_iso() -> str:
    return to_iso(now())


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def set_now(dt: datetime | None) -> None:
    """Freeze the clock (tests only). Pass ``None`` to unfreeze."""
    global _frozen
    if dt is not None and dt.tzinfo is None:
        raise ValueError("use a timezone-aware datetime")
    _frozen = dt
