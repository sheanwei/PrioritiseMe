"""S3 — resurfacing scheduled items and offering to close silent deferred ones.

"Today" is the owner's *local* date. Every function that needs it takes a
``tz`` argument; ``None`` means ``DEFAULT_TZ``, which itself defaults to the
machine's local zone. Tests pin ``DEFAULT_TZ`` (or pass ``tz``) so results do
not depend on where the suite runs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from taskbot.core import clock, db, items
from taskbot.core.models import EventKind, Item, Status

STALE_AFTER_WORKING_DAYS = 5
STALE_STATUSES = (Status.SCHEDULED, Status.DELEGATED)
STALE_KEPT = "stale_kept"  # slice-specific event kind: owner chose "Keep"

# None = the machine's local timezone. Tests override this.
DEFAULT_TZ: tzinfo | None = None


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


# --------------------------------------------------------------------------- dates


def working_days_between(a: date | datetime, b: date | datetime) -> int:
    """Monday-Friday dates strictly after ``a``'s date, up to and including ``b``'s.

    Pure function. Datetimes are reduced to their own (already-localised) date.
    Returns 0 when ``b`` is on or before ``a`` (no working days have elapsed).

    Fri 2026-11-13 -> Fri 2026-11-20 is 5; Fri -> Sat or Sun is 0; Fri -> Mon is 1.
    """
    start = a.date() if isinstance(a, datetime) else a
    end = b.date() if isinstance(b, datetime) else b
    total = (end - start).days
    if total <= 0:
        return 0
    full_weeks, rest = divmod(total, 7)
    count = full_weeks * 5
    weekday = start.weekday()
    for offset in range(1, rest + 1):
        if (weekday + offset) % 7 < 5:
            count += 1
    return count


def _tz(tz: tzinfo | None) -> tzinfo | None:
    return tz if tz is not None else DEFAULT_TZ


def _local(dt: datetime, tz: tzinfo | None) -> datetime:
    return dt.astimezone(_tz(tz))  # astimezone(None) = machine local zone


def local_now(tz: tzinfo | None = None) -> datetime:
    return _local(clock.now(), tz)


def local_today(tz: tzinfo | None = None) -> date:
    return local_now(tz).date()


def _local_midnight(day: date, tz: tzinfo | None) -> datetime:
    zone = _tz(tz)
    naive = datetime.combine(day, time())
    return naive.astimezone() if zone is None else naive.replace(tzinfo=zone)


def local_day_bounds_utc(day: date, tz: tzinfo | None = None) -> tuple[str, str]:
    """``[start, end)`` of a local calendar day as UTC ISO strings (for event queries)."""
    start = _local_midnight(day, tz)
    end = _local_midnight(day + timedelta(days=1), tz)
    return clock.to_iso(start), clock.to_iso(end)


@contextmanager
def _write_tx() -> Iterator[sqlite3.Connection]:
    """A connection holding the write lock, so check-then-write is race-free."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


# --------------------------------------------------------------------- resurfacing


def resurface_due(*, tz: tzinfo | None = None) -> list[int]:
    """FR-30: ``scheduled`` items whose date has arrived go back to the tray.

    Idempotent: an item resurfaces once, because it leaves ``scheduled``.
    Returns the ids moved by this call.
    """
    today = local_today(tz).isoformat()
    moved: list[int] = []
    with _write_tx() as conn:
        rows = conn.execute(
            "SELECT id FROM items WHERE status = ? AND scheduled_for IS NOT NULL"
            " AND substr(scheduled_for, 1, 10) <= ? ORDER BY scheduled_for, id",
            (Status.SCHEDULED.value, today),
        ).fetchall()
        for row in rows:
            items.transition(row["id"], Status.TRAY, conn=conn)
            items.log_event(row["id"], EventKind.RESURFACED, {}, conn=conn)
            moved.append(row["id"])
    return moved


