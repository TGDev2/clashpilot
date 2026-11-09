"""Acceptance tests for E06: player schedule and reliable local -> UTC conversion (SPEC ADR-10).

Frozen with the task. They describe `clashpilot.schedule.expand` and the routes
`PUT|GET /api/villages/{id}/schedule` and `GET /api/villages/{id}/schedule/preview`.

Shapes that the task text leaves open are fixed here:

- The profile model is `clashpilot.schedule.profile.ScheduleProfile`, a pydantic model built here
  from its JSON document. `start_local` / `end_local` are ISO-8601 local date-times without offset
  (`YYYY-MM-DDTHH:MM:SS`).
- `AvailabilityCalendar` is `clashpilot.schedule.expand.AvailabilityCalendar`, constructed with the
  keywords `tz, sleep, work, absence, available, preferred` (SPEC section 4), each interval being a
  `(start, end)` pair of integer epochs.
- `PUT schedule` answers 200 and replaces the whole profile. The schedule table has no foreign key
  to villages: these tests never import a village.
- `preview` answers a JSON object with the calendar fields at the top level: `tz`, and `sleep`,
  `work`, `absence`, `available`, `preferred`, each a list of `[start, end]` integer epochs,
  bounded by `clock()` and `clock() + days * 86400`.

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the modules do not exist.
"""

from __future__ import annotations

import datetime
import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

FIELDS = ("sleep", "work", "absence", "available", "preferred")

Span = tuple[int, int]


def utc(text: str) -> int:
    """Epoch of an ISO-8601 UTC instant such as `2026-03-28T22:00Z`."""
    return int(datetime.datetime.fromisoformat(text).timestamp())


NOW = utc("2026-06-10T02:00Z")


# --------------------------------------------------------------------------- profile documents


def window(start: str, end: str, kind: str = "sleep", weekday: int | None = None) -> dict[str, Any]:
    return {"weekday": weekday, "start": start, "end": end, "kind": kind}


def away(start_local: str, end_local: str) -> dict[str, Any]:
    return {"start_local": start_local, "end_local": end_local, "kind": "absence"}


def profile_doc(
    *weekly: dict[str, Any],
    tz: str = "Europe/Paris",
    exceptions: tuple[dict[str, Any], ...] = (),
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tz": tz,
        "weekly": list(weekly),
        "exceptions": list(exceptions),
        "policy": policy or {"mode": "strict", "tolerance_minutes": 0, "max_logins_per_day": 3},
    }


NIGHTS = profile_doc(window("23:00", "07:00", "sleep"))


# --------------------------------------------------------------------------- feature access


def parse_profile(document: dict[str, Any]) -> Any:
    from clashpilot.schedule.profile import ScheduleProfile

    return ScheduleProfile.model_validate_json(json.dumps(document))


def expand(document: dict[str, Any], from_utc: int, to_utc: int) -> Any:
    from clashpilot.schedule.expand import expand_windows

    return expand_windows(parse_profile(document), from_utc, to_utc)


def calendar(tz: str = "UTC", **fields: list[Span]) -> Any:
    from clashpilot.schedule.expand import AvailabilityCalendar

    return AvailabilityCalendar(tz=tz, **{name: fields.get(name, []) for name in FIELDS})


def spans(intervals: Any) -> list[Span]:
    return [(start, end) for start, end in intervals]


def assert_well_formed(result: Any, from_utc: int, to_utc: int) -> None:
    """Every interval list is made of integers, sorted, disjoint, non-empty and bounded."""
    for name in FIELDS:
        intervals = spans(getattr(result, name))
        for start, end in intervals:
            assert type(start) is int, name
            assert type(end) is int, name
            assert from_utc <= start < end <= to_utc, name
        for (_, previous_end), (next_start, _) in itertools.pairwise(intervals):
            assert previous_end <= next_start, name


# --------------------------------------------------------------------------- HTTP helpers


class MovableClock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def make_client(data_dir: Path, clock: MovableClock | None = None) -> TestClient:
    settings_ = Settings(data_dir=data_dir, frontend_dist=data_dir / "no-dist")
    return TestClient(create_app(settings_, clock=clock or MovableClock(NOW)))


