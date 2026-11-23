from datetime import UTC, datetime

import pytest

from kriflow_kernel import local_time_problem, next_local

TORONTO = "17:00 America/Toronto"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_reached_before_the_time_it_runs_the_same_day() -> None:
    assert next_local(TORONTO, utc("2026-10-30T15:00")) == utc("2026-10-30T21:00")  # EDT


def test_reached_after_the_time_it_rolls_to_the_next_day() -> None:
    assert next_local(TORONTO, utc("2026-10-30T21:00:01")) == utc("2026-10-31T21:00")


def test_reached_on_the_time_it_runs_then() -> None:
    assert next_local(TORONTO, utc("2026-10-30T21:00")) == utc("2026-10-30T21:00")


def test_the_local_hour_holds_across_daylight_saving_changes() -> None:
    # Toronto falls back on 2026-11-01: 17:00 is 21:00 UTC before and 22:00 UTC after.
    assert next_local(TORONTO, utc("2026-10-31T21:30")) == utc("2026-11-01T22:00")
    # and springs forward on 2026-03-08: 22:00 UTC before, 21:00 UTC after.
    assert next_local(TORONTO, utc("2026-03-07T22:30")) == utc("2026-03-08T21:00")


def test_a_time_the_spring_forward_skips_reads_as_the_hour_after() -> None:
    assert next_local("02:30 America/Toronto", utc("2026-03-08T05:00")) == utc("2026-03-08T07:30")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("17:00 America/Toronto", None),
        ("9:05 Europe/London", None),
        ("17:00 UTC", None),
        ("17:00", "a time and a timezone"),
        ("25:00 America/Toronto", "a time and a timezone"),
        ("5pm America/Toronto", "a time and a timezone"),
        ("17:00 Mars/Olympus", "unknown timezone 'Mars/Olympus'"),
        ("17:00 ../etc/passwd", "unknown timezone"),
        (1700, "a time and a timezone"),
    ],
)
def test_a_bad_time_or_timezone_is_named(text: object, problem: str | None) -> None:
    said = local_time_problem(text)
    assert said is None if problem is None else problem in (said or "")
    if problem is not None and isinstance(text, str):
        with pytest.raises(ValueError):
            next_local(text, utc("2026-10-30T15:00"))
