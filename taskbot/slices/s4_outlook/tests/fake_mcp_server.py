"""A stand-in Outlook MCP server for tests, spoken to over real stdio.

    python fake_mcp_server.py <call_log_path> [signed_in|signed_out]

Every tool call that reaches the server is appended to ``call_log_path``, so tests
can prove a refused tool was never sent. Mirrors the tool names and output shapes
of @softeria/ms-365-mcp-server.
"""

from __future__ import annotations

import json
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

LOG = sys.argv[1]
SIGNED_IN = (sys.argv[2] if len(sys.argv) > 2 else "signed_in") == "signed_in"

server = Server("fake-ms365")


def _record(name: str, args: dict) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": name, "args": args}) + "\n")


@server.tool(name="verify-login")
def verify_login() -> str:
    _record("verify-login", {})
    if SIGNED_IN:
        return json.dumps({"success": True, "message": "Login successful"})
    return json.dumps({"success": False, "message": "Login failed: No valid token found"})


@server.tool(name="list-mail-folder-messages")
def list_mail_folder_messages(
    mailFolderId: str, top: int = 10, orderby: str = "", select: str = ""
) -> str:
    _record("list-mail-folder-messages", {"mailFolderId": mailFolderId, "top": top})
    return json.dumps(
        {
            "value": [
                {
                    "id": "mcp-1",
                    "conversationId": "mcp-conv-1",
                    "from": {"emailAddress": {"name": "Dana", "address": "dana@example.com"}},
                    "subject": "Can you restart the staging queue?",
                    "receivedDateTime": "2026-11-19T09:00:00Z",
                    "bodyPreview": "Could you restart it please? It's stuck.",
                }
            ]
        }
    )


@server.tool(name="send-mail")
def send_mail(to: str = "", body: str = "") -> str:
    _record("send-mail", {"to": to})
    return json.dumps({"sent": True})


if __name__ == "__main__":
    server.run()
