"""S2 triage timebox: countdown data, expiry job, notify-once, re-assess note, extend.

Only ``taskbot.core`` is used. Timeboxed items are created elsewhere (S1) by
``transition(id, "timeboxed", outcome="timebox", timebox_end=<UTC ISO>)``.

Hooks for other slices / S6:
- ``get_note(item_id)`` / ``GET /api/s2_timebox/note/{id}``: the re-assess note
  and whether one is still required before deciding (FR-23).
- ``clear_reassess(item_id, conn=...)`` / ``POST /api/s2_timebox/reassess/{id}/clear``:
  call after the re-assessed item has been decided.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from taskbot.core import clock, db, items
from taskbot.core.items import ItemNotFound
from taskbot.core.models import EventKind, Item, Status

log = logging.getLogger("taskbot.s2_timebox")

JOB_NAME = "s2_timebox.expire"
JOB_EVERY_SECONDS = 30
EXTEND_MINUTES = 15
NOTE_MAX_LEN = 4000

EVENT_EXTENDED = "timebox_extended"
EVENT_NOTE_SAVED = "reassess_noted"
EVENT_REASSESS_CLEARED = "reassess_cleared"


class NotTimeboxed(ValueError):
    """The item is not (or no longer) in ``timeboxed``."""


class NotFlagged(ValueError):
    """The item is not flagged for re-assessment."""


# --------------------------------------------------------------------------- time


def _parse(value: str | None) -> datetime | None:
    """Parse a stored timestamp; a missing offset is taken as UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def precise_iso(dt: datetime) -> str:
    """UTC ISO string with milliseconds (``clock.to_iso`` truncates to seconds)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def server_time() -> dict[str, Any]:
    now = clock.now()
    return {"server_now": precise_iso(now), "server_now_ms": int(now.timestamp() * 1000)}


# --------------------------------------------------------------------------- countdown (FR-20)


def _timebox_view(item: Item, now: datetime) -> dict[str, Any]:
    end = _parse(item.timebox_end)
    remaining = None if end is None else (end - now).total_seconds()
    return {
        "id": item.id,
        "title": item.title,
        "type": item.type,
        "priority": item.priority,
        "timebox_end": item.timebox_end,
        "timebox_end_ms": None if end is None else int(end.timestamp() * 1000),
        "remaining_seconds": remaining,
        "expired": remaining is not None and remaining <= 0,
        "decided_at": item.decided_at,
    }


def list_timeboxes() -> dict[str, Any]:
    """All ``timeboxed`` items, soonest end first, plus the server clock for skew correction."""
    now = clock.now()
    views = [_timebox_view(i, now) for i in items.list_items(Status.TIMEBOXED)]
    views.sort(key=lambda v: (v["timebox_end_ms"] is None, v["timebox_end_ms"] or 0, v["id"]))
    return {**server_time(), "items": views}


# --------------------------------------------------------------------------- expiry job (FR-21)


def expire_due() -> list[int]:
    """Move every ``timeboxed`` item whose timebox has ended back to the tray.

    Idempotent: an item is handled once per timebox because it leaves
    ``timeboxed`` in the same transaction that logs ``timebox_expired``.
    Returns the ids expired by this call. Safe to call directly in tests.
    """
    now = clock.now()
    due = [
        i.id
        for i in items.list_items(Status.TIMEBOXED, newest_first=False)
        if (end := _parse(i.timebox_end)) is not None and end <= now
    ]
    expired: list[int] = []
    for item_id in due:
        try:
            if _expire_one(item_id, now):
                expired.append(item_id)
        except Exception:  # one bad row must not block the others
            log.exception("could not expire timebox for item %s", item_id)
    if expired:
        log.info("timeboxes expired: %s", expired)
    return expired


def _expire_one(item_id: int, now: datetime) -> bool:
    with db.connect() as conn:
        # Take the write lock first, then re-check: an extend or another tick may
        # have changed the item since it was listed.
        conn.execute("BEGIN IMMEDIATE")
        item = items.get_item(item_id, conn=conn)
        if item is None or item.status != Status.TIMEBOXED:
            return False
        end = _parse(item.timebox_end)
        if end is None or end > now:
            return False
        items.transition(
            item_id,
            Status.TRAY,
            conn=conn,
            reassess=1,
            reassess_note=None,
            timebox_expired_at=clock.to_iso(now),
            expiry_notified_at=None,
        )
        items.log_event(item_id, EventKind.TIMEBOX_EXPIRED, {}, conn=conn)
        return True


# --------------------------------------------------------------------------- notify once (FR-22)


def pending_expiries() -> dict[str, Any]:
    """Expiries no dashboard has shown yet (oldest first)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, status, timebox_end, timebox_expired_at FROM items"
            " WHERE timebox_expired_at IS NOT NULL AND expiry_notified_at IS NULL"
            " ORDER BY timebox_expired_at, id"
        ).fetchall()
    return {
        **server_time(),
        "items": [
            {
                "id": r["id"],
                "title": r["title"],
                "status": r["status"],
                "timebox_end": r["timebox_end"],
                "expired_at": r["timebox_expired_at"],
            }
            for r in rows
        ],
    }


