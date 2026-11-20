"""FR-32 / FR-33 and AC2, AC3: silent deferred items are offered for closure."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from taskbot.core import clock, db, items
from taskbot.slices.s3_resurface import service
from tests.fixtures import seed_item

# NOW (conftest) is Fri 2026-11-20 12:00 UTC.
DECIDED_FRI_13 = "2026-11-13T10:00:00+00:00"  # 7 calendar days ago = 5 working days
DECIDED_MON_16 = "2026-11-16T10:00:00+00:00"  # 4 working days ago
ACTIVITY_WED_18 = "2026-11-18T08:00:00+00:00"  # 2 days ago


def _events(item_id, kind):
    return items.list_events(item_id=item_id, kinds=[kind])


def _deferred(status="scheduled", **kw):
    values = {"decided_at": DECIDED_FRI_13, "title": f"Deferred {status}"}
    if status == "scheduled":
        values.update(outcome="schedule", scheduled_for="2026-12-15")
    else:
        values.update(outcome="delegate", delegated_to="Sam")
    values.update(kw)
    return seed_item(status=status, **values)


def _candidate_ids():
    return [c["id"] for c in service.list_candidates()]


# --- AC2 ------------------------------------------------------------------


def test_ac2_decided_seven_calendar_days_ago_is_a_candidate():
    item = _deferred()

    assert _candidate_ids() == [item.id]
    assert service.list_candidates()[0]["silent_days"] == 5
    assert service.scan_stale() == [item.id]

    [ev] = _events(item.id, "stale_suggested")
    assert ev.payload == {"silent_days": 5}
    with db.connect() as conn:
        assert service.get_state(item.id, conn=conn).suggested_at == clock.now_iso()


def test_ac2_activity_two_days_ago_is_not_a_candidate():
    item = _deferred(last_external_activity=ACTIVITY_WED_18)

    assert _candidate_ids() == []
    assert service.scan_stale() == []
    assert _events(item.id, "stale_suggested") == []


def test_four_working_days_is_not_yet_stale():
    _deferred(decided_at=DECIDED_MON_16)
    assert _candidate_ids() == []


def test_delegated_items_are_candidates_too():
    item = _deferred("delegated")
    assert _candidate_ids() == [item.id]


def test_future_scheduled_for_does_not_exempt_an_item():
    item = _deferred(scheduled_for="2027-03-01")
    assert _candidate_ids() == [item.id]


@pytest.mark.parametrize("status", ["tray", "active", "timeboxed", "declined", "done", "closed_stale"])
def test_other_statuses_are_never_candidates(status):
    seed_item(status=status, decided_at=DECIDED_FRI_13)
    assert _candidate_ids() == []
    assert service.scan_stale() == []


def test_stale_suggested_is_emitted_once_per_clock():
    item = _deferred()
    service.scan_stale()
    clock.set_now(clock.now() + timedelta(days=3))  # Mon 23rd: 6 silent days
    assert service.scan_stale() == []
    assert len(_events(item.id, "stale_suggested")) == 1


def test_new_external_activity_rearms_the_suggestion():
    item = _deferred()
    service.scan_stale()
    # The requester pings on Mon 23rd (S4 would set this column) ...
    items.update_fields(item.id, last_external_activity="2026-11-23T09:00:00+00:00")
    clock.set_now(datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert _candidate_ids() == []
    # ... then goes silent again for 5 working days (Mon 30th).
    clock.set_now(datetime(2026, 11, 30, 12, tzinfo=timezone.utc))
    assert service.scan_stale() == [item.id]
    assert [e.payload for e in _events(item.id, "stale_suggested")] == [
        {"silent_days": 5},
        {"silent_days": 5},
    ]


def test_clock_uses_the_local_date():
    # Decided Mon 16th 23:30 UTC = Tue 17th in UTC+2. By Mon 23rd that is
    # 5 working days in UTC but only 4 in UTC+2.
    _deferred(decided_at="2026-11-16T23:30:00+00:00")
    clock.set_now(datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert service.list_candidates(tz=timezone.utc) != []
    assert service.list_candidates(tz=timezone(timedelta(hours=2))) == []


def test_candidates_api_lists_and_suggests(client):
    item = _deferred(title="Update the wiki page")
    _deferred(decided_at=DECIDED_MON_16)

    data = client.get("/api/s3_resurface/candidates").json()

    assert data["threshold_days"] == 5
    [row] = data["items"]
    assert (row["id"], row["title"], row["silent_days"]) == (item.id, "Update the wiki page", 5)
    assert row["silent_since"] == DECIDED_FRI_13
    assert len(_events(item.id, "stale_suggested")) == 1
    client.get("/api/s3_resurface/candidates")
    assert len(_events(item.id, "stale_suggested")) == 1


# --- AC3: Close and Keep --------------------------------------------------


@pytest.mark.parametrize("status", ["scheduled", "delegated"])
def test_ac3_close_as_stale(client, status):
    item = _deferred(status)
    client.get("/api/s3_resurface/candidates")

    res = client.post(f"/api/s3_resurface/items/{item.id}/close")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "closed_stale"
    assert body["closed_at"] == clock.now_iso()
    [ev] = _events(item.id, "closed_stale")
    assert ev.payload == {"silent_days": 5}
    [changed] = _events(item.id, "status_changed")
    assert changed.payload == {"from": status, "to": "closed_stale"}
    assert _candidate_ids() == []


def test_ac3_keep_resets_the_clock(client):
    item = _deferred()
    client.get("/api/s3_resurface/candidates")

    res = client.post(f"/api/s3_resurface/items/{item.id}/keep")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "scheduled"
    assert body["stale_reset_at"] == clock.now_iso()
    assert body["stale_suggested_at"] is None
    [ev] = _events(item.id, "stale_kept")
    assert ev.payload == {"silent_days": 5}
    assert _events(item.id, "closed_stale") == []
    assert _candidate_ids() == []

    # Four working days later it is still quiet but not yet stale ...
    clock.set_now(datetime(2026, 11, 26, 12, tzinfo=timezone.utc))
    assert service.scan_stale() == []
    # ... and on the fifth it is offered again, once.
    clock.set_now(datetime(2026, 11, 27, 12, tzinfo=timezone.utc))
    assert service.scan_stale() == [item.id]
    assert service.scan_stale() == []
    assert len(_events(item.id, "stale_suggested")) == 2


def test_close_and_keep_reject_bad_requests(client):
    fresh = _deferred(decided_at=DECIDED_MON_16)
    tray = seed_item(status="tray", decided_at=DECIDED_FRI_13)

    assert client.post("/api/s3_resurface/items/9999/close").status_code == 404
    assert client.post("/api/s3_resurface/items/9999/keep").status_code == 404
    assert client.post(f"/api/s3_resurface/items/{fresh.id}/close").status_code == 409
    assert client.post(f"/api/s3_resurface/items/{tray.id}/close").status_code == 409
    assert client.post(f"/api/s3_resurface/items/{tray.id}/keep").status_code == 409
    assert items.get_item(fresh.id).status == "scheduled"
    assert items.get_item(tray.id).status == "tray"
    assert _events(fresh.id, "closed_stale") == []


def test_closing_twice_is_rejected(client):
    item = _deferred()
    assert client.post(f"/api/s3_resurface/items/{item.id}/close").status_code == 200
    assert client.post(f"/api/s3_resurface/items/{item.id}/close").status_code == 409
    assert len(_events(item.id, "closed_stale")) == 1
