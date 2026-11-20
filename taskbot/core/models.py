"""Enums, the item state machine, and plain data classes shared by all slices."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    MANUAL = "manual"
    EMAIL = "email"


class Status(StrEnum):
    TRAY = "tray"
    ACTIVE = "active"
    TIMEBOXED = "timeboxed"
    SCHEDULED = "scheduled"
    DELEGATED = "delegated"
    DECLINED = "declined"
    DONE = "done"
    CLOSED_STALE = "closed_stale"


class Outcome(StrEnum):
    DO_NOW = "do_now"
    TIMEBOX = "timebox"
    SCHEDULE = "schedule"
    DELEGATE = "delegate"
    DECLINE = "decline"


class WorkType(StrEnum):
    INCIDENT = "incident"
    INTERRUPT = "interrupt"
    TOIL = "toil"
    PLANNED = "planned"


class EventKind(StrEnum):
    """Known event kinds (PRD §4.3). Slices may log additional kinds as plain strings."""

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    SUGGESTED = "suggested"
    DECIDED = "decided"
    TIMEBOX_EXPIRED = "timebox_expired"
    RESURFACED = "resurfaced"
    EXTERNAL_ACTIVITY = "external_activity"
    STALE_SUGGESTED = "stale_suggested"
    CLOSED_STALE = "closed_stale"
    CLASSIFICATION_CORRECTED = "classification_corrected"


# The state machine. ``transition()`` rejects anything not listed here.
ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.TRAY: frozenset(
        {Status.ACTIVE, Status.TIMEBOXED, Status.SCHEDULED, Status.DELEGATED, Status.DECLINED}
    ),
    Status.TIMEBOXED: frozenset({Status.TRAY}),
    Status.SCHEDULED: frozenset({Status.TRAY, Status.CLOSED_STALE}),
    Status.ACTIVE: frozenset({Status.DONE}),
    Status.DELEGATED: frozenset({Status.DONE, Status.CLOSED_STALE}),
    Status.DECLINED: frozenset(),
    Status.DONE: frozenset(),
    Status.CLOSED_STALE: frozenset(),
}

TERMINAL_STATUSES: frozenset[Status] = frozenset(
    s for s, targets in ALLOWED_TRANSITIONS.items() if not targets
)

# Where an intake decision sends an item.
OUTCOME_TO_STATUS: dict[Outcome, Status] = {
    Outcome.DO_NOW: Status.ACTIVE,
    Outcome.TIMEBOX: Status.TIMEBOXED,
    Outcome.SCHEDULE: Status.SCHEDULED,
    Outcome.DELEGATE: Status.DELEGATED,
    Outcome.DECLINE: Status.DECLINED,
}


def is_open(status: str) -> bool:
    """An item still needs attention (not done/declined/closed)."""
    return Status(status) not in TERMINAL_STATUSES


@dataclass
class Item:
    id: int
    title: str
    source: str
    status: str
    created_at: str
    source_ref: str | None = None
    raw_context: str | None = None
    ask_card_json: str | None = None
    pressure_count: int = 0
    type: str | None = None
    outcome: str | None = None
    priority: int | None = None
    timebox_end: str | None = None
    scheduled_for: str | None = None
    delegated_to: str | None = None
    last_external_activity: str | None = None
    decided_at: str | None = None
    closed_at: str | None = None
    # Columns added by slice migrations land here, keyed by column name.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ask_card(self) -> dict[str, Any] | None:
        return json.loads(self.ask_card_json) if self.ask_card_json else None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Item:
        known = {f.name for f in fields(cls)} - {"extra"}
        data = dict(row)
        return cls(
            **{k: v for k, v in data.items() if k in known},
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form for API responses (ask_card parsed, raw json dropped)."""
        data = asdict(self)
        data.pop("ask_card_json")
        data["ask_card"] = self.ask_card
        return data


CORE_ITEM_COLUMNS: frozenset[str] = frozenset(f.name for f in fields(Item)) - {"extra"}


@dataclass
class Event:
    id: int
    item_id: int
    kind: str
    payload: dict[str, Any]
    at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Event:
        return cls(
            id=row["id"],
            item_id=row["item_id"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"] or "{}"),
            at=row["at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