def ack_expiry(item_id: int) -> bool:
    """Claim the notification for the item's latest expiry.

    Returns True only for the first caller, so with several tabs open the
    notification still fires exactly once. Raises ``ItemNotFound``.
    """
    with db.connect() as conn:
        if items.get_item(item_id, conn=conn) is None:
            raise ItemNotFound(item_id)
        cur = conn.execute(
            "UPDATE items SET expiry_notified_at = ?"
            " WHERE id = ? AND timebox_expired_at IS NOT NULL AND expiry_notified_at IS NULL",
            (clock.now_iso(), item_id),
        )
        return cur.rowcount == 1


# --------------------------------------------------------------------------- extend (FR-24)


def extend(item_id: int, minutes: int = EXTEND_MINUTES) -> Item:
    """Add ``minutes`` to a running timebox and log ``timebox_extended {minutes}``.

    If the end has already passed but the expiry job has not run yet, the
    extension counts from now, so the user always gets the full extra time.
    """
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        item = items.get_item(item_id, conn=conn)
        if item is None:
            raise ItemNotFound(item_id)
        if item.status != Status.TIMEBOXED:
            raise NotTimeboxed(f"item {item_id} is {item.status}, not timeboxed")
        now = clock.now()
        end = _parse(item.timebox_end)
        base = now if end is None or end < now else end
        updated = items.update_fields(
            item_id, conn=conn, timebox_end=clock.to_iso(base + timedelta(minutes=minutes))
        )
        items.log_event(item_id, EVENT_EXTENDED, {"minutes": minutes}, conn=conn)
        return updated


# --------------------------------------------------------------------------- re-assess (FR-23)


def _note_view(item: Item) -> dict[str, Any]:
    flagged = bool(item.extra.get("reassess"))
    note = item.extra.get("reassess_note")
    return {
        "item_id": item.id,
        "title": item.title,
        "status": item.status,
        "reassess": flagged,
        "note": note,
        # S1 must not let the owner decide while this is true (FR-23).
        "required": flagged and not (note or "").strip(),
        "expired_at": item.extra.get("timebox_expired_at"),
    }


def list_reassess() -> list[dict[str, Any]]:
    """Tray items flagged for re-assessment, oldest expiry first."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM items WHERE reassess = 1 AND status = ?"
            " ORDER BY timebox_expired_at, id",
            (Status.TRAY.value,),
        ).fetchall()
        found = [items.get_item(r["id"], conn=conn) for r in rows]
    return [_note_view(i) for i in found if i is not None]


def get_note(item_id: int) -> dict[str, Any]:
    item = items.get_item(item_id)
    if item is None:
        raise ItemNotFound(item_id)
    return _note_view(item)


def save_note(item_id: int, note: str) -> dict[str, Any]:
    text = (note or "").strip()
    if not text:
        raise ValueError("note must not be blank")
    if len(text) > NOTE_MAX_LEN:
        raise ValueError(f"note must be at most {NOTE_MAX_LEN} characters")
    with db.connect() as conn:
        item = items.get_item(item_id, conn=conn)
        if item is None:
            raise ItemNotFound(item_id)
        if not item.extra.get("reassess"):
            raise NotFlagged(f"item {item_id} is not flagged for re-assessment")
        updated = items.update_fields(item_id, conn=conn, reassess_note=text)
        items.log_event(item_id, EVENT_NOTE_SAVED, {}, conn=conn)
        return _note_view(updated)


def clear_reassess(item_id: int, *, conn: sqlite3.Connection | None = None) -> Item:
    """Drop the re-assess flag (the note is kept for history). For S6 / S1 after deciding."""
    if conn is None:
        with db.connect() as own:
            return clear_reassess(item_id, conn=own)
    item = items.get_item(item_id, conn=conn)
    if item is None:
        raise ItemNotFound(item_id)
    if not item.extra.get("reassess"):
        return item
    updated = items.update_fields(item_id, conn=conn, reassess=0)
    items.log_event(item_id, EVENT_REASSESS_CLEARED, {}, conn=conn)
    return updated
