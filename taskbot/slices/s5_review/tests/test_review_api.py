"""S5 HTTP API and panel wiring (FR-50, AC3 through the API)."""

from datetime import datetime, timezone

import pytest

from taskbot.core import clock
from tests.fixtures import seed_event, seed_item


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setenv("TASKBOT_REVIEW_TZ", "Asia/Singapore")
    # Sunday 17:00 UTC = Monday 01:00 SGT -> current week starts Mon 23 Nov
    clock.set_now(datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc))


def test_default_is_current_week_in_review_timezone(client):
    r = client.get("/api/s5_review/week")
    assert r.status_code == 200
    wk = r.json()["week"]
    assert (wk["start"], wk["end"]) == ("2026-11-23", "2026-11-29")
    assert wk["since"] == "2026-11-22T16:00:00+00:00"
    assert wk["is_current"] is True
    assert (wk["prev"], wk["next"]) == ("2026-11-16", "2026-11-30")


def test_start_is_normalised_to_monday(client):
    item = seed_item(title="Pager noise", status="declined", type="interrupt", outcome="decline",
                     ask_card={"request": "r", "requester": "Ops bot"})
    seed_event(item.id, "decided", {"outcome": "decline", "type": "interrupt", "accepted": False,
                                    "override_reason": "noise"}, at="2026-11-19T02:00:00+00:00")
    body = client.get("/api/s5_review/week", params={"start": "2026-11-22"}).json()  # a Sunday
    assert body["week"]["start"] == "2026-11-16"
    assert body["week"]["is_current"] is False
    assert body["decided"]["by_type"]["interrupt"] == 1
    assert body["overrides"]["log"][0]["reason"] == "noise"
    assert body["could_have_waited"]["count"] == 1


def test_ac3_empty_week_via_api(client):
    body = client.get("/api/s5_review/week", params={"start": "2020-01-01"}).json()
    assert body["decided"]["total"] == 0
    assert body["acceptance"]["percent"] is None
    assert body["overrides"]["count"] == 0


def test_bad_start_is_422(client):
    assert client.get("/api/s5_review/week", params={"start": "next tuesday"}).status_code == 422


def test_bad_timezone_is_reported(client, monkeypatch):
    monkeypatch.setenv("TASKBOT_REVIEW_TZ", "Nowhere/Special")
    r = client.get("/api/s5_review/week")
    assert r.status_code == 500
    assert "TASKBOT_REVIEW_TZ" in r.json()["detail"]


def test_panel_registered_and_safe(client):
    panels = client.get("/api/core/panels").json()
    review = [{k: p[k] for k in ("id", "title", "module", "order", "slice")} for p in panels if p["slice"] == "s5_review"]
    assert review == [
        {"id": "s5_review", "title": "Review", "module": "/slices/s5_review/panel.js", "order": 60, "slice": "s5_review"}
    ]
    js = client.get("/slices/s5_review/panel.js")
    assert js.status_code == 200
    assert "export function mount" in js.text
    assert "innerHTML" not in js.text
    assert '"items:changed"' in js.text