@pytest.fixture
def clock() -> MovableClock:
    return MovableClock(NOW)


@pytest.fixture
def client(tmp_path: Path, clock: MovableClock) -> TestClient:
    return make_client(tmp_path, clock)


def schedule_url(village_id: str = "2PP") -> str:
    return f"/api/villages/{village_id}/schedule"


def put_profile(client: TestClient, document: dict[str, Any], village_id: str = "2PP") -> None:
    response = client.put(schedule_url(village_id), json=document)
    assert response.status_code == 200, response.text


def get_profile(client: TestClient, village_id: str = "2PP") -> Any:
    response = client.get(schedule_url(village_id))
    assert response.status_code == 200, response.text
    return response.json()


def preview(client: TestClient, days: int = 7, village_id: str = "2PP") -> Any:
    response = client.get(f"{schedule_url(village_id)}/preview", params={"days": days})
    assert response.status_code == 200, response.text
    return response.json()


def assert_refused(response: Any, code: str) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str)
    assert detail["message"]


# --------------------------------------------------------------------------- calendar shape


RICH_WEEKLY = (
    window("23:00", "07:00", "sleep"),
    window("02:30", "02:45", "sleep", weekday=6),
    window("09:00", "12:00", "work"),
    window("11:00", "17:00", "work", weekday=0),
    window("11:00", "17:00", "work", weekday=4),
    window("12:00", "13:00", "available"),
    window("20:00", "22:00", "preferred", weekday=5),
    window("01:30", "03:30", "preferred", weekday=6),
)

ZONES = (
    "Europe/Paris",
    "America/New_York",
    "Australia/Lord_Howe",
    "Asia/Kathmandu",
    "Pacific/Apia",
    "UTC",
)


@pytest.mark.parametrize("tz", ["Europe/Paris", "America/New_York", "Asia/Tokyo"])
def test_expand_windows_returns_a_calendar_in_the_profile_timezone(tz: str) -> None:
    from clashpilot.schedule.expand import AvailabilityCalendar

    result = expand(
        profile_doc(window("23:00", "07:00"), tz=tz),
        utc("2026-06-01T00:00Z"),
        utc("2026-06-08T00:00Z"),
    )

    assert isinstance(result, AvailabilityCalendar)
    assert result.tz == tz


@pytest.mark.parametrize("tz", ZONES)
@pytest.mark.parametrize(
    ("from_text", "to_text"),
    [
        ("2026-03-01T05:17Z", "2026-04-30T19:43Z"),
        ("2026-10-01T05:17Z", "2026-11-30T19:43Z"),
    ],
)
def test_intervals_are_sorted_disjoint_half_open_and_bounded(
    tz: str, from_text: str, to_text: str
) -> None:
    document = profile_doc(
        *RICH_WEEKLY,
        tz=tz,
        exceptions=(
            away("2026-03-10T08:00:00", "2026-03-12T20:00:00"),
            away("2026-03-11T08:00:00", "2026-03-14T20:00:00"),
            away("2026-10-20T08:00:00", "2026-11-02T20:00:00"),
        ),
    )
    from_utc, to_utc = utc(from_text), utc(to_text)

    result = expand(document, from_utc, to_utc)

    assert_well_formed(result, from_utc, to_utc)
    assert spans(result.sleep), "the period holds nights"


def test_intervals_crossing_the_requested_range_are_truncated() -> None:
    from_utc, to_utc = utc("2026-06-10T00:00Z"), utc("2026-06-12T00:00Z")

    result = expand(NIGHTS, from_utc, to_utc)

    assert spans(result.sleep) == [
        (from_utc, utc("2026-06-10T05:00Z")),
        (utc("2026-06-10T21:00Z"), utc("2026-06-11T05:00Z")),
        (utc("2026-06-11T21:00Z"), to_utc),
    ]


