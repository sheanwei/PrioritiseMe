"""S1 using S7 links: blockers inherit urgency (the "Document A" case).

S7 is faked by putting a module into ``sys.modules`` that follows S7's contract:
``ensure_scanned(item_id)`` and ``links_for(item_id)``.
"""

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from taskbot.core import clock, items, llm
from taskbot.slices.s1_intake import prompts, service
from taskbot.slices.s1_intake.guardrails import (
    RULE_INCIDENT,
    RULE_UNKNOWN_SIZE,
    apply_guardrails,
)
from tests.fixtures import seed_event, seed_item

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
BASE = "/api/s1_intake/items"


@pytest.fixture(autouse=True)
def frozen():
    clock.set_now(NOW)


# --- fakes ------------------------------------------------------------------


class StubClient:
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


def no_links():
    return {"blocks": [], "blocked_by": [], "inherited_priority": None, "inherited_from": None}


class FakeS7:
    """Canned links per item id. ``pending`` links appear (with `linked` events) on scan."""

    def __init__(self):
        self.links = {}
        self.pending = {}
        self.scanned = []
        self.error = None  # raised by links_for
        self.scan_error = None  # raised by ensure_scanned

    def ensure_scanned(self, item_id):
        if self.scan_error:
            raise self.scan_error
        self.scanned.append(item_id)
        for other_id, links in self.pending.pop(item_id, {}).items():
            self.links.update(links)
            items.log_event(item_id, "linked", {"other_id": other_id, "role": "blocks", "reason": "r"})
            items.log_event(other_id, "linked", {"other_id": item_id, "role": "blocked_by", "reason": "r"})

    def links_for(self, item_id):
        if self.error:
            raise self.error
        return self.links.get(item_id, no_links())


@pytest.fixture
def fake_s7(monkeypatch):
    fake = FakeS7()
    pkg = types.ModuleType("taskbot.slices.s7_links")
    pkg.__path__ = []
    mod = types.ModuleType(service.S7_MODULE)
    mod.ensure_scanned = fake.ensure_scanned
    mod.links_for = fake.links_for
    pkg.service = mod
    monkeypatch.setitem(sys.modules, "taskbot.slices.s7_links", pkg)
    monkeypatch.setitem(sys.modules, service.S7_MODULE, mod)
    return fake


def other(id_, title, priority, status="tray", reason="needs it"):
    return {"link_id": 1, "id": id_, "title": title, "status": status, "priority": priority,
            "reason": reason, "source": "llm"}


def blocks(id_=13, title="Team A needs Document A", priority=2, status="tray"):
    return {
        "blocks": [other(id_, title, priority, status, "Document A comes from the Team B meeting")],
        "blocked_by": [],
        "inherited_priority": priority,
        "inherited_from": {"id": id_, "title": title, "priority": priority},
    }


def sug(**kw):
    base = {"type": "interrupt", "outcome": "schedule", "priority": 4, "rationale": "r", "solution_sketch": ""}
    return {**base, **kw}


# --- guardrail unit tests -----------------------------------------------------


def test_no_links_changes_nothing():
    for links in (None, no_links()):
        out = apply_guardrails(sug(), production_impact=False, size_known=True, links=links)
        assert (out["outcome"], out["priority"], out["rule_adjusted"], out["rules"]) == ("schedule", 4, False, [])


def test_priority_inheritance():
    links = blocks(priority=3)  # P3: inherits, but not urgent enough for do_now
    out = apply_guardrails(sug(outcome="schedule", priority=4), production_impact=False,
                           size_known=True, links=links)
    assert out["priority"] == 3
    assert out["outcome"] == "schedule"
    assert out["rules"] == ["Blocks #13 (P3): inherits its priority."]
    assert out["rule_adjusted"] is True and out["llm_outcome"] == "schedule"


def test_inheritance_never_lowers_priority():
    out = apply_guardrails(sug(outcome="timebox", priority=1), production_impact=False,
                           size_known=True, links=blocks(priority=2))
    assert out["priority"] == 1 and out["rules"] == []


def test_inheritance_without_source_uses_generic_note():
    links = {**no_links(), "inherited_priority": 2}
    out = apply_guardrails(sug(outcome="delegate"), production_impact=False, size_known=True, links=links)
    assert out["priority"] == 2
    assert out["rules"] == ["Blocks more urgent work (P2): inherits its priority."]


@pytest.mark.parametrize("outcome", ["schedule", "decline"])
def test_urgent_blocker_is_do_now(outcome):
    out = apply_guardrails(sug(outcome=outcome, priority=4), production_impact=False,
                           size_known=True, links=blocks(priority=2))
    assert out["outcome"] == "do_now" and out["priority"] == 2
    assert out["rules"] == ["Blocks #13 (P2): inherits its priority.", "Unblocks urgent #13 (P2): do it now."]
    assert out["llm_outcome"] == outcome


