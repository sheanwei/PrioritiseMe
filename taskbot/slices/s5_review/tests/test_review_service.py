"""FR-51..FR-55 and the S5 acceptance criteria, against seeded items/events.

Known week: Mon 16 Nov 2026 .. Sun 22 Nov 2026 in Asia/Singapore, i.e.
[2026-11-15T16:00Z, 2026-11-22T16:00Z). "Now" is frozen on Friday 20 Nov.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from taskbot.core import clock
from taskbot.slices.s5_review import service
from taskbot.slices.s5_review.weeks import review_tz, week_containing
from tests.fixtures import seed_event, seed_item

WEEK_START = date(2026, 11, 16)
IN_WEEK = "2026-11-18T03:00:00+00:00"  # Wed 11:00 SGT
BEFORE = "2026-11-15T15:59:59+00:00"  # Sun 23:59:59 SGT, previous week
FIRST_SECOND = "2026-11-15T16:00:00+00:00"  # Mon 00:00 SGT
LAST_SECOND = "2026-11-22T15:59:59+00:00"  # Sun 23:59:59 SGT
AFTER = "2026-11-22T16:00:00+00:00"  # next Monday 00:00 SGT


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setenv("TASKBOT_REVIEW_TZ", "Asia/Singapore")
    clock.set_now(datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc))


def review(day: date = WEEK_START) -> dict:
    return service.build_review(week_containing(day, review_tz()))


def decided_item(
    outcome: str,
    type_: str,
    *,
    accepted: bool | None = True,
    reason: str | None = None,
    requester: str | None = "Priya <priya@corp.example>",
    at: str = IN_WEEK,
    suggested: str | None = None,
    status: str | None = None,
    **cols,
):
    status = status or {
        "do_now": "active",
        "timebox": "timeboxed",
        "schedule": "scheduled",
        "delegate": "delegated",
        "decline": "declined",
    }[outcome]
    item = seed_item(
        title=f"{type_} {outcome}",
        type=type_,
        outcome=outcome,
        status=status,
        decided_at=at,
        ask_card={"request": "x", "requester": requester} if requester else None,
        **cols,
    )
    if suggested:
        seed_event(item.id, "suggested", {"outcome": suggested, "type": type_, "priority": 2, "rationale": "r"}, at=at)
    payload = {"outcome": outcome, "type": type_}
    if accepted is not None:
        payload["accepted"] = accepted
    if reason is not None:
        payload["override_reason"] = reason
    seed_event(item.id, "decided", payload, at=at)
    seed_event(item.id, "status_changed", {"from": "tray", "to": status}, at=at)
    return item


# --- AC1: seeded events for a known week produce the expected counts ---------


def test_ac1_known_week_counts():
    decided_item("do_now", "incident", accepted=None)  # incident logged afterwards
    decided_item("timebox", "interrupt", suggested="timebox")
    decided_item("schedule", "interrupt", accepted=False, reason="Not urgent, sprint review first", suggested="do_now")
    decided_item("decline", "interrupt", accepted=False, reason="Duplicate request", suggested="schedule")
    decided_item("delegate", "toil", suggested="delegate", at=FIRST_SECOND)
    decided_item("schedule", "planned", suggested="schedule", at=LAST_SECOND)
    # Outside the week: must not count
    decided_item("do_now", "incident", at=BEFORE)
    decided_item("decline", "toil", at=AFTER)

    # Staleness closures (one via S3's event, one via the status change only)
    s1 = seed_item(title="Old ask", status="closed_stale", type="interrupt")
    seed_event(s1.id, "closed_stale", {"silent_days": 5}, at=IN_WEEK)
    seed_event(s1.id, "status_changed", {"from": "scheduled", "to": "closed_stale"}, at=IN_WEEK)
    s2 = seed_item(title="Delegated, forgotten", status="closed_stale")
    seed_event(s2.id, "status_changed", {"from": "delegated", "to": "closed_stale"}, at=LAST_SECOND)
    s3 = seed_item(title="Closed last week", status="closed_stale")
    seed_event(s3.id, "closed_stale", {"silent_days": 6}, at=BEFORE)

    r = review()
    assert r["week"]["start"] == "2026-11-16" and r["week"]["end"] == "2026-11-22"
    assert r["week"]["is_current"] is True
    d = r["decided"]
    assert d["total"] == 6
    assert d["by_type"] == {"incident": 1, "interrupt": 3, "toil": 1, "planned": 1}
    assert d["by_outcome"] == {"do_now": 1, "timebox": 1, "schedule": 2, "delegate": 1, "decline": 1}

    assert r["staleness_closures"]["count"] == 2
    assert [i["title"] for i in r["staleness_closures"]["items"]] == ["Old ask", "Delegated, forgotten"]
    assert r["staleness_closures"]["items"][0]["silent_days"] == 5

    assert r["could_have_waited"]["count"] == 1
    assert r["could_have_waited"]["items"][0]["title"] == "interrupt decline"
    assert r["could_have_waited"]["items"][0]["label"] == "Could have waited"

    ov = r["overrides"]
    assert ov["count"] == 2
    assert [(o["suggested_outcome"], o["chosen_outcome"]) for o in ov["log"]] == [
        ("do_now", "schedule"),
        ("schedule", "decline"),
    ]
    assert ov["log"][0]["reason"] == "Not urgent, sprint review first"

    acc = r["acceptance"]
    # 3 accepted (timebox, delegate, planned schedule), 2 overridden, incident has no flag
    assert (acc["accepted"], acc["overridden"], acc["total"]) == (3, 2, 5)
    assert acc["rate"] == pytest.approx(0.6)
    assert acc["percent"] == 60


def test_re_decided_item_counts_once_with_latest_outcome():
    item = decided_item("timebox", "interrupt", at="2026-11-17T02:00:00+00:00")
    seed_event(item.id, "decided", {"outcome": "schedule", "type": "toil", "accepted": False,
                                    "override_reason": "after timebox"}, at="2026-11-17T04:00:00+00:00")
    r = review()
    assert r["decided"]["total"] == 1
    assert r["decided"]["decisions"] == 2
    assert r["decided"]["by_type"]["toil"] == 1 and r["decided"]["by_type"]["interrupt"] == 0
    assert r["decided"]["by_outcome"]["schedule"] == 1 and r["decided"]["by_outcome"]["timebox"] == 0
    assert (r["acceptance"]["accepted"], r["acceptance"]["overridden"]) == (1, 1)


def test_decided_at_column_fallback_and_unclassified():
    seed_item(title="no event", status="active", outcome="do_now", type="incident", decided_at=IN_WEEK)
    seed_item(title="no type", status="scheduled", outcome="schedule", decided_at=IN_WEEK)
    seed_item(title="outside", status="active", outcome="do_now", decided_at=AFTER)
    d = review()["decided"]
    assert d["total"] == 2
    assert d["by_type"]["incident"] == 1
    assert d["by_type"]["unclassified"] == 1
    assert d["by_outcome"]["do_now"] == 1 and d["by_outcome"]["schedule"] == 1


# --- FR-52: "Could have waited" -----------------------------------------------


def test_decline_with_followup_after_is_not_could_have_waited():
    quiet = decided_item("decline", "interrupt", at="2026-11-17T01:00:00+00:00")
    seed_event(quiet.id, "external_activity", {"via": "email"}, at="2026-11-16T01:00:00+00:00")  # before
    chased = decided_item("decline", "interrupt", at="2026-11-17T01:00:00+00:00")
    seed_event(chased.id, "external_activity", {"via": "email"}, at="2026-11-19T01:00:00+00:00")  # after
    # follow-up after the week still counts as "afterwards"
    late = decided_item("decline", "toil", at="2026-11-21T01:00:00+00:00")
    seed_event(late.id, "external_activity", {"via": "email"}, at="2026-11-25T01:00:00+00:00")

    waited = review()["could_have_waited"]
    assert [i["id"] for i in waited["items"]] == [quiet.id]
    assert waited["items"][0]["requester"] == "Priya <priya@corp.example>"


def test_decline_seen_only_as_status_change_counts():
    item = seed_item(title="Declined via status only", status="declined", type="interrupt")
    seed_event(item.id, "status_changed", {"from": "tray", "to": "declined"}, at=IN_WEEK)
    waited = review()["could_have_waited"]
    assert [i["title"] for i in waited["items"]] == ["Declined via status only"]


def test_email_archived_as_not_actionable_is_not_could_have_waited():
    # S4 behaviour: tray item -> declined with no decided event, plus classification_corrected
    archived = seed_item(title="Newsletter misread", status="declined", type="interrupt", source="email")
    seed_event(archived.id, "status_changed", {"from": "tray", "to": "declined"}, at=IN_WEEK)
    seed_event(archived.id, "classification_corrected", {"from": "needs_action", "to": "noise"}, at=IN_WEEK)
    waited = review()["could_have_waited"]
    assert waited["count"] == 0 and waited["items"] == []


def test_gate_decline_still_counts_even_if_email_corrected_later():
    item = decided_item("decline", "interrupt", at="2026-11-17T01:00:00+00:00", source="email")
    seed_event(item.id, "classification_corrected", {"from": "needs_action", "to": "fyi"}, at=IN_WEEK)
    waited = review()["could_have_waited"]
    assert [i["id"] for i in waited["items"]] == [item.id]


def test_correction_back_to_needs_action_does_not_exclude():
    item = seed_item(title="Declined via status", status="declined", type="interrupt")
    seed_event(item.id, "status_changed", {"from": "tray", "to": "declined"}, at=IN_WEEK)
    seed_event(item.id, "classification_corrected", {"from": "fyi", "to": "needs_action"}, at=BEFORE)
    assert review()["could_have_waited"]["count"] == 1


# --- AC2 / FR-53: override log grouping and highlighting ----------------------


def test_ac2_requester_with_three_overrides_is_highlighted():
    # Same person written two ways; grouped by email address -> 3 overrides
    for reason, who in [
        ("Waiting on vendor fix", "Dana Lee <DANA@corp.example>"),
        ("Vendor ticket open", "Dana Lee <DANA@corp.example>"),
        ("Can batch with release", "dana@corp.example"),
    ]:
        decided_item("schedule", "interrupt", accepted=False, reason=reason, requester=who)
    for reason in ["Low impact", "Low impact again"]:
        decided_item("decline", "interrupt", accepted=False, reason=reason, requester="Sam")
    decided_item("do_now", "incident", accepted=False, reason="Customer facing", requester=None)

    ov = review()["overrides"]
    groups = {g["key"]: g for g in ov["by_requester"]}
    assert groups["dana@corp.example"]["count"] == 3
    assert groups["dana@corp.example"]["highlight"] is True
    assert groups["dana@corp.example"]["label"] == "Dana Lee <DANA@corp.example>"
    assert groups["sam"]["count"] == 2 and groups["sam"]["highlight"] is False
    assert groups["(unknown)"]["count"] == 1
    assert ov["by_requester"][0]["key"] == "dana@corp.example"  # sorted by count
    flagged = [o["highlight_requester"] for o in ov["log"]]
    assert flagged == [True, True, True, False, False, False]


def test_reason_keyword_highlight_above_two():
    for n, reason in enumerate(["Vendor is slow", "vendor's outage", "VENDOR again", "Low impact"]):
        decided_item("schedule", "interrupt", accepted=False, reason=reason, requester=f"Requester {n}")
    ov = review()["overrides"]
    kw = {g["key"]: g for g in ov["by_keyword"]}
    assert kw["vendor"]["count"] == 3 and kw["vendor"]["highlight"] is True
    assert kw["impact"]["highlight"] is False
    assert ov["log"][0]["highlight_keywords"] == ["vendor"]
    assert all(not g["highlight"] for g in ov["by_requester"])


def test_reason_keywords_are_deterministic():
    assert service.reason_keywords("It's NOT urgent -- the vendor's fix is in 2 days, we can wait!") == [
        "urgent", "vendor", "fix", "days", "wait",
    ]
    assert service.reason_keywords("urgent urgent URGENT") == ["urgent"]
    assert service.reason_keywords(None) == []
    assert service.reason_keywords("") == []


# --- FR-54 -------------------------------------------------------------------


def test_acceptance_rate_zero_division():
    assert service.acceptance_rate(0, 0) is None
    assert service.acceptance_rate(1, 3) == 0.25


def test_only_incidents_without_suggestions_gives_no_rate():
    decided_item("do_now", "incident", accepted=None)
    acc = review()["acceptance"]
    assert acc == {"accepted": 0, "overridden": 0, "total": 0, "rate": None, "percent": None}


# --- AC3: empty week -----------------------------------------------------------


def test_ac3_empty_week():
    decided_item("do_now", "incident", at=BEFORE)  # data exists, just not this week
    r = review()
    assert r["decided"]["total"] == 0
    assert set(r["decided"]["by_type"].values()) == {0}
    assert set(r["decided"]["by_outcome"].values()) == {0}
    assert r["staleness_closures"] == {"count": 0, "items": []}
    assert r["could_have_waited"]["items"] == []
    assert r["overrides"]["log"] == [] and r["overrides"]["by_requester"] == [] and r["overrides"]["by_keyword"] == []
    assert r["acceptance"]["rate"] is None


def test_previous_and_future_weeks():
    decided_item("do_now", "incident", at=BEFORE)
    prev = review(date(2026, 11, 9))
    assert prev["decided"]["total"] == 1
    assert prev["week"]["is_current"] is False and prev["week"]["is_future"] is False
    nxt = review(date(2026, 11, 23))
    assert nxt["week"]["is_future"] is True


# --- FR-55: only items and item_events are read --------------------------------


def test_reads_only_items_and_item_events(monkeypatch):
    import sqlite3

    from taskbot.core import db

    decided_item("decline", "interrupt", accepted=False, reason="Not mine")
    seen: set[str] = set()
    real_connect = db.connect

    from contextlib import contextmanager

    @contextmanager
    def tracing_connect(*a, **kw):
        with real_connect(*a, **kw) as conn:
            def auth(action, arg1, arg2, dbname, source):
                if action == sqlite3.SQLITE_READ:
                    seen.add(arg1)
                return sqlite3.SQLITE_OK

            conn.set_authorizer(auth)
            yield conn

    monkeypatch.setattr(db, "connect", tracing_connect)
    review()
    assert seen <= {"items", "item_events", "sqlite_master"}
    assert {"items", "item_events"} <= seen