def test_a_night_already_started_before_the_local_day_of_the_range_is_kept() -> None:
    # 20:00Z on June 9 is already 05:00 on June 10 in Tokyo: the range opens inside a night that
    # started on the local date before the first local date of the range.
    document = profile_doc(window("23:00", "07:00"), tz="Asia/Tokyo")
    from_utc, to_utc = utc("2026-06-09T20:00Z"), utc("2026-06-10T15:00Z")

    assert spans(expand(document, from_utc, to_utc).sleep) == [
        (from_utc, utc("2026-06-09T22:00Z")),
        (utc("2026-06-10T14:00Z"), to_utc),
    ]


@settings(max_examples=50, deadline=None, database=None)
@given(
    offset=st.integers(min_value=0, max_value=14 * DAY),
    length=st.integers(min_value=1, max_value=3 * DAY),
)
def test_a_sub_range_is_the_clipped_expansion_of_a_wider_range(offset: int, length: int) -> None:
    document = profile_doc(
        *RICH_WEEKLY, exceptions=(away("2026-03-28T12:00:00", "2026-03-30T12:00:00"),)
    )
    wide_from, wide_to = utc("2026-03-18T00:00Z"), utc("2026-04-08T00:00Z")
    from_utc, to_utc = wide_from + offset, wide_from + offset + length

    wide = expand(document, wide_from, wide_to)
    narrow = expand(document, from_utc, to_utc)

    for name in FIELDS:
        clipped = [
            (max(start, from_utc), min(end, to_utc))
            for start, end in spans(getattr(wide, name))
            if start < to_utc and end > from_utc
        ]
        assert spans(getattr(narrow, name)) == clipped, name


# --------------------------------------------------------------------------- local_day


@pytest.mark.parametrize(
    ("tz", "instant", "expected"),
    [
        ("Europe/Paris", "2026-03-28T23:30Z", datetime.date(2026, 3, 29)),
        ("Europe/Paris", "2026-03-28T22:30Z", datetime.date(2026, 3, 28)),
        ("Europe/Paris", "2026-10-24T22:30Z", datetime.date(2026, 10, 25)),
        ("Asia/Tokyo", "2026-06-07T15:00Z", datetime.date(2026, 6, 8)),
        ("America/New_York", "2026-06-08T03:59Z", datetime.date(2026, 6, 7)),
    ],
)
def test_local_day_is_the_date_in_the_profile_timezone(
    tz: str, instant: str, expected: datetime.date
) -> None:
    t = utc(instant)
    result = expand(profile_doc(tz=tz), t - DAY, t + DAY)

    day = result.local_day(t)

    assert type(day) is datetime.date
    assert day == expected


# --------------------------------------------------------------------------- nights and DST


@pytest.mark.parametrize(
    ("start", "end", "hours"),
    [
        ("2026-03-28T22:00Z", "2026-03-29T05:00Z", 7),
        ("2026-10-24T21:00Z", "2026-10-25T06:00Z", 9),
        ("2026-06-10T21:00Z", "2026-06-11T05:00Z", 8),
        ("2026-01-14T22:00Z", "2026-01-15T06:00Z", 8),
    ],
)
def test_a_night_lasts_what_the_clock_change_makes_it_last(
    start: str, end: str, hours: int
) -> None:
    from_utc, to_utc = utc(start) - 3 * DAY, utc(end) + 3 * DAY

    sleep = spans(expand(NIGHTS, from_utc, to_utc).sleep)

    assert (utc(start), utc(end)) in sleep
    assert utc(end) - utc(start) == hours * HOUR


def test_over_a_whole_year_only_the_two_change_nights_differ_from_eight_hours() -> None:
    from_utc, to_utc = utc("2026-01-01T00:00Z"), utc("2027-01-01T00:00Z")

    sleep = spans(expand(NIGHTS, from_utc, to_utc).sleep)

    # The first and last intervals are truncated by the range.
    durations = Counter((end - start) / HOUR for start, end in sleep[1:-1])
    assert durations[7] == 1
    assert durations[9] == 1
    assert set(durations) == {7, 8, 9}
    assert durations[8] == 362


