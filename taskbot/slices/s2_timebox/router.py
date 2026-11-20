"""S2 API, mounted at /api/s2_timebox."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from taskbot.core.items import ItemNotFound
from taskbot.core.scheduler import register_job

from . import service

router = APIRouter()


def startup() -> None:
    register_job(service.JOB_NAME, service.JOB_EVERY_SECONDS, service.expire_due)


class NoteBody(BaseModel):
    note: str = Field(max_length=service.NOTE_MAX_LEN)


def _not_found() -> HTTPException:
    return HTTPException(404, "item not found")


@router.get("/timeboxes")
def timeboxes() -> dict[str, Any]:
    """Running timeboxes with ``server_now`` so the panel can correct clock skew (FR-20)."""
    return service.list_timeboxes()


@router.post("/timeboxes/{item_id}/extend")
def extend(item_id: int) -> dict[str, Any]:
    """Add 15 minutes to a running timebox (FR-24)."""
    try:
        item = service.extend(item_id)
    except ItemNotFound:
        raise _not_found() from None
    except service.NotTimeboxed as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**service.server_time(), "item": item.to_dict()}


@router.get("/expiries/pending")
def pending_expiries() -> dict[str, Any]:
    """Expiries no dashboard has notified about yet (FR-22)."""
    return service.pending_expiries()


@router.post("/expiries/{item_id}/ack")
def ack_expiry(item_id: int) -> dict[str, Any]:
    """Claim an expiry notification. ``claimed`` is true for the first caller only."""
    try:
        return {"item_id": item_id, "claimed": service.ack_expiry(item_id)}
    except ItemNotFound:
        raise _not_found() from None


@router.get("/reassess")
def reassess_list() -> list[dict[str, Any]]:
    """Tray items flagged for re-assessment (FR-23)."""
    return service.list_reassess()


@router.post("/reassess/{item_id}/clear")
def reassess_clear(item_id: int) -> dict[str, Any]:
    """Clear the re-assess flag once the item has been decided again (S6 wires this)."""
    try:
        return service.clear_reassess(item_id).to_dict()
    except ItemNotFound:
        raise _not_found() from None


@router.get("/note/{item_id}")
def get_note(item_id: int) -> dict[str, Any]:
    """The re-assess hook S1 reads: note text and whether one is still required (FR-23)."""
    try:
        return service.get_note(item_id)
    except ItemNotFound:
        raise _not_found() from None


@router.put("/note/{item_id}")
def put_note(item_id: int, body: NoteBody) -> dict[str, Any]:
    try:
        return service.save_note(item_id, body.note)
    except ItemNotFound:
        raise _not_found() from None
    except service.NotFlagged as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
