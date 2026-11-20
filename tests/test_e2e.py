"""S6 / FR-62: the whole flow end-to-end, offline (fake LLM, fake mail), via the HTTP API.

email -> tray -> intake gate -> timebox -> expiry -> re-assess note -> schedule
-> resurface -> delegate -> stale close -> weekly review.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from taskbot.core import clock, items
from taskbot.slices.s2_timebox import service as s2
from taskbot.slices.s3_resurface import service as s3

UTC = timezone.utc
MON = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)  # Monday after the fixture emails (19 Nov)


@pytest.fixture
def utc_everywhere(monkeypatch):
    monkeypatch.setattr(s3, "DEFAULT_TZ", UTC)
    monkeypatch.setenv("TASKBOT_REVIEW_TZ", "UTC")


def decide(client, item_id, **body):
    body.setdefault("type", "interrupt")
    body.setdefault("priority", 2)
    body.setdefault("override_reason", "e2e test decision")
    return client.post(f"/api/s1_intake/items/{item_id}/decide", json=body)


def test_full_flow_email_to_weekly_review(client, utc_everywhere):
    clock.set_now(MON)

    # 1. Email -> tray (S4, fake mailbox + fake classifier)
    sync = client.post("/api/s4_outlook/sync")
    assert sync.status_code == 200, sync.text
    tray = client.get("/api/core/items", params={"status": "tray"}).json()
    emails = [i for i in tray if i["source"] == "email"]
    assert emails, "fake mailbox should yield at least one needs-action email"
    item = emails[-1]
    item_id = item["id"]
    assert (item["ask_card"] or {}).get("requester"), "S4 pre-fills the requester (FR-61)"

    # 2. Intake gate: analysis keeps the pre-filled requester, then timebox it
    state = client.post(f"/api/s1_intake/items/{item_id}/analyze").json()
    assert state["suggestion"]
    assert state["ask_card"]["requester"]
    assert decide(client, item_id, outcome="timebox", timebox_minutes=30).status_code == 200
    assert items.get_item(item_id).status == "timeboxed"

    # 3. Timebox expires -> back in the tray, flagged for re-assessment
    clock.set_now(MON + timedelta(minutes=31))
    assert item_id in s2.expire_due()
    back = items.get_item(item_id)
    assert back.status == "tray" and back.extra["reassess"] == 1

    # 4. S6 wiring: can't decide until the re-assess note exists
    blocked = decide(client, item_id, outcome="schedule", scheduled_for="2026-11-25")
    assert blocked.status_code == 409
    assert "learned" in blocked.json()["detail"]
    note = client.put(f"/api/s2_timebox/note/{item_id}", json={"note": "Needs a DBA; not urgent"})
    assert note.status_code == 200
    assert decide(client, item_id, outcome="schedule", scheduled_for="2026-11-25").status_code == 200
    scheduled = items.get_item(item_id)
    assert scheduled.status == "scheduled"
    assert scheduled.extra["reassess"] == 0, "deciding clears S2's re-assess flag"
    assert client.get(f"/api/s2_timebox/note/{item_id}").json()["required"] is False

    # 5. Wednesday: it resurfaces (S3)
    clock.set_now(datetime(2026, 11, 25, 7, 0, tzinfo=UTC))
    assert client.post("/api/s3_resurface/run").status_code == 200
    assert items.get_item(item_id).status == "tray"
    assert [e.kind for e in items.list_events(item_id=item_id, kinds="resurfaced")] == ["resurfaced"]

    # 6. Delegate it; nobody follows up for 6 working days -> stale -> close
    clock.set_now(datetime(2026, 11, 25, 9, 0, tzinfo=UTC))
    assert decide(client, item_id, outcome="delegate", delegated_to="Chen").status_code == 200
    clock.set_now(datetime(2026, 12, 3, 10, 0, tzinfo=UTC))  # Thu: 6 working days later
    candidates = client.get("/api/s3_resurface/candidates").json()["items"]
    assert item_id in [c["id"] for c in candidates]
    assert client.post(f"/api/s3_resurface/items/{item_id}/close").status_code == 200
    assert items.get_item(item_id).status == "closed_stale"

    # 7. Weekly review (S5) sees both weeks
    week1 = client.get("/api/s5_review/week", params={"start": "2026-11-23"}).json()
    assert week1["decided"]  # decisions + overrides logged in week 1
    assert any(o.get("id") == item_id for o in _overrides(week1)), week1["overrides"]
    week2 = client.get("/api/s5_review/week", params={"start": "2026-12-03"}).json()
    assert item_id in [r["id"] for r in week2["staleness_closures"]["items"]]


def _overrides(review):
    o = review["overrides"]
    return o.get("items", o.get("log", [])) if isinstance(o, dict) else o


def test_incident_fast_path_clears_reassess_without_a_note(client):
    clock.set_now(MON)
    created = client.post("/api/core/items", json={"title": "Prod is down"}).json()
    item_id = created["id"]
    assert decide(client, item_id, outcome="timebox", timebox_minutes=5).status_code == 200
    clock.set_now(MON + timedelta(minutes=6))
    s2.expire_due()
    assert items.get_item(item_id).extra["reassess"] == 1
    res = client.post(f"/api/s1_intake/items/{item_id}/incident")
    assert res.status_code == 200
    after = items.get_item(item_id)
    assert after.status == "active" and after.extra["reassess"] == 0
