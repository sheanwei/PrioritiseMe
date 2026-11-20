"""S7 dependency links API, mounted at /api/s7_links."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from taskbot.core import llm
from taskbot.core.scheduler import register_job

from . import service

router = APIRouter()


class NewLink(BaseModel):
    blocker_id: int
    blocked_id: int
    reason: str = Field(default="", max_length=1000)


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.LinkError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(502, f"AI link detection failed: {exc}") from exc


@router.get("/items/{item_id}")
def get_links(item_id: int) -> dict[str, Any]:
    return _call(service.links_for, item_id)


@router.post("/items/{item_id}/detect")
def detect(item_id: int) -> dict[str, Any]:
    new = _call(service.detect_links, item_id)
    return {"new": new, "links": _call(service.links_for, item_id)}


@router.post("/links")
def add_link(body: NewLink) -> dict[str, Any]:
    return _call(service.add_link, body.blocker_id, body.blocked_id, body.reason)


@router.post("/links/{link_id}/dismiss")
def dismiss(link_id: int) -> dict[str, Any]:
    return _call(service.dismiss_link, link_id)


@router.get("/summary")
def summary() -> dict[str, dict[str, Any]]:
    return {str(k): v for k, v in service.summary().items()}


def startup() -> None:
    register_job("s7_links.scan", 60, service.scan_new)
