"""AC1 (fixtures sort into three classes; only needs_action reaches the tray),
AC2 (a reply bumps pressure instead of duplicating), FR-43/44 details."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from taskbot.core import clock, db, items, scheduler
from taskbot.core.models import EventKind, Source, Status
from taskbot.slices.s4_outlook import prompts, router, service
from tests.fixtures import seed_item

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)


def _sync() -> dict:
    return asyncio.run(service.sync("test"))


def _classes() -> dict[str, str]:
    with db.connect() as conn:
        return {r["id"]: r["classification"] for r in conn.execute("SELECT id, classification FROM emails")}


# --- AC1 ---------------------------------------------------------------------


def test_ac1_fake_fixtures_sort_into_three_classes_and_only_needs_action_reaches_tray():
    clock.set_now(NOW)
    result = _sync()
    assert result["ok"] and result["fetched"] == 7 and result["new"] == 7

    classes = _classes()
    assert set(classes.values()) == {"needs_action", "fyi", "noise"}
    assert classes == {
        "msg-001": "needs_action",
        "msg-002": "fyi",
        "msg-003": "noise",
        "msg-004": "needs_action",  # reply in msg-001's conversation
        "msg-005": "needs_action",
        "msg-006": "fyi",
        "msg-007": "noise",
    }

    tray = items.list_items(Status.TRAY)
    assert sorted(i.title for i in tray) == [
        "Please review PR #482 (retry logic for the ingest worker)",
        "Read access to db-3 for the analytics job",
    ]
    for item in tray:
        assert item.source == Source.EMAIL
        assert item.type == "interrupt"
        assert item.status == "tray"
        assert item.source_ref in {"conv-db3-access", "conv-pr-482"}
        assert item.ask_card["requester"]  # pre-filled for S1
        assert [e.kind for e in items.list_events(item_id=item.id)][0] == EventKind.CREATED
    # FYI / noise never become items
    assert len(items.list_items()) == 2


def test_ac1_with_model_answers_only_needs_action_becomes_items(stub_llm):
    """Same AC on the real classification path (stubbed client): the model's
    answer, not the keyword guess, decides."""
    stub = stub_llm(lambda prompt: "noise" if "Cloud Weekly" in prompt.split("Classify this message:")[-1]
                    else "fyi" if "PR #482" in prompt.split("Classify this message:")[-1]
                    else "needs_action")
    _sync()
    classes = _classes()
    assert classes["msg-003"] == "noise"
    assert classes["msg-005"] == "fyi"
    assert classes["msg-002"] == "needs_action"
    assert all(call["model"] == "cheap-model" for call in stub.calls)  # FR-44 cheap model
    assert {i.source_ref for i in items.list_items()} == {
        "conv-db3-access", "conv-release-42", "conv-offsite", "conv-promo-bf",
    }


def test_resync_is_deduplicated_by_message_id():
    _sync()
    before = len(items.list_items())
    again = _sync()
    assert again["new"] == 0 and again["classified"] == 0
    assert len(items.list_items()) == before
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 7


# --- AC2 ---------------------------------------------------------------------


def test_ac2_reply_in_open_conversation_bumps_pressure_not_duplicate():
    clock.set_now(NOW)
    _sync()
    convo = items.list_items(source=Source.EMAIL, source_ref="conv-db3-access")
    assert len(convo) == 1, "the follow-up must not create a second item"
    item = convo[0]
    assert item.pressure_count == 1
    assert item.last_external_activity == "2026-11-19T05:30:00+00:00"  # the reply's time
    assert "any update on this?" in item.raw_context
    ext = items.list_events(item_id=item.id, kinds=[EventKind.EXTERNAL_ACTIVITY])
    assert [e.payload for e in ext] == [{"via": "email"}]


def test_ac2_reply_bumps_a_decided_but_open_item(stub_llm):
    """A scheduled (deferred) item is still open: a reply keeps S3's staleness clock honest."""
    stub_llm(lambda prompt: "needs_action")
    item = seed_item(title="Earlier ask", source="email", source_ref="conv-pr-482",
                     status="scheduled", scheduled_for="2026-12-01", pressure_count=2)
    service.ingest([{
        "id": "x-1", "conversationId": "conv-pr-482", "subject": "RE: Earlier ask",
        "from": {"emailAddress": {"address": "a@example.com"}},
        "receivedDateTime": "2026-11-20T01:00:00Z", "bodyPreview": "Ping?",
    }])
    after = items.get_item(item.id)
    assert after.status == "scheduled"
    assert after.pressure_count == 3
    assert after.last_external_activity == "2026-11-20T01:00:00+00:00"
    assert len(items.list_items(source=Source.EMAIL)) == 1


