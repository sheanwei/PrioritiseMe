"""Item repository, the state-machine ``transition()``, and the event log.

Every function takes an optional ``conn``. Pass one to group several calls in a
single transaction; omit it and each call commits on its own.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

from . import clock, db
from .models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Event,
    EventKind,
    Item,
    Source,
    Status,
)

# Fields that only transition()/create_item() may set.
_PROTECTED = frozenset({"id", "status", "created_at"})


class InvalidTransition(ValueError):
    """Raised when a status change is not in ``ALLOWED_TRANSITIONS``."""


class ItemNotFound(KeyError):
    pass


@contextmanager
def _use(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
    else:
        with db.connect() as own:
            yield own


def _prepare_fields(conn: sqlite3.Connection, values: dict[str, Any]) -> dict[str, Any]:
    """Validate column names and serialise convenience keys."""
    values = dict(values)
    if "ask_card" in values:
        card = values.pop("ask_card")
        values["ask_card_json"] = None if card is None else json.dumps(card)
    bad = set(values) & _PROTECTED
    if bad:
        raise ValueError(f"cannot set {sorted(bad)} directly; use transition()")
    unknown = set(values) - db.table_columns(conn, "items")
    if unknown:
        raise ValueError(f"unknown item columns: {sorted(unknown)}")
    return {k: (v.value if isinstance(v, Enum) else v) for k, v in values.items()}


def log_event(
    item_id: int,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    at: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> Event:
    with _use(conn) as c:
        cur = c.execute(
            "INSERT INTO item_events (item_id, kind, payload_json, at) VALUES (?, ?, ?, ?)",
            (item_id, str(kind), json.dumps(payload or {}), at or clock.now_iso()),
        )
        row = c.execute("SELECT * FROM item_events WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Event.from_row(row)


def create_item(
    title: str,
    source: str = Source.MANUAL,
    *,
    raw_context: str | None = None,
    source_ref: str | None = None,
    conn: sqlite3.Connection | None = None,
    **fields: Any,
) -> Item:
    """Create an item in the tray and log a ``created`` event."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title must not be empty")
    source = Source(source)
    with _use(conn) as c:
        extra = _prepare_fields(c, fields)
        values: dict[str, Any] = {
            "title": title,
            "source": source.value,
            "source_ref": source_ref,
            "raw_context": raw_context,
            "status": Status.TRAY.value,
            "created_at": clock.now_iso(),
            **extra,
        }
        cols = ", ".join(f'"{k}"' for k in values)
        marks = ", ".join("?" for _ in values)
        cur = c.execute(f"INSERT INTO items ({cols}) VALUES ({marks})", tuple(values.values()))
        item_id = int(cur.lastrowid)
        log_event(item_id, EventKind.CREATED, {"source": source.value}, conn=c)
        return _get(c, item_id)


def _get(conn: sqlite3.Connection, item_id: int) -> Item:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ItemNotFound(item_id)
    return Item.from_row(row)


def get_item(item_id: int, *, conn: sqlite3.Connection | None = None) -> Item | None:
    with _use(conn) as c:
        try:
            return _get(c, item_id)
        except ItemNotFound:
            return None


def list_items(
    status: str | Iterable[str] | None = None,
    *,
    source: str | None = None,
    source_ref: str | None = None,
    limit: int | None = None,
    newest_first: bool = True,
    conn: sqlite3.Connection | None = None,
) -> list[Item]:
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        statuses = [status] if isinstance(status, str) else list(status)
        statuses = [Status(s).value for s in statuses]
        where.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if source is not None:
        where.append("source = ?")
        params.append(Source(source).value)
    if source_ref is not None:
        where.append("source_ref = ?")
        params.append(source_ref)
    sql = "SELECT * FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    direction = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY created_at {direction}, id {direction}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _use(conn) as c:
        return [Item.from_row(r) for r in c.execute(sql, params)]


def update_fields(item_id: int, *, conn: sqlite3.Connection | None = None, **fields: Any) -> Item:
    """Set non-status columns (core or slice-added). ``ask_card=dict`` is accepted."""
    with _use(conn) as c:
        _get(c, item_id)
        values = _prepare_fields(c, fields)
        if values:
            assignments = ", ".join(f'"{k}" = ?' for k in values)
            c.execute(f"UPDATE items SET {assignments} WHERE id = ?", (*values.values(), item_id))
        return _get(c, item_id)


def transition(
    item_id: int,
    to: str,
    *,
    conn: sqlite3.Connection | None = None,
    **fields: Any,
) -> Item:
    """Move an item to a new status, validating against the state machine.

    Extra ``fields`` are written in the same update. Logs ``status_changed``.
    Sets ``closed_at`` automatically when entering a terminal status.
    """
    target = Status(to)
    with _use(conn) as c:
        item = _get(c, item_id)
        current = Status(item.status)
        if target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransition(f"item {item_id}: {current.value} -> {target.value} not allowed")
        values = _prepare_fields(c, fields)
        values["status"] = target.value
        if target in TERMINAL_STATUSES and "closed_at" not in values:
            values["closed_at"] = clock.now_iso()
        assignments = ", ".join(f'"{k}" = ?' for k in values)
        c.execute(f"UPDATE items SET {assignments} WHERE id = ?", (*values.values(), item_id))
        log_event(
            item_id,
            EventKind.STATUS_CHANGED,
            {"from": current.value, "to": target.value},
            conn=c,
        )
        return _get(c, item_id)


def list_events(
    *,
    item_id: int | None = None,
    kinds: str | Iterable[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[Event]:
    """Events oldest-first. ``since`` is inclusive, ``until`` exclusive (ISO strings)."""
    where: list[str] = []
    params: list[Any] = []
    if item_id is not None:
        where.append("item_id = ?")
        params.append(item_id)
    if kinds is not None:
        kind_list = [str(kinds)] if isinstance(kinds, str) else [str(k) for k in kinds]
        where.append(f"kind IN ({', '.join('?' for _ in kind_list)})")
        params.extend(kind_list)
    if since is not None:
        where.append("at >= ?")
        params.append(since)
    if until is not None:
        where.append("at < ?")
        params.append(until)
    sql = "SELECT * FROM item_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY at, id"
    with _use(conn) as c:
        return [Event.from_row(r) for r in c.execute(sql, params)]
