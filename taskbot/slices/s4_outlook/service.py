"""Outlook ingestion: fetch, dedupe, classify, and turn needs-action mail into tray items.

Flow (FR-43..FR-46):
    sync() -> fetch (fake fixtures or MCP) -> ingest() -> per new message:
        classify (cheap model, last 10 corrections as examples)
        needs_action -> new tray item, or bump the conversation's open item
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from taskbot.core import clock, config, db, items, llm
from taskbot.core.models import EventKind, Source, Status, WorkType, is_open

from . import mcp_client, prompts, signin

log = logging.getLogger("taskbot.s4_outlook")

FAKE_MAIL_DIR = config.ROOT / "tests" / "fixtures" / "mail"
PREVIEW_STORE_CHARS = 2000
CORRECTION_EXAMPLES = 10

_sync_lock = threading.Lock()
_status: dict[str, Any] = {"connection": "unknown", "last_sync": None, "last_error": None}


# --- mail sources ------------------------------------------------------------


def _address(value: Any) -> str:
    """Graph ``from`` ({"emailAddress": {name, address}}) -> "Name <address>"."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        ea = value.get("emailAddress", value)
        name, addr = (ea.get("name") or "").strip(), (ea.get("address") or "").strip()
        if name and addr and name != addr:
            return f"{name} <{addr}>"
        return addr or name
    return ""


def _iso(value: Any) -> str:
    if not value:
        return clock.now_iso()
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return clock.now_iso()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return clock.to_iso(dt)