@pytest.mark.parametrize(
    ("tz", "weekly_window", "day", "expected"),
    [
        pytest.param(
            "Europe/Paris",
            window("02:30", "05:00", "work", weekday=6),
            "2026-03-29",
            ("2026-03-29T01:00Z", "2026-03-29T03:00Z"),
            id="paris-start-in-gap",
        ),
        pytest.param(
            "Europe/Paris",
            window("01:00", "02:30", "work", weekday=6),
            "2026-03-29",
            ("2026-03-29T00:00Z", "2026-03-29T01:00Z"),
            id="paris-end-in-gap",
        ),
        pytest.param(
            "Europe/Paris",
            window("02:30", "05:00", "work", weekday=6),
            "2026-10-25",
            ("2026-10-25T00:30Z", "2026-10-25T04:00Z"),
            id="paris-start-in-repeated-hour",
        ),
        pytest.param(
            "Europe/Paris",
            window("01:00", "02:30", "work", weekday=6),
            "2026-10-25",
            ("2026-10-24T23:00Z", "2026-10-25T01:30Z"),
            id="paris-end-in-repeated-hour",
        ),
        pytest.param(
            "America/New_York",
            window("02:30", "05:00", "work", weekday=6),
            "2026-03-08",
            ("2026-03-08T07:00Z", "2026-03-08T09:00Z"),
            id="new-york-start-in-gap",
        ),
        pytest.param(
            "America/New_York",
            window("01:30", "04:00", "work", weekday=6),
            "2026-11-01",
            ("2026-11-01T05:30Z", "2026-11-01T09:00Z"),
            id="new-york-start-in-repeated-hour",
        ),
        pytest.param(
            "America/New_York",
            window("00:30", "01:30", "work", weekday=6),
            "2026-11-01",
            ("2026-11-01T04:30Z", "2026-11-01T06:30Z"),
            id="new-york-end-in-repeated-hour",
        ),
    ],
)
def test_local_bounds_around_clock_changes(
    tz: str, weekly_window: dict[str, Any], day: str, expected: tuple[str, str]
) -> None:
    # The window applies to one Sunday only inside the range: one day on each side.
    from_utc, to_utc = utc(f"{day}T00:00Z") - DAY, utc(f"{day}T00:00Z") + 2 * DAY

    result = expand(profile_doc(weekly_window, tz=tz), from_utc, to_utc)

    assert spans(result.work) == [(utc(expected[0]), utc(expected[1]))]


def test_a_window_entirely_inside_the_gap_produces_no_interval() -> None:
    document = profile_doc(window("02:10", "02:40", "work", weekday=6))
    from_utc, to_utc = utc("2026-03-28T00:00Z"), utc("2026-03-31T00:00Z")

    assert spans(expand(document, from_utc, to_utc).work) == []


# --------------------------------------------------------------------------- can_act, is_sleep


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        pytest.param(99, True, id="free-before-sleep"),
        pytest.param(100, False, id="sleep-first-second"),
        pytest.param(150, False, id="sleep-beats-available"),
        pytest.param(199, False, id="sleep-last-second"),
        pytest.param(200, True, id="sleep-end-is-excluded"),
        pytest.param(300, False, id="work-first-second"),
        pytest.param(349, False, id="work-before-available"),
        pytest.param(350, True, id="work-and-available"),
        pytest.param(379, True, id="work-and-available-last-second"),
        pytest.param(380, False, id="work-after-available"),
        pytest.param(399, False, id="work-last-second"),
        pytest.param(400, True, id="work-end-is-excluded"),
        pytest.param(450, True, id="available-outside-work"),
        pytest.param(500, False, id="absence-first-second"),
        pytest.param(555, False, id="absence-beats-available"),
        pytest.param(599, False, id="absence-last-second"),
        pytest.param(600, True, id="absence-end-is-excluded"),
        pytest.param(750, True, id="preferred"),
    ],
)
def test_can_act(t: int, expected: bool) -> None:
    result = calendar(
        sleep=[(100, 200)],
        work=[(300, 400)],
        absence=[(500, 600)],
        available=[(150, 160), (350, 380), (440, 460), (550, 560)],
        preferred=[(700, 800)],
    )

    assert result.can_act(t) is expected


@pytest.mark.parametrize(
    ("t", "expected"), [(99, False), (100, True), (199, True), (200, False), (350, False)]
)
def test_is_sleep_is_half_open(t: int, expected: bool) -> None:
    assert calendar(sleep=[(100, 200)], work=[(300, 400)]).is_sleep(t) is expected


