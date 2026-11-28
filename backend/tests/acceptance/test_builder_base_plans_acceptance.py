"""Acceptance tests for E32: plans, critical path and town hall comparator per village.

SPEC (builder base backlog): "Plans par village", ADR-BB1 (one problem per village, same lane
names), ADR-BB2 (the builder hall carries the tag `town_hall` in its village), ADR-BB3 (`base`
explicit, `home` by default: nothing changes while `base` is not given). Frozen with the task.

Shapes that the task text leaves open are fixed here:

- `POST .../plans` takes `base` in its body, `"home"` (default) or `"builder"`; any other value is
  a 422 `invalid_request`, and nothing is stored. The plans table gains a column `base`; the
  migration gives `"home"` to the plans stored before it (judged on a database migrated with the
  files present before E32, `MIGRATIONS_BEFORE_E32`, then with every file).
- `GET .../plans/latest`, `GET .../recommendations/now`, `POST .../plans/{plan_id}/events`,
  `GET .../journal`, `GET .../plans/latest/calendar.ics` and `GET .../critical-path` take the query
  parameter `base` (same values, same 422); `POST .../town-hall/compare` takes `base` in its body.
  Without a plan of the base asked for, the plan routes answer 404 `no_plan` as today.
- The journal of a base is the list of the events whose `plan_id` is a plan of that base: without
  `?base=builder`, no entry names a builder base plan.
- An event sent on a plan that is not the latest of the base asked for is refused (409
  `plan_outdated`, or 404 for a plan of the other base): a builder plan never gets replanned, nor
  returned, through a route called without `?base=builder`.
- "Exactly as today" is read from the answers the code of `main` gave before E32 on this village
  (`TODAY_*` below, engine `rollout`, profile `repro`), and from the home answers, which stay the
  same once a builder base plan is stored.
- The forecast (`plan_id`), the scenarios (`plan_id`) and the item advisor (`reference_plan_id`)
  read the latest plan of base `home`: with only a builder base plan, they answer 404 `no_plan`.

Fictional data only (ADR-2): the pack `fixtures/packs/fictional_pack_builder_base.json` and the
export `fixtures/exports/fictional_export_builder_base.json` of E30 (exported at `NOW - 1 h`), to
which this file adds, in memory, a second star laboratory troop (`LANCER`, level 2 lasts 10 days)
so that the star laboratory holds two researches. The builder base: three builders busy until
`NOW + 23 h`, `+ 47 h` and `+ 71 h`, the star laboratory busy until `NOW + 2 h`. Resources: 50 000
gold and elixir at home, 100 000 builder gold and builder elixir (the storage cap). Always
available, no sleep. Today's builder plan without option starts the lancer's 10-day research first
(`NOW + 2 h`); `composition_first` with the composition `[GRUNT]` starts the grunt's first. Under
`finish_th` the builder hall waits for the lancer's research, unless the lab is skipped.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.api.app import create_app
from clashpilot.planning.model import Plan, Problem
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures"
PACK = FIXTURES / "packs" / "fictional_pack_builder_base.json"
EXPORT = FIXTURES / "exports" / "fictional_export_builder_base.json"
MIGRATIONS_DIR = BACKEND / "src" / "clashpilot" / "storage" / "migrations"
MIGRATIONS_BEFORE_E32 = (
    "0001_gamedata.sql",
    "0002_pack_overrides.sql",
    "0003_villages.sql",
    "0004_manual_state.sql",
    "0005_schedule.sql",
    "0006_plans.sql",
    "0007_journal.sql",
    "0008_scenarios.sql",
    "0009_manual_history.sql",
)

NOW = 1_795_852_800  # 2026-11-28T08:00:00Z
VILLAGE = "FICTIFOUVR"
PACK_ID = "fictional-test-pack-builder-plans"

HOME_TH, HOME_HUT, HOME_LAB, HOME_CANNON, HOME_TROOP = (
    9300001,
    9300002,
    9300003,
    9300004,
    9300005,
)
HALL, HUT, STAR_LAB, BUBBLE, ROTOR, GRUNT, KEEPER, HAIL = (
    9400001,
    9400002,
    9400003,
    9400004,
    9400005,
    9400006,
    9400007,
    9400009,
)
LANCER = 9400010  # added by this file: a star laboratory troop, level 2 lasts 10 days
HOME_IDS = {HOME_TH, HOME_HUT, HOME_LAB, HOME_CANNON, HOME_TROOP}
BUILDER_IDS = {HALL, HUT, STAR_LAB, BUBBLE, ROTOR, GRUNT, KEEPER, HAIL, LANCER}

ENGINE = {"engine": "rollout", "profile": "repro"}
BUILDER = {**ENGINE, "base": "builder"}


def task(data_id: int, level: int) -> str:
    return f"{data_id}:0:{level}"


HALL_TASK = task(HALL, 2)
HOME_TH_TASK = task(HOME_TH, 2)
GRUNT_TASK = task(GRUNT, 3)
LANCER_TASK = task(LANCER, 2)
BUILDER_TASKS = {
    task(HALL, 2),
    task(STAR_LAB, 2),
    task(BUBBLE, 3),
    task(BUBBLE, 4),
    task(ROTOR, 3),
    task(GRUNT, 3),
    task(KEEPER, 3),
    task(HAIL, 1),
    task(LANCER, 2),
}
BUILDER_FROZEN = {task(BUBBLE, 2), task(ROTOR, 2), task(KEEPER, 2), task(GRUNT, 2)}
# The builder base tasks that depend on no builder hall upgrade, and those outside the lab.
BUILDER_CURRENT = {task(BUBBLE, 3), task(ROTOR, 3), task(GRUNT, 3), task(LANCER, 2)}
BUILDER_CURRENT_OUTSIDE_LAB = {task(BUBBLE, 3), task(ROTOR, 3)}

# (task id, lane, slot, start - NOW, end - NOW, status) of the home plan `POST plans` gave before
# E32 for `ENGINE` without `base`, with its other figures.
TODAY_HOME_ROWS = [
    ("9300001:0:2", "builder", 1, 0, 172800, "planned"),
    ("9300003:0:2", "builder", 0, 172800, 259200, "planned"),
    ("9300004:0:2", "builder", 0, -3600, 82800, "frozen"),
    ("9300004:0:3", "builder", 1, 172800, 345600, "planned"),
    ("9300005:0:2", "lab", 0, 0, 43200, "planned"),
    ("9300005:0:3", "lab", 0, 259200, 345600, "planned"),
]
TODAY_HOME_INPUT_HASH = "96cfe6618f65b996a57ef0ccd9433c55c6625a540595d8bd95758ace7b58e756"
TODAY_HOME_SCORE = 0.869786918328585
TODAY_HOME_WARNINGS = ["no_farm_rate"]


def _estimate(value: int) -> dict[str, Any]:
    return {"value": value, "status": "estimated", "source": "model", "as_of": NOW}


# `GET .../critical-path` before E32, without `base`.
TODAY_CRITICAL_PATH: dict[str, Any] = {
    "target": {
        "task_id": "9300001:0:2",
        "data_id": 9300001,
        "name": "Beffroi du port",
        "to_level": 2,
    },
    "set_finish": _estimate(1795896000),
    "finish_after": _estimate(1796068800),
    "finish_parallel": _estimate(1796025600),
    "work_finish": [
        {"lane": "lab", "capacity": 1, "work_s": 43200, "finish": _estimate(1795896000)}
    ],
    "bound": {"kind": "chain", "lane": None},
    "critical_lanes": ["lab"],
    "calendar_wait_s": 0,
    "chains": [
        {
            "data_id": 9300005,
            "name": "Pique-lune",
            "from_level": 1,
            "to_level": 2,
            "task_ids": ["9300005:0:2"],
            "finish": _estimate(1795896000),
        }
    ],
    "tasks": [
        {
            "task_id": "9300001:0:2",
            "name": "Beffroi du port",
            "to_level": 2,
            "lane": "builder",
            "float_s": 0,
            "critical": True,
        },
        {
            "task_id": "9300005:0:2",
            "name": "Pique-lune",
            "to_level": 2,
            "lane": "lab",
            "float_s": 0,
            "critical": True,
        },
    ],
    "skip_lanes": [],
}


# --------------------------------------------------------------------------- inputs


def pack_doc() -> dict[str, Any]:
    """The E30 pack with a second star laboratory troop."""
    doc: dict[str, Any] = json.loads(PACK.read_text(encoding="utf-8"))
    doc = copy.deepcopy(doc)
    doc["pack_id"] = PACK_ID
    doc["items"].append(
        {
            "data_id": LANCER,
            "name": "Lancier de dune",
            "kind": "troop",
            "village": "builder",
            "lane": "lab",
            "category": "troop",
            "tags": [],
        }
    )
    doc["levels"] += [
        {
            "data_id": LANCER,
            "level": 1,
            "duration_s": 60,
            "cost": {"builder_elixir": 10},
            "requires": [],
            "source": "s1",
        },
        {
            "data_id": LANCER,
            "level": 2,
            "duration_s": 10 * DAY,
            "cost": {"builder_elixir": 2000},
            "requires": [{"data_id": STAR_LAB, "level": 1}],
            "source": "s1",
        },
    ]
    return doc


def export_doc(shift_s: int = 0) -> dict[str, Any]:
    """The E30 export with the lancer at level 1; `shift_s` moves its timestamp later."""
    doc: dict[str, Any] = json.loads(EXPORT.read_text(encoding="utf-8"))
    doc["units2"].append({"data": LANCER, "lvl": 1})
    doc["timestamp"] += shift_s
    return doc


def stock(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": NOW}


def manual_doc(*, builder: bool = True) -> dict[str, Any]:
    resources = {"gold": stock(50_000), "elixir": stock(50_000)}
    if builder:
        resources |= {"builder_gold": stock(100_000), "builder_elixir": stock(100_000)}
    return {"resources": resources}


PROFILE = {
    "tz": "UTC",
    "weekly": [],
    "exceptions": [],
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
    data_dir: Path


def ok(response: Any, *codes: int) -> Any:
    assert response.status_code in (codes or (200,)), response.text
    return response.json()


def error_code(response: Any, status: int) -> str:
    assert response.status_code == status, response.text
    code: str = response.json()["detail"]["code"]
    return code


def url(path: str) -> str:
    return f"/api/villages/{VILLAGE}{path}"


def import_export(v: Village, doc: Mapping[str, Any]) -> None:
    ok(
        v.client.post(
            "/api/villages/import",
            content=json.dumps(doc).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
        200,
        201,
    )


@pytest.fixture
def v(tmp_path: Path) -> Iterator[Village]:
    clock = Clock(NOW)
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    with TestClient(create_app(settings, clock=clock)) as client:
        village = Village(client, clock, tmp_path)
        pack = pack_doc()
        ok(client.post("/api/gamedata/packs", json=pack), 200, 201)
        ok(client.put(f"/api/gamedata/packs/{pack['pack_id']}/active"))
        import_export(village, export_doc())
        ok(client.put(url("/schedule"), json=PROFILE))
        ok(client.put(url("/manual"), json=manual_doc()))
        yield village


def post_plan(v: Village, body: Mapping[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.post(url("/plans"), json=dict(body)), 201)
    return plan


def query(base: str | None) -> dict[str, str]:
    return {} if base is None else {"base": base}


def latest(v: Village, base: str | None = None) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.get(url("/plans/latest"), params=query(base)))
    return plan


def recommendations(v: Village, base: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = ok(v.client.get(url("/recommendations/now"), params=query(base)))
    return body


def calendar_uids(v: Village, base: str | None = None) -> set[str]:
    response = v.client.get(url("/plans/latest/calendar.ics"), params=query(base))
    assert response.status_code == 200, response.text
    return {
        line.split(":", 1)[1].rsplit("@", 1)[0]
        for line in response.text.splitlines()
        if line.startswith("UID:")
    }


def journal(v: Village, base: str | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = ok(v.client.get(url("/journal"), params=query(base)))
    return entries


def send_event(v: Village, plan_id: int, body: Mapping[str, Any], base: str | None = None) -> Any:
    return v.client.post(url(f"/plans/{plan_id}/events"), params=query(base), json=dict(body))


PRIORITY = {"type": "priority_changed", "objective": {"idle": 0.3}}


def stored_bases(v: Village) -> dict[int, str]:
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        return dict(connection.execute("SELECT plan_id, base FROM plans").fetchall())


def plan_count(v: Village) -> int:
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM plans").fetchone()
    return int(count)


def data_id_of(task_id: str) -> int:
    return int(task_id.split(":")[0])


def plan_data_ids(plan: Mapping[str, Any]) -> set[int]:
    return {data_id_of(a["task_id"]) for a in plan["assignments"]}


def by_task(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["task_id"]: a for a in plan["assignments"]}


def rows(plan: Mapping[str, Any]) -> list[tuple[str, str, int, int, int, str]]:
    return sorted(
        (
            a["task_id"],
            a["lane"],
            a["lane_slot"],
            a["start"] - NOW,
            a["end"] - NOW,
            a["status"],
        )
        for a in plan["assignments"]
    )


def lab_order(plan: Mapping[str, Any]) -> list[str]:
    planned = [a for a in plan["assignments"] if a["lane"] == "lab" and a["status"] == "planned"]
    return [a["task_id"] for a in sorted(planned, key=lambda a: (a["start"], a["task_id"]))]


# --------------------------------------------------------------------------- engine spy


@dataclasses.dataclass
class Call:
    problem: Problem
    objective: Objective
    plan: Plan


class Spy:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Call] = []

    def solve(
        self, problem: Problem, objective: Objective, config: Any, reference: Plan | None = None
    ) -> Plan:
        plan: Plan = self.inner.solve(problem, objective, config, reference)
        self.calls.append(Call(problem, objective, plan))
        return plan


@contextmanager
def spying(key: str = "rollout") -> Iterator[Spy]:
    inner = PLANNERS[key]
    spy = Spy(inner)
    PLANNERS[key] = spy
    try:
        yield spy
    finally:
        PLANNERS[key] = inner


def task_of(problem: Problem, task_id: str) -> Any:
    (found,) = [t for t in problem.tasks if t.id == task_id]
    return found


def values(problem: Problem) -> dict[str, float]:
    return {t.id: t.value for t in problem.tasks}


# ================================================== 1. POST plans takes a base


def test_post_plans_takes_base_home_by_default_or_builder_and_stores_it(v: Village) -> None:
    home = post_plan(v, ENGINE)
    explicit_home = post_plan(v, {**ENGINE, "base": "home"})
    builder = post_plan(v, BUILDER)

    assert plan_data_ids(home) <= HOME_IDS
    assert rows(explicit_home) == rows(home)
    assert explicit_home["input_hash"] == home["input_hash"]
    assert plan_data_ids(builder) <= BUILDER_IDS
    assert plan_data_ids(builder)
    assert builder["input_hash"] != home["input_hash"]
    assert stored_bases(v) == {
        home["plan_id"]: "home",
        explicit_home["plan_id"]: "home",
        builder["plan_id"]: "builder",
    }


@pytest.mark.parametrize("base", ["moon", "", "Builder", 1])
def test_an_unknown_base_is_an_invalid_request_and_stores_nothing(v: Village, base: Any) -> None:
    response = v.client.post(url("/plans"), json={**ENGINE, "base": base})

    assert error_code(response, 422) == "invalid_request"
    assert plan_count(v) == 0


def test_every_route_refuses_an_unknown_base(v: Village) -> None:
    home = post_plan(v, ENGINE)
    wrong = {"base": "moon"}
    responses = {
        "plans/latest": v.client.get(url("/plans/latest"), params=wrong),
        "recommendations/now": v.client.get(url("/recommendations/now"), params=wrong),
        "journal": v.client.get(url("/journal"), params=wrong),
        "calendar.ics": v.client.get(url("/plans/latest/calendar.ics"), params=wrong),
        "critical-path": v.client.get(url("/critical-path"), params=wrong),
        "events": send_event(v, home["plan_id"], PRIORITY, base="moon"),
        "town-hall/compare": v.client.post(
            url("/town-hall/compare"), json={**ENGINE, "base": "moon"}
        ),
    }

    for route, response in responses.items():
        assert response.status_code == 422, (route, response.text)
        assert response.json()["detail"]["code"] == "invalid_request", route
    assert plan_count(v) == 1


def test_the_builder_plan_is_compiled_for_the_builder_village(v: Village) -> None:
    with spying() as spy:
        builder = post_plan(v, BUILDER)

    (call,) = spy.calls
    assert {t.id for t in call.problem.tasks} == BUILDER_TASKS
    assert {job.task_id for job in call.problem.frozen} == BUILDER_FROZEN
    assert call.problem.lanes["builder"] == 3
    assert call.problem.lanes["lab"] == 1
    assert simulate(call.problem, call.plan).valid
    assert plan_data_ids(builder) <= BUILDER_IDS
    assert not plan_data_ids(builder) & HOME_IDS
    frozen = {a["task_id"] for a in builder["assignments"] if a["status"] == "frozen"}
    assert frozen == BUILDER_FROZEN
    assert {a["task_id"] for a in builder["assignments"] if a["status"] == "planned"} <= (
        BUILDER_TASKS
    )


def test_the_builder_plan_needs_the_builder_resources_only(v: Village) -> None:
    ok(v.client.put(url("/manual"), json=manual_doc(builder=False)))

    response = v.client.post(url("/plans"), json=BUILDER)
    assert error_code(response, 409) == "missing_input"
    missing = response.json()["detail"]["missing"]
    assert "builder_gold" in missing
    assert "gold" not in missing
    assert plan_count(v) == 0

    home = post_plan(v, ENGINE)
    assert plan_data_ids(home) <= HOME_IDS


def test_the_migration_gives_base_home_to_the_plans_stored_before(tmp_path: Path) -> None:
    from clashpilot.storage.db import connect, migrate

    before = tmp_path / "migrations_before_e32"
    before.mkdir()
    for name in MIGRATIONS_BEFORE_E32:
        shutil.copy(MIGRATIONS_DIR / name, before / name)
    connection = connect(tmp_path / "data")
    try:
        migrate(connection, before)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(plans)")}
        assert "base" not in columns
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO plans (village_id, created_at, now, input_hash, engine, profile, "
            "pack_id, proof, assignments, trace, warnings, params, body, explanations, "
            "reliability) VALUES ('OLDVILLAGE', 1, 1, 'h', 'rollout', 'repro', 'p', '{}', "
            "'[]', '[]', '[]', '{}', '{}', '{}', '{}')"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")

        migrate(connection)

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(plans)")}
        assert "base" in columns
        assert [row["base"] for row in connection.execute("SELECT base FROM plans")] == ["home"]
    finally:
        connection.close()


# ================================================== 2. the routes read the latest plan of a base


def test_without_a_builder_plan_the_builder_base_has_no_plan(v: Village) -> None:
    post_plan(v, ENGINE)

    for path in ("/plans/latest", "/recommendations/now", "/plans/latest/calendar.ics"):
        response = v.client.get(url(path), params={"base": "builder"})
        assert error_code(response, 404) == "no_plan", path
        assert v.client.get(url(path)).status_code == 200, path
    response = v.client.post(url("/town-hall/compare"), json=BUILDER)
    assert error_code(response, 404) == "no_plan"


def test_the_latest_plan_routes_read_the_latest_plan_of_their_base(v: Village) -> None:
    home = post_plan(v, ENGINE)
    builder = post_plan(v, BUILDER)

    assert latest(v)["plan_id"] == home["plan_id"]
    assert latest(v, "home")["plan_id"] == home["plan_id"]
    read = latest(v, "builder")
    assert read["plan_id"] == builder["plan_id"]
    assert rows(read) == rows(builder)

    assert recommendations(v)["plan_id"] == home["plan_id"]
    assert recommendations(v, "home")["plan_id"] == home["plan_id"]
    advice = recommendations(v, "builder")
    assert advice["plan_id"] == builder["plan_id"]
    started = {a["task_id"] for a in advice["actions"] if a["action"] == "start"}
    assert started
    assert {data_id_of(name) for name in started} <= BUILDER_IDS

    assert calendar_uids(v) == {a["task_id"] for a in home["assignments"]}
    assert calendar_uids(v, "home") == {a["task_id"] for a in home["assignments"]}
    assert calendar_uids(v, "builder") == {a["task_id"] for a in builder["assignments"]}


def home_answers(v: Village) -> dict[str, Any]:
    """Every home answer a route gives without `base`."""
    calendar = v.client.get(url("/plans/latest/calendar.ics"))
    assert calendar.status_code == 200, calendar.text
    return {
        "plans/latest": latest(v),
        "recommendations/now": recommendations(v),
        "calendar.ics": calendar.text,
        "journal": journal(v),
        "critical-path": ok(v.client.get(url("/critical-path"))),
        "town-hall/compare": ok(v.client.post(url("/town-hall/compare"), json=ENGINE)),
        "forecast": ok(v.client.get(url("/forecast"))),
        "items/compare": ok(
            v.client.post(url("/items/compare"), json={"item": "book_of_building", **ENGINE})
        ),
    }


def test_the_home_answers_do_not_change_once_a_builder_plan_is_stored(v: Village) -> None:
    home = post_plan(v, ENGINE)
    ok(send_event(v, home["plan_id"], PRIORITY))
    before = home_answers(v)

    builder = post_plan(v, BUILDER)
    ok(send_event(v, builder["plan_id"], PRIORITY, base="builder"))
    after = home_answers(v)

    for route, answer in before.items():
        assert after[route] == answer, route


def test_without_base_a_home_plan_is_today_s(v: Village) -> None:
    home = post_plan(v, ENGINE)

    assert rows(home) == TODAY_HOME_ROWS
    assert home["input_hash"] == TODAY_HOME_INPUT_HASH
    assert home["score"] == pytest.approx(TODAY_HOME_SCORE, rel=1e-12)
    assert home["proof"]["kind"] == "heuristic"
    assert home["proof"]["objective"] == pytest.approx(TODAY_HOME_SCORE, rel=1e-12)
    assert home["warnings"] == TODAY_HOME_WARNINGS
    assert home["excluded"] == []
    assert rows(latest(v)) == TODAY_HOME_ROWS


def test_without_base_the_critical_path_is_today_s(v: Village) -> None:
    assert ok(v.client.get(url("/critical-path"))) == TODAY_CRITICAL_PATH
    assert ok(v.client.get(url("/critical-path"), params={"base": "home"})) == (TODAY_CRITICAL_PATH)


def test_the_stale_state_holds_for_both_bases(v: Village) -> None:
    post_plan(v, ENGINE)
    post_plan(v, BUILDER)
    assert latest(v)["stale"] is False
    assert latest(v, "builder")["stale"] is False

    import_export(v, export_doc(shift_s=60))

    assert latest(v)["stale"] is True
    assert latest(v, "builder")["stale"] is True


# ================================================== 3. replanning a builder base plan


def test_an_event_on_a_builder_plan_recompiles_the_builder_base_and_keeps_its_base(
    v: Village,
) -> None:
    home = post_plan(v, ENGINE)
    builder = post_plan(v, BUILDER)
    planned = [a for a in builder["assignments"] if a["status"] == "planned"]
    first = min(planned, key=lambda a: (a["start"], a["task_id"]))
    assert data_id_of(first["task_id"]) in BUILDER_IDS
    v.clock.now = first["start"]

    response = send_event(
        v,
        builder["plan_id"],
        {"type": "task_started", "task_id": first["task_id"], "at": first["start"]},
        base="builder",
    )
    replanned = ok(response)
    new_id = replanned["plan_id"]
    assert isinstance(new_id, int)

    read = latest(v, "builder")
    assert read["plan_id"] == new_id
    assert by_task(read)[first["task_id"]]["status"] == "frozen"
    assert plan_data_ids(read) <= BUILDER_IDS
    assert stored_bases(v)[new_id] == "builder"
    assert latest(v)["plan_id"] == home["plan_id"]

    builder_plans = {plan_id for plan_id, base in stored_bases(v).items() if base == "builder"}
    entries = journal(v, "builder")
    assert [(e["type"], e["task_id"], e["plan_id"]) for e in entries] == [
        ("task_started", first["task_id"], new_id)
    ]
    for base in (None, "home"):
        assert not {e["plan_id"] for e in journal(v, base)} & builder_plans, base


def test_a_builder_replanning_by_the_engine_keeps_the_builder_base(v: Village) -> None:
    post_plan(v, ENGINE)
    builder = post_plan(v, BUILDER)

    with spying() as spy:
        replanned = ok(send_event(v, builder["plan_id"], PRIORITY, base="builder"))

    assert spy.calls, "the engine replans the builder base"
    for call in spy.calls:
        assert {t.id for t in call.problem.tasks} <= BUILDER_TASKS
        assert {data_id_of(job.task_id) for job in call.problem.frozen} <= BUILDER_IDS
    read = latest(v, "builder")
    assert read["plan_id"] == replanned["plan_id"]
    assert plan_data_ids(read) <= BUILDER_IDS
    assert stored_bases(v)[read["plan_id"]] == "builder"


def test_an_event_is_judged_against_the_latest_plan_of_its_base(v: Village) -> None:
    first_home = post_plan(v, ENGINE)
    first_builder = post_plan(v, BUILDER)

    # The latest home plan stays open to events although a builder plan came after it.
    second_home = ok(send_event(v, first_home["plan_id"], PRIORITY))["plan_id"]
    assert latest(v)["plan_id"] == second_home
    assert latest(v, "builder")["plan_id"] == first_builder["plan_id"]

    response = send_event(v, first_home["plan_id"], PRIORITY)
    assert error_code(response, 409) == "plan_outdated"

    second_builder = ok(send_event(v, first_builder["plan_id"], PRIORITY, base="builder"))
    assert latest(v, "builder")["plan_id"] == second_builder["plan_id"]
    assert latest(v)["plan_id"] == second_home
    response = send_event(v, first_builder["plan_id"], PRIORITY, base="builder")
    assert error_code(response, 409) == "plan_outdated"

    # A builder plan is never replanned through a route called without `?base=builder`.
    count = plan_count(v)
    response = send_event(v, second_builder["plan_id"], PRIORITY)
    assert response.status_code in (404, 409), response.text
    assert plan_count(v) == count
    assert latest(v)["plan_id"] == second_home


# ================================================== 4. critical path and town hall per base


def test_the_builder_critical_path_aims_at_the_builder_hall(v: Village) -> None:
    path = ok(v.client.get(url("/critical-path"), params={"base": "builder"}))

    assert path["target"]["task_id"] == HALL_TASK
    assert path["target"]["data_id"] == HALL
    assert path["target"]["to_level"] == 2
    assert path["set_finish"]["value"] is not None
    assert {t["task_id"] for t in path["tasks"]} == BUILDER_CURRENT | {HALL_TASK}
    for chain in path["chains"]:
        assert chain["data_id"] in BUILDER_IDS, chain
        assert {data_id_of(name) for name in chain["task_ids"]} <= BUILDER_IDS, chain
    assert {lane["lane"] for lane in path["work_finish"]} <= {"builder", "lab"}

    skipped = ok(
        v.client.get(url("/critical-path"), params={"base": "builder", "skip_lanes": "lab"})
    )
    assert skipped["skip_lanes"] == ["lab"]
    assert {t["task_id"] for t in skipped["tasks"]} == BUILDER_CURRENT_OUTSIDE_LAB | {HALL_TASK}

    home = ok(v.client.get(url("/critical-path")))
    assert home["target"]["task_id"] == HOME_TH_TASK


def test_the_town_hall_comparator_reads_the_latest_plan_of_its_base(v: Village) -> None:
    home = post_plan(v, ENGINE)
    builder = post_plan(v, BUILDER)

    for body in (ENGINE, {**ENGINE, "base": "home"}):
        answer = ok(v.client.post(url("/town-hall/compare"), json=body))
        assert answer["reference_plan_id"] == home["plan_id"]
        assert answer["target"]["task_id"] == HOME_TH_TASK

    answer = ok(v.client.post(url("/town-hall/compare"), json=BUILDER))
    assert answer["reference_plan_id"] == builder["plan_id"]
    assert answer["target"]["task_id"] == HALL_TASK
    assert answer["target"]["data_id"] == HALL
    kinds = [option["kind"] for option in answer["options"]]
    assert "after" in kinds
    assert len(kinds) == len(set(kinds))
    for option in answer["options"]:
        assert set(option["unfinished_by_lane"]) <= {"builder", "lab"}, option
        assert {hero["data_id"] for hero in option["heroes"]} <= BUILDER_IDS, option


# ================================================== 5. plan options on the star laboratory


def test_town_hall_skip_lanes_lab_skips_the_star_laboratory(v: Village) -> None:
    with spying() as spy:
        waiting = post_plan(v, {**BUILDER, "strategy": "finish_th"})
        skipping = post_plan(
            v, {**BUILDER, "strategy": "finish_th", "town_hall_skip_lanes": ["lab"]}
        )

    plain_call, skip_call = spy.calls
    assert set(task_of(plain_call.problem, HALL_TASK).requires) == BUILDER_CURRENT
    assert set(task_of(skip_call.problem, HALL_TASK).requires) == BUILDER_CURRENT_OUTSIDE_LAB
    for call in spy.calls:
        assert simulate(call.problem, call.plan).valid

    assert skipping["town_hall_skip_lanes"] == ["lab"]
    for plan in (waiting, skipping):
        assert plan_data_ids(plan) <= BUILDER_IDS
    # The hall waits for the lancer's 10-day research, unless the star laboratory is skipped.
    lancer_end = by_task(waiting)[LANCER_TASK]["end"]
    assert by_task(waiting)[HALL_TASK]["start"] >= lancer_end
    assert by_task(skipping)[HALL_TASK]["start"] < by_task(skipping)[LANCER_TASK]["end"]
    assert set(stored_bases(v).values()) == {"builder"}


def test_composition_first_puts_the_composition_first_on_the_star_laboratory(
    v: Village,
) -> None:
    with spying() as spy:
        plain = post_plan(v, {**BUILDER, "composition": [GRUNT]})
        first = post_plan(v, {**BUILDER, "composition": [GRUNT], "composition_first": True})

    plain_call, first_call = spy.calls
    plain_values, first_values = values(plain_call.problem), values(first_call.problem)
    assert plain_values[LANCER_TASK] > 0
    assert first_values[LANCER_TASK] == 0
    assert first_values[GRUNT_TASK] == pytest.approx(2 * plain_values[GRUNT_TASK])
    for call in spy.calls:
        assert simulate(call.problem, call.plan).valid

    assert first["composition_first"] is True
    # Today the lancer's research goes first; with the option, the grunt's (the composition).
    assert lab_order(plain)[0] == LANCER_TASK
    assert lab_order(first)[0] == GRUNT_TASK
    for plan in (plain, first):
        assert plan_data_ids(plan) <= BUILDER_IDS


# ================================================== 6. forecast, scenarios and items stay home


def test_forecast_scenarios_and_items_read_the_latest_home_plan(v: Village) -> None:
    home = post_plan(v, ENGINE)
    post_plan(v, BUILDER)

    forecast = ok(v.client.get(url("/forecast")))
    assert forecast["plan_id"] == home["plan_id"]

    scenario = ok(
        v.client.post(
            url("/scenarios"),
            json={
                "name": "Moins de jeu",
                "overlays": [{"type": "reduce_play", "max_logins_per_day": 3}],
                **ENGINE,
            },
        ),
        200,
        201,
    )
    assert scenario["plan_id"] == home["plan_id"]
    assert {data_id_of(a["task_id"]) for a in scenario["assignments"]} <= HOME_IDS

    item = ok(v.client.post(url("/items/compare"), json={"item": "book_of_building", **ENGINE}))
    assert item["reference_plan_id"] == home["plan_id"]
    gems = ok(v.client.post(url("/gems/compare"), json=ENGINE))
    assert gems["reference_plan_id"] == home["plan_id"]


def test_with_only_a_builder_plan_forecast_scenarios_and_items_have_no_plan(v: Village) -> None:
    post_plan(v, BUILDER)

    responses: Sequence[tuple[str, Any]] = (
        ("forecast", v.client.get(url("/forecast"))),
        (
            "scenarios",
            v.client.post(
                url("/scenarios"),
                json={
                    "name": "Moins de jeu",
                    "overlays": [{"type": "reduce_play", "max_logins_per_day": 3}],
                    **ENGINE,
                },
            ),
        ),
        (
            "items/compare",
            v.client.post(url("/items/compare"), json={"item": "book_of_building", **ENGINE}),
        ),
        ("gems/compare", v.client.post(url("/gems/compare"), json=ENGINE)),
    )
    for route, response in responses:
        assert response.status_code == 404, (route, response.text)
        assert response.json()["detail"]["code"] == "no_plan", route