@pytest.mark.parametrize("outcome", ["timebox", "delegate"])
def test_urgent_blocker_leaves_active_outcomes(outcome):
    out = apply_guardrails(sug(outcome=outcome, priority=2), production_impact=False,
                           size_known=True, links=blocks(priority=2))
    assert out["outcome"] == outcome and out["rules"] == []


@pytest.mark.parametrize("status", ["done", "declined", "closed_stale"])
def test_closed_blocked_item_is_not_urgent(status):
    links = {**no_links(), "blocks": [other(13, "x", 1, status)]}
    out = apply_guardrails(sug(), production_impact=False, size_known=True, links=links)
    assert out["outcome"] == "schedule" and out["rules"] == []


def test_urgent_blocker_then_size_unknown_makes_timebox():
    out = apply_guardrails(sug(outcome="schedule", priority=4), production_impact=False,
                           size_known=False, links=blocks(priority=1))
    assert out["outcome"] == "timebox" and out["priority"] == 1
    assert out["rules"] == [
        "Blocks #13 (P1): inherits its priority.",
        "Unblocks urgent #13 (P1): do it now.",
        RULE_UNKNOWN_SIZE,
    ]
    assert out["llm_outcome"] == "schedule"


def test_incident_rule_comes_first():
    out = apply_guardrails(sug(type="incident", outcome="schedule", priority=3), production_impact=True,
                           size_known=False, links=blocks(priority=2))
    assert (out["outcome"], out["priority"]) == ("do_now", 1)
    assert out["rules"] == [RULE_INCIDENT]


def test_link_guardrails_do_not_mutate_input():
    original, links = sug(), blocks()
    apply_guardrails(original, production_impact=False, size_known=True, links=links)
    assert original == sug() and links == blocks()


# --- context ------------------------------------------------------------------


def test_analyze_puts_links_in_the_prompt(client, real_llm, fake_s7):
    stub = real_llm([analysis()])
    item = seed_item(title="Get Document A from Team B")
    fake_s7.links[item.id] = {
        **blocks(),
        "blocked_by": [other(7, "Book a room with Team B", 3, "scheduled", "meeting needs a room")],
    }
    state = client.post(f"{BASE}/{item.id}/analyze").json()

    assert fake_s7.scanned == [item.id]
    user = stub.calls[0]["messages"][1]["content"]
    assert "## Linked items" in user
    assert ("This item BLOCKS #13 'Team A needs Document A' (P2, status tray): "
            "Document A comes from the Team B meeting") in user
    assert "This item IS BLOCKED BY #7 'Book a room with Team B' (P3, status scheduled)" in user
    assert "Inherited priority: P2 (from #13 'Team A needs Document A'" in user
    assert "no rush" in stub.calls[0]["messages"][0]["content"]  # the SYSTEM guidance
    assert state["links"] == fake_s7.links[item.id]
    assert client.get(f"{BASE}/{item.id}").json()["links"] == fake_s7.links[item.id]


def test_resuggest_applies_link_guardrail(client, real_llm, fake_s7):
    stub = real_llm([analysis(), analysis(outcome="decline", priority=4)])
    item = seed_item()
    fake_s7.links[item.id] = blocks(priority=1)
    client.post(f"{BASE}/{item.id}/analyze")
    edited = {"cost_of_delay": "none", "deadline_real": "none", "blocked_count": 0, "delegable": False}
    state = client.post(f"{BASE}/{item.id}/resuggest", json={"intake": edited}).json()
    sug_ = state["suggestion"]
    assert (sug_["outcome"], sug_["priority"]) == ("do_now", 1)
    assert "Unblocks urgent #13 (P1): do it now." in sug_["rules"]
    assert "## Linked items" in stub.calls[1]["messages"][1]["content"]
    assert state["links"] == blocks(priority=1)


# --- the Document A scenario ------------------------------------------------


