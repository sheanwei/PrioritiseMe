"""Weekly review computation (FR-51..FR-55).

FR-55: everything here is derived from the ``items`` and ``item_events``
tables only, through ``taskbot.core.items`` plus one read-only ``SELECT`` on
``items``. No other slice's tables are touched.

Definitions (all "in the week" means ``week.since_iso <= at < week.until_iso``):

* **Decision** — a ``decided`` event in the week. FR-53/FR-54 count every
  decision (an item re-assessed after a timebox is decided again, against a
  new suggestion). FR-51 counts *items*: each item once, using its latest
  decision in the week. Items whose ``decided_at`` column falls in the week but
  that have no ``decided`` event in the week are counted from their columns
  (``type``/``outcome``), so a producer that only sets columns still shows up.
* **Staleness closure** — a ``closed_stale`` event, or a ``status_changed``
  event with ``to = closed_stale``, in the week (one per item).
* **Could have waited** — an item declined in the week (a ``decided`` event
  with ``outcome = decline`` or a ``status_changed`` to ``declined``) with no
  ``external_activity`` event after the decline, up to now.
* **Override** — a ``decided`` event with ``accepted`` false. Decisions without
  an ``accepted`` boolean (e.g. logged without a suggestion) count towards
  FR-51 but not towards the acceptance rate.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Any

from taskbot.core import clock, db, items
from taskbot.core.models import EventKind, Item, Outcome, Status, WorkType

from .weeks import Week, tz_label, week_containing

HIGHLIGHT_ABOVE = 2  # FR-53: "appears more than twice"
UNKNOWN_REQUESTER = "(unknown)"
UNCLASSIFIED = "unclassified"

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do does
    doesn't doing don't down during each few for from further get got had hadn't has hasn't have
    haven't having he her here hers herself him himself his how i i'm if in into is isn't it it's
    its itself just let's me more most mustn't my myself no nor not now of off on once only or other
    ought our ours ourselves out over own really same shan't she should shouldn't so some still such
    than that that's the their theirs them themselves then there there's these they they're this
    those through to too under until up very was wasn't we we're were weren't what what's when where
    which while who whom why will with won't would wouldn't you you're your yours yourself
    yourselves already actually anyway since though thing things one ok okay yes yet via per etc
    need needs needed want wants wanted think thought going gonna lot bit much many
    """.split()
)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'_-]*")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# --- small pure helpers (unit-tested directly) -------------------------------


def reason_keywords(reason: str | None) -> list[str]:
    """Deterministic keywords: lowercase words, minus stopwords, numbers and
    words shorter than 3 letters; a trailing possessive ``'s`` is dropped.
    Each keyword appears once per reason, in first-seen order."""
    seen: dict[str, None] = {}
    for raw in _WORD_RE.findall((reason or "").lower()):
        word = raw.strip("'-_")
        if word.endswith("'s"):
            word = word[:-2]
        if len(word) < 3 or word.isdigit() or word in STOPWORDS:
            continue
        seen.setdefault(word, None)
    return list(seen)


def requester_of(item: Item | None) -> tuple[str, str]:
    """``(group_key, display_name)`` from the item's ask card requester.
    The key is the email address when there is one, so ``Ana <ana@x.io>`` and
    ``ana@x.io`` group together; otherwise the lowercased name."""
    card = item.ask_card if item is not None else None
    raw = str((card or {}).get("requester") or "").strip()
    if not raw:
        return UNKNOWN_REQUESTER, UNKNOWN_REQUESTER
    m = _EMAIL_RE.search(raw)
    key = m.group(0).lower() if m else " ".join(raw.lower().split())
    return key, raw


def acceptance_rate(accepted: int, overridden: int) -> float | None:
    """FR-54: share of AI suggestions accepted, or ``None`` when there were none."""
    total = accepted + overridden
    return None if total == 0 else accepted / total


