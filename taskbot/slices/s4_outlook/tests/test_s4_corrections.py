"""AC4: correcting a classification creates or archives the matching item (FR-46)."""

from __future__ import annotations

import asyncio

from taskbot.core import db, items
from taskbot.core.models import EventKind, Source, Status


def _sync() -> dict:
    from taskbot.slices.s4_outlook import service

    return asyncio.run(service.sync("test"))


def _email(email_id: str) -> dict:
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone())


def test_ac4_fyi_marked_needs_action_creates_item_and_logs_correction(client):
    client.post("/api/s4_outlook/sync")
    res = client.post("/api/s4_outlook/emails/msg-006/correct", json={"classification": "needs_action"})
    assert res.status_code == 200
    body = res.json()
    assert body["from"] == "fyi" and body["to"] == "needs_action" and body["action"] == "created"

    item = items.get_item(body["item_id"])
    assert item.status == Status.TRAY and item.source == Source.EMAIL
    assert item.source_ref == "conv-offsite"
    assert item.title == "Announcement: team offsite moved to 4 December"
    events = items.list_events(item_id=item.id, kinds=[EventKind.CLASSIFICATION_CORRECTED])
    assert [e.payload for e in events] == [{"from": "fyi", "to": "needs_action"}]

    row = _email("msg-006")
    assert row["classification"] == "needs_action" and row["user_corrected"] == 1
    assert row["llm_classification"] == "fyi"


def test_ac4_needs_action_marked_not_actionable_archives_tray_item(client):
    client.post("/api/s4_outlook/sync")
    item_id = _email("msg-005")["item_id"]
    assert items.get_item(item_id).status == Status.TRAY

    body = client.post("/api/s4_outlook/emails/msg-005/correct", json={"classification": "fyi"}).json()
    assert body["action"] == "archived" and body["item_id"] == item_id

    item = items.get_item(item_id)
    assert item.status == Status.DECLINED  # the only legal way out of the tray
    assert item.closed_at is not None
    assert item.outcome is None  # not a decision: S1/S5 can tell it apart
    kinds = [e.kind for e in items.list_events(item_id=item_id)]
    assert kinds[-2:] == [EventKind.STATUS_CHANGED, EventKind.CLASSIFICATION_CORRECTED]
    assert items.list_events(item_id=item_id, kinds=["classification_corrected"])[0].payload == {
        "from": "needs_action", "to": "fyi"}
    assert all(i.id != item_id for i in items.list_items(Status.TRAY))


def test_correcting_a_followup_removes_its_pressure_bump(client):
    client.post("/api/s4_outlook/sync")
    item_id = _email("msg-004")["item_id"]
    assert items.get_item(item_id).pressure_count == 1
    body = client.post("/api/s4_outlook/emails/msg-004/correct", json={"classification": "noise"}).json()
    assert body["action"] == "unbumped"
    item = items.get_item(item_id)
    assert item.pressure_count == 0 and item.status == Status.TRAY


def test_correcting_into_an_open_conversation_bumps_instead_of_duplicating(client):
    client.post("/api/s4_outlook/sync")
    # make msg-004 non-actionable first, then flip it back
    client.post("/api/s4_outlook/emails/msg-004/correct", json={"classification": "fyi"})
    body = client.post("/api/s4_outlook/emails/msg-004/correct", json={"classification": "needs_action"}).json()
    assert body["action"] == "bumped"
    assert len(items.list_items(source=Source.EMAIL, source_ref="conv-db3-access")) == 1


def test_decided_item_is_left_alone_but_correction_is_logged(client):
    client.post("/api/s4_outlook/sync")
    item_id = _email("msg-001")["item_id"]
    items.transition(item_id, Status.SCHEDULED, outcome="schedule", scheduled_for="2026-12-01")
    body = client.post("/api/s4_outlook/emails/msg-001/correct", json={"classification": "fyi"}).json()
    assert body["action"] == "left_unchanged:scheduled"
    assert items.get_item(item_id).status == Status.SCHEDULED
    assert items.list_events(item_id=item_id, kinds=["classification_corrected"])


def test_noise_to_fyi_touches_no_items(client):
    client.post("/api/s4_outlook/sync")
    before = len(items.list_items())
    body = client.post("/api/s4_outlook/emails/msg-003/correct", json={"classification": "fyi"}).json()
    assert body["action"] == "none"
    assert len(items.list_items()) == before
    assert _email("msg-003")["user_corrected"] == 1


def test_same_class_is_a_no_op_and_bad_input_rejected(client):
    client.post("/api/s4_outlook/sync")
    body = client.post("/api/s4_outlook/emails/msg-002/correct", json={"classification": "fyi"}).json()
    assert body["action"] == "none"
    assert _email("msg-002")["user_corrected"] == 0
    assert client.post("/api/s4_outlook/emails/nope/correct", json={"classification": "fyi"}).status_code == 404
    assert client.post("/api/s4_outlook/emails/msg-002/correct", json={"classification": "spam"}).status_code == 422


def test_corrections_feed_back_into_next_classification(client, stub_llm):
    client.post("/api/s4_outlook/sync")
    client.post("/api/s4_outlook/emails/msg-006/correct", json={"classification": "needs_action"})
    stub = stub_llm(lambda prompt: "fyi")
    from taskbot.slices.s4_outlook import service

    service.ingest([{"id": "later", "subject": "Another offsite update", "from": "eng-office@example.com",
                     "receivedDateTime": "2026-11-20T00:00:00Z", "bodyPreview": "..."}])
    prompt = stub.calls[0]["messages"][-1]["content"]
    assert "Announcement: team offsite moved to 4 December" in prompt
    assert "=> correct class: needs_action" in prompt
