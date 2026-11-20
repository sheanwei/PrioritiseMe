import json
import textwrap

from fastapi.testclient import TestClient

from taskbot.core.app import create_app


# --- AC1: quick-add item appears in the tray ---

def test_quick_add_appears_in_tray(client):
    res = client.post("/api/core/items", json={"title": "Check disk alert on db-3"})
    assert res.status_code == 201
    created = res.json()
    assert created["status"] == "tray"

    tray = client.get("/api/core/items", params={"status": "tray"}).json()
    assert [i["title"] for i in tray] == ["Check disk alert on db-3"]

    events = client.get(f"/api/core/items/{created['id']}/events").json()
    assert [e["kind"] for e in events] == ["created"]


def test_quick_add_with_thread_and_validation(client):
    res = client.post("/api/core/items", json={"title": "Reply to Sam", "raw_context": "  +1\n+1  "})
    assert res.json()["raw_context"] == "+1\n+1"
    assert client.post("/api/core/items", json={"title": ""}).status_code == 422
    assert client.post("/api/core/items", json={"title": "   "}).status_code == 422
    assert client.post("/api/core/items", json={}).status_code == 422


def test_item_endpoints(client):
    assert client.get("/api/core/items/999").status_code == 404
    assert client.get("/api/core/items/999/events").status_code == 404
    assert client.get("/api/core/items", params={"status": "bogus"}).status_code == 422


def test_shell_health_and_panels(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "TaskBot" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/panels/tray.js").status_code == 200

    health = client.get("/api/core/health").json()
    assert health["llm"]["mode"] == "fake"
    assert health["mail"]["mode"] == "fake"

    panels = client.get("/api/core/panels").json()
    assert panels[0]["id"] == "tray"


# --- slice discovery contract ---

def test_slice_discovery_mounts_router_static_panel_and_startup(tmp_path, monkeypatch):
    pkg = tmp_path / "fakeslices"
    (pkg / "demo" / "static").mkdir(parents=True)
    (pkg / "_ignored").mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "demo" / "__init__.py").write_text("")
    (pkg / "demo" / "router.py").write_text(textwrap.dedent("""
        from fastapi import APIRouter
        router = APIRouter()
        started = []

        @router.get("/ping")
        def ping():
            return {"pong": True, "started": started}

        def startup():
            started.append(True)
    """))
    (pkg / "_ignored" / "router.py").write_text("raise RuntimeError('must not import')")
    (pkg / "demo" / "static" / "panel.js").write_text("export function mount() {}")
    (pkg / "demo" / "static" / "panel.json").write_text(
        json.dumps([
            {"title": "Demo A", "module": "panel.js", "order": 5},
            {"id": "demo-b", "title": "Demo B", "module": "panel.js", "order": 50},
        ])
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    with TestClient(create_app(slices_dir=pkg, slices_package="fakeslices")) as c:
        assert c.get("/api/demo/ping").json() == {"pong": True, "started": [True]}
        assert c.get("/slices/demo/panel.js").status_code == 200
        panels = c.get("/api/core/panels").json()
        assert [(p["id"], p["module"]) for p in panels] == [
            ("demo-0", "/slices/demo/panel.js"),
            ("tray", "/static/panels/tray.js"),
            ("demo-b", "/slices/demo/panel.js"),
        ]