def test_reply_to_closed_conversation_creates_a_new_item(stub_llm):
    stub_llm(lambda prompt: "needs_action")
    old = seed_item(title="Old ask", source="email", source_ref="conv-z", status="done")
    service.ingest([{
        "id": "z-2", "conversationId": "conv-z", "subject": "RE: Old ask (again)",
        "from": "b@example.com", "receivedDateTime": "2026-11-20T01:00:00Z", "bodyPreview": "Again?",
    }])
    convo = items.list_items(source=Source.EMAIL, source_ref="conv-z")
    assert len(convo) == 2
    assert items.get_item(old.id).pressure_count == 0


# --- FR-44: last 10 corrections are few-shot examples ------------------------


def test_prompt_includes_last_ten_user_corrections(stub_llm):
    with db.connect() as conn:
        for n in range(12):
            conn.execute(
                'INSERT INTO emails (id, conversation_id, "from", subject, received_at, body_preview, '
                "classification, user_corrected, corrected_at, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'noise', 1, ?, ?)",
                (f"c-{n}", f"cc-{n}", "x@example.com", f"Corrected subject {n:02d}",
                 "2026-11-01T00:00:00+00:00", "body", f"2026-11-10T00:00:{n:02d}+00:00",
                 "2026-11-01T00:00:00+00:00"),
            )
    stub = stub_llm(lambda prompt: "fyi")
    service.ingest([{"id": "new-1", "subject": "Hello", "from": "y@example.com",
                     "receivedDateTime": "2026-11-20T00:00:00Z", "bodyPreview": "hi"}])
    prompt = stub.calls[0]["messages"][-1]["content"]
    included = [n for n in range(12) if f"Corrected subject {n:02d}" in prompt]
    assert included == list(range(2, 12)), "the 10 most recent corrections"
    assert prompt.index("Corrected subject 11") < prompt.index("Corrected subject 02")
    assert "=> correct class: noise" in prompt
    assert prompt.rstrip().endswith("hi")


def test_schema_has_examples_and_fake_mode_uses_keyword_guess():
    s = prompts.schema("noise")
    assert s["examples"][0]["classification"] == "noise"
    assert prompts.guess_classification({"subject": "Weekly newsletter", "body_preview": ""}) == "noise"


# --- NFR-4: LLM failure leaves mail unclassified, retried next sync ----------


def test_llm_failure_leaves_messages_unclassified_and_retries(stub_llm):
    def boom(prompt: str) -> str:
        raise RuntimeError("LLM down")

    stub_llm(boom)
    first = _sync()
    assert first["ok"] and first["failed"] == 7 and first["classified"] == 0
    assert set(_classes().values()) == {None}
    assert items.list_items() == []
    assert service.counts()["unclassified"] == 7

    stub_llm(lambda prompt: "fyi")
    second = _sync()
    assert second["classified"] == 7
    assert set(_classes().values()) == {"fyi"}


# --- FR-43: polling job + Sync now ------------------------------------------


def test_poll_job_registered_every_five_minutes_and_runs():
    router.startup()
    job = next(j for j in scheduler.jobs() if j.name == "s4_outlook.poll")
    assert job.every_seconds == 300
    assert scheduler.run_all_once() == {"s4_outlook.poll": True}
    assert len(items.list_items(Status.TRAY)) == 2


def test_sync_now_endpoint_and_state(client):
    assert any(j.name == "s4_outlook.poll" for j in scheduler.jobs())
    res = client.post("/api/s4_outlook/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] and body["created"] == 2 and body["bumped"] == 1

    state = client.get("/api/s4_outlook/state").json()
    assert state["counts"] == {"needs_action": 3, "fyi": 2, "noise": 2, "unclassified": 0}
    assert len(state["recent"]) == 7
    assert state["status"]["mode"] == "fake"
    assert state["status"]["last_sync"]["ok"] is True

    fyi = client.get("/api/s4_outlook/emails", params={"classification": ["fyi", "noise"]}).json()
    assert {e["classification"] for e in fyi} == {"fyi", "noise"} and len(fyi) == 4


def test_mail_fetch_failure_is_reported_not_raised(monkeypatch):
    async def broken() -> list:
        raise service.mcp_client.MailError("server exploded")

    monkeypatch.setattr(service, "fetch_raw", broken)
    result = _sync()
    assert result["ok"] is False and "server exploded" in result["error"]
    assert service.status()["connection"] == "error"
