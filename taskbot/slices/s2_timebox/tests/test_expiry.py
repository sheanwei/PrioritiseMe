"""FR-21 / FR-22: expiry job and notify-once. AC1 and AC2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taskbot.core import clock, items, scheduler
from taskbot.core.models import EventKind
from taskbot.slices.s2_timebox import service
from tests.fixtures import seed_item

NOW = datetime(2026, 11, 20, 9, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return clock.to_iso(dt)


def timeboxed(end: datetime, **kw):
    return seed_item(status="timeboxed", outcome="timebox", timebox_end=iso(end), **kw)


def expired_events(item_id: int):
    return items.list_events(item_id=item_id, kinds=[EventKind.TIMEBOX_EXPIRED])


def test_startup_registers_30s_job(client):
    job = {j.name: j for j in scheduler.jobs()}[service.JOB_NAME]
    assert job.every_seconds == 30
    assert job.fn is service.expire_due


def test_ac1_ended_item_back_in_tray_flagged_after_one_tick(client):
    clock.set_now(NOW)
    item = timeboxed(NOW - timedelta(minutes=1), title="Check disk alert on db-3")

    assert scheduler.run_all_once()[service.JOB_NAME] is True  # one tick

    after = items.get_item(item.id)
    assert after.status == "tray"
    assert after.extra["reassess"] == 1
    assert after.extra["timebox_expired_at"] == iso(NOW)
    changes = items.list_events(item_id=item.id, kinds=[EventKind.STATUS_CHANGED])
    assert [e.payload for e in changes] == [{"from": "timeboxed", "to": "tray"}]
    assert len(expired_events(item.id)) == 1
    # Shows up in the tray (core API) and the re-assess list.
    tray_ids = [i["id"] for i in client.get("/api/core/items", params={"status": "tray"}).json()]
    assert item.id in tray_ids
    assert [r["item_id"] for r in client.get("/api/s2_timebox/reassess").json()] == [item.id]


def test_job_leaves_running_timeboxes_alone_and_expires_at_exact_end():
    clock.set_now(NOW)
    running = timeboxed(NOW + timedelta(seconds=1))
    at_end = timeboxed(NOW)
    no_end = seed_item(status="timeboxed", outcome="timebox")
    tray_item = seed_item(status="tray", timebox_end=iso(NOW - timedelta(hours=1)))

    assert service.expire_due() == [at_end.id]
    assert items.get_item(running.id).status == "timeboxed"
    assert items.get_item(no_end.id).status == "timeboxed"
    assert items.get_item(tray_item.id).extra["reassess"] == 0
    assert expired_events(tray_item.id) == []


def test_ac2_expiry_event_fires_exactly_once():
    clock.set_now(NOW)
    item = timeboxed(NOW - timedelta(seconds=5))

    assert service.expire_due() == [item.id]
    for minutes in (0, 1, 2):  # later ticks are no-ops
        clock.set_now(NOW + timedelta(minutes=minutes))
        assert service.expire_due() == []

    assert len(expired_events(item.id)) == 1
    assert expired_events(item.id)[0].payload == {}


def test_ac2_notification_claimed_exactly_once(client):
    clock.set_now(NOW)
    item = timeboxed(NOW - timedelta(seconds=5), title="Timebox me")
    service.expire_due()
    service.expire_due()

    pending = client.get("/api/s2_timebox/expiries/pending").json()["items"]
    assert [(p["id"], p["title"]) for p in pending] == [(item.id, "Timebox me")]

    # Two tabs race for it: only the first claim notifies.
    first = client.post(f"/api/s2_timebox/expiries/{item.id}/ack").json()
    second = client.post(f"/api/s2_timebox/expiries/{item.id}/ack").json()
    assert first["claimed"] is True
    assert second["claimed"] is False
    assert client.get("/api/s2_timebox/expiries/pending").json()["items"] == []
    assert client.post("/api/s2_timebox/expiries/999/ack").status_code == 404


def test_expiry_while_dashboard_closed_stays_pending_for_banner(client):
    clock.set_now(NOW)
    item = timeboxed(NOW - timedelta(minutes=10))
    service.expire_due()  # background job; nobody polls
    clock.set_now(NOW + timedelta(hours=3))  # dashboard opened much later
    pending = client.get("/api/s2_timebox/expiries/pending").json()["items"]
    assert [p["id"] for p in pending] == [item.id]
    assert pending[0]["expired_at"] == iso(NOW)


def test_second_timebox_on_same_item_expires_and_notifies_again(client):
    clock.set_now(NOW)
    item = timeboxed(NOW - timedelta(seconds=1))
    service.expire_due()
    client.post(f"/api/s2_timebox/expiries/{item.id}/ack")
    client.put(f"/api/s2_timebox/note/{item.id}", json={"note": "Needs a vendor ticket"})

    # Re-assessed and timeboxed again (what S1 does), then that one ends too.
    later = NOW + timedelta(hours=1)
    clock.set_now(later)
    items.transition(item.id, "timeboxed", outcome="timebox", timebox_end=iso(later + timedelta(minutes=30)))
    clock.set_now(later + timedelta(minutes=31))
    assert service.expire_due() == [item.id]

    after = items.get_item(item.id)
    assert after.extra["reassess"] == 1
    assert after.extra["reassess_note"] is None  # a fresh note is needed
    assert len(expired_events(item.id)) == 2
    assert client.post(f"/api/s2_timebox/expiries/{item.id}/ack").json()["claimed"] is True


def test_job_tolerates_timestamps_without_offset_or_with_z():
    clock.set_now(NOW)
    naive = seed_item(status="timeboxed", timebox_end="2026-11-20T08:59:00")
    zulu = seed_item(status="timeboxed", timebox_end="2026-11-20T08:59:00Z")
    garbage = seed_item(status="timeboxed", timebox_end="not a date")
    assert sorted(service.expire_due()) == sorted([naive.id, zulu.id])
    assert items.get_item(garbage.id).status == "timeboxed"
