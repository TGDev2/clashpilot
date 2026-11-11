"""Acceptance tests for E21: the iCalendar export of the latest plan.

SPEC section 7. Frozen with the task.

`GET /api/villages/{id}/plans/latest/calendar.ics` answers the latest plan as an iCalendar file
(RFC 5545, written by hand, no dependency):

- status 200, content type `text/calendar`; CRLF line endings; `BEGIN:VCALENDAR` first and
  `END:VCALENDAR` last; `VERSION:2.0` and a `PRODID`;
- one `VEVENT` per upgrade end of the plan: the end of every planned assignment is the `DTSTART` or
  the `DTEND` of one event, and no event exists for something that is not an assignment of the plan
  (a running job of the export may have one, it is an assignment too);
- every `DTSTART`, `DTEND` and `DTSTAMP` is a UTC date-time `YYYYMMDDTHHMMSSZ` without a `TZID`
  parameter; every event carries `UID`, `DTSTAMP`, `DTSTART` and `SUMMARY`;
- the `UID` of a task does not change from one export to the next (nor from one plan to the next):
  it comes from the task, never from the plan, the export or the clock;
- without a plan: 404 `{"detail": {"code": "no_plan", "message": ...}}`.

The setup mirrors `test_plan_api_acceptance.py` (fictional pack, fictional export, manual input,
schedule profile, `rollout` engine in its reproducible profile).
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

HOUR = 3600

TESTS = Path(__file__).resolve().parents[1]
PACK_FIXTURE = TESTS / "fixtures" / "packs" / "fictional_pack_v1.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

NOW = int(datetime.datetime(2026, 10, 24, 20, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = NOW - HOUR

TAG = "#FICTIF09"
VILLAGE = "FICTIF09"
ICS_PATH = f"/api/villages/{VILLAGE}/plans/latest/calendar.ics"

TOWN_HALL = 9000001
HUT = 9000002
LAB = 9000003
CANNON = 9000004
HERO = 9000006
TROOP = 9000007

UTC_STAMP = re.compile(r"\d{8}T\d{6}Z")
PROPERTY = re.compile(r"^(?P<name>[A-Za-z-]+)(?P<params>;[^:]*)?:(?P<value>.*)$")


def support() -> ModuleType:
    name = "e21_acceptance_support_problems"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SUPPORT_PROBLEMS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def export_doc(*, timestamp: int, hero_timer: int) -> dict[str, Any]:
    return {
        "tag": TAG,
        "timestamp": timestamp,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 2},
            {"data": HUT, "lvl": 1},
            {"data": HUT, "lvl": 1},
            {"data": LAB, "lvl": 1},
            {"data": CANNON, "lvl": 2},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
        # Running: ends at `timestamp + hero_timer`, a frozen job of the plan.
        "heroes": [{"data": HERO, "lvl": 1, "timer": hero_timer}],
    }


def resource(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": NOW - HOUR}


MANUAL: dict[str, Any] = {
    "resources": {
        "gold": resource(100_000),
        "elixir": resource(50_000),
        "dark_elixir": resource(5_000),
    },
    "builders_total": 3,
    "farm_rate": {
        "gold": {"low": 500, "expected": 1000, "high": 1500},
        "elixir": {"low": 500, "expected": 1000, "high": 1500},
        "dark_elixir": {"low": 10, "expected": 20, "high": 30},
    },
}

PROFILE: dict[str, Any] = {
    "tz": "Europe/Paris",
    "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
    "exceptions": [],
    "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
}

PLAN_BODY = {"horizon_days": 30, "engine": "rollout", "profile": "repro"}


def import_export(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.post(
        "/api/villages/import",
        content=json.dumps(doc).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text


def plan_now(client: TestClient) -> dict[str, Any]:
    response = client.post(f"/api/villages/{VILLAGE}/plans", json=PLAN_BODY)
    assert response.status_code == 201, response.text
    plan: dict[str, Any] = response.json()
    return plan


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    overrides = dataclasses.asdict(support().TEST_CONFIG)
    settings = Settings(
        data_dir=tmp_path,
        frontend_dist=tmp_path / "no-dist",
        planner_overrides=overrides,
    )
    created = TestClient(create_app(settings, clock=lambda: NOW))
    pack = json.loads(PACK_FIXTURE.read_text(encoding="utf-8"))
    assert created.post("/api/gamedata/packs", json=pack).status_code in (200, 201)
    assert created.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200
    import_export(created, export_doc(timestamp=EXPORTED_AT, hero_timer=5000))
    assert created.put(f"/api/villages/{VILLAGE}/manual", json=MANUAL).status_code == 200
    assert created.put(f"/api/villages/{VILLAGE}/schedule", json=PROFILE).status_code == 200
    return created


# --------------------------------------------------------------------------- iCalendar reader


def unfolded_lines(body: str) -> list[str]:
    """The content lines of `body`, folded lines joined (RFC 5545 3.1)."""
    lines: list[str] = []
    for raw in body.split("\r\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def events_of(body: str) -> list[dict[str, list[tuple[str, str]]]]:
    """Each VEVENT as `{property name: [(parameters, value), ...]}`."""
    events: list[dict[str, list[tuple[str, str]]]] = []
    current: dict[str, list[tuple[str, str]]] | None = None
    for line in unfolded_lines(body):
        if line == "BEGIN:VEVENT":
            assert current is None, "nested VEVENT"
            current = {}
        elif line == "END:VEVENT":
            assert current is not None, "END:VEVENT without BEGIN"
            events.append(current)
            current = None
        elif current is not None and line:
            match = PROPERTY.match(line)
            assert match is not None, f"not a content line: {line!r}"
            current.setdefault(match["name"].upper(), []).append(
                (match["params"] or "", match["value"])
            )
    assert current is None, "VEVENT never closed"
    return events


def epoch_of(stamp: str) -> int:
    assert UTC_STAMP.fullmatch(stamp), f"not a UTC date-time: {stamp!r}"
    parsed = datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    return int(parsed.replace(tzinfo=datetime.UTC).timestamp())


def only(event: dict[str, list[tuple[str, str]]], name: str) -> str:
    values = event.get(name)
    assert values is not None and len(values) == 1, (name, event)
    return values[0][1]


def instants(event: dict[str, list[tuple[str, str]]]) -> set[int]:
    return {
        epoch_of(value) for key in ("DTSTART", "DTEND") for _params, value in event.get(key, [])
    }


# --------------------------------------------------------------------------------------- tests


def test_calendar_is_a_text_calendar_with_crlf_lines(client: TestClient) -> None:
    plan_now(client)

    response = client.get(ICS_PATH)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].lower().startswith("text/calendar")
    body = response.text
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.endswith("END:VCALENDAR\r\n")
    assert "\n" not in body.replace("\r\n", ""), "a bare LF line ending"
    lines = unfolded_lines(body)
    assert "VERSION:2.0" in lines
    assert any(line.startswith("PRODID:") and len(line) > len("PRODID:") for line in lines)


def test_one_event_per_planned_end_in_utc(client: TestClient) -> None:
    plan = plan_now(client)
    planned = [a for a in plan["assignments"] if a["status"] == "planned"]
    assert planned, "the fictional plan must plan something"

    events = events_of(client.get(ICS_PATH).text)

    assert len(planned) <= len(events) <= len(plan["assignments"])
    known = {a["end"] for a in plan["assignments"]} | {a["start"] for a in plan["assignments"]}
    for event in events:
        assert only(event, "UID")
        assert only(event, "SUMMARY")
        epoch_of(only(event, "DTSTAMP"))
        assert len(event["DTSTART"]) == 1
        for name in ("DTSTART", "DTEND", "DTSTAMP"):
            for params, value in event.get(name, []):
                assert "TZID" not in params.upper(), (name, params)
                assert UTC_STAMP.fullmatch(value), (name, value)
        assert instants(event) <= known, event
    # Each planned assignment owns one event of its own: its end and its name.
    free = list(events)
    for assignment in planned:
        owner = next(
            (
                e
                for e in free
                if assignment["end"] in instants(e) and assignment["name"] in only(e, "SUMMARY")
            ),
            None,
        )
        assert owner is not None, (assignment["task_id"], assignment["end"], assignment["name"])
        free.remove(owner)


def test_event_uids_are_unique_within_a_calendar(client: TestClient) -> None:
    plan_now(client)

    events = events_of(client.get(ICS_PATH).text)

    assert events
    uids = [only(event, "UID") for event in events]
    assert len(set(uids)) == len(uids), uids


def test_uids_are_stable_from_one_export_to_the_next(client: TestClient) -> None:
    first = plan_now(client)
    before = sorted(only(event, "UID") for event in events_of(client.get(ICS_PATH).text))

    # A newer export of the same village: same content, two minutes later, same running job end.
    import_export(client, export_doc(timestamp=EXPORTED_AT + 120, hero_timer=5000 - 120))
    second = plan_now(client)
    after = sorted(only(event, "UID") for event in events_of(client.get(ICS_PATH).text))

    assert second["plan_id"] != first["plan_id"]
    assert before
    assert after == before


def test_without_a_plan_the_calendar_is_a_404_no_plan(client: TestClient) -> None:
    response = client.get(ICS_PATH)

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "no_plan"
    assert isinstance(detail["message"], str) and detail["message"]
