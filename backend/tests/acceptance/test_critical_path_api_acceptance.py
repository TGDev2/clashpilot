"""Acceptance tests for E23: `GET /api/villages/{village_id}/critical-path`.

SPEC (critical path backlog): architecture `api/routers/critical.py` (same inputs and errors as
`POST .../plans`, at the application clock), ADR-CP1 and ADR-CP4. The computation itself is frozen
by `test_critical_path_acceptance.py` (E22); these tests judge the route. Frozen with the task.

Shapes that the task text leaves open are fixed here:

- the answer holds at least `target`, `set_finish`, `finish_after`, `finish_parallel`,
  `work_finish`, `bound`, `critical_lanes`, `calendar_wait_s`, `chains`, `tasks` and `skip_lanes`
  (the query values, `[]` without them, order free);
- `set_finish`, `finish_after` and `finish_parallel` are provenance objects whose `as_of` is the
  application clock. The `finish` of a `work_finish` or `chains` entry may be a plain epoch or such
  an object;
- `calendar_wait_s` is the calendar wait of the first chain;
- a chain goes from `from_level` (the level before its first task) to `to_level` (the level of its
  last one). `from_level` is not judged when a running job precedes the chain;
- `tasks` lists the tasks of the current set and may also list the target; its order is free;
- without a town hall task, `target` and `bound` are null, the three dates are null (as a whole or
  as their `value`) and the lists are empty.

Every test goes through HTTP with the fictional pack
`backend/tests/fixtures/packs/fictional_pack_critical.json` and the export
`backend/tests/fixtures/exports/fictional_export_critical.json` (exported one hour before `NOW` =
2026-11-27T08:00:00Z): town hall 1, five builders, a laboratory, and on the builder lane a hero at
level 1 with four levels left (6, 7, 8 and 9 days). Two other builder upgrades last 1 day and
1 day + 1 s; the lab holds 1 + 2 days on one troop and 1 day on another. The town hall upgrade lasts
2 days. Durations are changed, when a test needs it, through the manual corrections of the pack.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"
SCHEMA_TS = ROOT / "frontend" / "src" / "api" / "schema.d.ts"
ROUTE = "/api/villages/{village_id}/critical-path"

NOW = 1_795_766_400  # 2026-11-27T08:00:00Z
EXPORTED_AT = NOW - HOUR
VILLAGE = "FICTIFCRIT"

TOWN_HALL = 9200001
HUT = 9200002
LAB = 9200003
HERO = 9200004
BALLISTA = 9200005
CATAPULT = 9200006
SCOUT = 9200007
SAPPER = 9200008


def task(data_id: int, level: int) -> str:
    return f"{data_id}:0:{level}"


TARGET = task(TOWN_HALL, 2)
HERO_TASKS = [task(HERO, level) for level in (2, 3, 4, 5)]
# The current level: everything that does not depend on the town hall upgrade. The laboratory
# level 2 needs town hall 2, so it is not part of it.
CURRENT: dict[str, tuple[int, int, str]] = {
    **{task(HERO, level): (HERO, level, "builder") for level in (2, 3, 4, 5)},
    task(BALLISTA, 2): (BALLISTA, 2, "builder"),
    task(CATAPULT, 2): (CATAPULT, 2, "builder"),
    task(SCOUT, 2): (SCOUT, 2, "lab"),
    task(SCOUT, 3): (SCOUT, 3, "lab"),
    task(SAPPER, 2): (SAPPER, 2, "lab"),
}
HERO_DAYS = 30
BUILDER_WORK = (HERO_DAYS + 1) * DAY + DAY + 1  # hero, ballista, catapult
LAB_WORK = 4 * DAY

ANSWER_KEYS = {
    "target",
    "set_finish",
    "finish_after",
    "finish_parallel",
    "work_finish",
    "bound",
    "critical_lanes",
    "calendar_wait_s",
    "chains",
    "tasks",
    "skip_lanes",
}
DATES = ("set_finish", "finish_after", "finish_parallel")


# --------------------------------------------------------------------------- fixtures


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CRITICAL_PACK.read_text(encoding="utf-8"))
    return loaded


def names(pack: Mapping[str, Any] | None = None) -> dict[int, str]:
    return {item["data_id"]: item["name"] for item in (pack or pack_doc())["items"]}


def export_doc(
    *, timestamp: int = EXPORTED_AT, levels: Mapping[int, int] | None = None
) -> dict[str, Any]:
    """The fixture export, with another timestamp or other levels."""
    doc: dict[str, Any] = json.loads(CRITICAL_EXPORT.read_text(encoding="utf-8"))
    doc["timestamp"] = timestamp
    for section in ("buildings", "units", "heroes"):
        for entry in doc[section]:
            if levels and entry["data"] in levels:
                entry["lvl"] = levels[entry["data"]]
    return doc


def stock(value: int, as_of: int = NOW) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": as_of}


def manual_doc(**extra: Any) -> dict[str, Any]:
    resources = {name: stock(1_000_000) for name in ("gold", "elixir", "dark_elixir")}
    return {"resources": resources, **extra}


def local(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S")


def profile_doc(*absences: tuple[int, int]) -> dict[str, Any]:
    """Always available, in UTC, except during the `(start, end)` epochs of `absences`."""
    return {
        "tz": "UTC",
        "weekly": [],
        "exceptions": [
            {"start_local": local(start), "end_local": local(end), "kind": "absence"}
            for start, end in absences
        ],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


# --------------------------------------------------------------------------- HTTP fixture


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@dataclasses.dataclass
class Village:
    client: TestClient
    clock: Clock


def app_client(tmp_path: Path) -> Village:
    clock = Clock(NOW)
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    return Village(TestClient(create_app(settings, clock=clock)), clock)


def activate(v: Village, pack: Mapping[str, Any]) -> None:
    response = v.client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = v.client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text


def import_export(v: Village, doc: Mapping[str, Any]) -> None:
    response = v.client.post(
        "/api/villages/import",
        content=json.dumps(doc).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE


def put_manual(v: Village, doc: Mapping[str, Any]) -> None:
    response = v.client.put(f"/api/villages/{VILLAGE}/manual", json=doc)
    assert response.status_code == 200, response.text


def put_schedule(v: Village, doc: Mapping[str, Any]) -> None:
    response = v.client.put(f"/api/villages/{VILLAGE}/schedule", json=doc)
    assert response.status_code == 200, response.text


def village(tmp_path: Path) -> Village:
    """The fixture village with its manual input and schedule; no plan."""
    v = app_client(tmp_path)
    activate(v, pack_doc())
    import_export(v, export_doc())
    put_manual(v, manual_doc())
    put_schedule(v, profile_doc())
    return v


def correct(v: Village, data_id: int, level: int, duration_s: int) -> None:
    pack_id = pack_doc()["pack_id"]
    response = v.client.patch(
        f"/api/gamedata/packs/{pack_id}/items/{data_id}/levels/{level}",
        json={"duration_s": duration_s},
    )
    assert response.status_code == 200, response.text


def get_path(v: Village, *skip_lanes: str, village_id: str = VILLAGE) -> Any:
    params = [("skip_lanes", lane) for lane in skip_lanes]
    return v.client.get(f"/api/villages/{village_id}/critical-path", params=params)


def critical(v: Village, *skip_lanes: str) -> dict[str, Any]:
    response = get_path(v, *skip_lanes)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert set(body) >= ANSWER_KEYS, sorted(ANSWER_KEYS - set(body))
    return body


# --------------------------------------------------------------------------- reading the answer


def estimate(value: int, as_of: int = NOW) -> dict[str, Any]:
    return {"value": value, "status": "estimated", "source": "model", "as_of": as_of}


def epoch_of(finish: Any, as_of: int = NOW) -> int:
    """A `finish` of `work_finish` or `chains`: a plain epoch or a provenance object."""
    if isinstance(finish, dict):
        assert finish == estimate(finish["value"], as_of), finish
        finish = finish["value"]
    assert type(finish) is int, finish
    return finish


def lanes(body: Mapping[str, Any], as_of: int = NOW) -> dict[str, tuple[int, int, int]]:
    """lane -> (capacity, work_s, finish) of `work_finish`."""
    entries = body["work_finish"]
    for entry in entries:
        assert set(entry) == {"lane", "capacity", "work_s", "finish"}, entry
    found = {e["lane"]: (e["capacity"], e["work_s"], epoch_of(e["finish"], as_of)) for e in entries}
    assert len(found) == len(entries), entries
    return found


def rows(body: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """`tasks` by id; every row is complete and critical exactly up to 300 s of float."""
    found: dict[str, dict[str, Any]] = {}
    for row in body["tasks"]:
        assert set(row) == {"task_id", "name", "to_level", "lane", "float_s", "critical"}, row
        assert type(row["float_s"]) is int, row
        assert row["critical"] is (row["float_s"] <= 300), row
        assert row["task_id"] not in found, row
        found[row["task_id"]] = row
    return found


def chain_summaries(body: Mapping[str, Any]) -> list[tuple[int, list[str], int]]:
    return [(c["data_id"], c["task_ids"], epoch_of(c["finish"])) for c in body["chains"]]


# --------------------------------------------------------------------------- fixtures guarantee


def test_the_critical_fixtures_give_a_builder_hero_as_first_chain_and_a_chain_bound(
    tmp_path: Path,
) -> None:
    pack = pack_doc()
    assert pack["fictional"] is True
    hero = next(item for item in pack["items"] if item["data_id"] == HERO)
    assert hero["kind"] == "hero" and hero["lane"] == "builder"
    owned = {entry["data"]: entry["lvl"] for entry in export_doc()["heroes"]}
    remaining = [
        row for row in pack["levels"] if row["data_id"] == HERO and row["level"] > owned[HERO]
    ]
    assert len(remaining) >= 4
    durations = {task(row["data_id"], row["level"]): row["duration_s"] for row in pack["levels"]}
    rest = sum(durations[name] for name in CURRENT if name not in HERO_TASKS)
    assert sum(row["duration_s"] for row in remaining) > rest

    body = critical(village(tmp_path))

    first = body["chains"][0]
    assert first["data_id"] == HERO
    assert first["task_ids"] == HERO_TASKS
    assert body["bound"] == {"kind": "chain", "lane": None}


# --------------------------------------------------------------------------- the answer


def test_route_answers_the_target_and_the_three_estimated_dates(tmp_path: Path) -> None:
    body = critical(village(tmp_path))

    assert body["target"] == {
        "task_id": TARGET,
        "data_id": TOWN_HALL,
        "name": names()[TOWN_HALL],
        "to_level": 2,
    }
    assert body["set_finish"] == estimate(NOW + HERO_DAYS * DAY)
    assert body["finish_after"] == estimate(NOW + (HERO_DAYS + 2) * DAY)
    assert body["finish_parallel"] == estimate(NOW + HERO_DAYS * DAY)
    assert body["calendar_wait_s"] == 0
    assert body["skip_lanes"] == []


def test_route_answers_the_work_bound_of_each_lane_and_what_fixes_the_date(
    tmp_path: Path,
) -> None:
    body = critical(village(tmp_path))

    assert lanes(body) == {
        # Five builders share the work, the end rounded up to the second.
        "builder": (5, BUILDER_WORK, NOW + -(-BUILDER_WORK // 5)),
        "lab": (1, LAB_WORK, NOW + LAB_WORK),
    }
    assert body["bound"] == {"kind": "chain", "lane": None}
    assert body["critical_lanes"] == []


def test_route_answers_at_most_three_named_chains_longest_first(tmp_path: Path) -> None:
    body = critical(village(tmp_path))

    chains = body["chains"]
    for chain in chains:
        assert set(chain) == {"data_id", "name", "from_level", "to_level", "task_ids", "finish"}
    assert [(c["data_id"], c["name"], c["from_level"], c["to_level"]) for c in chains] == [
        (HERO, names()[HERO], 1, 5),
        (SCOUT, names()[SCOUT], 1, 3),
        (CATAPULT, names()[CATAPULT], 1, 2),
    ]
    assert chain_summaries(body) == [
        (HERO, HERO_TASKS, NOW + HERO_DAYS * DAY),
        (SCOUT, [task(SCOUT, 2), task(SCOUT, 3)], NOW + 3 * DAY),
        (CATAPULT, [task(CATAPULT, 2)], NOW + DAY + 1),
    ]


def test_route_answers_every_task_of_the_current_level_with_its_float(tmp_path: Path) -> None:
    found = rows(critical(village(tmp_path)))

    assert set(CURRENT) <= set(found) <= {*CURRENT, TARGET}, sorted(found)
    for name, (data_id, level, lane) in CURRENT.items():
        row = found[name]
        assert (row["name"], row["to_level"], row["lane"]) == (names()[data_id], level, lane)
    expected = {
        **dict.fromkeys(HERO_TASKS, 0),
        task(BALLISTA, 2): 29 * DAY,
        task(CATAPULT, 2): 29 * DAY - 1,
        task(SCOUT, 2): 27 * DAY,
        task(SCOUT, 3): 27 * DAY,
        task(SAPPER, 2): 29 * DAY,
    }
    assert {name: found[name]["float_s"] for name in expected} == expected
    assert {name for name in CURRENT if found[name]["critical"]} == set(HERO_TASKS)


def test_a_task_is_critical_exactly_when_its_float_is_at_most_300_seconds(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    correct(v, BALLISTA, 2, HERO_DAYS * DAY - 300)
    correct(v, CATAPULT, 2, HERO_DAYS * DAY - 301)

    body = critical(v)

    assert body["bound"] == {"kind": "chain", "lane": None}
    found = rows(body)
    assert found[task(BALLISTA, 2)]["float_s"] == 300
    assert found[task(BALLISTA, 2)]["critical"] is True
    assert found[task(CATAPULT, 2)]["float_s"] == 301
    assert found[task(CATAPULT, 2)]["critical"] is False


def test_a_saturated_lane_fixes_the_date_and_all_its_tasks_are_critical(tmp_path: Path) -> None:
    v = village(tmp_path)
    correct(v, SAPPER, 2, 40 * DAY)

    body = critical(v)

    assert body["bound"] == {"kind": "work", "lane": "lab"}
    assert body["critical_lanes"] == ["lab"]
    assert lanes(body)["lab"] == (1, 43 * DAY, NOW + 43 * DAY)
    assert body["set_finish"] == estimate(NOW + 43 * DAY)
    assert body["finish_after"] == estimate(NOW + 45 * DAY)
    assert body["finish_parallel"] == estimate(NOW + 43 * DAY)
    found = rows(body)
    for name, (_, _, lane) in CURRENT.items():
        assert found[name]["critical"] is (lane == "lab"), found[name]
    assert [found[name]["float_s"] for name in HERO_TASKS] == [13 * DAY] * 4


def test_without_a_town_hall_task_the_target_is_null_and_nothing_is_dated(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    import_export(v, export_doc(timestamp=EXPORTED_AT + 60, levels={TOWN_HALL: 2}))

    body = critical(v)

    assert body["target"] is None
    assert body["bound"] is None
    for key in DATES:
        assert body[key] is None or body[key]["value"] is None, (key, body[key])
    for key in ("work_finish", "critical_lanes", "chains", "tasks"):
        assert body[key] == [], (key, body[key])


# --------------------------------------------------------------------------- names, corrections


def test_names_come_from_the_active_pack(tmp_path: Path) -> None:
    v = village(tmp_path)
    renamed = pack_doc()
    renamed["pack_id"] = "fictional-test-pack-critical-renamed"
    for item in renamed["items"]:
        item["name"] = f"{item['name']} (bis)"
    activate(v, renamed)

    body = critical(v)

    expected = names(renamed)
    assert body["target"]["name"] == expected[TOWN_HALL]
    for chain in body["chains"]:
        assert chain["name"] == expected[chain["data_id"]], chain
    found = rows(body)
    for name, (data_id, _, _) in CURRENT.items():
        assert found[name]["name"] == expected[data_id], found[name]


def test_the_manual_corrections_of_the_active_pack_are_applied(tmp_path: Path) -> None:
    v = village(tmp_path)
    assert critical(v)["set_finish"] == estimate(NOW + HERO_DAYS * DAY)

    correct(v, HERO, 5, 20 * DAY)
    body = critical(v)

    assert body["set_finish"] == estimate(NOW + 41 * DAY)
    assert chain_summaries(body)[0] == (HERO, HERO_TASKS, NOW + 41 * DAY)
    assert body["chains"][0]["name"] == names()[HERO]
    assert lanes(body)["builder"][1] == BUILDER_WORK + 11 * DAY


# --------------------------------------------------------------------------- skip_lanes


def test_skip_lanes_is_passed_to_the_computation_and_echoed(tmp_path: Path) -> None:
    v = village(tmp_path)
    correct(v, SAPPER, 2, 40 * DAY)
    assert critical(v)["bound"] == {"kind": "work", "lane": "lab"}

    body = critical(v, "lab")

    assert body["skip_lanes"] == ["lab"]
    assert body["bound"] == {"kind": "chain", "lane": None}
    assert body["critical_lanes"] == []
    assert body["set_finish"] == estimate(NOW + HERO_DAYS * DAY)
    assert body["finish_after"] == estimate(NOW + (HERO_DAYS + 2) * DAY)
    assert set(lanes(body)) == {"builder"}
    assert {chain["data_id"] for chain in body["chains"]} <= {HERO, BALLISTA, CATAPULT}
    builder = {name for name, (_, _, lane) in CURRENT.items() if lane == "builder"}
    assert builder <= set(rows(body)) <= {*builder, TARGET}


def test_skip_lanes_can_be_repeated(tmp_path: Path) -> None:
    body = critical(village(tmp_path), "lab", "builder")

    assert sorted(body["skip_lanes"]) == ["builder", "lab"]
    assert body["work_finish"] == []
    assert body["critical_lanes"] == []
    assert body["chains"] == []
    assert set(rows(body)) <= {TARGET}


@pytest.mark.parametrize(
    "skip_lanes",
    [
        ("forge",),
        ("lab", "forge"),
        ("pet_house",),  # a lane of the game, absent from this village
        ("LAB",),
    ],
)
def test_a_lane_unknown_to_the_problem_is_refused(
    tmp_path: Path, skip_lanes: tuple[str, ...]
) -> None:
    response = get_path(village(tmp_path), *skip_lanes)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "unknown_lane", response.text


# --------------------------------------------------------------------------- inputs


def test_the_latest_export_is_used(tmp_path: Path) -> None:
    v = village(tmp_path)
    import_export(v, export_doc(timestamp=NOW - 600, levels={HERO: 3}))
    # An older export imported afterwards does not take over.
    import_export(v, export_doc(timestamp=EXPORTED_AT - HOUR))

    body = critical(v)

    assert chain_summaries(body)[0] == (HERO, HERO_TASKS[2:], NOW + 17 * DAY)
    assert (body["chains"][0]["from_level"], body["chains"][0]["to_level"]) == (3, 5)
    assert body["set_finish"] == estimate(NOW + 17 * DAY)


def test_a_job_running_in_the_export_counts_for_its_remaining_time(tmp_path: Path) -> None:
    v = village(tmp_path)
    running = export_doc(timestamp=NOW - 600)
    running["heroes"][0]["timer"] = 2 * DAY + 600  # level 2 ends at NOW + 2 days
    import_export(v, running)

    body = critical(v)

    finish = NOW + (2 + 7 + 8 + 9) * DAY
    assert body["set_finish"] == estimate(finish)
    first = body["chains"][0]
    assert first["data_id"] == HERO and epoch_of(first["finish"]) == finish
    assert set(HERO_TASKS[1:]) <= set(first["task_ids"])
    assert task(HERO, 2) not in rows(body)
    assert lanes(body)["builder"][1] == BUILDER_WORK - 4 * DAY  # 6 days replaced by 2


def test_the_manual_builder_count_is_used(tmp_path: Path) -> None:
    v = village(tmp_path)
    put_manual(v, manual_doc(builders_total=1))

    body = critical(v)

    assert lanes(body)["builder"] == (1, BUILDER_WORK, NOW + BUILDER_WORK)
    assert body["bound"] == {"kind": "work", "lane": "builder"}
    assert body["critical_lanes"] == ["builder"]
    assert body["set_finish"] == estimate(NOW + BUILDER_WORK)
    found = rows(body)
    for name, (_, _, lane) in CURRENT.items():
        assert found[name]["critical"] is (lane == "builder"), found[name]


def test_the_manual_events_are_used(tmp_path: Path) -> None:
    v = village(tmp_path)
    event = {
        "label": "Bonus imaginaire",
        "start": NOW - HOUR,
        "end": NOW + 400 * DAY,
        "lane": "builder",
        "duration_pct": 50,
        "cost_pct": 100,
    }
    put_manual(v, manual_doc(events=[event]))

    body = critical(v)

    assert body["set_finish"] == estimate(NOW + 15 * DAY)
    assert body["finish_after"] == estimate(NOW + 16 * DAY)
    assert chain_summaries(body)[0] == (HERO, HERO_TASKS, NOW + 15 * DAY)


def test_the_journal_is_used(tmp_path: Path) -> None:
    v = village(tmp_path)
    response = v.client.post(
        f"/api/villages/{VILLAGE}/plans", json={"engine": "baseline", "profile": "repro"}
    )
    assert response.status_code == 201, response.text
    response = v.client.post(
        f"/api/villages/{VILLAGE}/plans/{response.json()['plan_id']}/events",
        json={"type": "task_started", "task_id": task(HERO, 2), "at": NOW},
    )
    assert response.status_code in (200, 201), response.text
    v.clock.now = NOW + DAY

    body = critical(v)

    # Started at NOW according to the journal: the chain still ends 30 days after NOW, not 31.
    assert body["set_finish"] == estimate(NOW + HERO_DAYS * DAY, as_of=NOW + DAY)
    found = rows(body)
    assert task(HERO, 2) not in found
    assert set(HERO_TASKS[1:]) <= set(found)


def test_the_computation_starts_at_the_application_clock(tmp_path: Path) -> None:
    v = village(tmp_path)
    v.clock.now = NOW + 3 * DAY

    body = critical(v)

    as_of = NOW + 3 * DAY
    assert body["set_finish"] == estimate(as_of + HERO_DAYS * DAY, as_of)
    assert body["finish_after"] == estimate(as_of + (HERO_DAYS + 2) * DAY, as_of)
    assert body["finish_parallel"] == estimate(as_of + HERO_DAYS * DAY, as_of)
    assert lanes(body, as_of)["lab"] == (1, LAB_WORK, as_of + LAB_WORK)


def test_the_schedule_delays_the_chains(tmp_path: Path) -> None:
    v = village(tmp_path)
    # Away when the hero level 2 ends (day 6): level 3 starts on day 8.
    put_schedule(v, profile_doc((NOW + 5 * DAY, NOW + 8 * DAY)))

    body = critical(v)

    assert body["set_finish"] == estimate(NOW + 32 * DAY)
    assert chain_summaries(body)[0] == (HERO, HERO_TASKS, NOW + 32 * DAY)
    assert body["calendar_wait_s"] == 2 * DAY


def test_the_calendar_is_expanded_over_a_horizon_of_365_days(tmp_path: Path) -> None:
    v = village(tmp_path)
    correct(v, HERO, 2, 100 * DAY)
    correct(v, HERO, 3, 264 * DAY + HOUR)  # ends on day 364, one hour in
    correct(v, HERO, 4, HOUR)
    correct(v, HERO, 5, 2 * HOUR)
    put_schedule(
        v,
        profile_doc(
            (NOW + 364 * DAY, NOW + 365 * DAY),  # inside the horizon: level 4 waits
            (NOW + 365 * DAY, NOW + 370 * DAY),  # beyond it: level 5 does not
        ),
    )

    body = critical(v)

    finish = NOW + 365 * DAY + 3 * HOUR
    assert chain_summaries(body)[0] == (HERO, HERO_TASKS, finish)
    assert body["set_finish"] == estimate(finish)
    assert body["calendar_wait_s"] == DAY - HOUR


def test_reading_the_critical_path_stores_nothing(tmp_path: Path) -> None:
    v = village(tmp_path)
    critical(v)

    response = v.client.get(f"/api/villages/{VILLAGE}/plans/latest")
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "no_plan"
    response = v.client.get(f"/api/villages/{VILLAGE}/journal")
    assert response.status_code == 200 and response.json() == [], response.text


# --------------------------------------------------------------------------- errors


def same_error_as_post_plans(v: Village, village_id: str = VILLAGE) -> dict[str, Any]:
    """The route and `POST .../plans` refuse with the same status, code and missing inputs."""
    posted = v.client.post(f"/api/villages/{village_id}/plans", json={"engine": "baseline"})
    read = get_path(v, village_id=village_id)
    assert read.status_code == posted.status_code, (read.text, posted.text)
    detail: dict[str, Any] = read.json()["detail"]
    expected = posted.json()["detail"]
    assert detail["code"] == expected["code"], (detail, expected)
    assert detail.get("missing") == expected.get("missing"), (detail, expected)
    assert detail["message"]
    return {"status": read.status_code, **detail}


def test_unknown_village_is_404_village_not_found(tmp_path: Path) -> None:
    v = app_client(tmp_path)
    # Checked before the pack, as POST plans does.
    found = same_error_as_post_plans(v, "NOSUCHTAG")
    assert (found["status"], found["code"]) == (404, "village_not_found")
    activate(v, pack_doc())
    found = same_error_as_post_plans(v, "NOSUCHTAG")
    assert (found["status"], found["code"]) == (404, "village_not_found")


def test_without_active_pack_is_409_no_active_pack(tmp_path: Path) -> None:
    v = app_client(tmp_path)
    import_export(v, export_doc())

    found = same_error_as_post_plans(v)

    assert (found["status"], found["code"]) == (409, "no_active_pack")


def test_missing_inputs_are_409_missing_input_with_their_names(tmp_path: Path) -> None:
    v = app_client(tmp_path)
    activate(v, pack_doc())
    import_export(v, export_doc())

    found = same_error_as_post_plans(v)
    assert (found["status"], found["code"]) == (409, "missing_input")
    assert {"gold", "elixir", "dark_elixir", "schedule_profile"} <= set(found["missing"])

    partial = manual_doc()
    del partial["resources"]["dark_elixir"]
    put_manual(v, partial)
    put_schedule(v, profile_doc())
    found = same_error_as_post_plans(v)
    assert (found["status"], found["code"]) == (409, "missing_input")
    assert found["missing"] == ["dark_elixir"]


# --------------------------------------------------------------------------- API document

# The guard of E05 (`test_village_state_sync_acceptance.py`).
FORBIDDEN_NAMES = re.compile(
    r"password|passwd|mot_?de_?passe|supercell_?id|username|login(?!s_per_day)|e_?mail|credential",
    re.IGNORECASE,
)


def schema_names(node: Any) -> Iterator[str]:
    """Property and parameter names found anywhere in an OpenAPI document."""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            yield from (str(name) for name in properties)
        if node.get("in") in {"query", "header", "path", "cookie"} and "name" in node:
            yield str(node["name"])
        for value in node.values():
            yield from schema_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from schema_names(value)


def test_the_openapi_document_declares_the_route_without_login_like_property(
    tmp_path: Path,
) -> None:
    schema = app_client(tmp_path).client.get("/openapi.json").json()

    assert ROUTE in schema["paths"], sorted(schema["paths"])
    operation = schema["paths"][ROUTE]["get"]
    query = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}
    assert query == {"skip_lanes"}
    assert sorted({name for name in schema_names(schema) if FORBIDDEN_NAMES.search(name)}) == []


@pytest.mark.timeout(120)
def test_the_generated_client_schema_holds_the_route_without_drift() -> None:
    assert f'"{ROUTE}"' in SCHEMA_TS.read_text(encoding="utf-8"), "run node scripts/gen-api.mjs"

    done = subprocess.run(
        ["node", str(ROOT / "scripts" / "gen-api.mjs"), "--check"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
