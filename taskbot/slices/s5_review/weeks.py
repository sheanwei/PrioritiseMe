"""Week boundaries for the review (FR-50).

A review week runs Monday 00:00 to the end of Sunday in the owner's *local*
timezone, so a decision made at 07:00 Monday in Singapore (Sunday 23:00 UTC)
belongs to the new week, as the owner experienced it.

The timezone comes from ``TASKBOT_REVIEW_TZ`` (an S5-owned key, read through
``core.config.get``). Default: ``Asia/Singapore``. It accepts an IANA name
(``Europe/London``) or a fixed offset (``+08:00``, ``UTC``).

Windows has no system tz database and ``tzdata`` is not a project dependency,
so if ``zoneinfo`` cannot resolve the name we fall back to a fixed offset for
the names we know (Singapore has been a constant UTC+8 since 1982, so this is
exact for the default). Unknown names without tz data raise ``ValueError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from taskbot.core import clock, config

TZ_ENV = "TASKBOT_REVIEW_TZ"
DEFAULT_TZ = "Asia/Singapore"

# Zones with no DST that we can represent exactly without tz data.
_FIXED_FALLBACK = {
    "asia/singapore": timedelta(hours=8),
    "utc": timedelta(0),
    "etc/utc": timedelta(0),
    "z": timedelta(0),
}
_OFFSET_RE = re.compile(r"^(?:UTC)?([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


def review_tz(name: str | None = None) -> tzinfo:
    """Resolve the review timezone (env ``TASKBOT_REVIEW_TZ`` when ``name`` is None)."""
    name = (name or config.get(TZ_ENV, DEFAULT_TZ) or DEFAULT_TZ).strip()
    m = _OFFSET_RE.match(name)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return timezone(sign * delta, name)
    if name.lower() in {"utc", "z", "etc/utc"}:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        fixed = _FIXED_FALLBACK.get(name.lower())
        if fixed is None:
            raise ValueError(
                f"{TZ_ENV}={name!r} is not a known timezone (install 'tzdata' or use an offset like +08:00)"
            ) from None
        return timezone(fixed, name)


def tz_label(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or str(tz)


@dataclass(frozen=True)
class Week:
    start: date  # Monday (local)
    tz: tzinfo

    @property
    def end(self) -> date:
        """Sunday (local), inclusive."""
        return self.start + timedelta(days=6)

    @property
    def since_iso(self) -> str:
        """Inclusive UTC lower bound: Monday 00:00 local."""
        return clock.to_iso(datetime.combine(self.start, time(0), self.tz))

    @property
    def until_iso(self) -> str:
        """Exclusive UTC upper bound: the next Monday 00:00 local."""
        return clock.to_iso(datetime.combine(self.start + timedelta(days=7), time(0), self.tz))

    def prev(self) -> Week:
        return Week(self.start - timedelta(days=7), self.tz)

    def next(self) -> Week:
        return Week(self.start + timedelta(days=7), self.tz)


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_containing(day: date | None = None, tz: tzinfo | None = None) -> Week:
    """The week containing local ``day`` (default: today, via ``clock.now()``)."""
    tz = tz or review_tz()
    if day is None:
        day = clock.now().astimezone(tz).date()
    return Week(monday_of(day), tz)
