"""AC3: a tool not on the allowlist is rejected before it is sent (FR-41), plus the
real stdio MCP client path (FR-40) against a local stand-in server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from taskbot.core import items
from taskbot.slices.s4_outlook import mcp_client, service
from taskbot.slices.s4_outlook.mcp_client import ReadOnlySession, ToolNotAllowed

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")


class RecordingSession:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def call_tool(self, name, arguments=None):
        self.sent.append(name)


# --- AC3 (unit) ----------------------------------------------------------------


@pytest.mark.parametrize("tool", ["send-mail", "delete-mail-message", "move-mail-message",
                                  "update-mail-message", "create-draft-email", "login", "bogus"])
def test_ac3_non_allowlisted_tool_is_rejected_before_sending(tool):
    inner = RecordingSession()
    session = ReadOnlySession(inner)
    with pytest.raises(ToolNotAllowed):
        asyncio.run(session.call_tool(tool, {"to": "x@example.com"}))
    assert inner.sent == [], "the refused call must never reach the server"


def test_allowlisted_tools_pass_through():
    inner = RecordingSession()
    session = ReadOnlySession(inner)
    for tool in mcp_client.DEFAULT_ALLOWLIST:
        asyncio.run(session.call_tool(tool, {}))
    assert inner.sent == list(mcp_client.DEFAULT_ALLOWLIST)


def test_default_allowlist_is_read_only_names():
    names = mcp_client.allowlist()
    assert names == frozenset(mcp_client.DEFAULT_ALLOWLIST)
    assert all(n.startswith(mcp_client.READ_ONLY_PREFIXES) for n in names)


def test_allowlist_is_configurable(monkeypatch):
    monkeypatch.setenv("TASKBOT_MAIL_TOOL_ALLOWLIST", "verify-login, list-mail-messages")
    assert mcp_client.allowlist() == {"verify-login", "list-mail-messages"}
    with pytest.raises(ToolNotAllowed):
        mcp_client.check_allowed("get-mail-message")


def test_allowlist_config_cannot_add_write_tools(monkeypatch):
    monkeypatch.setenv("TASKBOT_MAIL_TOOL_ALLOWLIST", "list-mail-messages,send-mail")
    with pytest.raises(ValueError, match="send-mail"):
        mcp_client.allowlist()


def test_command_comes_from_config(monkeypatch):
    monkeypatch.delenv("TASKBOT_MAIL_MCP_COMMAND", raising=False)
    assert mcp_client.command()[:3] == ["npx", "-y", "@softeria/ms-365-mcp-server"]
    assert "--read-only" in mcp_client.command()
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", "node my-server.js --flag")
    assert mcp_client.command() == ["node", "my-server.js", "--flag"]


# --- FR-40 + AC3 over real stdio --------------------------------------------------


def _use_fake_server(monkeypatch, tmp_path, mode="signed_in") -> Path:
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("TASKBOT_MAIL", "mcp")
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", f'"{sys.executable}" "{FAKE_SERVER}" "{log}" {mode}')
    monkeypatch.setenv("TASKBOT_MAIL_TIMEOUT_SECONDS", "60")
    return log


def _calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [json.loads(line)["tool"] for line in log.read_text().splitlines()]


def test_stdio_client_fetches_mail_and_creates_items(monkeypatch, tmp_path):
    log = _use_fake_server(monkeypatch, tmp_path)
    result = asyncio.run(service.sync("test"))
    assert result["ok"], result
    assert result["new"] == 1 and result["created"] == 1
    assert [i.title for i in items.list_items()] == ["Can you restart the staging queue?"]
    assert _calls(log) == ["verify-login", "list-mail-folder-messages"]
    assert service.status()["connection"] == "ok"


def test_ac3_over_stdio_refused_tool_never_reaches_server(monkeypatch, tmp_path):
    log = _use_fake_server(monkeypatch, tmp_path)

    async def scenario() -> None:
        async with mcp_client.open_session() as session:
            tools = {t.name for t in (await session.list_tools()).tools}
            assert "send-mail" in tools  # the server offers it...
            with pytest.raises(ToolNotAllowed):
                await session.call_tool("send-mail", {"to": "boss@example.com", "body": "hi"})
            await session.call_tool("verify-login", {})

    asyncio.run(scenario())
    assert _calls(log) == ["verify-login"], "send-mail must never be sent"


def test_signed_out_server_reports_needs_signin_without_crashing(monkeypatch, tmp_path):
    _use_fake_server(monkeypatch, tmp_path, mode="signed_out")
    result = asyncio.run(service.sync("test"))
    assert result["ok"] is False and result["needs_signin"] is True
    assert "No valid token" in result["error"]
    assert service.status()["connection"] == "needs_signin"
    assert items.list_items() == []


def test_missing_server_binary_is_a_reported_error(monkeypatch):
    monkeypatch.setenv("TASKBOT_MAIL", "mcp")
    monkeypatch.setenv("TASKBOT_MAIL_MCP_COMMAND", "definitely-not-a-real-binary-xyz --preset mail")
    result = asyncio.run(service.sync("test"))
    assert result["ok"] is False
    assert service.status()["connection"] == "error"