def test_document_a_scenario(client, real_llm, fake_s7):
    stub = real_llm([
        analysis(type="interrupt", outcome="do_now", priority=2, production_impact=True,
                 rationale="Team A is blocked with production impact."),
        analysis(outcome="schedule", priority=4,
                 solution_sketch="Draft reply: no rush, I'll set up the Team B meeting next week."),
        analysis(type="interrupt", outcome="do_now", priority=2, production_impact=True),
    ])
    email1 = seed_item(title="Team A needs Document A", source="email",
                       raw_context="Team A needs Document A today, production impact")
    first = client.post(f"{BASE}/{email1.id}/analyze").json()
    assert first["suggestion"]["priority"] == 2 and first["links"] == no_links()

    # Email 2 arrives later; S7 finds that it blocks email 1 when it is scanned.
    clock.set_now(NOW + timedelta(minutes=10))
    email2 = seed_item(title="Document A requires a Team B meeting", source="email",
                       raw_context="Document A can only be obtained by meeting Team B")
    fake_s7.pending[email2.id] = {email1.id: {
        email2.id: blocks(id_=email1.id, priority=2),
        email1.id: {**no_links(), "blocked_by": [other(email2.id, "Document A requires a Team B meeting", None)]},
    }}
    state = client.post(f"{BASE}/{email2.id}/analyze").json()
    sug_ = state["suggestion"]
    assert sug_["outcome"] in ("do_now", "timebox")
    assert sug_["priority"] == 2
    assert sug_["llm_outcome"] == "schedule" and sug_["rule_adjusted"] is True
    assert sug_["rules"] == [
        f"Blocks #{email1.id} (P2): inherits its priority.",
        f"Unblocks urgent #{email1.id} (P2): do it now.",
    ]
    assert f"This item BLOCKS #{email1.id} 'Team A needs Document A' (P2, status tray)" in \
        stub.calls[1]["messages"][1]["content"]
    [ev] = [e for e in items.list_events(item_id=email2.id) if e.kind == "suggested"]
    assert ev.payload["outcome"] == "do_now" and ev.payload["priority"] == 2

    # Re-opening email 1: its analysis predates the link, so it is re-run with it.
    assert client.get(f"{BASE}/{email1.id}").json()["suggestion"] is None
    again = client.post(f"{BASE}/{email1.id}/analyze").json()
    assert len(stub.calls) == 3
    assert f"This item IS BLOCKED BY #{email2.id}" in stub.calls[2]["messages"][1]["content"]
    assert again["links"]["blocked_by"][0]["id"] == email2.id
    # ...and only once: the new analysis has seen that link.
    client.post(f"{BASE}/{email1.id}/analyze")
    client.post(f"{BASE}/{email2.id}/analyze")
    assert len(stub.calls) == 3


# --- freshness ------------------------------------------------------------------


def test_linked_event_after_analysis_makes_it_stale(client, real_llm):
    stub = real_llm([analysis(), analysis()])
    item = seed_item()
    client.post(f"{BASE}/{item.id}/analyze")
    client.post(f"{BASE}/{item.id}/analyze")
    assert len(stub.calls) == 1
    seed_event(item.id, "linked", {"other_id": 99, "role": "blocked_by", "reason": "r"})  # same second
    assert client.get(f"{BASE}/{item.id}").json()["suggestion"] is None
    client.post(f"{BASE}/{item.id}/analyze")
    assert len(stub.calls) == 2
    client.post(f"{BASE}/{item.id}/analyze")
    assert len(stub.calls) == 2


def test_legacy_analysis_without_event_id_uses_timestamps(client):
    item = seed_item()
    client.post(f"{BASE}/{item.id}/analyze")
    from taskbot.core import db
    with db.connect() as conn:
        row = conn.execute("SELECT analysis_json FROM s1_intake WHERE item_id = ?", (item.id,)).fetchone()
        legacy = json.loads(row["analysis_json"])
        legacy.pop("linked_event_id")
        conn.execute("UPDATE s1_intake SET analysis_json = ? WHERE item_id = ?", (json.dumps(legacy), item.id))
    seed_event(item.id, "linked", {}, at=clock.to_iso(NOW - timedelta(minutes=1)))
    assert client.get(f"{BASE}/{item.id}").json()["suggestion"] is not None
    seed_event(item.id, "linked", {}, at=clock.to_iso(NOW + timedelta(minutes=1)))
    assert client.get(f"{BASE}/{item.id}").json()["suggestion"] is None


# --- S7 absent or failing -----------------------------------------------------


def test_s7_absent_behaves_as_before(client, real_llm):
    stub = real_llm([analysis()])
    item = seed_item()
    state = client.post(f"{BASE}/{item.id}/analyze").json()
    assert state["links"] is None
    assert state["suggestion"]["outcome"] == "schedule" and state["suggestion"]["rules"] == []
    assert "Linked items" not in stub.calls[0]["messages"][1]["content"]
    assert client.get(f"{BASE}/{item.id}").json()["links"] is None


@pytest.mark.parametrize("where", ["scan", "links"])
def test_s7_raising_does_not_break_analysis(client, real_llm, fake_s7, where, caplog):
    stub = real_llm([analysis()])
    item = seed_item()
    fake_s7.links[item.id] = blocks()
    if where == "scan":
        fake_s7.scan_error = RuntimeError("s7 down")
    else:
        fake_s7.error = RuntimeError("s7 down")
    res =client.post(f"{BASE}/{item.id}/analyze")
    assert res.status_code == 200
    state = res.json()
    assert state["error"] is None and state["links"] is None
    assert state["suggestion"]["outcome"] == "schedule" and state["suggestion"]["rules"] == []
    assert "Linked items" not in stub.calls[0]["messages"][1]["content"]
    assert "s7 down" in caplog.text