def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Graph message JSON (also the fixture format) -> our row shape. None if unusable."""
    msg_id = raw.get("id")
    if not msg_id:
        return None
    return {
        "id": str(msg_id),
        "conversation_id": raw.get("conversationId") or raw.get("conversation_id") or str(msg_id),
        "from": _address(raw.get("from")),
        "subject": (raw.get("subject") or "").strip(),
        "received_at": _iso(raw.get("receivedDateTime") or raw.get("received_at")),
        "body_preview": (raw.get("bodyPreview") or raw.get("body_preview") or "")[:PREVIEW_STORE_CHARS],
    }


def read_fake_mail(folder: Path | None = None) -> list[dict[str, Any]]:
    """FR-48: every ``*.json`` in the fixture folder; each holds one message or a list."""
    folder = folder or FAKE_MAIL_DIR
    out: list[dict[str, Any]] = []
    for file in sorted(folder.glob("*.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        out.extend(data if isinstance(data, list) else [data])
    return out


async def fetch_raw() -> list[dict[str, Any]]:
    if config.settings().mail_mode == "fake":
        return read_fake_mail()
    return await mcp_client.fetch_messages()


# --- classification ----------------------------------------------------------


def recent_corrections(limit: int = CORRECTION_EXAMPLES, *, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        'SELECT "from", subject, body_preview, classification FROM emails '
        "WHERE user_corrected = 1 ORDER BY corrected_at DESC, rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def classify(msg: dict[str, Any], corrections: list[dict[str, Any]]) -> tuple[str, str]:
    """Returns (classification, reason). Raises ``llm.LLMError`` on failure."""
    result = llm.complete_json(
        prompts.SYSTEM,
        prompts.user_prompt(msg, corrections),
        prompts.schema(prompts.guess_classification(msg)),
        model=config.settings().opencode_model_cheap,
    )
    return result["classification"], result.get("reason", "")


# --- items -------------------------------------------------------------------


def _open_item_for(conversation_id: str, *, conn: sqlite3.Connection) -> items.Item | None:
    for item in items.list_items(source=Source.EMAIL, source_ref=conversation_id, conn=conn):
        if is_open(item.status):
            return item
    return None


def _context_block(msg: dict[str, Any]) -> str:
    return (
        f"From: {msg['from']}\nSubject: {msg['subject']}\nReceived: {msg['received_at']}\n\n"
        f"{msg['body_preview']}"
    )


def _apply_needs_action(msg: dict[str, Any], *, conn: sqlite3.Connection) -> tuple[int, str]:
    """FR-45. Returns (item_id, 'created' | 'bumped')."""
    existing = _open_item_for(msg["conversation_id"], conn=conn)
    if existing is not None:
        latest = max(filter(None, [existing.last_external_activity, msg["received_at"]]))
        context = ((existing.raw_context or "") + "\n\n---\n" + _context_block(msg)).strip()
        items.update_fields(
            existing.id,
            pressure_count=existing.pressure_count + 1,
            last_external_activity=latest,
            raw_context=context,
            conn=conn,
        )
        items.log_event(existing.id, EventKind.EXTERNAL_ACTIVITY, {"via": "email"}, conn=conn)
        return existing.id, "bumped"

    item = items.create_item(
        msg["subject"] or "(no subject)",
        Source.EMAIL,
        raw_context=_context_block(msg),
        source_ref=msg["conversation_id"],
        type=WorkType.INTERRUPT,  # proposed; S1's gate decides
        last_external_activity=msg["received_at"],
        # §4.4: S4 may pre-fill the ask card (S1 overwrites it).
        ask_card={
            "request": msg["subject"] or "(no subject)",
            "requester": msg["from"],
            "deadline": None,
            "deadline_kind": "none",
            "open_questions": [],
        },
        conn=conn,
    )
    return item.id, "created"


# --- ingest ------------------------------------------------------------------


def ingest(raw_messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Store new messages, classify unclassified ones, apply FR-45. Idempotent."""
    msgs = [m for m in (normalize(r) for r in raw_messages) if m is not None]
    msgs.sort(key=lambda m: (m["received_at"], m["id"]))  # originals before replies
    summary = {"fetched": len(msgs), "new": 0, "classified": 0, "created": 0, "bumped": 0, "failed": 0}

    with db.connect() as conn:
        for m in msgs:
            cur = conn.execute(
                'INSERT OR IGNORE INTO emails (id, conversation_id, "from", subject, received_at, '
                "body_preview, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (m["id"], m["conversation_id"], m["from"], m["subject"], m["received_at"],
                 m["body_preview"], clock.now_iso()),
            )
            summary["new"] += cur.rowcount
        pending = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM emails WHERE classification IS NULL ORDER BY received_at, id"
            )
        ]
        corrections = recent_corrections(conn=conn)

    for row in pending:
        try:
            label, reason = classify(row, corrections)
        except llm.LLMError as exc:
            # NFR-4: leave unclassified; the next sync retries it.
            summary["failed"] += 1
            _status["last_error"] = f"classification failed: {exc}"
            continue
        with db.connect() as conn:
            item_id, role = None, None
            if label == "needs_action":
                item_id, role = _apply_needs_action(row, conn=conn)
                summary[role] += 1
            conn.execute(
                "UPDATE emails SET classification = ?, llm_classification = ?, classification_reason = ?, "
                "classified_at = ?, item_id = ?, item_role = ? WHERE id = ?",
                (label, label, reason[:300], clock.now_iso(), item_id, role, row["id"]),
            )
        summary["classified"] += 1
    return summary


# --- corrections (FR-46) -----------------------------------------------------


class NotFound(LookupError):
    pass


