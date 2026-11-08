"""When a schedule runs (ADR-0040 D4) — pure functions, no I/O.

Every time is on the 30-minute grid (:00 or :30) in the schedule's own timezone. Two presets:

    {"kind": "times", "at": ["08:00", "13:00", "18:00"], "days": [1, 2, 3, 4, 5]}
    {"kind": "every", "minutes": 120, "from": "08:00", "to": "18:00", "days": [1]}

`days` are ISO weekdays (1 = Monday); absent means every day. Times are local wall times: one
that does not exist on the night clocks go forward is skipped, and one that happens twice on the
night they go back runs once, at its first occurrence.
"""

from __future__ import annotations

import functools
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

GRID_MINUTES = 30
ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]
_HHMM = re.compile(r"^\d{2}:\d{2}$")
_KEYS = {"times": {"kind", "at", "days"}, "every": {"kind", "minutes", "from", "to", "days"}}


class RuleError(ValueError):
    """The rule is not one the platform runs; nothing was saved."""


@functools.cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(name: Any) -> str:
    if not isinstance(name, str) or name not in _zones():
        raise RuleError(f"unknown timezone {name!r}")
    return name


def _slot(value: Any) -> str:
    if not isinstance(value, str) or not _HHMM.match(value):
        raise RuleError(f"a time is written HH:MM, not {value!r}")
    hour, minute = int(value[:2]), int(value[3:])
    if hour > 23 or minute not in (0, GRID_MINUTES):
        raise RuleError(f"{value} is not on the 30-minute grid (:00 or :30)")
    return value


def _days(value: Any) -> list[int]:
    if value is None:
        return list(ALL_DAYS)
    if not isinstance(value, list) or not value:
        raise RuleError("days is a non-empty list of weekdays, 1 (Monday) to 7 (Sunday)")
    for day in value:
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 7:
            raise RuleError(f"{day!r} is not a weekday from 1 (Monday) to 7 (Sunday)")
    return sorted(set(value))


def validate(rule: Any) -> dict:
    """The rule in its one stored form, or RuleError."""
    if not isinstance(rule, dict) or rule.get("kind") not in _KEYS:
        raise RuleError("a rule is {'kind': 'times', ...} or {'kind': 'every', ...}")
    kind = rule["kind"]
    if extra := set(rule) - _KEYS[kind]:
        raise RuleError(f"unknown fields in a '{kind}' rule: {sorted(extra)}")
    days = _days(rule.get("days"))
    if kind == "times":
        at = rule.get("at")
        if not isinstance(at, list) or not at:
            raise RuleError("'times' needs at least one time")
        return {"kind": "times", "at": sorted({_slot(t) for t in at}), "days": days}
    minutes = rule.get("minutes")
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, int)
        or minutes % GRID_MINUTES
        or not GRID_MINUTES <= minutes <= 24 * 60
    ):
        raise RuleError("'every' runs every 30 minutes or a multiple of 30, up to once a day")
    start, end = _slot(rule.get("from", "00:00")), _slot(rule.get("to", "23:30"))
    if end < start:
        raise RuleError(f"'from' {start} is after 'to' {end}")
    return {"kind": "every", "minutes": minutes, "from": start, "to": end, "days": days}


def _times_of_day(rule: dict) -> list[time]:
    if rule["kind"] == "times":
        return [time(int(t[:2]), int(t[3:])) for t in rule["at"]]
    step = rule["minutes"]
    first = int(rule["from"][:2]) * 60 + int(rule["from"][3:])
    last = int(rule["to"][:2]) * 60 + int(rule["to"][3:])
    return [time(m // 60, m % 60) for m in range(first, last + 1, step)]


def _exists(local: datetime, zone: ZoneInfo) -> bool:
    """A wall time that clocks skip comes back from UTC as a different wall time."""
    back = local.astimezone(timezone.utc).astimezone(zone)
    return back.replace(tzinfo=None) == local.replace(tzinfo=None)


def next_after(rule: dict, tz: str, after: datetime) -> datetime:
    """The first time strictly after `after` (UTC-aware), in UTC."""
    if after.tzinfo is None:
        raise ValueError("`after` must carry a timezone")
    rule, zone = validate(rule), ZoneInfo(validate_timezone(tz))
    times = _times_of_day(rule)
    start: date = after.astimezone(zone).date() - timedelta(days=1)
    # Two weeks: a weekday is always within eight days, but a time the clock change skips on it
    # (Sunday 02:30 in spring) is then a week later still.
    for offset in range(16):
        day = start + timedelta(days=offset)
        if day.isoweekday() not in rule["days"]:
            continue
        for moment in times:
            local = datetime.combine(day, moment, tzinfo=zone)  # fold=0: the first of a repeat
            if not _exists(local, zone):
                continue
            utc = local.astimezone(timezone.utc)
            if utc > after:
                return utc
    raise AssertionError("a valid rule always has a time within two weeks")


def upcoming(rule: dict, tz: str, after: datetime, count: int) -> list[datetime]:
    """The next `count` times after `after`: what the Studio previews."""
    times: list[datetime] = []
    for _ in range(count):
        after = next_after(rule, tz, after)
        times.append(after)
    return times
