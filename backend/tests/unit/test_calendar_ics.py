"""The hand-written iCalendar of a plan: escaping, folding, UTC stamps and stable UIDs."""

from __future__ import annotations

import datetime

from clashpilot.api.ics import calendar_of

NOW = int(datetime.datetime(2026, 11, 12, 10, 0, tzinfo=datetime.UTC).timestamp())


def assignment(task_id: str, name: str, start: int, end: int) -> dict[str, object]:
    return {"task_id": task_id, "name": name, "lane": "builder", "start": start, "end": end}


def lines(body: str) -> list[str]:
    return body.split("\r\n")[:-1]


def test_empty_plan_is_a_valid_empty_calendar() -> None:
    body = calendar_of([], NOW)

    assert lines(body) == [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ClashPilot//Plan//FR",
        "CALSCALE:GREGORIAN",
        "END:VCALENDAR",
    ]


def test_event_is_in_utc_with_a_uid_from_the_task_alone() -> None:
    first = calendar_of([assignment("1000001:1:5", "Canon", NOW, NOW + 3600)], NOW)
    later = calendar_of([assignment("1000001:1:5", "Canon", NOW, NOW + 3600)], NOW + 999)

    assert "DTSTART:20261112T100000Z" in lines(first)
    assert "DTEND:20261112T110000Z" in lines(first)
    assert "UID:1000001:1:5@clashpilot.local" in lines(first)
    assert "UID:1000001:1:5@clashpilot.local" in lines(later)
    assert "DTSTAMP:20261112T100000Z" in lines(first)
    assert "DTSTAMP:20261112T101639Z" in lines(later)
    assert "TRIGGER;RELATED=END:-PT10M" in lines(first)


def test_text_is_escaped_and_long_lines_are_folded_on_character_boundaries() -> None:
    name = "Tour; du, chef \\ " + "é" * 60
    body = calendar_of([assignment("1:0:1", name, NOW, NOW + 60)], NOW)

    unfolded = body.replace("\r\n ", "")
    assert "Tour\\; du\\, chef \\\\ " in unfolded
    assert "é" * 60 in unfolded
    for raw in body.split("\r\n"):
        assert len(raw.encode("utf-8")) <= 75
