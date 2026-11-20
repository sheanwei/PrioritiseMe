"""S5 Weekly review API, mounted at /api/s5_review."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import service
from .weeks import review_tz, week_containing

router = APIRouter()


@router.get("/week")
def week_review(
    start: str | None = Query(
        default=None,
        description="Any date (YYYY-MM-DD) in the wanted week; normalised to its Monday. "
        "Default: the current week in the review timezone.",
    ),
) -> dict[str, Any]:
    """FR-50..FR-54 for one Monday-to-Sunday week."""
    try:
        tz = review_tz()
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc
    day: date | None = None
    if start:
        try:
            day = date.fromisoformat(start)
        except ValueError as exc:
            raise HTTPException(422, "start must be a date like 2026-11-16") from exc
    return service.build_review(week_containing(day, tz))
