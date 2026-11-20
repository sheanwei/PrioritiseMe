"""S3 API, mounted at /api/s3_resurface."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from taskbot.core.scheduler import register_job

from . import service

router = APIRouter()

JOB_NAME = "s3_resurface.tick"
# FR-30 asks for a daily job; ticking every 15 min means items resurface soon
# after local midnight even if the app runs for days. The job is idempotent.
JOB_EVERY_SECONDS = 900


def startup() -> None:
    register_job(JOB_NAME, JOB_EVERY_SECONDS, service.tick)


@router.get("/resurfaced")
def resurfaced() -> dict[str, Any]:
    return {"date": service.local_today().isoformat(), "items": service.resurfaced_today()}


@router.get("/candidates")
def candidates() -> dict[str, Any]:
    service.scan_stale()  # idempotent; keeps the event log in step with what is shown
    return {
        "threshold_days": service.STALE_AFTER_WORKING_DAYS,
        "items": service.list_candidates(),
    }


@router.post("/run")
def run_now() -> dict[str, list[int]]:
    """Run the job immediately (demo / manual trigger)."""
    return service.tick()


def _act(fn: Callable[[int], dict[str, Any]], item_id: int) -> dict[str, Any]:
    try:
        return fn(item_id)
    except service.NotFound as exc:
        raise HTTPException(404, "item not found") from exc
    except service.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/items/{item_id}/close")
def close(item_id: int) -> dict[str, Any]:
    return _act(service.close_stale, item_id)


@router.post("/items/{item_id}/keep")
def keep(item_id: int) -> dict[str, Any]:
    return _act(service.keep, item_id)
