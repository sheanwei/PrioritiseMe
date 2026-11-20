"""S4 Outlook ingestion API, mounted at /api/s4_outlook."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from taskbot.core import config
from taskbot.core.scheduler import register_job

from . import service, signin

router = APIRouter()

POLL_SECONDS = 5 * 60  # FR-43


def startup() -> None:
    register_job("s4_outlook.poll", POLL_SECONDS, service.poll_job)


class Correction(BaseModel):
    classification: Literal["needs_action", "fyi", "noise"]


@router.get("/state")
def state(limit: int = Query(default=15, ge=1, le=100)) -> dict[str, Any]:
    """Everything the Mail panel needs: counts, latest messages, connection + sign-in."""
    return {
        "counts": service.counts(),
        "recent": service.list_emails(limit=limit),
        "status": service.status(),
    }


@router.get("/status")
def status() -> dict[str, Any]:
    return service.status()


@router.get("/emails")
def emails(
    classification: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> list[dict[str, Any]]:
    return service.list_emails(classification, limit)


@router.post("/sync")
async def sync_now() -> dict[str, Any]:
    """FR-43 "Sync now". Mail/LLM failures come back in the body, not as a 500."""
    return await service.sync("manual")


@router.post("/emails/{email_id}/correct")
def correct(email_id: str, body: Correction) -> dict[str, Any]:
    try:
        return service.correct(email_id, body.classification)
    except service.NotFound:
        raise HTTPException(404, "email not found") from None


@router.get("/signin")
def signin_state() -> dict[str, Any]:
    return signin.state()


@router.post("/signin")
def signin_start() -> dict[str, Any]:
    """FR-42: start the device-code sign-in; poll GET /signin for the URL + code."""
    if config.settings().mail_mode != "mcp":
        raise HTTPException(409, "sign-in is only needed with TASKBOT_MAIL=mcp")
    return signin.start()
