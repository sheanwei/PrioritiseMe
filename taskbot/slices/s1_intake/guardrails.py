"""Guardrails (FR-13): rules applied after the LLM that override its suggestion.

Pure functions only, so they are easy to test and reason about.
"""

from __future__ import annotations

from typing import Any

RULE_INCIDENT = "Incident with production impact: do it now."
RULE_UNKNOWN_SIZE = "Size unknown: timebox it and re-assess."
# Link rules (S7 links): formatted with the linked item's id and priority.
RULE_INHERIT = "Blocks #{id} (P{priority}): inherits its priority."
RULE_INHERIT_ANON = "Blocks more urgent work (P{priority}): inherits its priority."
RULE_URGENT_BLOCKER = "Unblocks urgent #{id} (P{priority}): do it now."

URGENT_PRIORITY = 2  # blocking an open item at this priority or better means do_now
_CLOSED_STATUSES = frozenset({"done", "declined", "closed_stale"})
_DEFERRING_OUTCOMES = frozenset({"schedule", "decline"})


def _as_priority(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        p = int(value)
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 4 else None


def urgent_blocked(links: dict[str, Any] | None) -> dict[str, Any] | None:
    """The most urgent open item this one directly blocks, if it is P1/P2."""
    best: dict[str, Any] | None = None
    for other in (links or {}).get("blocks") or []:
        if not isinstance(other, dict) or other.get("status") in _CLOSED_STATUSES:
            continue
        p = _as_priority(other.get("priority"))
        if p is None or p > URGENT_PRIORITY:
            continue
        if best is None or p < best["priority"]:
            best = {"id": other.get("id"), "priority": p}
    return best


def apply_guardrails(
    suggestion: dict[str, Any],
    *,
    production_impact: bool,
    size_known: bool,
    links: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``suggestion`` with the rules applied.

    Rules, in order:
      1. type ``incident`` with production impact -> outcome ``do_now``, priority 1.
         When it applies, the rules below are skipped (nothing can beat do_now / P1,
         and an incident is never turned into a timebox).
      2. Priority inheritance: ``links["inherited_priority"]`` (S7) better than the
         suggested priority -> take it. The item unblocks more urgent work.
      3. Urgent blocker: the item directly blocks an open P1/P2 item and the AI
         proposed ``schedule`` or ``decline`` -> outcome ``do_now``.
      4. size unknown and the outcome is ``do_now`` -> outcome ``timebox``.
         Only "start working on it now" needs a known size; scheduling,
         delegating or declining an unknown-size item is a fine decision.

    ``links`` is S7's ``links_for`` dict, or ``None`` when there are no links.

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
    else:
        links = links or {}
        inherited = _as_priority(links.get("inherited_priority"))
        current = _as_priority(out.get("priority"))
        if inherited is not None and (current is None or inherited < current):
            source = links.get("inherited_from")
            if isinstance(source, dict) and source.get("id") is not None:
                rules.append(RULE_INHERIT.format(id=source["id"], priority=inherited))
            else:
                rules.append(RULE_INHERIT_ANON.format(priority=inherited))
            out["priority"] = inherited

        urgent = urgent_blocked(links)
        if urgent and out.get("outcome") in _DEFERRING_OUTCOMES:
            rules.append(RULE_URGENT_BLOCKER.format(**urgent))
            out["outcome"] = "do_now"

        if not size_known and out.get("outcome") == "do_now":
            rules.append(RULE_UNKNOWN_SIZE)
            out["outcome"] = "timebox"

    out["rule_adjusted"] = bool(rules)
    out["rules"] = rules
    return out
