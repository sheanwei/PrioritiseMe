"""S8 API, mounted at /api/s8_tasks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from . import service

router = APIRouter()


@router.get("/open")
def open_tasks() -> dict[str, Any]:
    return service.open_tasks()


@router.post("/items/{item_id}/done")
def mark_done(item_id: int) -> dict[str, Any]:
    try:
        return service.mark_done(item_id)
    except service.NotFound:
        raise HTTPException(404, "item not found") from None
    except service.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