def test_can_act_and_is_sleep_on_an_expanded_profile() -> None:
    document = profile_doc(
        window("23:00", "07:00", "sleep"),
        window("09:00", "17:00", "work"),
        window("12:00", "13:00", "available"),
    )
    result = expand(document, utc("2026-06-10T00:00Z"), utc("2026-06-12T00:00Z"))
    night_end = utc("2026-06-10T05:00Z")

    assert result.is_sleep(night_end - 1) is True
    assert result.can_act(night_end - 1) is False
    assert result.is_sleep(night_end) is False
    assert result.can_act(night_end) is True
    assert result.can_act(utc("2026-06-10T08:00Z")) is False, "at work"
    assert result.can_act(utc("2026-06-10T10:30Z")) is True, "lunch break at work"
    assert result.can_act(utc("2026-06-10T16:00Z")) is True, "free evening"
    assert result.is_sleep(utc("2026-06-10T21:00Z")) is True


# --------------------------------------------------------------------------- weekdays, merging


@pytest.mark.parametrize("weekday", range(7))
def test_a_weekday_window_applies_on_that_local_day_only(weekday: int) -> None:
    # June 8, 2026 is a Monday.
    document = profile_doc(window("09:00", "10:00", "work", weekday=weekday))
    day = utc("2026-06-08T07:00Z") + weekday * DAY

    result = expand(document, utc("2026-06-08T00:00Z"), utc("2026-06-15T00:00Z"))

    assert spans(result.work) == [(day, day + HOUR)]


def test_a_daily_window_has_a_null_weekday_and_applies_every_day() -> None:
    document = profile_doc(window("09:00", "10:00", "work"))

    result = expand(document, utc("2026-06-08T00:00Z"), utc("2026-06-15T00:00Z"))

    first = utc("2026-06-08T07:00Z")
    assert spans(result.work) == [(first + n * DAY, first + n * DAY + HOUR) for n in range(7)]


def test_the_weekday_is_read_on_the_local_date_not_the_utc_date() -> None:
    # Monday 00:30 in Tokyo is still Sunday in UTC.
    document = profile_doc(window("00:30", "01:00", "work", weekday=0), tz="Asia/Tokyo")

    result = expand(document, utc("2026-06-02T00:00Z"), utc("2026-06-14T00:00Z"))

    assert spans(result.work) == [(utc("2026-06-07T15:30Z"), utc("2026-06-07T16:00Z"))]


def test_a_weekday_window_crossing_midnight_belongs_to_its_starting_day() -> None:
    # Friday nights only (June 12 and 19, 2026): nothing starts on the Saturdays they run into.
    document = profile_doc(window("23:00", "02:00", "work", weekday=4))

    result = expand(document, utc("2026-06-08T00:00Z"), utc("2026-06-22T00:00Z"))

    assert spans(result.work) == [
        (utc("2026-06-12T21:00Z"), utc("2026-06-13T00:00Z")),
        (utc("2026-06-19T21:00Z"), utc("2026-06-20T00:00Z")),
    ]


def test_a_monday_night_is_not_the_night_of_sunday_to_monday() -> None:
    document = profile_doc(window("23:00", "07:00", "sleep", weekday=0))

    result = expand(document, utc("2026-06-07T00:00Z"), utc("2026-06-14T00:00Z"))

    assert spans(result.sleep) == [(utc("2026-06-08T21:00Z"), utc("2026-06-09T05:00Z"))]


def test_a_window_ending_at_midnight_ends_the_next_day() -> None:
    document = profile_doc(window("22:00", "00:00", "work", weekday=2))

    result = expand(document, utc("2026-06-08T00:00Z"), utc("2026-06-15T00:00Z"))

    assert spans(result.work) == [(utc("2026-06-10T20:00Z"), utc("2026-06-10T22:00Z"))]


