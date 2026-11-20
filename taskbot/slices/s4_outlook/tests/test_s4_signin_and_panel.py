"""FR-42 (device-code sign-in surfaced, no crash while waiting), FR-46/47 panel rules."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from taskbot.slices.s4_outlook import mcp_client, signin

STATIC = Path(__file__).resolve().parents[1] / "static"

DEVICE_MSG = (
    "To sign in, use a web browser to open the page https://microsoft.com/devicelogin "
    "and enter the code ABCD-1234X to authenticate."
)


def _fake_login_script(tmp_path: Path, *, succeed: bool, release: Path) -> Path:
    """Mimics `ms-365-mcp-server --login`: prints the device code, waits, then reports."""
    script = tmp_path / "fake_login.py"
    script.write_text(
        "import sys, time, json, pathlib\n"
        "assert sys.argv[-1] == '--login'\n"
        f"print({DEVICE_MSG!r}, flush=True)\n"
        f"release = pathlib.Path({str(release)!r})\n"
        "for _ in range(200):\n"
        "    if release.exists(): break\n"
        "    time.sleep(0.05)\n"
        f"print(json.dumps({{'success': {succeed!r}, 'message': 'x'}}), flush=True)\n"
        f"sys.exit({0 if succeed else 1})\n"
    )
    return script


def _wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_parse_device_code():
    found = mcp_client.parse_device_code("\n" + DEVICE_MSG + "\n")
    assert found["url"] == "https://microsoft.com/devicelogin"
    assert found["code"] == "ABCD-1234X"
    assert mcp_client.parse_device_code("Login failed: No valid token found") is None


def test_fr42_device_code_is_shown_and_app_keeps_serving_while_waiting(client, monkeypatch, tmp_path):
    release = tmp_path / "release"
    script = _fake_login_script(tmp_path, succeed=True, release=release)
    monkeypatch.setenv("TASKBOT_MAIL", "mcp")
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", f'"{sys.executable}" "{script}"')

    started = client.post("/api/s4_outlook/signin")
    assert started.status_code == 200
    assert _wait_for(lambda: signin.state()["state"] == "waiting_for_user")

    shown = client.get("/api/s4_outlook/signin").json()
    assert shown["url"] == "https://microsoft.com/devicelogin" and shown["code"] == "ABCD-1234X"
    # while sign-in is pending the rest of the app still works
    assert client.get("/api/core/health").status_code == 200
    assert client.get("/api/s4_outlook/state").json()["status"]["signin"]["code"] == "ABCD-1234X"
    # a second click does not start a second login
    assert client.post("/api/s4_outlook/signin").json()["code"] == "ABCD-1234X"

    release.touch()
    assert _wait_for(lambda: signin.state()["state"] == "signed_in")
    assert signin.state()["code"] is None


def test_failed_signin_is_reported(monkeypatch, tmp_path):
    release = tmp_path / "release"
    release.touch()
    script = _fake_login_script(tmp_path, succeed=False, release=release)
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", f'"{sys.executable}" "{script}"')
    signin.start()
    assert _wait_for(lambda: signin.state()["state"] == "failed")
    assert signin.state()["error"]


def test_signin_with_missing_binary_does_not_raise(monkeypatch):
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", "definitely-not-a-real-binary-xyz")
    assert signin.start()["state"] == "failed"


def test_signin_endpoint_refused_in_fake_mode(client):
    assert client.post("/api/s4_outlook/signin").status_code == 409


def test_panel_manifest_order_and_title(client):
    manifest = json.loads((STATIC / "panel.json").read_text())
    assert manifest == {"title": "Mail", "module": "panel.js", "order": 50, "region": "mail"}
    panels = client.get("/api/core/panels").json()
    mail = next(p for p in panels if p["slice"] == "s4_outlook")
    assert mail["title"] == "Mail" and mail["order"] == 50
    assert client.get(mail["module"]).status_code == 200


def test_fr47_panel_never_notifies_and_never_uses_innerhtml():
    js = (STATIC / "panel.js").read_text()
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    assert "notify(" not in code  # FR-47: email never triggers a browser notification
    assert "Notification" not in code
    assert "innerHTML" not in code and "outerHTML" not in code and "insertAdjacentHTML" not in code
