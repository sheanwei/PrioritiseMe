import json
from types import SimpleNamespace

import pytest

from taskbot.core import db, items, llm, scheduler
from taskbot.slices.s7_links import service
from tests.fixtures import seed_item


class StubClient:
    """Mimics openai.OpenAI().chat.completions.create, returning queued replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("TASKBOT_LLM", "real")
    monkeypatch.setenv("OPENCODE_MODEL", "test-model")
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")


def use_stub(*replies):
    stub = StubClient([r if isinstance(r, (str, Exception)) else json.dumps(r) for r in replies])
    llm.client_factory = lambda: stub
    return stub


def links(*entries):
    return {"links": [{"other_id": o, "role": r, "reason": why} for o, r, why in entries]}


def events(item_id, kind):
    return [e.payload for e in items.list_events(item_id=item_id, kinds=[kind])]


def link_rows():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM item_links ORDER BY id")]


# --- detection --------------------------------------------------------------


def test_fake_mode_finds_no_links():
    a, b = seed_item(title="A"), seed_item(title="B")
    assert service.detect_links(a.id) == []
    assert link_rows() == []
    assert service.is_scanned(a.id) and not service.is_scanned(b.id)


def test_detect_creates_links_and_events_on_both_sides(real_mode):
    a = seed_item(title="A", raw_context="x" * 2000, ask_card={"request": "Send doc A"})
    b = seed_item(title="B")
    c = seed_item(title="C")
    done = seed_item(title="Done", status="done")
    stub = use_stub(
        links(
            (b.id, "blocks", "B needs A first"),
            (c.id, "blocked_by", "C   unlocks A"),
            (a.id, "blocks", "self"),  # self-link
            (999, "blocks", "unknown id"),
            (done.id, "blocks", "closed item"),
            (b.id, "blocked_by", "duplicate of b"),
        )
    )
    new = service.detect_links(a.id)

    assert [(n["other_id"], n["role"]) for n in new] == [(b.id, "blocks"), (c.id, "blocked_by")]
    assert [(r["blocker_id"], r["blocked_id"], r["source"]) for r in link_rows()] == [
        (a.id, b.id, "ai"),
        (c.id, a.id, "ai"),
    ]
    assert events(a.id, "linked") == [
        {"other_id": b.id, "role": "blocks", "reason": "B needs A first"},
        {"other_id": c.id, "role": "blocked_by", "reason": "C unlocks A"},
    ]
    assert events(b.id, "linked") == [{"other_id": a.id, "role": "blocked_by", "reason": "B needs A first"}]
    assert events(c.id, "linked") == [{"other_id": a.id, "role": "blocks", "reason": "C unlocks A"}]
    assert service.is_scanned(a.id)

    # Context: other open items only, ask card request, raw_context capped.
    user = stub.calls[0]["messages"][1]["content"]
    assert "Send doc A" in user and "x" * 600 in user and "x" * 601 not in user
    assert f'"id": {done.id}' not in user


def test_existing_pair_is_not_duplicated_in_either_direction(real_mode):
    a, b = seed_item(title="A"), seed_item(title="B")
    use_stub(links((b.id, "blocks", "first")), links((a.id, "blocked_by", "again")))
    assert len(service.detect_links(a.id)) == 1
    assert service.detect_links(b.id) == []
    assert len(link_rows()) == 1
    assert len(events(a.id, "linked")) == 1


def test_dismissed_pair_is_never_revived(real_mode):
    a, b = seed_item(title="A"), seed_item(title="B")
    use_stub(
        links((b.id, "blocks", "x")),
        links((b.id, "blocks", "x")),
        links((b.id, "blocked_by", "reverse")),
    )
    [link] = service.detect_links(a.id)
    service.dismiss_link(link["link_id"])
    assert len(events(a.id, "link_dismissed")) == 1 and len(events(b.id, "link_dismissed")) == 1

    assert service.detect_links(a.id) == []
    assert service.detect_links(a.id) == []
    assert [r["status"] for r in link_rows()] == ["dismissed"]
    assert service.links_for(a.id)["blocks"] == []
    # Dismissing again is a no-op.
    service.dismiss_link(link["link_id"])
    assert len(events(a.id, "link_dismissed")) == 1


def test_llm_error_records_nothing_and_reraises(real_mode):
    a, _b = seed_item(title="A"), seed_item(title="B")
    use_stub("nope", "still nope")
    with pytest.raises(llm.LLMError):
        service.detect_links(a.id)
    assert not service.is_scanned(a.id)
    assert link_rows() == []


def test_no_other_open_items_skips_the_llm(real_mode):
    a = seed_item(title="A")
    stub = use_stub()
    assert service.detect_links(a.id) == []
    assert stub.calls == [] and service.is_scanned(a.id)


def test_ensure_scanned_runs_once_and_swallows_llm_errors(real_mode):
    a, _b = seed_item(title="A"), seed_item(title="B")
    stub = use_stub("bad", "bad", links())
    service.ensure_scanned(a.id)  # LLMError swallowed, not recorded
    assert not service.is_scanned(a.id)
    service.ensure_scanned(a.id)
    service.ensure_scanned(a.id)
    assert len(stub.calls) == 3 and service.is_scanned(a.id)


# --- the live-test scenario -------------------------------------------------


def test_document_a_scenario(real_mode):
    email1 = seed_item(
        title="Team A needs Document A today",
        source="email",
        raw_context="Team A needs Document A today, production impact.",
        priority=2,
    )
    email2 = seed_item(
        title="Meet Team B for Document A",
        source="email",
        raw_context="Document A can only be obtained by meeting Team B.",
        status="scheduled",
        priority=4,
    )
    use_stub(links((email1.id, "blocks", "Document A for Team A can only come from Team B.")))
    service.detect_links(email2.id)

    two = service.links_for(email2.id)
    assert two["inherited_priority"] == 2
    assert two["inherited_from"] == {"id": email1.id, "title": email1.title, "priority": 2}
    assert [b["id"] for b in two["blocks"]] == [email1.id]

    one = service.links_for(email1.id)
    assert one["blocked_by"][0]["id"] == email2.id and one["inherited_priority"] is None
    assert service.summary()[email2.id] == {"blocks": 1, "blocked_by": 0, "inherited_priority": 2}


# --- inheritance ------------------------------------------------------------


def test_transitive_inheritance_with_cycle_and_closed_items():
    a = seed_item(title="A", priority=4)
    b = seed_item(title="B", priority=3)
    c = seed_item(title="C", priority=1)
    service.add_link(a.id, b.id, "a before b")
    service.add_link(b.id, c.id, "b before c")
    service.add_link(c.id, a.id, "cycle")

    res = service.links_for(a.id)
    assert res["inherited_priority"] == 1 and res["inherited_from"]["id"] == c.id
    assert service.links_for(c.id)["inherited_priority"] is None  # already best
    assert service.summary()[b.id]["inherited_priority"] == 1

    # Closing C: it no longer counts, and the chain through it is cut.
    with db.connect() as conn:
        conn.execute("UPDATE items SET status = 'done' WHERE id = ?", (c.id,))
    res = service.links_for(a.id)
    assert res["inherited_priority"] == 3 and res["inherited_from"]["id"] == b.id
    assert service.links_for(b.id)["blocks"] == []
    assert c.id not in service.summary()


def test_inheritance_ignores_unprioritised_and_worse_items():
    a = seed_item(title="A", priority=2)
    b = seed_item(title="B")  # no priority, no S1 suggestion
    d = seed_item(title="D", priority=3)
    service.add_link(a.id, b.id, "")
    service.add_link(a.id, d.id, "")
    assert service.links_for(a.id)["inherited_priority"] is None
    assert service.links_for(a.id)["inherited_from"] is None


def test_inheritance_uses_s1_suggestion_when_priority_is_null(monkeypatch):
    from taskbot.slices.s1_intake import service as s1

    a, b = seed_item(title="A"), seed_item(title="B")
    monkeypatch.setattr(s1, "suggested_priority", lambda i: 1 if i == b.id else None, raising=False)
    service.add_link(a.id, b.id, "")
    assert service.links_for(a.id)["inherited_priority"] == 1


# --- manual links -----------------------------------------------------------


def test_add_link_validation_and_events():
    a, b = seed_item(title="A"), seed_item(title="B")
    done = seed_item(title="Done", status="done")
    link = service.add_link(a.id, b.id, "  needs   it ")
    assert link["source"] == "user" and link["reason"] == "needs it"
    assert events(b.id, "linked") == [{"other_id": a.id, "role": "blocked_by", "reason": "needs it"}]
    assert service.add_link(a.id, b.id, "again")["link_id"] == link["link_id"]  # no duplicate
    with pytest.raises(service.Conflict):
        service.add_link(b.id, a.id, "reverse of an active link")
    with pytest.raises(service.Conflict):
        service.add_link(a.id, a.id, "self")
    with pytest.raises(service.Conflict):
        service.add_link(a.id, done.id, "closed")
    with pytest.raises(service.NotFound):
        service.add_link(a.id, 999, "missing")


# --- background job ---------------------------------------------------------


def test_scan_new_batch_limit_and_idempotence():
    ids = [seed_item(title=f"I{n}", created_at=f"2026-11-{n + 1:02d}T00:00:00+00:00").id for n in range(12)]
    seed_item(title="closed", status="done")
    assert service.scan_new() == 10
    assert [i for i in ids if service.is_scanned(i)] == ids[:10]  # oldest first
    assert service.scan_new() == 2
    assert service.scan_new() == 0


def test_scan_new_survives_llm_error(real_mode):
    seed_item(title="A"), seed_item(title="B")
    use_stub("bad", "bad")
    assert service.scan_new() == 0  # stops at the first failure without raising
    assert link_rows() == []


def test_startup_registers_job(client):
    assert "s7_links.scan" in [j.name for j in scheduler.jobs()]
    assert scheduler.run_all_once()["s7_links.scan"] is True


# --- API --------------------------------------------------------------------


def test_api_status_codes(client):
    a, b = seed_item(title="A"), seed_item(title="B")
    done = seed_item(title="Done", status="done")

    assert client.get("/api/s7_links/items/999").status_code == 404
    assert client.post("/api/s7_links/items/999/detect").status_code == 404
    assert client.post(f"/api/s7_links/items/{done.id}/detect").status_code == 409

    r = client.post(f"/api/s7_links/items/{a.id}/detect")
    assert r.status_code == 200 and r.json()["new"] == []
    assert r.json()["links"]["blocks"] == []

    post = lambda x, y: client.post("/api/s7_links/links", json={"blocker_id": x, "blocked_id": y, "reason": "r"})  # noqa: E731
    assert post(a.id, a.id).status_code == 409
    assert post(a.id, done.id).status_code == 409
    assert post(a.id, 999).status_code == 404
    r = post(a.id, b.id)
    assert r.status_code == 200
    link_id = r.json()["link_id"]

    got = client.get(f"/api/s7_links/items/{a.id}").json()
    assert got["blocks"][0]["id"] == b.id and got["blocks"][0]["link_id"] == link_id
    assert client.get("/api/s7_links/summary").json() == {
        str(a.id): {"blocks": 1, "blocked_by": 0, "inherited_priority": None},
        str(b.id): {"blocks": 0, "blocked_by": 1, "inherited_priority": None},
    }
    assert client.post(f"/api/s7_links/links/{link_id}/dismiss").json()["status"] == "dismissed"
    assert client.post("/api/s7_links/links/999/dismiss").status_code == 404
    assert client.get("/api/s7_links/summary").json() == {}


def test_api_detect_llm_failure_is_502(client, real_mode):
    a, _b = seed_item(title="A"), seed_item(title="B")
    use_stub("bad", "bad")
    assert client.post(f"/api/s7_links/items/{a.id}/detect").status_code == 502
