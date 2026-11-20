"""Unit tests for the pure parts of the intake gate: guardrails (FR-13) and helpers."""

import pytest

from taskbot.core.models import Item
from taskbot.slices.s1_intake import service
from taskbot.slices.s1_intake.guardrails import RULE_INCIDENT, RULE_UNKNOWN_SIZE, apply_guardrails


def sug(**kw):
    base = {"type": "interrupt", "outcome": "schedule", "priority": 3, "rationale": "r", "solution_sketch": ""}
    return {**base, **kw}


# --- AC3: an incident with production impact is always do_now ---

@pytest.mark.parametrize("outcome", ["timebox", "schedule", "delegate", "decline", "do_now"])
@pytest.mark.parametrize("size_known", [True, False])
def test_incident_with_production_impact_is_always_do_now(outcome, size_known):
    out = apply_guardrails(sug(type="incident", outcome=outcome, priority=2),
                           production_impact=True, size_known=size_known)
    assert out["outcome"] == "do_now"
    assert out["priority"] == 1
    assert out["rule_adjusted"] is True
    assert out["rules"] == [RULE_INCIDENT]
    assert out["llm_outcome"] == outcome


def test_incident_already_do_now_p1_is_not_marked_adjusted():
    out = apply_guardrails(sug(type="incident", outcome="do_now", priority=1),
                           production_impact=True, size_known=True)
    assert out["outcome"] == "do_now"
    assert out["rule_adjusted"] is False
    assert out["rules"] == []


def test_incident_without_production_impact_is_left_alone():
    out = apply_guardrails(sug(type="incident", outcome="schedule"), production_impact=False, size_known=True)
    assert out["outcome"] == "schedule"
    assert out["rule_adjusted"] is False


def test_production_impact_on_non_incident_is_left_alone():
    out = apply_guardrails(sug(type="interrupt", outcome="schedule"), production_impact=True, size_known=True)
    assert out["outcome"] == "schedule"
    assert out["rule_adjusted"] is False


@pytest.mark.parametrize("outcome", ["do_now", "schedule", "delegate", "decline"])
def test_unknown_size_becomes_timebox(outcome):
    out = apply_guardrails(sug(outcome=outcome), production_impact=False, size_known=False)
    assert out["outcome"] == "timebox"
    assert out["priority"] == 3
    assert out["rule_adjusted"] is True
    assert out["rules"] == [RULE_UNKNOWN_SIZE]


def test_unknown_size_already_timebox_not_adjusted():
    out = apply_guardrails(sug(outcome="timebox"), production_impact=False, size_known=False)
    assert out["rule_adjusted"] is False


def test_guardrails_do_not_mutate_input():
    original = sug(type="incident")
    apply_guardrails(original, production_impact=True, size_known=False)
    assert original == sug(type="incident")


# --- helpers ---

def test_truncate_context_keeps_head_and_tail():
    short = "hello"
    assert service.truncate_context(short) == short
    assert service.truncate_context(None) == ""
    text = "A" * 5_000 + "M" * 50_000 + "Z" * 5_000
    out = service.truncate_context(text)
    assert len(out) < 20_100
    assert out.startswith("A" * 3_000)
    assert out.endswith("Z" * 5_000)
    assert "truncated" in out


def test_limit_sentences():
    assert service.limit_sentences("One. Two! Three? Four. Five.") == "One. Two! Three?"
    assert service.limit_sentences("Just one") == "Just one"
    assert service.limit_sentences("") == ""


@pytest.mark.parametrize("value,ok", [
    ("2026-11-25", True), ("2026-02-30", False), ("25/11/2026", False), ("", False), (None, False),
    ("2026-11-25T00:00", False),
])
def test_parse_schedule_date(value, ok):
    if ok:
        assert service.parse_schedule_date(value) == value
    else:
        with pytest.raises(service.Invalid):
            service.parse_schedule_date(value)


# --- FR-23 hook: reassess_note is appended when present, harmless when absent ---

def _item(**extra):
    return Item(id=999, title="Flaky deploy", source="manual", status="tray",
                created_at="2026-11-20T09:00:00+00:00", raw_context="thread", extra=extra)


def test_reassess_note_is_added_to_context_when_present():
    ctx = service.build_context(_item(reassess_note="It is a DNS issue, ~2h of work."))
    assert "Re-assess note" in ctx
    assert "It is a DNS issue, ~2h of work." in ctx


def test_context_without_reassess_column_is_fine():
    ctx = service.build_context(_item())
    assert "Re-assess note" not in ctx
    ctx = service.build_context(_item(reassess_note=None))
    assert "Re-assess note" not in ctx
