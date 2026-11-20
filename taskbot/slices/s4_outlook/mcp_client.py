"""Read-only MCP client for the Outlook MCP server (FR-40, FR-41).

The server is launched over stdio from ``TASKBOT_MAIL_MCP_COMMAND``. Every tool
call goes through :class:`ReadOnlySession`, which refuses any tool name that is
not on the allowlist *before* anything is written to the server's stdin.

Default tool names were taken from ``@softeria/ms-365-mcp-server`` 0.187.0
(``tools/list`` with ``--preset mail --read-only``), probed without signing in.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from taskbot.core import config

DEFAULT_COMMAND = "npx -y @softeria/ms-365-mcp-server --preset mail --read-only"
DEFAULT_ALLOWLIST = (
    "verify-login",
    "list-mail-folder-messages",
    "list-mail-messages",
    "get-mail-message",
    "read-mail-text",
    "list-mail-folders",
)
# FR-41: "list/get/search only" (+ read-mail-text, a read-only triage view, and
# verify-login, a local token check). Entries outside these prefixes are a config error.
READ_ONLY_PREFIXES = ("list-", "get-", "search-", "read-", "verify-")

# Env vars passed through to the server process on top of the SDK's safe defaults:
# proxy / CA settings (corporate networks) and the server's own MS365_MCP_* options
# (e.g. MS365_MCP_CLIENT_ID for your own app registration).
_PASSTHROUGH_ENV = re.compile(
    r"^(https?_proxy|no_proxy|all_proxy|node_extra_ca_certs|ssl_cert_file|npm_config_\w+|ms365_mcp_\w+)$",
    re.IGNORECASE,
)

SELECT_FIELDS = "id,conversationId,from,subject,receivedDateTime,bodyPreview"


class MailError(RuntimeError):
    """The mail server could not be reached or returned an error."""


class ToolNotAllowed(PermissionError):
    """A tool call was refused by the read-only allowlist (never sent)."""


class NeedsSignIn(MailError):
    """The MCP server has no valid Microsoft token."""


# --- configuration -----------------------------------------------------------


def command() -> list[str]:
    raw = config.get("TASKBOT_MAIL_MCP_COMMAND") or DEFAULT_COMMAND
    argv = shlex.split(raw, posix=os.name != "nt")
    argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
    if not argv:
        raise MailError("TASKBOT_MAIL_MCP_COMMAND is empty")
    return argv


def allowlist() -> frozenset[str]:
    raw = config.get("TASKBOT_MAIL_TOOL_ALLOWLIST")
    names = [n.strip() for n in raw.split(",")] if raw else list(DEFAULT_ALLOWLIST)
    names = [n for n in names if n]
    bad = [n for n in names if not n.startswith(READ_ONLY_PREFIXES)]
    if bad:
        raise ValueError(
            f"TASKBOT_MAIL_TOOL_ALLOWLIST may only contain read-only tools "
            f"({'/'.join(p.rstrip('-') for p in READ_ONLY_PREFIXES)}); refused: {bad}"
        )
    return frozenset(names)


def folder() -> str:
    return config.get("TASKBOT_MAIL_FOLDER", "inbox") or "inbox"


def fetch_limit() -> int:
    return max(1, min(int(config.get("TASKBOT_MAIL_FETCH_LIMIT", "25") or "25"), 100))


def timeout_seconds() -> float:
    return float(config.get("TASKBOT_MAIL_TIMEOUT_SECONDS", "180") or "180")


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if _PASSTHROUGH_ENV.match(k)}


# --- allowlist enforcement ---------------------------------------------------


def check_allowed(name: str, allowed: frozenset[str] | None = None) -> None:
    allowed = allowlist() if allowed is None else allowed
    if name not in allowed:
        raise ToolNotAllowed(f"MCP tool {name!r} is not on the read-only allowlist")


class ReadOnlySession:
    """Wraps an MCP ``ClientSession``; only allowlisted ``call_tool`` gets through."""

    def __init__(self, session: Any, allowed: frozenset[str] | None = None) -> None:
        self._session = session
        self.allowed = allowlist() if allowed is None else allowed

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        check_allowed(name, self.allowed)  # raises before anything is sent
        return await self._session.call_tool(name, arguments or {})

    async def list_tools(self) -> Any:
        return await self._session.list_tools()


# --- result parsing ----------------------------------------------------------


def result_text(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def result_is_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


def result_json(result: Any) -> Any:
    text = result_text(result)
    try:
        return json.loads(text)
    except ValueError:
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        if structured is not None:
            return structured
        raise MailError(f"unexpected tool output: {text[:300]}") from None


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_CODE_RE = re.compile(r"\benter the code\s+([A-Z0-9-]{6,})", re.IGNORECASE)


def parse_device_code(text: str) -> dict[str, str] | None:
    """Pull the URL and code out of an MSAL device-code message, if present."""
    code = _CODE_RE.search(text or "")
    if not code:
        return None
    url = _URL_RE.search(text)
    message = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return {
        "url": url.group(0).rstrip(".,") if url else "https://microsoft.com/devicelogin",
        "code": code.group(1),
        "message": message[:500],
    }


# --- live session ------------------------------------------------------------


def _own_error(exc: BaseException) -> BaseException | None:
    """Find our own error inside an (anyio) exception group, if any."""
    if isinstance(exc, (ToolNotAllowed, MailError)):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _own_error(inner)
            if found is not None:
                return found
    return None


@asynccontextmanager
async def open_session() -> AsyncIterator[ReadOnlySession]:
    """Launch the configured MCP server over stdio and yield a read-only session."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    argv = command()
    params = StdioServerParameters(command=argv[0], args=argv[1:], env=child_env())
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errlog:
        try:
            async with stdio_client(params, errlog=errlog) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield ReadOnlySession(session)
        except (ToolNotAllowed, MailError):
            raise
        except Exception as exc:
            own = _own_error(exc)
            if own is not None:
                raise own from exc
            errlog.seek(0)
            tail = errlog.read()[-600:].strip()
            detail = f"{type(exc).__name__}: {exc}"
            raise MailError(f"MCP server failed ({detail}){': ' + tail if tail else ''}") from exc


async def fetch_with(session: ReadOnlySession) -> list[dict[str, Any]]:
    """Check sign-in, then list the newest messages in the configured folder."""
    login = await session.call_tool("verify-login", {})
    text = result_text(login)
    device = parse_device_code(text)
    if device:
        raise NeedsSignIn(device["message"])
    try:
        status = json.loads(text)
    except ValueError:
        status = {}
    if not (isinstance(status, dict) and status.get("success")):
        message = status.get("message") if isinstance(status, dict) else None
        raise NeedsSignIn(message or text[:300] or "not signed in")

    result = await session.call_tool(
        "list-mail-folder-messages",
        {
            "mailFolderId": folder(),
            "top": fetch_limit(),
            "orderby": "receivedDateTime desc",
            "select": SELECT_FIELDS,
        },
    )
    data = result_json(result)
    if result_is_error(result) or (isinstance(data, dict) and data.get("error")):
        err = data.get("error") if isinstance(data, dict) else data
        device = parse_device_code(json.dumps(err))
        if device:
            raise NeedsSignIn(device["message"])
        raise MailError(f"list-mail-folder-messages failed: {str(err)[:300]}")
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        return data["value"]
    if isinstance(data, list):
        return data
    raise MailError("list-mail-folder-messages returned no 'value' list")


async def fetch_messages() -> list[dict[str, Any]]:
    import anyio

    with anyio.fail_after(timeout_seconds()):
        async with open_session() as session:
            return await fetch_with(session)
