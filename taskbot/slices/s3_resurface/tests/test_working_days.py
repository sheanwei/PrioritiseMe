"""FR-34: working_days_between is a pure Monday-Friday counter."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from taskbot.slices.s3_resurface.service import working_days_between

FRI = date(2026, 11, 13)
SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)
MON = date(2026, 11, 16)
NEXT_FRI = date(2026, 11, 20)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (FRI, NEXT_FRI, 5),  # the PRD example: 7 calendar days spanning a weekend
        (FRI, FRI, 0),  # same day
        (FRI, SAT, 0),  # weekend days never count
        (FRI, SUN, 0),
        (FRI, MON, 1),
        (SAT, SUN, 0),
        (SAT, MON, 1),
        (SUN, MON, 1),
        (MON, MON + timedelta(days=1), 1),
        (MON, NEXT_FRI, 4),  # Tue..Fri
        (MON, MON + timedelta(days=7), 5),
        (FRI, FRI + timedelta(days=14), 10),
        (SAT, SAT + timedelta(days=14), 10),
        (date(2026, 12, 31), date(2027, 1, 4), 2),  # across a year boundary: Fri 1 Jan, Mon 4 Jan
    ],
)
def test_known_spans(a, b, expected):
    assert working_days_between(a, b) == expected


def test_reversed_order_is_zero():
    assert working_days_between(NEXT_FRI, FRI) == 0
    assert working_days_between(MON, SAT) == 0


def test_accepts_datetimes_using_their_own_date():
    a = datetime(2026, 11, 13, 23, 59, tzinfo=timezone.utc)
    b = datetime(2026, 11, 20, 0, 1, tzinfo=timezone.utc)
    assert working_days_between(a, b) == 5
    assert working_days_between(a, NEXT_FRI) == 5  # mixed date/datetime


def test_is_pure():
    args = (FRI, NEXT_FRI)
    assert working_days_between(*args) == working_days_between(*args)
    assert args == (FRI, NEXT_FRI)


def test_matches_brute_force_over_many_spans():
    start = date(2026, 1, 1)
    for i in range(0, 21):
        for span in range(-3, 40):
            a = start + timedelta(days=i)
            b = a + timedelta(days=span)
            brute = sum(
                1 for k in range(1, max(span, 0) + 1) if (a + timedelta(days=k)).weekday() < 5
            )
            assert working_days_between(a, b) == brute, (a, b)
