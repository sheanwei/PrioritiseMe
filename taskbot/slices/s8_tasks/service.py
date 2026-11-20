"""S8 "My tasks": open, triaged items grouped by status, plus "mark done".

Groups (in display order): Doing now (active), Timeboxed, Scheduled, Delegated.
Inside a group: priority first (P1 first, unset last), then the date that
matters for that group — timebox end, schedule date, or decision time
(oldest first for doing-now and delegated work).
"""

from __future__ import annotations

from typing import Any

from taskbot.core import clock, db, items
from taskbot.core.models import Item, Status

GROUPS: list[tuple[Status, str]] = [
    (Status.ACTIVE, "Doing now"),
    (Status.TIMEBOXED, "Timeboxed"),
    (Status.SCHEDULED, "Scheduled"),
    (Status.DELEGATED, "Delegated"),
]
DONE_FROM = frozenset({Status.ACTIVE, Status.DELEGATED})
EVENT_COMPLETED = "completed"
_UNSET_PRIORITY = 9
_FAR_FUTURE = "9999"


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def _date_key(item: Item) -> str:
    status = Status(item.status)
    if status is Status.TIMEBOXED:
        return item.timebox_end or _FAR_FUTURE
    if status is Status.SCHEDULED:
        return item.scheduled_for or _FAR_FUTURE
    return item.decided_at or item.created_at


def sort_key(item: Item) -> tuple[int, str, int]:
    priority = item.priority if item.priority is not None else _UNSET_PRIORITY
    return (priority, _date_key(item), item.id)


def _row(item: Item) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "status": item.status,
        "type": item.type,
        "priority": item.priority,
        "source": item.source,
        "timebox_end": item.timebox_end,
        "scheduled_for": item.scheduled_for,
        "delegated_to": item.delegated_to,
        "decided_at": item.decided_at,
        "can_mark_done": Status(item.status) in DONE_FROM,
    }


def open_tasks() -> dict[str, Any]:
    """All open, triaged tasks, grouped and sorted. Empty groups are kept (count 0)."""
    found = items.list_items([s for s, _ in GROUPS])
    groups = []
    for status, label in GROUPS:
        members = sorted((i for i in found if i.status == status.value), key=sort_key)
        groups.append({"status": status.value, "label": label, "count": len(members),
                       "items": [_row(i) for i in members]})
    return {"total": len(found), "groups": groups, "server_now": clock.now_iso()}


def mark_done(item_id: int) -> dict[str, Any]:
    """Doing-now or delegated -> done, with a ``completed`` event."""
    with db.connect() as conn:
        item = items.get_item(item_id, conn=conn)
        if item is None:
            raise NotFound(item_id)
        if Status(item.status) not in DONE_FROM:
            raise Conflict(f"item {item_id} is {item.status}; only doing-now or delegated tasks can be marked done")
        updated = items.transition(item_id, Status.DONE, conn=conn)
        items.log_event(item_id, EVENT_COMPLETED, {"from": item.status}, conn=conn)
    return _row(updated)
