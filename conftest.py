"""Shared pytest fixtures for core and every slice.

Every test gets a fresh, migrated SQLite file, fake LLM, fake mail, and the
scheduler off — no network, no API key needed (NFR-6).
"""

from __future__ import annotations

import pytest

from taskbot.core import clock, db, llm, scheduler


@pytest.fixture(autouse=True)
def taskbot_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKBOT_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("TASKBOT_LLM", "fake")
    monkeypatch.setenv("TASKBOT_MAIL", "fake")
    monkeypatch.setenv("TASKBOT_SCHEDULER", "off")
    monkeypatch.setenv("TASKBOT_DEBUG", "0")
    # A developer's .env must not change test behaviour (e.g. OPENCODE_EXTRA_BODY
    # adds an `extra_body` kwarg that strict test stubs reject).
    monkeypatch.delenv("OPENCODE_EXTRA_BODY", raising=False)
    monkeypatch.delenv("TASKBOT_REVIEW_TZ", raising=False)
    db.migrate()
    yield tmp_path
    clock.set_now(None)
    scheduler.reset()
    llm.client_factory = None


@pytest.fixture
def client():
    """TestClient with lifespan (migrations + slice startup hooks) running."""
    from fastapi.testclient import TestClient

    from taskbot.core.app import create_app

    with TestClient(create_app()) as c:
        yield c
