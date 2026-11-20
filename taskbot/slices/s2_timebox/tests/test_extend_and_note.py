"""FR-23 (re-assess note hook) and FR-24 (extend), plus the panel manifest."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from taskbot.core import clock, items
from taskbot.slices.s2_timebox import service
from tests.fixtures import seed_item

NOW = datetime(2026, 11, 20, 9, 0, 0, tzinfo=timezone.utc)
BASE = "/api/s2_timebox"


def extended_events(item_id: int):
    return items.list_events(item_id=item_id, kinds=[service.EVENT_EXTENDED])


# ---------------------------------------------------------------- FR-24 extend


def test_extend_adds_15_minutes_and_logs_each_extension(client):
    clock.set_now(NOW)
    item = seed_item(status="timeboxed", timebox_end=clock.to_iso(NOW + timedelta(minutes=5)))

    r1 = client.post(f"{BASE}/timeboxes/{item.id}/extend")
    r2 = client.post(f"{BASE}/timeboxes/{item.id}/extend")

    assert r1.status_code == 200
    assert r1.json()["item"]["timebox_end"] == "2026-11-20T09:20:00+00:00"
    assert r2.json()["item"]["timebox_end"] == "2026-11-20T09:35:00+00:00"
    assert [e.payload for e in extended_events(item.id)] == [{"minutes": 15}, {"minutes": 15}]
    assert items.get_item(item.id).status == "timeboxed"


def test_extend_after_end_but_before_sweep_counts_from_now(client):
    clock.set_now(NOW)
    item = seed_item(status="timeboxed", timebox_end=clock.to_iso(NOW - timedelta(seconds=10)))
    res = client.post(f"{BASE}/timeboxes/{item.id}/extend").json()
    assert res["item"]["timebox_end"] == "2026-11-20T09:15:00+00:00"
    assert service.expire_due() == []  # the extension wins over the pending expiry


def test_extend_only_while_timeboxed(client):
    clock.set_now(NOW)
    tray = seed_item(status="tray")
    assert client.post(f"{BASE}/timeboxes/{tray.id}/extend").status_code == 409
    assert client.post(f"{BASE}/timeboxes/9999/extend").status_code == 404
    assert extended_events(tray.id) == []


# ---------------------------------------------------------------- FR-23 note hook


def expired_item(**kw):
    clock.set_now(NOW)
    item = seed_item(status="timeboxed", timebox_end=clock.to_iso(NOW - timedelta(minutes=1)), **kw)
    service.expire_due()
    return item


def test_note_required_until_saved_then_readable_by_s1(client):
    item = expired_item(title="Flaky deploy")

    got = client.get(f"{BASE}/note/{item.id}").json()
    assert got == {
        "item_id": item.id, "title": "Flaky deploy", "status": "tray", "reassess": True,
        "note": None, "required": True, "expired_at": "2026-11-20T09:00:00+00:00",
    }

    put = client.put(f"{BASE}/note/{item.id}", json={"note": "  Root cause is a DNS TTL  "})
    assert put.status_code == 200
    assert put.json()["note"] == "Root cause is a DNS TTL"
    assert put.json()["required"] is False

    got = client.get(f"{BASE}/note/{item.id}").json()
    assert got["note"] == "Root cause is a DNS TTL" and got["required"] is False
    assert items.get_item(item.id).extra["reassess_note"] == "Root cause is a DNS TTL"
    assert [e.kind for e in items.list_events(item_id=item.id, kinds=[service.EVENT_NOTE_SAVED])] == [
        service.EVENT_NOTE_SAVED
    ]


def test_blank_note_is_rejected(client):
    item = expired_item()
    assert client.put(f"{BASE}/note/{item.id}", json={"note": "   "}).status_code == 422
    assert client.put(f"{BASE}/note/{item.id}", json={}).status_code == 422
    assert client.put(f"{BASE}/note/{item.id}", json={"note": "x" * 4001}).status_code == 422
    assert client.get(f"{BASE}/note/{item.id}").json()["required"] is True


def test_note_only_for_flagged_items(client):
    plain = seed_item(status="tray")
    assert client.get(f"{BASE}/note/{plain.id}").json()["required"] is False
    assert client.put(f"{BASE}/note/{plain.id}", json={"note": "hi"}).status_code == 409
    assert client.get(f"{BASE}/note/9999").status_code == 404
    assert client.put(f"{BASE}/note/9999", json={"note": "hi"}).status_code == 404


def test_reassess_list_and_clear_hook(client):
    item = expired_item(title="Re-assess me")
    other = seed_item(status="tray")

    rows = client.get(f"{BASE}/reassess").json()
    assert [r["item_id"] for r in rows] == [item.id]
    assert other.id not in [r["item_id"] for r in rows]

    cleared = client.post(f"{BASE}/reassess/{item.id}/clear")
    assert cleared.status_code == 200
    assert cleared.json()["extra"]["reassess"] == 0
    assert client.get(f"{BASE}/reassess").json() == []
    assert client.get(f"{BASE}/note/{item.id}").json()["required"] is False
    # Idempotent; logs once.
    client.post(f"{BASE}/reassess/{item.id}/clear")
    assert len(items.list_events(item_id=item.id, kinds=[service.EVENT_REASSESS_CLEARED])) == 1
    assert client.post(f"{BASE}/reassess/9999/clear").status_code == 404


def test_clear_reassess_joins_a_callers_transaction():
    from taskbot.core import db

    item = expired_item()
    with db.connect() as conn:
        service.clear_reassess(item.id, conn=conn)
        items.transition(item.id, "scheduled", conn=conn, outcome="schedule", scheduled_for="2026-11-25")
    after = items.get_item(item.id)
    assert after.status == "scheduled" and after.extra["reassess"] == 0


def test_reassess_list_hides_items_that_left_the_tray(client):
    item = expired_item()
    items.transition(item.id, "active", outcome="do_now")
    assert client.get(f"{BASE}/reassess").json() == []


# ---------------------------------------------------------------- panel


def test_panels_registered_status_30_and_detail_15(client):
    panels = client.get("/api/core/panels").json()
    mine = [{k: p[k] for k in ("id", "title", "module", "order")} for p in panels if p["slice"] == "s2_timebox"]
    assert sorted(mine, key=lambda p: p["id"]) == [
        {"id": "s2_timebox-reassess", "title": "Re-assess", "module": "/slices/s2_timebox/reassess.js", "order": 15},
        {"id": "s2_timebox-status", "title": "Timeboxes", "module": "/slices/s2_timebox/status.js", "order": 30},
    ]
    manifest = json.loads((Path(__file__).resolve().parents[1] / "static" / "panel.json").read_text())
    assert [m["region"] for m in manifest] == ["status", "detail"]
    for path in ("status.js", "reassess.js", "countdown.js", "panel.css"):
        assert client.get(f"/slices/s2_timebox/{path}").status_code == 200
