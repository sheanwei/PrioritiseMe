"""Configuration loaded from the environment and the project's .env file.

Settings are read fresh on every call to ``settings()`` so tests (and slices)
can change environment variables without restarting the process.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"  # NFR-1: never bind to a public interface
# Chat-completions endpoint listed by https://opencode.ai/zen/v1/models
DEFAULT_BASE_URL = "https://opencode.ai/inference/openai/v1"

# Real environment variables always win over .env values.
load_dotenv(ROOT / ".env", override=False)


def get(name: str, default: str | None = None) -> str | None:
    """Read any env var (slices use this for their own keys). Empty counts as unset."""
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = (get(name, default) or default).lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _flag(name: str, default: str) -> bool:
    return (get(name, default) or default).lower() in {"1", "true", "on", "yes"}


@dataclass(frozen=True)
class Settings:
    opencode_api_key: str | None
    opencode_base_url: str
    opencode_model: str | None
    opencode_model_cheap: str | None
    llm_mode: str  # "real" | "fake"
    mail_mode: str  # "mcp" | "fake"
    mail_mcp_command: str | None
    port: int
    db_path: Path
    scheduler_enabled: bool
    debug: bool
    # Extra JSON merged into every chat-completions request body, e.g.
    # {"thinking": {"type": "disabled"}} to skip reasoning for faster replies.
    opencode_extra_body: dict[str, Any] | None = None


def _json_object(name: str) -> dict[str, Any] | None:
    raw = get(name)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def settings() -> Settings:
    model = get("OPENCODE_MODEL")
    db_path = Path(get("TASKBOT_DB", "taskbot.db") or "taskbot.db")
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Settings(
        opencode_api_key=get("OPENCODE_API_KEY"),
        opencode_base_url=get("OPENCODE_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        opencode_model=model,
        opencode_model_cheap=get("OPENCODE_MODEL_CHEAP", model),
        llm_mode=_choice("TASKBOT_LLM", "real", {"real", "fake"}),
        mail_mode=_choice("TASKBOT_MAIL", "mcp", {"mcp", "fake"}),
        mail_mcp_command=get("TASKBOT_MAIL_MCP_COMMAND"),
        port=int(get("TASKBOT_PORT", "8765") or "8765"),
        db_path=db_path,
        scheduler_enabled=_flag("TASKBOT_SCHEDULER", "on"),
        debug=_flag("TASKBOT_DEBUG", "0"),
        opencode_extra_body=_json_object("OPENCODE_EXTRA_BODY"),
    )
