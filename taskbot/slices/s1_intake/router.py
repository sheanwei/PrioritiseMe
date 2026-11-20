"""S1 intake gate API, mounted at /api/s1_intake."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from taskbot.core.items import InvalidTransition

from . import service

router = APIRouter()


class Intake(BaseModel):
    """The four intake questions. ``None`` means unanswered (manual path, FR-18)."""

    cost_of_delay: Literal["none", "minor", "significant"] | None = None
    deadline_real: Literal["real", "felt", "none"] | None = None
    blocked_count: int | None = Field(default=None, ge=0, le=100_000)
    delegable: bool | None = None


class AskCard(BaseModel):
    request: str = Field(default="", max_length=500)
    requester: str = Field(default="", max_length=200)
    deadline: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    deadline_kind: Literal["real", "felt", "none"] = "none"
    open_questions: list[str] = Field(default_factory=list, max_length=20)


class Resuggest(BaseModel):
    intake: Intake
    production_impact: bool = False
    size_known: bool = True


class Decide(BaseModel):
    outcome: Literal["do_now", "timebox", "schedule", "delegate", "decline"]
    type: Literal["incident", "interrupt", "toil", "planned"]
    priority: int = Field(ge=1, le=4)
    override_reason: str | None = Field(default=None, max_length=1000)
    timebox_minutes: int | None = Field(default=None, ge=1, le=480)
    scheduled_for: str | None = None
    delegated_to: str | None = Field(default=None, max_length=200)
    intake: Intake | None = None


class LogAfter(BaseModel):
    ask_card: AskCard
    intake: Intake


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.GateError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/items/{item_id}")
def get_gate(item_id: int) -> dict[str, Any]:
    """Gate state without calling the LLM."""
    return _call(service.get_state, item_id)


@router.post("/items/{item_id}/analyze")
def analyze(item_id: int, force: bool = False) -> dict[str, Any]:
    """Open the gate: one LLM call (cached until the item is decided again)."""
    return _call(service.analyze, item_id, force=force)


@router.post("/items/{item_id}/resuggest")
def resuggest(item_id: int, body: Resuggest) -> dict[str, Any]:
    return _call(
        service.resuggest,
        item_id,
        body.intake.model_dump(),
        production_impact=body.production_impact,
        size_known=body.size_known,
    )


@router.post("/items/{item_id}/decide")
def decide(item_id: int, body: Decide) -> dict[str, Any]:
    data = body.model_dump()
    return _call(service.decide, item_id, **data)


@router.post("/items/{item_id}/incident")
def incident(item_id: int) -> dict[str, Any]:
    """Incident fast path (FR-17)."""
    return _call(service.incident_fast_path, item_id)


@router.put("/items/{item_id}/log")
def log_after(item_id: int, body: LogAfter) -> dict[str, Any]:
    card = body.ask_card.model_dump()
    card["open_questions"] = [q.strip() for q in card["open_questions"] if q.strip()]
    return _call(service.log_after, item_id, ask_card=card, intake=body.intake.model_dump())


@router.get("/pending-log")
def pending_log() -> list[dict[str, Any]]:
    return service.pending_logs()