def resurfaced_today(*, tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """FR-31: items with a ``resurfaced`` event during the local today, newest first."""
    since, until = local_day_bounds_utc(local_today(tz), tz)
    latest: dict[int, str] = {}
    for ev in items.list_events(kinds=[EventKind.RESURFACED], since=since, until=until):
        latest[ev.item_id] = ev.at
    result = []
    for item_id, at in sorted(latest.items(), key=lambda kv: (kv[1], kv[0]), reverse=True):
        item = items.get_item(item_id)
        if item is not None:
            result.append({**item.to_dict(), "resurfaced_at": at})
    return result


# ----------------------------------------------------------------------- staleness


@dataclass(frozen=True)
class StaleState:
    """Row of ``s3_stale_state`` (all None when the item has none yet)."""

    reset_at: str | None = None
    suggested_at: str | None = None


@dataclass(frozen=True)
class Staleness:
    clock_start: datetime  # UTC
    silent_days: int

    @property
    def is_stale(self) -> bool:
        return self.silent_days >= STALE_AFTER_WORKING_DAYS


def get_state(item_id: int, *, conn: sqlite3.Connection) -> StaleState:
    row = conn.execute(
        "SELECT reset_at, suggested_at FROM s3_stale_state WHERE item_id = ?", (item_id,)
    ).fetchone()
    return StaleState(row["reset_at"], row["suggested_at"]) if row else StaleState()


def _put_state(conn: sqlite3.Connection, item_id: int, state: StaleState) -> None:
    conn.execute(
        "INSERT INTO s3_stale_state (item_id, reset_at, suggested_at) VALUES (?, ?, ?)"
        " ON CONFLICT (item_id) DO UPDATE SET"
        " reset_at = excluded.reset_at, suggested_at = excluded.suggested_at",
        (item_id, state.reset_at, state.suggested_at),
    )


def clock_start(item: Item, state: StaleState) -> datetime:
    """Later of decided_at, last_external_activity and the Keep reset (FR-32/33).

    Falls back to ``created_at`` for an item that somehow has none of them.
    """
    stamps = [item.decided_at, item.last_external_activity, state.reset_at]
    parsed = [clock.parse_iso(s) for s in stamps if s]
    return max(parsed) if parsed else clock.parse_iso(item.created_at)


def staleness(item: Item, state: StaleState, *, tz: tzinfo | None = None) -> Staleness:
    start = clock_start(item, state)
    days = working_days_between(_local(start, tz), local_now(tz))
    return Staleness(start, days)


def _candidates(
    tz: tzinfo | None, conn: sqlite3.Connection
) -> list[tuple[Item, StaleState, Staleness]]:
    found = []
    for item in items.list_items(list(STALE_STATUSES), newest_first=False, conn=conn):
        state = get_state(item.id, conn=conn)
        st = staleness(item, state, tz=tz)
        if st.is_stale:
            found.append((item, state, st))
    return found


def _suggested_this_clock(state: StaleState, st: Staleness) -> bool:
    return state.suggested_at is not None and clock.parse_iso(state.suggested_at) >= st.clock_start


def scan_stale(*, tz: tzinfo | None = None) -> list[int]:
    """FR-32: emit ``stale_suggested {silent_days}`` once per staleness clock.

    The suggestion is recorded in ``s3_stale_state.suggested_at``. It counts for
    the current clock only if it is not older than the clock start, so a new
    decision, new external activity or a Keep re-arms it. Idempotent.
    Note: a future ``scheduled_for`` does not exempt an item; requester silence
    is the signal.
    """
    now = clock.now_iso()
    suggested: list[int] = []
    with _write_tx() as conn:
        for item, state, st in _candidates(tz, conn):
            if _suggested_this_clock(state, st):
                continue
            _put_state(conn, item.id, StaleState(state.reset_at, now))
            items.log_event(
                item.id, EventKind.STALE_SUGGESTED, {"silent_days": st.silent_days}, conn=conn
            )
            suggested.append(item.id)
    return suggested


def _row(item: Item, state: StaleState) -> dict[str, Any]:
    return {**item.to_dict(), "stale_reset_at": state.reset_at, "stale_suggested_at": state.suggested_at}


def list_candidates(*, tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """FR-33: the "Probably not needed" list, most silent first."""
    with db.connect() as conn:
        rows = [
            {**_row(item, state), "silent_days": st.silent_days,
             "silent_since": clock.to_iso(st.clock_start)}
            for item, state, st in _candidates(tz, conn)
        ]
    rows.sort(key=lambda r: (-r["silent_days"], r["id"]))
    return rows


def _load_open(conn: sqlite3.Connection, item_id: int) -> tuple[Item, StaleState]:
    item = items.get_item(item_id, conn=conn)
    if item is None:
        raise NotFound(item_id)
    if item.status not in STALE_STATUSES:
        raise Conflict(f"item {item_id} is {item.status}, not scheduled or delegated")
    return item, get_state(item_id, conn=conn)


def close_stale(item_id: int, *, tz: tzinfo | None = None) -> dict[str, Any]:
    """FR-33 Close: ``closed_stale`` status plus a ``closed_stale {silent_days}`` event."""
    with _write_tx() as conn:
        item, state = _load_open(conn, item_id)
        st = staleness(item, state, tz=tz)
        if not st.is_stale:
            raise Conflict(
                f"item {item_id} has only {st.silent_days} silent working day(s); not stale"
            )
        closed = items.transition(item_id, Status.CLOSED_STALE, conn=conn)
        items.log_event(item_id, EventKind.CLOSED_STALE, {"silent_days": st.silent_days}, conn=conn)
        return _row(closed, state)


def keep(item_id: int, *, tz: tzinfo | None = None) -> dict[str, Any]:
    """FR-33 Keep: restart the staleness clock now and clear the suggestion."""
    with _write_tx() as conn:
        item, state = _load_open(conn, item_id)
        st = staleness(item, state, tz=tz)
        new_state = StaleState(reset_at=clock.now_iso(), suggested_at=None)
        _put_state(conn, item_id, new_state)
        items.log_event(item_id, STALE_KEPT, {"silent_days": st.silent_days}, conn=conn)
        return _row(item, new_state)


# ----------------------------------------------------------------------------- job


def tick() -> dict[str, list[int]]:
    """The background job: resurface due items, then flag stale ones."""
    return {"resurfaced": resurface_due(), "stale_suggested": scan_stale()}
