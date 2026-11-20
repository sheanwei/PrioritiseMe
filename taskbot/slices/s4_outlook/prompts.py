"""Email classification prompt and schema (FR-44).

The schema's ``examples[0]`` is what ``llm.complete_json`` returns in fake mode
(examples are never sent to the real model). We fill it with a cheap keyword
guess so the offline demo sorts mail plausibly; the real model ignores it.
"""

from __future__ import annotations

import re
from typing import Any

CLASSES = ("needs_action", "fyi", "noise")
PREVIEW_CHARS = 1000  # §8: send only the first part of each body

SYSTEM = """\
You sort one engineer's incoming work email into exactly one class:

- needs_action: someone is asking the owner (directly or by clear implication) to do, decide,
  review, answer or fix something. Follow-ups and "any update?" pings on such requests count.
- fyi: informational and relevant to the owner's work, but asks nothing of them
  (announcements, status updates, deploy notices, meeting notes, CCs).
- noise: newsletters, marketing, automated digests, social notifications, anything the owner
  can safely ignore.

When unsure between needs_action and fyi, prefer needs_action only if a person (not a bot) is
waiting on the owner. Give a short reason (one sentence).
The owner's own past corrections are the strongest signal: follow them for similar mail."""


def schema(example_classification: str = "needs_action") -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "classification": {"type": "string", "enum": list(CLASSES)},
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["classification", "reason"],
        "additionalProperties": False,
        "examples": [
            {
                "classification": example_classification,
                "reason": "Offline keyword guess (fake LLM mode).",
            }
        ],
    }


def _format_message(msg: dict[str, Any]) -> str:
    preview = (msg.get("body_preview") or "")[:PREVIEW_CHARS]
    return f"From: {msg.get('from') or ''}\nSubject: {msg.get('subject') or ''}\n\n{preview}"


def user_prompt(msg: dict[str, Any], corrections: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if corrections:
        parts.append("The owner corrected these earlier classifications (most recent first):")
        for c in corrections:
            parts.append(
                "---\n"
                + _format_message({**c, "body_preview": (c.get("body_preview") or "")[:300]})
                + f"\n=> correct class: {c['classification']}"
            )
        parts.append("---\n")
    parts.append("Classify this message:\n" + _format_message(msg))
    return "\n".join(parts)


# --- offline guess (only used as the fake-mode example) ---------------------

_NOISE = re.compile(
    r"unsubscribe|newsletter|digest|webinar|promo|% off|sale ends|no-?reply@|noreply@|marketing",
    re.IGNORECASE,
)
_FYI = re.compile(
    r"^\s*(fyi|announcement|\[announce)|for your information|no action (is )?(needed|required)"
    r"|has been deployed|release notes|meeting notes|out of office",
    re.IGNORECASE,
)
_ASK = re.compile(
    r"\?|\b(can|could|would) you\b|\bplease\b|\bneed(s|ed)? (you|your)\b|\bby (mon|tue|wed|thu|fri|eod|tomorrow)"
    r"|\bany update\b|\breview\b|\bapprove\b|\burgent\b",
    re.IGNORECASE,
)


def guess_classification(msg: dict[str, Any]) -> str:
    text = f"{msg.get('from') or ''}\n{msg.get('subject') or ''}\n{msg.get('body_preview') or ''}"
    if _NOISE.search(text):
        return "noise"
    if _FYI.search(msg.get("subject") or "") or _FYI.search(msg.get("body_preview") or ""):
        return "fyi"
    if _ASK.search(text):
        return "needs_action"
    return "fyi"
