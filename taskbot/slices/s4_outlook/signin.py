"""First-run Microsoft sign-in via the MCP server's device-code flow (FR-42).

``start()`` runs ``<TASKBOT_MAIL_MCP_COMMAND> --login`` in a background thread.
That CLI prints MSAL's "open https://microsoft.com/devicelogin and enter the code
XXXX" message and then blocks until the owner finishes signing in (or it times
out). We parse the URL and code and expose them through ``state()`` so the
dashboard can show them. Nothing here blocks a request or the event loop.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import deque
from typing import Any

from taskbot.core import clock

from . import mcp_client

SIGNIN_TIMEOUT_SECONDS = 15 * 60

_lock = threading.Lock()
_state: dict[str, Any] = {"state": "idle"}
_proc: subprocess.Popen[str] | None = None


def state() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _set(**values: Any) -> None:
    with _lock:
        _state.update(values)


def reset() -> None:
    """Forget sign-in state (tests)."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _proc.kill()
        _proc = None
        _state.clear()
        _state["state"] = "idle"


def note_device_code(device: dict[str, str]) -> None:
    """Record a device-code prompt seen elsewhere (e.g. in a tool result)."""
    _set(state="waiting_for_user", **device, at=clock.now_iso(), error=None)


def start() -> dict[str, Any]:
    """Start the device-code login if it is not already running. Returns ``state()``."""
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return dict(_state)
        argv = mcp_client.command()
        exe = shutil.which(argv[0]) or argv[0]
        env = _login_env()
        try:
            _proc = subprocess.Popen(
                [exe, *argv[1:], "--login"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            _state.clear()
            _state.update(state="failed", error=f"could not start sign-in: {exc}", at=clock.now_iso())
            return dict(_state)
        _state.clear()
        _state.update(state="starting", started_at=clock.now_iso())
        proc = _proc
    threading.Thread(target=_watch, args=(proc,), name="s4-signin", daemon=True).start()
    threading.Thread(target=_timeout, args=(proc,), name="s4-signin-timeout", daemon=True).start()
    return state()


def _login_env() -> dict[str, str]:
    from mcp.client.stdio import get_default_environment

    return {**get_default_environment(), **mcp_client.child_env()}


def _watch(proc: subprocess.Popen[str]) -> None:
    tail: deque[str] = deque(maxlen=40)
    buffer = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        tail.append(line.rstrip())
        buffer = (buffer + line)[-4000:]
        if state().get("state") != "waiting_for_user":
            device = mcp_client.parse_device_code(buffer)
            if device:
                note_device_code(device)
    code = proc.wait()
    output = "\n".join(tail)
    if code == 0 and '"success":true' in output.replace(" ", ""):
        _set(state="signed_in", url=None, code=None, message="Signed in.", at=clock.now_iso(), error=None)
    else:
        _set(state="failed", at=clock.now_iso(), error=(output[-600:] or f"sign-in exited with code {code}"))


def _timeout(proc: subprocess.Popen[str]) -> None:
    try:
        proc.wait(timeout=SIGNIN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
