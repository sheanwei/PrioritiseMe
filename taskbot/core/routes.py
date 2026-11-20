"""Core API under /api/core: health, panel registry, quick-add, item reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import __version__
from . import items
from .config import settings
from .models import Source

router = APIRouter(tags=["core"])


class QuickAdd(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    raw_context: str | None = Field(default=None, max_length=200_000)


@router.get("/health")
def health() -> dict[str, Any]:
    s = settings()
    return {
        "version": __version__,
        "llm": {"mode": s.llm_mode, "model": s.opencode_model, "configured": bool(s.opencode_api_key)},
        "mail": {"mode": s.mail_mode},
    }


@router.get("/panels")
def panels(request: Request) -> list[dict[str, Any]]:
    from .app import core_panels  # local import avoids a cycle

    result = [{**p, "slice": "core"} for p in core_panels()]
    for s in request.app.state.slices:
        result.extend(s.panels())
    return sorted(result, key=lambda p: (p["order"], p["id"]))


@router.post("/items", status_code=201)
def quick_add(body: QuickAdd) -> dict[str, Any]:
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "title must not be blank")
    raw = (body.raw_context or "").strip() or None
    return items.create_item(title, Source.MANUAL, raw_context=raw).to_dict()


@router.get("/items")
def list_items(
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    try:
        found = items.list_items(status, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [i.to_dict() for i in found]


@router.get("/items/{item_id}")
def get_item(item_id: int) -> dict[str, Any]:
    item = items.get_item(item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return item.to_dict()


@router.get("/items/{item_id}/events")
def item_events(item_id: int) -> list[dict[str, Any]]:
    if items.get_item(item_id) is None:
        raise HTTPException(404, "item not found")
    return [e.to_dict() for e in items.list_events(item_id=item_id)]
