"""Seed helpers so slices can test without each other's UI (FR-07).

    from tests.fixtures import seed_item, seed_event

    item = seed_item(status="scheduled", scheduled_for="2026-11-19", decided_at="...")
    seed_event(item.id, "decided", {"outcome": "schedule", "accepted": True}, at="...")

These write rows directly and bypass the state machine on purpose — use them
to set up "given" state. Use ``taskbot.core.items`` in the code under test.
"""

from __future__ import annotations

import json
from typing import Any

from taskbot.core import clock, db, items
from taskbot.core.models import Event, Item


def seed_item(**overrides: Any) -> Item:
    values: dict[str, Any] = {
        "title": "Seeded item",
        "source": "manual",
        "status": "tray",
        "created_at": clock.now_iso(),
        "pressure_count": 0,
    }
    if "ask_card" in overrides:
        card = overrides.pop("ask_card")
        overrides["ask_card_json"] = None if card is None else json.dumps(card)
    values.update(overrides)
    with db.connect() as conn:
        unknown = set(values) - db.table_columns(conn, "items")
        if unknown:
            raise ValueError(f"unknown item columns: {sorted(unknown)}")
        cols = ", ".join(f'"{k}"' for k in values)
        marks = ", ".join("?" for _ in values)
        cur = conn.execute(f"INSERT INTO items ({cols}) VALUES ({marks})", tuple(values.values()))
        item_id = int(cur.lastrowid)
    item = items.get_item(item_id)
    assert item is not None
    return item


def seed_event(
    item_id: int, kind: str, payload: dict[str, Any] | None = None, *, at: str | None = None
) -> Event:
    return items.log_event(item_id, kind, payload or {}, at=at)
