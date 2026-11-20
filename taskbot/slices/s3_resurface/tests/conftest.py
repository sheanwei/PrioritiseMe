from __future__ import annotations

from datetime import datetime, timezone

import pytest

from taskbot.core import clock
from taskbot.slices.s3_resurface import service

# Friday 2026-11-20, midday UTC.
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_utc(monkeypatch):
    """Deterministic "local" zone (UTC) and a frozen clock for every S3 test."""
    monkeypatch.setattr(service, "DEFAULT_TZ", timezone.utc)
    clock.set_now(NOW)
    yield NOW
