"""iCalendar (RFC 5545) written by hand for the assignments of a plan. Pure: no I/O, no clock.

One VEVENT per assignment, from its start to its end, in UTC. The UID comes from the task id alone,
so it does not change from one export or one plan to the next. A VALARM rings `ALARM_LEAD_MINUTES`
before the end.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from typing import Any

PRODID = "-//ClashPilot//Plan//FR"
UID_DOMAIN = "clashpilot.local"
ALARM_LEAD_MINUTES = 10
_FOLD_OCTETS = 75


def _stamp(epoch: int) -> str:
    moment = datetime.datetime.fromtimestamp(epoch, datetime.UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    """TEXT value escaping (RFC 5545 3.3.11)."""
    escaped = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _fold(line: str) -> list[str]:
    """Fold a content line at 75 octets, never inside a UTF-8 sequence (RFC 5545 3.1)."""
    parts: list[str] = []
    current = ""
    size = 0
    limit = _FOLD_OCTETS
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current, size, limit = " ", 1, _FOLD_OCTETS
        current += char
        size += width
    parts.append(current)
    return parts


def _event(assignment: Mapping[str, Any], stamp: str) -> list[str]:
    name = str(assignment["name"])
    lane = str(assignment["lane"])
    return [
        "BEGIN:VEVENT",
        f"UID:{assignment['task_id']}@{UID_DOMAIN}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_stamp(int(assignment['start']))}",
        f"DTEND:{_stamp(int(assignment['end']))}",
        f"SUMMARY:{_escape(f'Fin : {name}')}",
        f"DESCRIPTION:{_escape(f'Fin de {name} ({lane}).')}",
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape(f'{name} se termine bientôt.')}",
        f"TRIGGER;RELATED=END:-PT{ALARM_LEAD_MINUTES}M",
        "END:VALARM",
        "END:VEVENT",
    ]


def calendar_of(assignments: Iterable[Mapping[str, Any]], now: int) -> str:
    """The calendar of `assignments` (dicts of a plan answer), stamped at `now` (UTC epoch)."""
    stamp = _stamp(now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN"]
    for assignment in assignments:
        lines.extend(_event(assignment, stamp))
    lines.append("END:VCALENDAR")
    return "".join(f"{part}\r\n" for line in lines for part in _fold(line))
