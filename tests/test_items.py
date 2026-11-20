from datetime import datetime, timezone

import pytest

from taskbot.core import clock, db, items
from taskbot.core.models import (
    ALLOWED_TRANSITIONS,
    OUTCOME_TO_STATUS,
    TERMINAL_STATUSES,
    Outcome,
    Status,
    is_open,
)
from tests.fixtures import seed_event, seed_item

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def test_create_item_goes_to_tray_and_logs_created():
    clock.set_now(T0)
    item = items.create_item("  Check disk alert on db-3 ", raw_context="thread...")
    assert item.title == "Check disk alert on db-3"
    assert item.status == "tray"
    assert item.source == "manual"
    assert item.created_at == "2026-11-20T09:00:00+00:00"
    events = items.list_events(item_id=item.id)
    assert [(e.kind, e.payload) for e in events] == [("created", {"source": "manual"})]


def test_create_item_rejects_blank_title_and_bad_source():
    with pytest.raises(ValueError):
        items.create_item("   ")
    with pytest.raises(ValueError):
        items.create_item("x", source="slack")


def test_illegal_transition_raises_and_logs_nothing():
    item = items.create_item("x")
    with pytest.raises(items.InvalidTransition):
        items.transition(item.id, Status.DONE)  # tray -> done is not allowed
    assert items.get_item(item.id).status == "tray"
    assert [e.kind for e in items.list_events(item_id=item.id)] == ["created"]


def test_legal_transition_updates_fields_and_logs_status_changed():
    item = items.create_item("x")
    moved = items.transition(
        item.id, Status.SCHEDULED, outcome=Outcome.SCHEDULE, scheduled_for="2026-11-25"
    )
    assert moved.status == "scheduled"
    assert moved.outcome == "schedule"
    assert moved.scheduled_for == "2026-11-25"
    assert moved.closed_at is None
    last = items.list_events(item_id=item.id)[-1]
    assert last.kind == "status_changed"
    assert last.payload == {"from": "tray", "to": "scheduled"}


def test_terminal_transition_sets_closed_at():
    clock.set_now(T0)
    item = items.create_item("x")
    done = items.transition(item.id, Status.DECLINED)
    assert done.closed_at == "2026-11-20T09:00:00+00:00"
    assert not is_open(done.status)


@pytest.mark.parametrize("source", list(Status))
def test_every_disallowed_transition_is_rejected(source):
    for target in Status:
        if target in ALLOWED_TRANSITIONS[source]:
            continue
        item = seed_item(status=source.value)
        with pytest.raises(items.InvalidTransition):
            items.transition(item.id, target)


def test_state_machine_shape():
    assert TERMINAL_STATUSES == {Status.DECLINED, Status.DONE, Status.CLOSED_STALE}
    for outcome, status in OUTCOME_TO_STATUS.items():
        assert status in ALLOWED_TRANSITIONS[Status.TRAY], outcome


def test_update_fields_guards_and_ask_card_roundtrip():
    item = items.create_item("x")
    card = {"request": "Fix it", "requester": "a@b.c", "deadline": None,
            "deadline_kind": "none", "open_questions": []}
    updated = items.update_fields(item.id, ask_card=card, pressure_count=3, type="interrupt")
    assert updated.ask_card == card
    assert updated.to_dict()["ask_card"] == card
    assert "ask_card_json" not in updated.to_dict()
    assert updated.pressure_count == 3
    with pytest.raises(ValueError, match="transition"):
        items.update_fields(item.id, status="done")
    with pytest.raises(ValueError, match="unknown"):
        items.update_fields(item.id, nonsense=1)
    with pytest.raises(items.ItemNotFound):
        items.update_fields(9999, title="y")


def test_slice_added_columns_appear_in_extra_and_are_updatable():
    with db.connect() as conn:
        # A made-up column: real slice migrations add their own columns too, so
        # check this key rather than the whole of ``extra``.
        conn.execute("ALTER TABLE items ADD COLUMN zz_test_extra INTEGER DEFAULT 0")
    item = items.create_item("x", zz_test_extra=1)
    assert item.extra["zz_test_extra"] == 1
    assert items.update_fields(item.id, zz_test_extra=0).extra["zz_test_extra"] == 0


def test_list_items_filters_and_orders():
    a = seed_item(title="a", created_at="2026-11-20T09:00:00+00:00")
    b = seed_item(title="b", created_at="2026-11-20T10:00:00+00:00")
    seed_item(title="c", status="done", created_at="2026-11-20T11:00:00+00:00")
    e = seed_item(title="e", source="email", source_ref="conv-1",
                  created_at="2026-11-20T12:00:00+00:00")
    assert [i.title for i in items.list_items("tray")] == ["e", "b", "a"]
    assert [i.title for i in items.list_items(["tray"], newest_first=False)][:2] == ["a", "b"]
    assert [i.id for i in items.list_items(source="email")] == [e.id]
    assert [i.id for i in items.list_items(source_ref="conv-1")] == [e.id]
    assert len(items.list_items()) == 4
    assert a.id != b.id


def test_list_events_filters():
    item = seed_item()
    seed_event(item.id, "decided", {"accepted": True}, at="2026-11-16T09:00:00+00:00")
    seed_event(item.id, "decided", {"accepted": False}, at="2026-11-23T09:00:00+00:00")
    seed_event(item.id, "resurfaced", at="2026-11-17T09:00:00+00:00")
    week = items.list_events(
        kinds="decided", since="2026-11-16T00:00:00+00:00", until="2026-11-23T00:00:00+00:00"
    )
    assert [e.payload for e in week] == [{"accepted": True}]
    assert [e.kind for e in items.list_events(item_id=item.id)] == ["decided", "resurfaced", "decided"]


def test_shared_connection_groups_writes_in_one_transaction():
    with pytest.raises(RuntimeError):
        with db.connect() as conn:
            item = items.create_item("rolled back", conn=conn)
            items.transition(item.id, Status.ACTIVE, conn=conn)
            raise RuntimeError("boom")
    assert items.list_items() == []
