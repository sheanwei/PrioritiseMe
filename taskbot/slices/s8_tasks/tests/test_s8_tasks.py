from taskbot.core import items
from tests.fixtures import seed_item

BASE = "/api/s8_tasks"


def _group(data, status):
    return next(g for g in data["groups"] if g["status"] == status)


def test_groups_in_order_and_only_open_triaged_items(client):
    seed_item(title="still in tray")  # untriaged: not a task yet
    seed_item(title="finished", status="done")
    seed_item(title="declined", status="declined")
    seed_item(title="doing", status="active", priority=1)
    seed_item(title="boxed", status="timeboxed", timebox_end="2026-11-20T10:00:00+00:00")
    seed_item(title="later", status="scheduled", scheduled_for="2026-11-26")
    seed_item(title="handed off", status="delegated", delegated_to="Chen")

    data = client.get(f"{BASE}/open").json()
    assert [g["label"] for g in data["groups"]] == ["Doing now", "Timeboxed", "Scheduled", "Delegated"]
    assert data["total"] == 4
    assert [[i["title"] for i in g["items"]] for g in data["groups"]] == [
        ["doing"], ["boxed"], ["later"], ["handed off"]]
    assert _group(data, "delegated")["items"][0]["delegated_to"] == "Chen"


def test_sorted_by_priority_then_group_date(client):
    seed_item(title="P3 soon", status="scheduled", priority=3, scheduled_for="2026-11-21")
    seed_item(title="P1 late", status="scheduled", priority=1, scheduled_for="2026-12-10")
    seed_item(title="P1 early", status="scheduled", priority=1, scheduled_for="2026-11-25")
    seed_item(title="no priority", status="scheduled", scheduled_for="2026-11-20")
    seed_item(title="box P2 later", status="timeboxed", priority=2, timebox_end="2026-11-20T11:00:00+00:00")
    seed_item(title="box P2 sooner", status="timeboxed", priority=2, timebox_end="2026-11-20T10:00:00+00:00")

    data = client.get(f"{BASE}/open").json()
    assert [i["title"] for i in _group(data, "scheduled")["items"]] == [
        "P1 early", "P1 late", "P3 soon", "no priority"]
    assert [i["title"] for i in _group(data, "timeboxed")["items"]] == ["box P2 sooner", "box P2 later"]


def test_mark_done_from_active_and_delegated(client):
    a = seed_item(title="a", status="active")
    d = seed_item(title="d", status="delegated", delegated_to="Chen")
    for item in (a, d):
        res = client.post(f"{BASE}/items/{item.id}/done")
        assert res.status_code == 200
        after = items.get_item(item.id)
        assert after.status == "done" and after.closed_at
        kinds = [e.kind for e in items.list_events(item_id=item.id)]
        assert kinds[-2:] == ["status_changed", "completed"]
    assert client.get(f"{BASE}/open").json()["total"] == 0


def test_mark_done_refused_elsewhere(client):
    s = seed_item(status="scheduled", scheduled_for="2026-11-26")
    t = seed_item(status="tray")
    assert client.post(f"{BASE}/items/{s.id}/done").status_code == 409
    assert client.post(f"{BASE}/items/{t.id}/done").status_code == 409
    assert client.post(f"{BASE}/items/9999/done").status_code == 404
    rows = client.get(f"{BASE}/open").json()["groups"][2]["items"]
    assert rows[0]["can_mark_done"] is False


def test_panel_is_an_open_list_footer_section(client):
    panel = next(p for p in client.get("/api/core/panels").json() if p["id"] == "s8_tasks")
    assert panel["region"] == "list-footer" and panel["open"] is True
    assert panel["order"] < 45  # above "Probably not needed"
    assert client.get(panel["module"]).status_code == 200
