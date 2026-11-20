"""FR-30 / FR-31 and AC1: scheduled items come back on their date."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taskbot.core import clock, items, scheduler
from taskbot.slices.s3_resurface import router, service
from tests.fixtures import seed_event, seed_item

PLUS_2 = timezone(timedelta(hours=2))
MINUS_8 = timezone(timedelta(hours=-8))


def _kinds(item_id):
    return [e.kind for e in items.list_events(item_id=item_id)]


def _scheduled(day, **kw):
    return seed_item(
        status="scheduled", outcome="schedule", scheduled_for=day,
        decided_at="2026-11-18T09:00:00+00:00", **kw,
    )


# --- AC1 ------------------------------------------------------------------


def test_ac1_item_scheduled_for_yesterday_resurfaces():
    item = _scheduled("2026-11-19")

    assert service.resurface_due() == [item.id]

    after = items.get_item(item.id)
    assert after.status == "tray"
    assert _kinds(item.id) == ["status_changed", "resurfaced"]
    resurfaced = items.list_events(item_id=item.id, kinds=["resurfaced"])
    assert resurfaced[0].payload == {}
    assert resurfaced[0].at == clock.now_iso()
    changed = items.list_events(item_id=item.id, kinds=["status_changed"])[0]
    assert changed.payload == {"from": "scheduled", "to": "tray"}


def test_job_is_idempotent():
    item = _scheduled("2026-11-19")
    service.tick()
    service.tick()
    assert service.resurface_due() == []
    assert _kinds(item.id).count("resurfaced") == 1


def test_today_resurfaces_future_and_other_statuses_do_not():
    today = _scheduled("2026-11-20")
    tomorrow = _scheduled("2026-11-21")
    long_ago = _scheduled("2026-10-01")
    delegated = seed_item(status="delegated", scheduled_for="2026-11-01")
    unscheduled = seed_item(status="scheduled", scheduled_for=None)

    moved = service.resurface_due()

    assert sorted(moved) == sorted([today.id, long_ago.id])
    assert items.get_item(tomorrow.id).status == "scheduled"
    assert items.get_item(delegated.id).status == "delegated"
    assert items.get_item(unscheduled.id).status == "scheduled"


def test_today_is_the_local_date():
    # 23:30 UTC on Fri 20th is already Sat 21st in UTC+2, still Fri 20th in UTC-8.
    clock.set_now(datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc))
    sat = _scheduled("2026-11-21")

    assert service.resurface_due(tz=MINUS_8) == []
    assert service.resurface_due(tz=timezone.utc) == []
    assert service.resurface_due(tz=PLUS_2) == [sat.id]


def test_startup_registers_job_that_runs_on_start(client):
    item = _scheduled("2026-11-19")
    job = {j.name: j for j in scheduler.jobs()}[router.JOB_NAME]
    assert job.run_on_start
    assert job.every_seconds <= 24 * 3600

    assert scheduler.run_all_once()[router.JOB_NAME] is True
    assert items.get_item(item.id).status == "tray"


# --- FR-31: "Resurfacing today" ------------------------------------------


def test_resurfaced_today_lists_only_todays_resurfacings():
    yesterday_item = seed_item(status="tray", title="Came back yesterday")
    seed_event(yesterday_item.id, "resurfaced", at="2026-11-19T15:00:00+00:00")
    first = _scheduled("2026-11-19", title="First")
    service.resurface_due()
    clock.set_now(clock.now() + timedelta(hours=1))
    second = _scheduled("2026-11-20", title="Second")
    service.resurface_due()

    listed = service.resurfaced_today()

    assert [r["id"] for r in listed] == [second.id, first.id]  # newest first
    assert listed[0]["resurfaced_at"] == "2026-11-20T13:00:00+00:00"
    assert listed[0]["status"] == "tray"


def test_resurfaced_today_uses_local_day_bounds():
    item = seed_item(status="tray")
    # 22:30 UTC on the 19th = 00:30 on the 20th in UTC+2.
    seed_event(item.id, "resurfaced", at="2026-11-19T22:30:00+00:00")

    assert service.resurfaced_today(tz=timezone.utc) == []
    assert [r["id"] for r in service.resurfaced_today(tz=PLUS_2)] == [item.id]


def test_resurfaced_api(client):
    item = _scheduled("2026-11-19", title="Renew TLS cert")
    assert client.post("/api/s3_resurface/run").json()["resurfaced"] == [item.id]

    data = client.get("/api/s3_resurface/resurfaced").json()

    assert data["date"] == "2026-11-20"
    assert [(i["id"], i["title"]) for i in data["items"]] == [(item.id, "Renew TLS cert")]


def test_panels_registered_and_served(client):
    panels = client.get("/api/core/panels").json()
    mine = [p for p in panels if p["slice"] == "s3_resurface"]
    assert [(p["title"], p["order"]) for p in mine] == [
        ("Resurfacing today", 40),
        ("Probably not needed", 45),
    ]
    for p in mine:
        res = client.get(p["module"])
        assert res.status_code == 200
        assert "export function mount" in res.text
        assert "innerHTML" not in res.text
