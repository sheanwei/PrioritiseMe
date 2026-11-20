"""S1 acceptance criteria (PRD §5 S1) as API tests, in fake mode unless stated."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskbot.core import clock, db, items, llm
from taskbot.slices.s1_intake import prompts
from tests.fixtures import seed_item


def stored_intake(item_id):
    with db.connect() as conn:
        row = conn.execute("SELECT intake_json FROM s1_intake WHERE item_id = ?", (item_id,)).fetchone()
    return json.loads(row["intake_json"])

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
BASE = "/api/s1_intake/items"

THREAD = "\n".join(
    ["Dana: Can you export last quarter's usage numbers for the finance report?"]
    + ["Sam: +1", "Lee: any update?", "Sam: +1 bump"]
    + [f"Msg {i}: me too" for i in range(11)]
)


@pytest.fixture(autouse=True)
def frozen():
    clock.set_now(NOW)


class StubClient:
    """OpenAI-like client returning queued replies (an Exception is raised)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else RuntimeError("no more replies")
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


@pytest.fixture
def real_llm(monkeypatch):
    """Real-mode LLM path with a stub client instead of the network."""
    monkeypatch.setenv("TASKBOT_LLM", "real")
    monkeypatch.setenv("OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    def use(replies):
        stub = StubClient(replies)
        llm.client_factory = lambda: stub
        return stub

    return use


def analysis(**overrides):
    data = json.loads(json.dumps(prompts.ANALYSIS_SCHEMA["examples"][0]))
    for key, value in overrides.items():
        if key in data["suggestion"]:
            data["suggestion"][key] = value
        else:
            data[key] = value
    return json.dumps(data)


def events(item_id, kind):
    return [e for e in items.list_events(item_id=item_id) if e.kind == kind]


# --- AC1: opening a seeded tray item shows the ask card and a suggestion (FR-10) ---

def test_open_shows_ask_card_and_suggestion(client):
    item = seed_item(title="Usage export for finance", raw_context=THREAD)
    res = client.post(f"{BASE}/{item.id}/analyze")
    assert res.status_code == 200
    state = res.json()

    card = state["ask_card"]
    assert card["request"]
    assert set(card) >= {"request", "requester", "deadline", "deadline_kind", "open_questions"}
    assert state["pressure_count"] > 0
    assert set(state["intake"]) == {"cost_of_delay", "deadline_real", "blocked_count", "delegable"}
    sug = state["suggestion"]
    assert sug["outcome"] == "schedule"
    assert sug["type"] == "interrupt"
    assert 1 <= sug["priority"] <= 4
    assert sug["rationale"] and sug["solution_sketch"]
    assert sug["rule_adjusted"] is False
    assert state["error"] is None

    stored = items.get_item(item.id)
    assert stored.ask_card == card
    assert stored.pressure_count == state["pressure_count"]
    [suggested] = events(item.id, "suggested")
    assert suggested.payload["outcome"] == "schedule"
    assert {"outcome", "type", "priority", "rationale"} <= set(suggested.payload)

    # Re-opening the item reuses the analysis: still one LLM call / one suggestion.
    again = client.post(f"{BASE}/{item.id}/analyze").json()
    assert again["suggestion"] == sug
    assert len(events(item.id, "suggested")) == 1
    assert client.get(f"{BASE}/{item.id}").json()["suggestion"] == sug


def test_open_makes_exactly_one_llm_call(client, real_llm):
    stub = real_llm([analysis()])
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    client.post(f"{BASE}/{item.id}/analyze")
    assert len(stub.calls) == 1


def test_panel_is_registered_and_served(client):
    panels = client.get("/api/core/panels").json()
    [panel] = [p for p in panels if p["slice"] == "s1_intake"]
    assert panel["title"] == "Intake" and panel["order"] == 20
    assert client.get(panel["module"]).status_code == 200


def test_panel_never_uses_innerhtml_and_thread_is_collapsed():
    js = (Path(__file__).parents[1] / "static" / "panel.js").read_text(encoding="utf-8")
    for sink in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js
    # FR-12: the raw thread sits in a <details> without `open`.
    assert 'h("details", { class: "s1-raw" }' in js


def test_unknown_item_and_non_tray_item(client):
    assert client.post(f"{BASE}/9999/analyze").status_code == 404
    assert client.get(f"{BASE}/9999").status_code == 404
    done = seed_item(status="active")
    assert client.post(f"{BASE}/{done.id}/analyze").status_code == 409


def test_prefilled_requester_survives_blank_llm_requester(client, real_llm):
    data = json.loads(analysis())
    data["ask_card"]["requester"] = ""
    real_llm([json.dumps(data)])
    item = seed_item(ask_card={"request": "", "requester": "ops@example.com", "deadline": None,
                               "deadline_kind": "none", "open_questions": []}, pressure_count=5)
    state = client.post(f"{BASE}/{item.id}/analyze").json()
    assert state["ask_card"]["requester"] == "ops@example.com"
    assert state["pressure_count"] == 5  # never lowered below what S4 already counted


# --- FR-19: commitments in the context; FR-10: thread truncated ---

def test_context_includes_current_commitments(client, real_llm):
    stub = real_llm([analysis()])
    seed_item(title="Migrate billing DB", status="active")
    seed_item(title="Look into flaky CI", status="timeboxed")
    seed_item(title="Rotate TLS certs", status="scheduled", scheduled_for="2026-11-25")
    seed_item(title="Old declined thing", status="declined")
    item = seed_item(title="New ask", raw_context="x" * 100_000)
    client.post(f"{BASE}/{item.id}/analyze")

    user = stub.calls[0]["messages"][1]["content"]
    assert "Migrate billing DB" in user
    assert "Look into flaky CI" in user
    assert "Rotate TLS certs (on 2026-11-25)" in user
    assert "Old declined thing" not in user
    assert len(user) < 25_000


# --- FR-11: editing an answer and re-suggesting ---

def test_resuggest_uses_edited_answers(client, real_llm):
    stub = real_llm([analysis(), analysis(outcome="delegate", priority=4)])
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    edited = {"cost_of_delay": "none", "deadline_real": "none", "blocked_count": 0, "delegable": True}
    res = client.post(f"{BASE}/{item.id}/resuggest",
                      json={"intake": edited, "production_impact": False, "size_known": True})
    assert res.status_code == 200
    state = res.json()
    assert state["intake"] == edited
    assert state["suggestion"]["outcome"] == "delegate"
    assert '"cost_of_delay": "none"' in stub.calls[1]["messages"][1]["content"]
    assert len(events(item.id, "suggested")) == 2


def test_resuggest_applies_guardrails_to_edited_facts(client):
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    state = client.post(f"{BASE}/{item.id}/resuggest", json={
        "intake": {"cost_of_delay": "minor", "deadline_real": "felt", "blocked_count": 1, "delegable": False},
        "production_impact": False, "size_known": False,
    }).json()
    assert state["suggestion"]["outcome"] == "timebox"
    assert state["suggestion"]["rule_adjusted"] is True
    assert state["size_known"] is False


def test_resuggest_rejects_bad_answers(client):
    item = seed_item()
    bad = {"intake": {"cost_of_delay": "huge"}}
    assert client.post(f"{BASE}/{item.id}/resuggest", json=bad).status_code == 422


# --- AC3 / FR-13: guardrails applied after the LLM ---

@pytest.mark.parametrize("llm_outcome", ["schedule", "decline", "delegate", "timebox"])
def test_incident_with_production_impact_is_suggested_do_now(client, real_llm, llm_outcome):
    real_llm([analysis(type="incident", outcome=llm_outcome, priority=3, production_impact=True)])
    item = seed_item(title="API 500s in prod")
    sug = client.post(f"{BASE}/{item.id}/analyze").json()["suggestion"]
    assert sug["outcome"] == "do_now"
    assert sug["rule_adjusted"] is True
    assert sug["llm_outcome"] == llm_outcome
    [ev] = events(item.id, "suggested")
    assert ev.payload["outcome"] == "do_now" and ev.payload["rule_adjusted"] is True


def test_unknown_size_is_suggested_timebox(client, real_llm):
    real_llm([analysis(outcome="do_now", size_known=False)])
    item = seed_item()
    sug = client.post(f"{BASE}/{item.id}/analyze").json()["suggestion"]
    assert sug["outcome"] == "timebox" and sug["rule_adjusted"] is True


def test_long_rationale_is_capped_at_three_sentences(client, real_llm):
    real_llm([analysis(rationale="One. Two. Three. Four. Five.")])
    item = seed_item()
    assert client.post(f"{BASE}/{item.id}/analyze").json()["suggestion"]["rationale"] == "One. Two. Three."


# --- AC2 / FR-15: override needs a reason, enforced server-side ---

@pytest.mark.parametrize("reason", [None, "", "   ", "\n\t"])
def test_override_without_reason_is_rejected(client, reason):
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")  # suggests schedule / interrupt / P3
    res = client.post(f"{BASE}/{item.id}/decide",
                      json={"outcome": "do_now", "type": "interrupt", "priority": 3, "override_reason": reason})
    assert res.status_code == 422
    assert "reason" in res.json()["detail"]
    assert items.get_item(item.id).status == "tray"
    assert events(item.id, "decided") == []
    assert events(item.id, "status_changed") == []


def test_changing_only_priority_or_type_is_also_an_override(client):
    item = seed_item()
    client.post(f"{BASE}/{item.id}/analyze")
    for body in ({"type": "interrupt", "priority": 1}, {"type": "toil", "priority": 3}):
        res = client.post(f"{BASE}/{item.id}/decide",
                          json={"outcome": "schedule", "scheduled_for": "2026-11-25", **body})
        assert res.status_code == 422


def test_override_with_reason_is_logged(client):
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    res = client.post(f"{BASE}/{item.id}/decide", json={
        "outcome": "decline", "type": "interrupt", "priority": 4,
        "override_reason": "  Finance can pull this\nfrom the dashboard  ",
    })
    assert res.status_code == 200
    assert items.get_item(item.id).status == "declined"
    [ev] = events(item.id, "decided")
    assert ev.payload["accepted"] is False
    assert ev.payload["override_reason"] == "Finance can pull this from the dashboard"
    assert ev.payload["outcome"] == "decline" and ev.payload["type"] == "interrupt"


# --- AC4 / FR-14, FR-16: every decision -> correct state + decided event ---

@pytest.mark.parametrize("outcome,extra,status,column,expected", [
    ("do_now", {}, "active", None, None),
    ("timebox", {"timebox_minutes": 45}, "timeboxed", "timebox_end", "2026-11-20T09:45:00+00:00"),
    ("timebox", {}, "timeboxed", "timebox_end", "2026-11-20T09:30:00+00:00"),
    ("schedule", {"scheduled_for": "2026-11-25"}, "scheduled", "scheduled_for", "2026-11-25"),
    ("delegate", {"delegated_to": " Priya "}, "delegated", "delegated_to", "Priya"),
    ("decline", {}, "declined", None, None),
])
def test_every_decision_moves_item_and_logs_decided(client, real_llm, outcome, extra, status, column, expected):
    real_llm([analysis(outcome=outcome, type="toil", priority=2)])
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    intake = {"cost_of_delay": "minor", "deadline_real": "felt", "blocked_count": 2, "delegable": False}
    res = client.post(f"{BASE}/{item.id}/decide",
                      json={"outcome": outcome, "type": "toil", "priority": 2, "intake": intake, **extra})
    assert res.status_code == 200, res.text

    after = items.get_item(item.id)
    assert after.status == status
    assert after.outcome == outcome and after.type == "toil" and after.priority == 2
    assert after.decided_at == NOW.isoformat()
    if column:
        assert getattr(after, column) == expected
    assert stored_intake(item.id) == intake

    [changed] = events(item.id, "status_changed")
    assert changed.payload == {"from": "tray", "to": status}
    [decided] = events(item.id, "decided")
    assert decided.payload["accepted"] is True
    assert decided.payload["outcome"] == outcome
    assert decided.payload["type"] == "toil"
    assert "override_reason" not in decided.payload
    assert res.json()["event"]["kind"] == "decided"


@pytest.mark.parametrize("outcome,extra", [
    ("schedule", {}),
    ("schedule", {"scheduled_for": "next week"}),
    ("schedule", {"scheduled_for": "2026-13-01"}),
    ("delegate", {}),
    ("delegate", {"delegated_to": "   "}),
    ("timebox", {"timebox_minutes": 0}),
])
def test_outcome_inputs_are_validated(client, outcome, extra):
    item = seed_item()
    res = client.post(f"{BASE}/{item.id}/decide", json={
        "outcome": outcome, "type": "interrupt", "priority": 3, "override_reason": "testing", **extra})
    assert res.status_code == 422
    assert items.get_item(item.id).status == "tray"


def test_cannot_decide_an_item_twice(client):
    item = seed_item()
    body = {"outcome": "decline", "type": "interrupt", "priority": 4}
    assert client.post(f"{BASE}/{item.id}/decide", json=body).status_code == 200
    assert client.post(f"{BASE}/{item.id}/decide", json=body).status_code == 409
    assert len(events(item.id, "decided")) == 1


def test_returned_item_is_reanalysed_and_old_suggestion_not_reused(client, real_llm):
    stub = real_llm([analysis(outcome="timebox"), analysis(outcome="schedule")])
    item = seed_item(raw_context=THREAD)
    client.post(f"{BASE}/{item.id}/analyze")
    client.post(f"{BASE}/{item.id}/decide", json={"outcome": "timebox", "type": "interrupt", "priority": 3})
    items.transition(item.id, "tray")  # e.g. S2 expires the timebox

    clock.set_now(NOW + timedelta(minutes=31))
    state = client.get(f"{BASE}/{item.id}").json()
    assert state["suggestion"] is None  # stale analysis hidden
    state = client.post(f"{BASE}/{item.id}/analyze").json()
    assert state["suggestion"]["outcome"] == "schedule"
    assert len(stub.calls) == 2


# --- FR-17: incident fast path ---

def test_incident_fast_path_then_log_after(client):
    item = seed_item(title="Checkout is down")
    res = client.post(f"{BASE}/{item.id}/incident")
    assert res.status_code == 200
    after = items.get_item(item.id)
    assert (after.status, after.type, after.outcome, after.priority) == ("active", "incident", "do_now", 1)
    [decided] = events(item.id, "decided")
    assert decided.payload["incident_fast_path"] is True
    assert decided.payload["outcome"] == "do_now" and decided.payload["type"] == "incident"
    assert decided.payload["accepted"] is True
    assert [p["id"] for p in client.get("/api/s1_intake/pending-log").json()] == [item.id]

    items.transition(item.id, "done")
    res = client.put(f"{BASE}/{item.id}/log", json={
        "ask_card": {"request": "Restore checkout", "requester": "on-call alert", "deadline": None,
                     "deadline_kind": "real", "open_questions": [" Root cause? ", ""]},
        "intake": {"cost_of_delay": "significant", "deadline_real": "real", "blocked_count": 500,
                   "delegable": False},
    })
    assert res.status_code == 200
    logged = items.get_item(item.id)
    assert logged.ask_card["request"] == "Restore checkout"
    assert logged.ask_card["open_questions"] == ["Root cause?"]
    assert stored_intake(item.id)["cost_of_delay"] == "significant"
    assert client.get("/api/s1_intake/pending-log").json() == []


def test_incident_fast_path_only_from_tray(client):
    item = seed_item(status="scheduled")
    assert client.post(f"{BASE}/{item.id}/incident").status_code == 409
    assert client.post(f"{BASE}/9999/incident").status_code == 404


# --- AC5 / FR-18: LLM failure, the manual path still works ---

def test_llm_failure_shows_error_and_manual_decision_works(client, real_llm):
    stub = real_llm([RuntimeError("boom"), RuntimeError("boom again")])
    item = seed_item(title="Something vague", raw_context=THREAD)

    res = client.post(f"{BASE}/{item.id}/analyze")
    assert res.status_code == 200
    state = res.json()
    assert "boom" in state["error"]
    assert state["suggestion"] is None
    assert state["intake"] is None
    assert len(stub.calls) == 2  # core retries once
    assert events(item.id, "suggested") == []

    res = client.post(f"{BASE}/{item.id}/decide", json={
        "outcome": "schedule", "type": "interrupt", "priority": 3, "scheduled_for": "2026-11-27",
        "intake": {"cost_of_delay": None, "deadline_real": None, "blocked_count": None, "delegable": None},
    })
    assert res.status_code == 200, res.text
    after = items.get_item(item.id)
    assert after.status == "scheduled" and after.scheduled_for == "2026-11-27"
    [decided] = events(item.id, "decided")
    assert decided.payload["accepted"] is False
    assert decided.payload["manual"] is True
    assert decided.payload["override_reason"]


def test_llm_invalid_json_is_a_failure_too(client, real_llm):
    real_llm(["not json", '{"still": "wrong"}'])
    item = seed_item()
    state = client.post(f"{BASE}/{item.id}/analyze").json()
    assert state["error"] and state["suggestion"] is None


def test_resuggest_failure_keeps_answers_and_reports_error(client, real_llm):
    real_llm([RuntimeError("down"), RuntimeError("down")])
    item = seed_item()
    edited = {"cost_of_delay": "minor", "deadline_real": "felt", "blocked_count": 1, "delegable": True}
    state = client.post(f"{BASE}/{item.id}/resuggest", json={"intake": edited}).json()
    assert "down" in state["error"]
    assert state["intake"] == edited
    assert stored_intake(item.id) == edited


def test_analysis_finishing_after_a_decision_is_dropped(client, monkeypatch):
    item = seed_item()
    real_complete = llm.complete_json

    def decide_meanwhile(*args, **kwargs):
        client.post(f"{BASE}/{item.id}/incident")  # user hits the fast path while the LLM runs
        return real_complete(*args, **kwargs)

    monkeypatch.setattr(llm, "complete_json", decide_meanwhile)
    assert client.post(f"{BASE}/{item.id}/analyze").status_code == 409
    assert items.get_item(item.id).ask_card is None
    assert events(item.id, "suggested") == []
