"""When a schedule runs (ADR-0040 D4): every time on the 30-minute grid, in the workspace's local
time, with both DST changes handled. Pure functions, no database."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schedules.rules import RuleError, next_after, upcoming, validate, validate_timezone

BERLIN = "Europe/Berlin"
UTC = timezone.utc
THREE_A_DAY = {"kind": "times", "days": [1, 2, 3, 4, 5], "at": ["08:00", "13:00", "18:00"]}


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_three_times_on_weekdays_runs_at_each_and_skips_the_weekend() -> None:
    # Friday 6 November 2026, 12:00 in Berlin (CET, UTC+1).
    friday_noon = _utc(2026, 11, 6, 11, 0)
    assert upcoming(THREE_A_DAY, BERLIN, friday_noon, 3) == [
        _utc(2026, 11, 6, 12, 0),  # Fri 13:00
        _utc(2026, 11, 6, 17, 0),  # Fri 18:00
        _utc(2026, 11, 9, 7, 0),  # Mon 08:00
    ]


def test_the_next_time_is_strictly_after_the_one_given() -> None:
    at_eight = _utc(2026, 11, 9, 7, 0)  # Mon 08:00 Berlin
    assert next_after(THREE_A_DAY, BERLIN, at_eight) == _utc(2026, 11, 9, 12, 0)


def test_no_days_means_every_day() -> None:
    rule = {"kind": "times", "at": ["09:00"]}
    saturday = _utc(2026, 11, 7, 9, 0)  # Sat 10:00 Berlin
    assert next_after(rule, BERLIN, saturday) == _utc(2026, 11, 8, 8, 0)  # Sun 09:00


def test_every_two_hours_within_opening_times() -> None:
    rule = {"kind": "every", "minutes": 120, "from": "08:00", "to": "18:00", "days": [1]}
    monday = _utc(2026, 11, 9, 0, 0)
    times = upcoming(rule, BERLIN, monday, 7)
    assert [t.astimezone(UTC).hour + 1 for t in times] == [8, 10, 12, 14, 16, 18, 8]
    assert times[-1] == _utc(2026, 11, 16, 7, 0)  # the next Monday


def test_every_thirty_minutes_is_the_fastest() -> None:
    rule = {"kind": "every", "minutes": 30}
    assert upcoming(rule, BERLIN, _utc(2026, 11, 9, 10, 10), 2) == [
        _utc(2026, 11, 9, 10, 30),
        _utc(2026, 11, 9, 11, 0),
    ]


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "times", "at": ["08:15"]},  # off the grid
        {"kind": "times", "at": []},
        {"kind": "times", "at": ["25:00"]},
        {"kind": "times", "at": ["8"]},
        {"kind": "times", "at": ["08:00"], "days": [0]},
        {"kind": "times", "at": ["08:00"], "days": [8]},
        {"kind": "every", "minutes": 15},  # faster than the grid
        {"kind": "every", "minutes": 45},  # not a multiple of 30
        {"kind": "every", "minutes": 60, "from": "18:00", "to": "08:00"},
        {"kind": "cron", "expression": "*/5 * * * *"},
        {"kind": "times", "at": ["08:00"], "extra": True},
        "08:00",
    ],
)
def test_a_rule_off_the_grid_or_malformed_is_refused(rule) -> None:
    with pytest.raises(RuleError):
        validate(rule)


def test_a_valid_rule_is_normalised() -> None:
    rule = {"kind": "times", "at": ["18:00", "08:00", "08:00"], "days": [5, 1]}
    assert validate(rule) == {"kind": "times", "at": ["08:00", "18:00"], "days": [1, 5]}
    assert validate({"kind": "every", "minutes": 60}) == {
        "kind": "every", "minutes": 60, "from": "00:00", "to": "23:30", "days": [1, 2, 3, 4, 5, 6, 7],
    }


def test_spring_forward_skips_the_hour_that_does_not_exist() -> None:
    # 28 March 2027: Berlin goes from 02:00 CET straight to 03:00 CEST.
    rule = {"kind": "every", "minutes": 30, "from": "01:00", "to": "04:00"}
    after = _utc(2027, 3, 28, 0, 30)  # 01:30 CET
    assert upcoming(rule, BERLIN, after, 2) == [
        _utc(2027, 3, 28, 1, 0),  # 03:00 CEST; 02:00 and 02:30 do not exist
        _utc(2027, 3, 28, 1, 30),
    ]


def test_fall_back_runs_a_repeated_time_once_at_its_first_occurrence() -> None:
    # 25 October 2026: Berlin's 02:00–03:00 happens twice, first CEST (UTC+2), then CET (UTC+1).
    rule = {"kind": "times", "at": ["02:30", "03:00"]}
    after = _utc(2026, 10, 25, 0, 0)  # 02:00 CEST
    assert upcoming(rule, BERLIN, after, 2) == [
        _utc(2026, 10, 25, 0, 30),  # 02:30 CEST, the first
        _utc(2026, 10, 25, 2, 0),  # 03:00 CET; the second 02:30 (01:30 UTC) is not run
    ]


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(ValueError):
        next_after(THREE_A_DAY, BERLIN, datetime(2026, 11, 6, 12, 0))


def test_an_unknown_timezone_is_refused() -> None:
    assert validate_timezone("Europe/Berlin") == "Europe/Berlin"
    for bad in ("Mars/Olympus", "", "../etc/passwd"):
        with pytest.raises(RuleError):
            validate_timezone(bad)


@pytest.mark.parametrize("after", [_utc(2027, 3, 27, 12, 0), _utc(2027, 3, 21, 2, 0)])
def test_a_weekly_time_the_clock_change_skips_runs_the_week_after(after) -> None:
    # Sunday 28 March 2027 has no 02:30 in Berlin; the next one is Sunday 4 April (CEST, UTC+2).
    rule = {"kind": "times", "days": [7], "at": ["02:30"]}
    assert next_after(rule, BERLIN, after) == _utc(2027, 4, 4, 0, 30)