@pytest.mark.parametrize(
    ("weekly", "expected"),
    [
        pytest.param(
            (window("09:00", "12:00", "work"), window("11:00", "14:00", "work")),
            ("2026-06-10T07:00Z", "2026-06-10T12:00Z"),
            id="partial-overlap",
        ),
        pytest.param(
            (window("09:00", "17:00", "work"), window("10:00", "11:00", "work")),
            ("2026-06-10T07:00Z", "2026-06-10T15:00Z"),
            id="contained",
        ),
        pytest.param(
            (window("11:00", "14:00", "work"), window("09:00", "12:00", "work", weekday=2)),
            ("2026-06-10T07:00Z", "2026-06-10T12:00Z"),
            id="daily-and-weekday",
        ),
    ],
)
def test_overlaps_of_the_same_kind_are_merged(
    weekly: tuple[dict[str, Any], ...], expected: tuple[str, str]
) -> None:
    result = expand(profile_doc(*weekly), utc("2026-06-10T00:00Z"), utc("2026-06-11T00:00Z"))

    assert spans(result.work) == [(utc(expected[0]), utc(expected[1]))]


def test_an_evening_window_merges_into_the_overlapping_night() -> None:
    document = profile_doc(
        window("23:00", "07:00", "sleep"), window("22:00", "00:30", "sleep", weekday=2)
    )

    result = expand(document, utc("2026-06-10T12:00Z"), utc("2026-06-11T12:00Z"))

    assert spans(result.sleep) == [(utc("2026-06-10T20:00Z"), utc("2026-06-11T05:00Z"))]


def test_windows_of_different_kinds_are_kept_apart() -> None:
    document = profile_doc(window("09:00", "12:00", "work"), window("11:00", "14:00", "available"))

    result = expand(document, utc("2026-06-10T00:00Z"), utc("2026-06-11T00:00Z"))

    assert spans(result.work) == [(utc("2026-06-10T07:00Z"), utc("2026-06-10T10:00Z"))]
    assert spans(result.available) == [(utc("2026-06-10T09:00Z"), utc("2026-06-10T12:00Z"))]


# --------------------------------------------------------------------------- absences


ABSENCE_CASES = [
    pytest.param(
        "2026-07-14T09:00:00",
        "2026-07-16T18:00:00",
        "2026-07-14T07:00Z",
        "2026-07-16T16:00Z",
        id="summer",
    ),
    pytest.param(
        "2026-03-28T12:00:00",
        "2026-03-30T12:00:00",
        "2026-03-28T11:00Z",
        "2026-03-30T10:00Z",
        id="across-the-spring-change",
    ),
    pytest.param(
        "2026-03-29T02:30:00",
        "2026-03-29T12:00:00",
        "2026-03-29T01:00Z",
        "2026-03-29T10:00Z",
        id="start-in-gap",
    ),
    pytest.param(
        "2026-10-25T02:30:00",
        "2026-10-25T12:00:00",
        "2026-10-25T00:30Z",
        "2026-10-25T11:00Z",
        id="start-in-repeated-hour",
    ),
    pytest.param(
        "2026-10-25T01:00:00",
        "2026-10-25T02:30:00",
        "2026-10-24T23:00Z",
        "2026-10-25T01:30Z",
        id="end-in-repeated-hour",
    ),
]


