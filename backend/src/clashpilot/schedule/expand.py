"""Expand a schedule profile into UTC intervals (SPEC section 4, ADR-10).

Days are walked as local dates, never by adding 24 h in UTC, so that a clock change makes a night
last 7 or 9 hours. Everything here is pure: instants come in as parameters.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from operator import itemgetter
from zoneinfo import ZoneInfo

from clashpilot.schedule.profile import ScheduleProfile

Interval = tuple[int, int]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SECOND = timedelta(seconds=1)
_ONE_DAY = timedelta(days=1)


def _contains(intervals: Sequence[Interval], t: int) -> bool:
    index = bisect_right(intervals, t, key=itemgetter(0)) - 1
    return index >= 0 and t < intervals[index][1]


def _from_epoch(t: int) -> datetime:
    return _EPOCH + timedelta(seconds=t)


def _to_epoch(instant: datetime) -> int:
    return (instant - _EPOCH) // _SECOND


def _offset_at(t: int, zone: ZoneInfo) -> timedelta | None:
    return _from_epoch(t).astimezone(zone).utcoffset()


def _gap_transition(local: datetime, zone: ZoneInfo) -> int:
    """The instant at which the clock jumps over the nonexistent time `local`."""
    # In a gap, fold=1 reads the offset after the jump and so lands before it; fold=0 lands after.
    before = _to_epoch(local.replace(tzinfo=zone, fold=1))
    after = _to_epoch(local.replace(tzinfo=zone, fold=0))
    offset_before = _offset_at(before, zone)
    while after - before > 1:
        middle = (before + after) // 2
        if _offset_at(middle, zone) == offset_before:
            before = middle
        else:
            after = middle
    return after


def _local_to_epoch(local: datetime, zone: ZoneInfo, fold: int) -> int:
    """Epoch of a naive local time; `fold` picks the occurrence of a repeated hour."""
    instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != local:
        return _gap_transition(local, zone)
    return _to_epoch(instant)


def local_to_epoch(local: datetime, tz: str) -> int:
    """Epoch of a naive local time in the IANA zone `tz`.

    A time skipped by a clock change gives the first valid instant (the jump itself); a repeated
    time gives its first occurrence.
    """
    return _local_to_epoch(local, ZoneInfo(tz), fold=0)


def _normalized(spans: list[Interval], lower: int, upper: int) -> list[Interval]:
    """Clip `spans` to `[lower, upper)`, then sort them and merge what touches."""
    merged: list[Interval] = []
    for start, end in sorted((max(start, lower), min(end, upper)) for start, end in spans):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


@dataclass(frozen=True)
class AvailabilityCalendar:
    """Sorted, disjoint, half-open UTC intervals for each kind of window."""

    tz: str
    sleep: list[Interval]
    work: list[Interval]
    absence: list[Interval]
    available: list[Interval]
    preferred: list[Interval]

    def is_sleep(self, t: int) -> bool:
        return _contains(self.sleep, t)

    def can_act(self, t: int) -> bool:
        if _contains(self.sleep, t) or _contains(self.absence, t):
            return False
        return not _contains(self.work, t) or _contains(self.available, t)

    def local_day(self, t: int) -> date:
        return _from_epoch(t).astimezone(ZoneInfo(self.tz)).date()


def expand_windows(profile: ScheduleProfile, from_utc: int, to_utc: int) -> AvailabilityCalendar:
    """The windows of `profile` as UTC intervals bounded by `[from_utc, to_utc)`."""
    zone = ZoneInfo(profile.tz)
    spans: dict[str, list[Interval]] = {
        "sleep": [],
        "work": [],
        "available": [],
        "preferred": [],
        "absence": [],
    }

    # One extra day on each side: a window started the day before can reach into the range.
    day = _from_epoch(from_utc).astimezone(zone).date() - _ONE_DAY
    last_day = _from_epoch(to_utc).astimezone(zone).date() + _ONE_DAY
    while day <= last_day:
        for window in profile.weekly:
            if window.weekday is None or window.weekday == day.weekday():
                start = time.fromisoformat(window.start)
                end = time.fromisoformat(window.end)
                end_day = day + _ONE_DAY if end <= start else day
                spans[window.kind].append(
                    (
                        _local_to_epoch(datetime.combine(day, start), zone, fold=0),
                        _local_to_epoch(datetime.combine(end_day, end), zone, fold=1),
                    )
                )
        day += _ONE_DAY

    for exception in profile.exceptions:
        spans["absence"].append(
            (
                _local_to_epoch(datetime.fromisoformat(exception.start_local), zone, fold=0),
                _local_to_epoch(datetime.fromisoformat(exception.end_local), zone, fold=1),
            )
        )

    return AvailabilityCalendar(
        tz=profile.tz,
        sleep=_normalized(spans["sleep"], from_utc, to_utc),
        work=_normalized(spans["work"], from_utc, to_utc),
        absence=_normalized(spans["absence"], from_utc, to_utc),
        available=_normalized(spans["available"], from_utc, to_utc),
        preferred=_normalized(spans["preferred"], from_utc, to_utc),
    )
