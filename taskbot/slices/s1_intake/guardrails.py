"""Guardrails (FR-13): rules applied after the LLM that override its suggestion.

Pure functions only, so they are easy to test and reason about.
"""

from __future__ import annotations

from typing import Any

RULE_INCIDENT = "Incident with production impact: do it now."
RULE_UNKNOWN_SIZE = "Size unknown: timebox it and re-assess."


def apply_guardrails(
    suggestion: dict[str, Any], *, production_impact: bool, size_known: bool
) -> dict[str, Any]:
    """Return a copy of ``suggestion`` with the rules applied.

    Rules, first match wins:
      1. type ``incident`` with production impact -> outcome ``do_now``, priority 1.
      2. size unknown and the AI proposed ``do_now`` -> outcome ``timebox``.
         Only "start working on it now" needs a known size; scheduling,
         delegating or declining an unknown-size item is a fine decision.

    The result carries ``rule_adjusted`` (did any rule change it), ``rules``
    (human-readable reasons) and ``llm_outcome`` (what the model proposed).
    """
    out = dict(suggestion)
    out["llm_outcome"] = suggestion.get("outcome")
    rules: list[str] = []

    if out.get("type") == "incident" and production_impact:
        if out.get("outcome") != "do_now" or out.get("priority") != 1:
            rules.append(RULE_INCIDENT)
        out["outcome"] = "do_now"
        out["priority"] = 1
    elif not size_known and out.get("outcome") == "do_now":
        rules.append(RULE_UNKNOWN_SIZE)
        out["outcome"] = "timebox"

    out["rule_adjusted"] = bool(rules)
    out["rules"] = rules
    return out
