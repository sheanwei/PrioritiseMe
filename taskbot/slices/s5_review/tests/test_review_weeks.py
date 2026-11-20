"""FR-50 week boundaries (Monday 00:00 to Sunday end, review timezone)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from taskbot.core import clock
from taskbot.slices.s5_review import weeks
from taskbot.slices.s5_review.weeks import Week, review_tz, week_containing

SGT = timezone(timedelta(hours=8))


def test_default_timezone_is_singapore(monkeypatch):
    monkeypatch.delenv("TASKBOT_REVIEW_TZ", raising=False)
    tz = review_tz()
    assert datetime(2026, 11, 16, tzinfo=tz).utcoffset() == timedelta(hours=8)


def test_week_bounds_are_local_monday_midnight_in_utc():
    wk = week_containing(date(2026, 11, 19), review_tz("Asia/Singapore"))
    assert wk.start == date(2026, 11, 16)
    assert wk.end == date(2026, 11, 22)
    assert wk.since_iso == "2026-11-15T16:00:00+00:00"
    assert wk.until_iso == "2026-11-22T16:00:00+00:00"
    assert wk.prev().start == date(2026, 11, 9)
    assert wk.next().start == date(2026, 11, 23)


@pytest.mark.parametrize(
    ("utc_now", "expected_monday"),
    [
        # Sunday 23:59 SGT -> still the week of Mon 16 Nov
        (datetime(2026, 11, 22, 15, 59, tzinfo=timezone.utc), date(2026, 11, 16)),
        # Monday 00:00 SGT (= Sunday 16:00 UTC) -> new week
        (datetime(2026, 11, 22, 16, 0, tzinfo=timezone.utc), date(2026, 11, 23)),
        # Monday 00:00 SGT exactly at the start
        (datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc), date(2026, 11, 16)),
        # Sunday 23:59 UTC is already Monday in Singapore
        (datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc), date(2026, 11, 16)),
    ],
)
def test_current_week_follows_clock_now_in_local_time(utc_now, expected_monday):
    clock.set_now(utc_now)
    assert week_containing(tz=review_tz("Asia/Singapore")).start == expected_monday


def test_year_boundary_week():
    wk = week_containing(date(2027, 1, 1), review_tz("+08:00"))  # a Friday
    assert wk.start == date(2026, 12, 28)
    assert wk.end == date(2027, 1, 3)


def test_env_override_and_offsets(monkeypatch):
    monkeypatch.setenv("TASKBOT_REVIEW_TZ", "UTC")
    assert week_containing(date(2026, 11, 16)).since_iso == "2026-11-16T00:00:00+00:00"
    assert Week(date(2026, 11, 16), review_tz("-05:30")).since_iso == "2026-11-16T05:30:00+00:00"


def test_fallback_without_tz_database(monkeypatch):
    """Windows without tzdata: Asia/Singapore still resolves (fixed UTC+8)."""

    def missing(name):
        raise weeks.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(weeks, "ZoneInfo", missing)
    assert review_tz("Asia/Singapore").utcoffset(None) == timedelta(hours=8)
    with pytest.raises(ValueError):
        review_tz("Mars/Olympus_Mons")