def _group(entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Count ``(key, label)`` pairs; sort by count desc then label; flag > 2."""
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for key, label in entries:
        counts[key] += 1
        labels.setdefault(key, label)
    rows = [
        {"key": k, "label": labels[k], "count": n, "highlight": n > HIGHLIGHT_ABOVE}
        for k, n in counts.items()
    ]
    return sorted(rows, key=lambda r: (-r["count"], r["label"].lower()))


def _brief(item: Item | None, item_id: int) -> dict[str, Any]:
    if item is None:
        return {"id": item_id, "title": f"#{item_id}", "type": None, "status": None}
    return {"id": item.id, "title": item.title, "type": item.type, "status": item.status}


# --- the review ---------------------------------------------------------------


def build_review(week: Week) -> dict[str, Any]:
    since, until = week.since_iso, week.until_iso
    with db.connect() as conn:
        week_events = items.list_events(
            kinds=[EventKind.DECIDED, EventKind.STATUS_CHANGED, EventKind.CLOSED_STALE],
            since=since,
            until=until,
            conn=conn,
        )
        cache: dict[int, Item | None] = {}

        def item(item_id: int) -> Item | None:
            if item_id not in cache:
                cache[item_id] = items.get_item(item_id, conn=conn)
            return cache[item_id]

        decisions = [e for e in week_events if e.kind == EventKind.DECIDED]
        mix = _decided_mix(conn, decisions, since, until, item)
        stale = _stale_closures(week_events, item)
        waited = _could_have_waited(conn, week_events, item)
        overrides, acceptance = _overrides_and_acceptance(conn, decisions, item)

    current_start = week_containing(tz=week.tz).start
    return {
        "week": {
            "start": week.start.isoformat(),
            "end": week.end.isoformat(),
            "since": since,
            "until": until,
            "timezone": tz_label(week.tz),
            "prev": week.prev().start.isoformat(),
            "next": week.next().start.isoformat(),
            "is_current": week.start == current_start,
            "is_future": week.start > current_start,
        },
        "decided": mix,
        "staleness_closures": stale,
        "could_have_waited": waited,
        "overrides": overrides,
        "acceptance": acceptance,
        "generated_at": clock.now_iso(),
    }


def _decided_mix(conn: sqlite3.Connection, decisions, since: str, until: str, item) -> dict[str, Any]:
    """FR-51: each item once, by its latest decision in the week."""
    latest: dict[int, dict[str, Any]] = {}
    for e in decisions:  # oldest-first, so later ones overwrite
        it = item(e.item_id)
        latest[e.item_id] = {
            "type": e.payload.get("type") or (it.type if it else None),
            "outcome": e.payload.get("outcome") or (it.outcome if it else None),
        }
    # Column fallback: decided_at in the week, but no decided event in the week.
    rows = conn.execute(
        "SELECT * FROM items WHERE decided_at >= ? AND decided_at < ? ORDER BY id",
        (since, until),
    ).fetchall()
    for row in rows:
        it = Item.from_row(row)
        if it.id not in latest:
            latest[it.id] = {"type": it.type, "outcome": it.outcome}

    by_type = Counter({t.value: 0 for t in WorkType})
    by_outcome = Counter({o.value: 0 for o in Outcome})
    for d in latest.values():
        by_type[d["type"] or UNCLASSIFIED] += 1
        if d["outcome"]:
            by_outcome[d["outcome"]] += 1
    if not by_type.get(UNCLASSIFIED):
        by_type.pop(UNCLASSIFIED, None)
    return {
        "total": len(latest),
        "decisions": len(decisions),
        "by_type": dict(by_type),
        "by_outcome": dict(by_outcome),
    }


def _stale_closures(week_events, item) -> dict[str, Any]:
    """FR-52 (first half): items closed as stale in the week."""
    found: dict[int, dict[str, Any]] = {}
    for e in week_events:
        is_close = e.kind == EventKind.CLOSED_STALE or (
            e.kind == EventKind.STATUS_CHANGED and e.payload.get("to") == Status.CLOSED_STALE
        )
        if not is_close:
            continue
        entry = found.setdefault(e.item_id, {**_brief(item(e.item_id), e.item_id), "at": e.at, "silent_days": None})
        if e.payload.get("silent_days") is not None:
            entry["silent_days"] = e.payload["silent_days"]
    rows = sorted(found.values(), key=lambda r: (r["at"], r["id"]))
    return {"count": len(rows), "items": rows}


def _could_have_waited(conn: sqlite3.Connection, week_events, item) -> dict[str, Any]:
    """FR-52 (second half): declined in the week, no requester follow-up after."""
    declines: dict[int, Any] = {}
    for e in week_events:
        declined = (e.kind == EventKind.DECIDED and e.payload.get("outcome") == Outcome.DECLINE) or (
            e.kind == EventKind.STATUS_CHANGED and e.payload.get("to") == Status.DECLINED
        )
        if declined and e.item_id not in declines:
            declines[e.item_id] = e  # earliest decline signal in the week
    rows = []
    for item_id, decline in declines.items():
        followups = [
            f
            for f in items.list_events(
                item_id=item_id, kinds=[EventKind.EXTERNAL_ACTIVITY], since=decline.at, conn=conn
            )
            if (f.at, f.id) > (decline.at, decline.id)
        ]
        if followups:
            continue
        _, requester = requester_of(item(item_id))
        rows.append(
            {
                **_brief(item(item_id), item_id),
                "declined_at": decline.at,
                "requester": requester,
                "label": "Could have waited",
            }
        )
    rows.sort(key=lambda r: (r["declined_at"], r["id"]))
    return {"count": len(rows), "label": "Could have waited", "items": rows}


def _overrides_and_acceptance(conn: sqlite3.Connection, decisions, item) -> tuple[dict, dict]:
    """FR-53 override log + groupings, FR-54 acceptance rate."""
    accepted = overridden = 0
    log: list[dict[str, Any]] = []
    by_requester: list[tuple[str, str]] = []
    by_keyword: list[tuple[str, str]] = []
    for e in decisions:
        flag = e.payload.get("accepted")
        if flag is True:
            accepted += 1
            continue
        if flag is not False:
            continue  # no suggestion to accept or override
        overridden += 1
        it = item(e.item_id)
        key, requester = requester_of(it)
        reason = (e.payload.get("override_reason") or "").strip()
        keywords = reason_keywords(reason)
        suggested = [
            s
            for s in items.list_events(item_id=e.item_id, kinds=[EventKind.SUGGESTED], conn=conn)
            if (s.at, s.id) < (e.at, e.id)
        ]
        suggestion = suggested[-1].payload.get("outcome") if suggested else None
        log.append(
            {
                **_brief(it, e.item_id),
                "at": e.at,
                "requester": requester,
                "requester_key": key,
                "suggested_outcome": suggestion,
                "chosen_outcome": e.payload.get("outcome"),
                "reason": reason,
                "keywords": keywords,
            }
        )
        by_requester.append((key, requester))
        by_keyword.extend((k, k) for k in keywords)

    req_groups = _group(by_requester)
    kw_groups = _group(by_keyword)
    hot_requesters = {g["key"] for g in req_groups if g["highlight"]}
    hot_keywords = {g["key"] for g in kw_groups if g["highlight"]}
    for row in log:
        row["highlight_requester"] = row["requester_key"] in hot_requesters
        row["highlight_keywords"] = [k for k in row["keywords"] if k in hot_keywords]
    rate = acceptance_rate(accepted, overridden)
    return (
        {
            "count": overridden,
            "log": log,
            "by_requester": req_groups,
            "by_keyword": kw_groups,
            "highlight_threshold": HIGHLIGHT_ABOVE,
        },
        {
            "accepted": accepted,
            "overridden": overridden,
            "total": accepted + overridden,
            "rate": rate,
            "percent": None if rate is None else round(rate * 100),
        },
    )