def correct(email_id: str, to: str) -> dict[str, Any]:
    if to not in prompts.CLASSES:
        raise ValueError(f"classification must be one of {list(prompts.CLASSES)}")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        if row is None:
            raise NotFound(email_id)
        row = dict(row)
        frm = row["classification"]
        result: dict[str, Any] = {"id": email_id, "from": frm, "to": to, "item_id": row["item_id"],
                                  "action": "none"}
        if frm == to:
            return result

        item_id, role = row["item_id"], row["item_role"]
        if to == "needs_action":
            item_id, role = _apply_needs_action(row, conn=conn)
            result["action"] = role  # 'created' or 'bumped'
        elif frm == "needs_action" and item_id is not None:
            item = items.get_item(item_id, conn=conn)
            if item is not None and role == "created":
                if item.status == Status.TRAY:
                    # The only legal exit from the tray that isn't "work on it".
                    items.transition(item.id, Status.DECLINED, conn=conn)
                    result["action"] = "archived"
                else:
                    result["action"] = f"left_unchanged:{item.status}"
            elif item is not None and role == "bumped":
                items.update_fields(item.id, pressure_count=max(item.pressure_count - 1, 0), conn=conn)
                result["action"] = "unbumped"
        # else fyi <-> noise: no item involved

        if item_id is not None and (to == "needs_action" or frm == "needs_action"):
            items.log_event(item_id, EventKind.CLASSIFICATION_CORRECTED, {"from": frm, "to": to}, conn=conn)
        conn.execute(
            "UPDATE emails SET classification = ?, user_corrected = 1, corrected_at = ?, "
            "item_id = ?, item_role = ? WHERE id = ?",
            (to, clock.now_iso(), item_id, role, email_id),
        )
        result["item_id"] = item_id
        return result


# --- sync + state ------------------------------------------------------------


async def sync(trigger: str = "manual") -> dict[str, Any]:
    """Fetch and ingest. Never raises for mail/LLM trouble; the result says what happened."""
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "skipped": "a sync is already running"}
    started = clock.now_iso()
    try:
        try:
            raw = await fetch_raw()
        except mcp_client.NeedsSignIn as exc:
            _status.update(connection="needs_signin", last_error=str(exc))
            device = mcp_client.parse_device_code(str(exc))
            if device:
                signin.note_device_code(device)
            return _finish(trigger, started, ok=False, error=str(exc), needs_signin=True)
        except (mcp_client.MailError, mcp_client.ToolNotAllowed, ValueError, OSError, TimeoutError) as exc:
            _status.update(connection="error", last_error=str(exc))
            log.warning("mail fetch failed: %s", exc)
            return _finish(trigger, started, ok=False, error=str(exc))
        _status.update(connection="ok", last_error=None)
        summary = await asyncio.to_thread(ingest, raw)
        return _finish(trigger, started, ok=True, **summary)
    finally:
        _sync_lock.release()


def _finish(trigger: str, started: str, **result: Any) -> dict[str, Any]:
    result = {"trigger": trigger, "at": started, **result}
    _status["last_sync"] = result
    return result


async def poll_job() -> None:
    await sync("poll")


def reset_status() -> None:
    """Forget in-memory sync status (tests)."""
    _status.clear()
    _status.update(connection="unknown", last_sync=None, last_error=None)


def counts() -> dict[str, int]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT COALESCE(classification, 'unclassified') AS c, COUNT(*) AS n FROM emails GROUP BY c"
        ).fetchall()
    out = {c: 0 for c in (*prompts.CLASSES, "unclassified")}
    out.update({r["c"]: r["n"] for r in rows})
    return out


def list_emails(classification: list[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    sql = (
        'SELECT id, conversation_id, "from", subject, received_at, body_preview, classification, '
        "user_corrected, classification_reason, item_id, item_role FROM emails"
    )
    params: list[Any] = []
    if classification:
        sql += f" WHERE classification IN ({', '.join('?' for _ in classification)})"
        params.extend(classification)
    sql += " ORDER BY received_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    for r in rows:
        r["user_corrected"] = bool(r["user_corrected"])
        r["body_preview"] = (r["body_preview"] or "")[:300]
    return rows


def status() -> dict[str, Any]:
    s = config.settings()
    info: dict[str, Any] = {"mode": s.mail_mode, **_status, "signin": signin.state()}
    try:
        info["allowlist"] = sorted(mcp_client.allowlist())
    except ValueError as exc:
        info["allowlist"] = []
        info["config_error"] = str(exc)
    if s.mail_mode == "mcp":
        info["command"] = " ".join(mcp_client.command())
    return info