@pytest.mark.parametrize(("start_local", "end_local", "start", "end"), ABSENCE_CASES)
def test_an_absence_in_local_dates_blocks_its_whole_duration(
    start_local: str, end_local: str, start: str, end: str
) -> None:
    document = profile_doc(exceptions=(away(start_local, end_local),))
    first, last = utc(start), utc(end)

    result = expand(document, first - 2 * DAY, last + 2 * DAY)

    assert spans(result.absence) == [(first, last)]
    assert result.can_act(first - 1) is True
    assert result.can_act(first) is False
    assert result.can_act((first + last) // 2) is False
    assert result.can_act(last - 1) is False
    assert result.can_act(last) is True


def test_an_absence_also_blocks_an_available_window() -> None:
    document = profile_doc(
        window("12:00", "13:00", "available"),
        exceptions=(away("2026-07-14T09:00:00", "2026-07-16T18:00:00"),),
    )

    result = expand(document, utc("2026-07-13T00:00Z"), utc("2026-07-17T00:00Z"))

    assert result.can_act(utc("2026-07-15T10:30Z")) is False


def test_an_absence_is_truncated_to_the_requested_range() -> None:
    document = profile_doc(exceptions=(away("2026-07-14T09:00:00", "2026-07-16T18:00:00"),))
    from_utc, to_utc = utc("2026-07-15T00:00Z"), utc("2026-07-16T00:00Z")

    assert spans(expand(document, from_utc, to_utc).absence) == [(from_utc, to_utc)]


def test_an_absence_outside_the_requested_range_does_not_appear() -> None:
    document = profile_doc(exceptions=(away("2026-07-14T09:00:00", "2026-07-16T18:00:00"),))

    result = expand(document, utc("2026-08-01T00:00Z"), utc("2026-08-02T00:00Z"))

    assert spans(result.absence) == []


def test_overlapping_absences_are_merged() -> None:
    document = profile_doc(
        exceptions=(
            away("2026-07-14T09:00:00", "2026-07-15T09:00:00"),
            away("2026-07-15T08:00:00", "2026-07-16T18:00:00"),
        )
    )

    result = expand(document, utc("2026-07-13T00:00Z"), utc("2026-07-17T00:00Z"))

    assert spans(result.absence) == [(utc("2026-07-14T07:00Z"), utc("2026-07-16T16:00Z"))]


# --------------------------------------------------------------------------- schedule routes


FULL_PROFILE = profile_doc(
    window("23:00", "07:00", "sleep"),
    window("09:00", "17:30", "work", weekday=0),
    window("12:00", "13:00", "available", weekday=1),
    window("20:15", "22:45", "preferred", weekday=6),
    tz="America/New_York",
    exceptions=(away("2026-07-14T09:00:00", "2026-07-16T18:00:00"),),
    policy={"mode": "tolerance", "tolerance_minutes": 45, "max_logins_per_day": 4},
)


def test_put_then_get_returns_the_identical_profile(client: TestClient) -> None:
    put_profile(client, FULL_PROFILE)

    assert get_profile(client) == FULL_PROFILE


@pytest.mark.parametrize("mode", ["strict", "tolerance", "progress", "min_logins"])
def test_every_policy_mode_is_stored(client: TestClient, mode: str) -> None:
    document = profile_doc(
        window("23:00", "07:00"),
        policy={"mode": mode, "tolerance_minutes": 10, "max_logins_per_day": 2},
    )

    put_profile(client, document)

    assert get_profile(client) == document


def test_put_replaces_the_previous_profile(client: TestClient) -> None:
    put_profile(client, FULL_PROFILE)
    put_profile(client, NIGHTS)

    assert get_profile(client) == NIGHTS


def test_each_village_has_its_own_profile(client: TestClient) -> None:
    put_profile(client, FULL_PROFILE, village_id="2PP")
    put_profile(client, NIGHTS, village_id="9QQ")

    assert get_profile(client, village_id="2PP") == FULL_PROFILE
    assert get_profile(client, village_id="9QQ") == NIGHTS


def test_the_profile_survives_a_restart(tmp_path: Path) -> None:
    put_profile(make_client(tmp_path), FULL_PROFILE)

    assert get_profile(make_client(tmp_path)) == FULL_PROFILE


@pytest.mark.parametrize("tz", ["Mars/Olympus", "../../etc/passwd", "Europe", ""])
def test_an_unknown_timezone_is_refused(client: TestClient, tz: str) -> None:
    put_profile(client, NIGHTS)

    response = client.put(schedule_url(), json=profile_doc(window("23:00", "07:00"), tz=tz))

    assert_refused(response, "invalid_timezone")
    assert get_profile(client) == NIGHTS, "a refused profile changes nothing"


@pytest.mark.parametrize(
    "zero",
    [
        window("08:00", "08:00", "work"),
        window("00:00", "00:00", "sleep"),
        window("23:59", "23:59", "preferred", weekday=3),
    ],
)
def test_a_zero_length_window_is_refused(client: TestClient, zero: dict[str, Any]) -> None:
    put_profile(client, NIGHTS)

    response = client.put(schedule_url(), json=profile_doc(window("09:00", "10:00"), zero))

    assert_refused(response, "invalid_window")
    assert get_profile(client) == NIGHTS, "a refused profile changes nothing"


# --------------------------------------------------------------------------- preview


def test_preview_returns_seven_days_of_utc_windows_from_the_clock(client: TestClient) -> None:
    put_profile(client, NIGHTS)

    body = preview(client, days=7)

    assert body["tz"] == "Europe/Paris"
    end = NOW + 7 * DAY
    assert spans(body["sleep"]) == [
        (NOW, utc("2026-06-10T05:00Z")),
        *(
            (utc("2026-06-10T21:00Z") + n * DAY, utc("2026-06-11T05:00Z") + n * DAY)
            for n in range(6)
        ),
        (utc("2026-06-16T21:00Z"), end),
    ]
    for name in ("work", "absence", "available", "preferred"):
        assert body[name] == [], name


def test_preview_carries_every_kind_of_window(client: TestClient) -> None:
    document = profile_doc(
        window("09:00", "10:00", "work", weekday=2),
        window("12:00", "13:00", "available", weekday=2),
        window("20:00", "21:00", "preferred", weekday=2),
        exceptions=(away("2026-06-12T09:00:00", "2026-06-12T18:00:00"),),
    )
    put_profile(client, document)

    body = preview(client, days=7)

    assert spans(body["work"]) == [(utc("2026-06-10T07:00Z"), utc("2026-06-10T08:00Z"))]
    assert spans(body["available"]) == [(utc("2026-06-10T10:00Z"), utc("2026-06-10T11:00Z"))]
    assert spans(body["preferred"]) == [(utc("2026-06-10T18:00Z"), utc("2026-06-10T19:00Z"))]
    assert spans(body["absence"]) == [(utc("2026-06-12T07:00Z"), utc("2026-06-12T16:00Z"))]


def test_preview_honours_the_number_of_days(client: TestClient) -> None:
    put_profile(client, NIGHTS)

    body = preview(client, days=2)

    assert spans(body["sleep"]) == [
        (NOW, utc("2026-06-10T05:00Z")),
        (utc("2026-06-10T21:00Z"), utc("2026-06-11T05:00Z")),
        (utc("2026-06-11T21:00Z"), NOW + 2 * DAY),
    ]


def test_preview_starts_at_the_current_clock(client: TestClient, clock: MovableClock) -> None:
    put_profile(client, NIGHTS)
    preview(client, days=1)

    clock.now = NOW + 3 * DAY
    body = preview(client, days=1)

    assert spans(body["sleep"]) == [
        (NOW + 3 * DAY, utc("2026-06-13T05:00Z")),
        (utc("2026-06-13T21:00Z"), NOW + 4 * DAY),
    ]


def test_preview_crosses_a_clock_change(client: TestClient, clock: MovableClock) -> None:
    put_profile(client, NIGHTS)
    clock.now = utc("2026-03-26T12:00Z")

    sleep = spans(preview(client, days=7)["sleep"])

    assert (utc("2026-03-28T22:00Z"), utc("2026-03-29T05:00Z")) in sleep
    assert (utc("2026-03-27T22:00Z"), utc("2026-03-28T06:00Z")) in sleep
    assert (utc("2026-03-29T21:00Z"), utc("2026-03-30T05:00Z")) in sleep


def test_a_new_preview_reflects_a_changed_profile_at_once(client: TestClient) -> None:
    put_profile(client, NIGHTS)
    assert spans(preview(client, days=1)["sleep"])[0] == (NOW, utc("2026-06-10T05:00Z"))

    put_profile(client, profile_doc(window("01:00", "02:00", "sleep", weekday=3)))
    assert spans(preview(client, days=2)["sleep"]) == [
        (utc("2026-06-10T23:00Z"), utc("2026-06-11T00:00Z"))
    ]

    put_profile(client, profile_doc(window("23:00", "07:00", "sleep"), tz="Asia/Tokyo"))
    body = preview(client, days=1)
    assert body["tz"] == "Asia/Tokyo"
    assert spans(body["sleep"]) == [(utc("2026-06-10T14:00Z"), utc("2026-06-10T22:00Z"))]
