"""Acceptance tests for E24: the `critical_path` plan option (SPEC ADR-CP2).

`POST /api/villages/{village_id}/plans` takes `critical_path` (boolean, false by default),
orthogonal to `strategy`: after the strategy, the values of the tasks grow with their criticality
(`critical_path(problem)` of E22, computed on the problem the strategy produced); the engines hold
no logic of their own for it. Frozen with the task.

Shapes that the task text leaves open are fixed here:

- `PlanOut` (the `POST plans` answer and `GET plans/latest`) carries `critical_path` as a boolean;
  the OpenAPI schema of the request and of the answer declares it as a boolean defaulting to false;
- `PlanParams` (`clashpilot.planning.service`) has a `critical_path` attribute, and
  `PlanParams.from_json` of a document without that key gives false;
- a replanning (`POST .../plans/{plan_id}/events`) and a scenario (`POST .../scenarios`) hand the
  engine the task values of the plan they start from, criticality included;
- "today's assignments" are the assignments the code gave before the option existed, recorded
  below (`TODAY_*`) for three requests on the fixture villages; `input_hash` is not compared;
- "the current level" is every task that does not depend on the town hall upgrade (`CURRENT`); the
  hero chain of the builder lane is critical there (float 0 in `GET .../critical-path`).

Every test goes through HTTP, with engine `rollout` and profile `repro`, on the fictional pack
`backend/tests/fixtures/packs/fictional_pack_critical.json` and the export
`backend/tests/fixtures/exports/fictional_export_critical.json` (exported one hour before `NOW` =
2026-11-27T08:00:00Z, five builders, a hero at level 1 with four levels left of 6, 7, 8 and 9 days;
two 1-day defenses on the builder lane; three lab researches). The E24 village changes it through
the API only: two builders in the export, both defenses cost 500 dark elixir (manual corrections
of the pack), 200 dark elixir in stock earning 4 per hour, always available, no sleep. Its plans use
the `defense` strategy. Without the option, the plan spends the dark elixir on the defenses and the
hero waits about two days twice (last current task ending after 34.7 days); with it, the hero's
levels follow one another and the current level ends after 30 days.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import sqlite3
from collections.abc import Iterator, Mapping
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
from clashpilot.planning.simulate import effective_duration, simulate
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR
STEP_S = 60

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"

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


TOWN_HALL_TASK = task(TOWN_HALL, 2)
HERO_TASKS = [task(HERO, level) for level in (2, 3, 4, 5)]
CURRENT = {
    *HERO_TASKS,
    task(BALLISTA, 2),
    task(CATAPULT, 2),
    task(SCOUT, 2),
    task(SCOUT, 3),
    task(SAPPER, 2),
}
CURRENT_BUILDER = {*HERO_TASKS, task(BALLISTA, 2), task(CATAPULT, 2)}
ENGINE = {"engine": "rollout", "profile": "repro"}
DEFENSE = {**ENGINE, "strategy": "defense"}
TEN_STRATEGIES = {
    "balanced",
    "offense",
    "defense",
    "th_rush",
    "heroes",
    "war_cwl",
    "low_play",
    "resources",
    "finish_th",
    "custom_goal",
}
OBJECTIVE_FIELDS = (
    "value",
    "goal",
    "idle",
    "night",
    "logins",
    "spill",
    "perturbation",
    "preferred",
)

# (task_id, lane, lane_slot, start - NOW, end - NOW, status) given by the code before E24.
Row = tuple[str, str, int, int, int, str]
TODAY_E24_DEFENSE: list[Row] = [
    ("9200001:0:2", "builder", 0, 0, 172800, "planned"),
    ("9200003:0:2", "builder", 0, 172800, 259200, "planned"),
    ("9200004:0:2", "builder", 1, 0, 518400, "planned"),
    ("9200004:0:3", "builder", 0, 720000, 1324800, "planned"),
    ("9200004:0:4", "builder", 0, 1530000, 2221200, "planned"),
    ("9200004:0:5", "builder", 0, 2221200, 2998800, "planned"),
    ("9200005:0:2", "builder", 1, 1170000, 1256400, "planned"),
    ("9200006:0:2", "builder", 0, 450000, 536401, "planned"),
    ("9200007:0:2", "lab", 0, 86400, 172800, "planned"),
    ("9200007:0:3", "lab", 0, 172800, 345600, "planned"),
    ("9200008:0:2", "lab", 0, 0, 86400, "planned"),
]
TODAY_E24_PLAIN: list[Row] = [
    ("9200001:0:2", "builder", 0, 0, 172800, "planned"),
    ("9200003:0:2", "builder", 0, 450000, 536400, "planned"),
    ("9200004:0:2", "builder", 1, 0, 518400, "planned"),
    ("9200004:0:3", "builder", 1, 518400, 1123200, "planned"),
    ("9200004:0:4", "builder", 0, 1123200, 1814400, "planned"),
    ("9200004:0:5", "builder", 0, 1814400, 2592000, "planned"),
    ("9200005:0:2", "builder", 1, 1980000, 2066400, "planned"),
    ("9200006:0:2", "builder", 0, 720000, 806401, "planned"),
    ("9200007:0:2", "lab", 0, 86400, 172800, "planned"),
    ("9200007:0:3", "lab", 0, 450000, 622800, "planned"),
    ("9200008:0:2", "lab", 0, 0, 86400, "planned"),
]
TODAY_E23_PLAIN: list[Row] = [
    ("9200001:0:2", "builder", 0, 0, 172800, "planned"),
    ("9200003:0:2", "builder", 0, 172800, 259200, "planned"),
    ("9200004:0:2", "builder", 1, 0, 518400, "planned"),
    ("9200004:0:3", "builder", 0, 518400, 1123200, "planned"),
    ("9200004:0:4", "builder", 0, 1123200, 1814400, "planned"),
    ("9200004:0:5", "builder", 0, 1814400, 2592000, "planned"),
    ("9200005:0:2", "builder", 2, 0, 86400, "planned"),
    ("9200006:0:2", "builder", 3, 0, 86401, "planned"),
    ("9200007:0:2", "lab", 0, 86400, 172800, "planned"),
    ("9200007:0:3", "lab", 0, 172800, 345600, "planned"),
    ("9200008:0:2", "lab", 0, 0, 86400, "planned"),
]


# --------------------------------------------------------------------------- fixture documents


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CRITICAL_PACK.read_text(encoding="utf-8"))
    return loaded


def export_doc(*, builders: int) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(CRITICAL_EXPORT.read_text(encoding="utf-8"))
    doc["timestamp"] = EXPORTED_AT
    for entry in doc["buildings"]:
        if entry["data"] == HUT:
            entry["cnt"] = builders
    return doc


def stock(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": NOW}


def manual_doc(*, dark_elixir: int, farm_rate: Mapping[str, int] | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "resources": {
            "gold": stock(1_000_000),
            "elixir": stock(1_000_000),
            "dark_elixir": stock(dark_elixir),
        }
    }
    if farm_rate is not None:
        doc["farm_rate"] = {
            name: {"low": rate, "expected": rate, "high": rate} for name, rate in farm_rate.items()
        }
    return doc


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


def base_village(tmp_path: Path, *, builders: int) -> Village:
    clock = Clock(NOW)
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    v = Village(TestClient(create_app(settings, clock=clock)), clock, tmp_path)
    pack = pack_doc()
    ok(v.client.post("/api/gamedata/packs", json=pack), 200, 201)
    ok(v.client.put(f"/api/gamedata/packs/{pack['pack_id']}/active"))
    ok(
        v.client.post(
            "/api/villages/import",
            content=json.dumps(export_doc(builders=builders)).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
        200,
        201,
    )
    ok(v.client.put(f"/api/villages/{VILLAGE}/schedule", json=PROFILE))
    return v


def e23_village(tmp_path: Path) -> Village:
    """The E23 fixture as committed: five builders, every resource plentiful."""
    v = base_village(tmp_path, builders=5)
    ok(
        v.client.put(
            f"/api/villages/{VILLAGE}/manual",
            json=manual_doc(dark_elixir=1_000_000, farm_rate=None),
        )
    )
    return v


def e24_village(tmp_path: Path) -> Village:
    """Two builders; the defenses and the hero compete for a scarce dark elixir."""
    v = base_village(tmp_path, builders=2)
    pack_id = pack_doc()["pack_id"]
    for data_id in (BALLISTA, CATAPULT):
        ok(
            v.client.patch(
                f"/api/gamedata/packs/{pack_id}/items/{data_id}/levels/2",
                json={"cost": {"dark_elixir": 500}},
            )
        )
    manual = manual_doc(dark_elixir=200, farm_rate={"gold": 1000, "elixir": 1000, "dark_elixir": 4})
    ok(v.client.put(f"/api/villages/{VILLAGE}/manual", json=manual))
    return v


def post_plan(v: Village, body: Mapping[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.post(f"/api/villages/{VILLAGE}/plans", json=dict(body)), 201)
    return plan


def latest(v: Village) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.get(f"/api/villages/{VILLAGE}/plans/latest"))
    return plan


def rows(plan: Mapping[str, Any]) -> list[Row]:
    return sorted(
        (
            a["task_id"],
            a["lane"],
            a["lane_slot"],
            a["start"] - plan["now"],
            a["end"] - plan["now"],
            a["status"],
        )
        for a in plan["assignments"]
    )


def by_task(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["task_id"]: a for a in plan["assignments"]}


def last_current_end(plan: Mapping[str, Any]) -> int:
    found = by_task(plan)
    assert set(found) >= CURRENT, sorted(CURRENT - set(found))
    return int(max(found[name]["end"] for name in CURRENT))


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


def values(problem: Problem) -> dict[str, float]:
    return {t.id: t.value for t in problem.tasks}


def check_first_accepted_instants(problem: Problem, plan: Plan) -> None:
    """Each hero level starts at the end of the previous one (`now` for the first) or, failing
    that, at the first instant `simulate` accepts after it (every `STEP_S` before is refused)."""
    tasks = {t.id: t for t in problem.tasks}
    found = {a.task_id: a for a in plan.assignments}
    assert set(HERO_TASKS) <= set(found), plan.assignments
    previous_end = problem.now
    for name in HERO_TASKS:
        placed = found[name]
        assert placed.start >= previous_end, (name, placed, previous_end)
        for instant in range(previous_end, placed.start, STEP_S):
            moved = dataclasses.replace(
                placed,
                start=instant,
                end=instant + effective_duration(problem, tasks[name], instant),
            )
            other = tuple(moved if a.task_id == name else a for a in plan.assignments)
            sim = simulate(problem, dataclasses.replace(plan, assignments=other))
            assert not sim.valid, (name, "could start at", instant, "instead of", placed.start)
        previous_end = placed.end


# --------------------------------------------------------------------------- criterion 1


def test_post_plans_accepts_critical_path_false_by_default_and_echoes_it(tmp_path: Path) -> None:
    v = e24_village(tmp_path)

    without = post_plan(v, DEFENSE)
    assert without["critical_path"] is False
    assert latest(v)["critical_path"] is False

    off = post_plan(v, {**DEFENSE, "critical_path": False})
    assert off["critical_path"] is False

    on = post_plan(v, {**DEFENSE, "critical_path": True})
    assert on["critical_path"] is True
    assert on["strategy"] == "defense"
    stored = latest(v)
    assert stored["plan_id"] == on["plan_id"]
    assert stored["critical_path"] is True

    # Changing the option changes the inputs of the engine, hence the input hash.
    assert on["input_hash"] != without["input_hash"]
    assert off["input_hash"] == without["input_hash"]

    # The resolved request is stored with the plan.
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        params = {
            plan_id: json.loads(text)
            for plan_id, text in connection.execute("SELECT plan_id, params FROM plans")
        }
    assert params[without["plan_id"]]["critical_path"] is False
    assert params[on["plan_id"]]["critical_path"] is True


def test_the_openapi_schema_declares_critical_path_as_a_boolean_false_by_default(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    document = create_app(settings, clock=Clock(NOW)).openapi()
    route = document["paths"]["/api/villages/{village_id}/plans"]["post"]
    request = route["requestBody"]["content"]["application/json"]["schema"]
    answer = route["responses"]["201"]["content"]["application/json"]["schema"]
    schemas = document["components"]["schemas"]
    for schema in (request, answer):
        # A body schema is a reference, possibly inside `anyOf` with null (optional body).
        refs = [s["$ref"] for s in (schema, *schema.get("anyOf", ())) if "$ref" in s]
        assert len(refs) == 1, schema
        ref = refs[0]
        prop = schemas[ref.rsplit("/", 1)[-1]]["properties"]["critical_path"]
        assert prop.get("type") == "boolean", (ref, prop)
        assert prop.get("default") is False, (ref, prop)


def test_plan_params_without_the_key_read_back_with_critical_path_false() -> None:
    from clashpilot.planning.service import PlanParams

    old = {"horizon_days": 90, "engine": "rollout", "profile": "repro", "strategy": "defense"}
    assert PlanParams.from_json(old).critical_path is False
    assert PlanParams.from_json({**old, "critical_path": True}).critical_path is True
    assert PlanParams.from_json({**old, "critical_path": False}).critical_path is False


def test_a_plan_stored_without_the_key_is_replanned_with_critical_path_false(
    tmp_path: Path,
) -> None:
    v = e24_village(tmp_path)
    plan = post_plan(v, {**DEFENSE, "critical_path": True})
    assert plan["critical_path"] is True

    # A plan stored before E24: its params have no `critical_path` key.
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        (text,) = connection.execute(
            "SELECT params FROM plans WHERE plan_id = ?", (plan["plan_id"],)
        ).fetchone()
        params = json.loads(text)
        params.pop("critical_path")
        connection.execute(
            "UPDATE plans SET params = ? WHERE plan_id = ?", (json.dumps(params), plan["plan_id"])
        )

    event = {"type": "resources_corrected", "resources": {"dark_elixir": 300}}
    answer = ok(
        v.client.post(f"/api/villages/{VILLAGE}/plans/{plan['plan_id']}/events", json=event)
    )
    replanned = latest(v)
    assert replanned["plan_id"] == answer["plan_id"] != plan["plan_id"]
    assert replanned["critical_path"] is False


# --------------------------------------------------------------------------- criterion 2


@pytest.mark.parametrize("option", [{}, {"critical_path": False}], ids=["absent", "false"])
@pytest.mark.parametrize(
    ("make", "request_body", "today"),
    [
        (e24_village, DEFENSE, TODAY_E24_DEFENSE),
        (e24_village, ENGINE, TODAY_E24_PLAIN),
        (e23_village, ENGINE, TODAY_E23_PLAIN),
    ],
    ids=["e24-defense", "e24-plain", "e23-plain"],
)
def test_without_the_option_the_assignments_are_today_s(
    tmp_path: Path, make: Any, request_body: Mapping[str, Any], today: list[Row], option: Any
) -> None:
    v = make(tmp_path)
    plan = post_plan(v, {**request_body, **option})
    assert plan["now"] == NOW
    assert rows(plan) == today
    assert plan["critical_path"] is False


# --------------------------------------------------------------------------- criteria 3 and 4


def test_the_fixture_hero_chain_is_critical_and_waits_in_the_default_plan(tmp_path: Path) -> None:
    v = e24_village(tmp_path)
    path = ok(v.client.get(f"/api/villages/{VILLAGE}/critical-path"))
    floats = {row["task_id"]: row for row in path["tasks"]}
    assert path["bound"]["kind"] == "chain"
    for name in HERO_TASKS:
        assert floats[name]["float_s"] == 0 and floats[name]["critical"] is True, floats[name]
    for name in (task(BALLISTA, 2), task(CATAPULT, 2)):
        assert floats[name]["critical"] is False, floats[name]

    default = by_task(post_plan(v, DEFENSE))
    hero = [default[name] for name in HERO_TASKS]
    waiting = [
        after for before, after in itertools.pairwise(hero) if after["start"] > before["end"]
    ]
    assert waiting, hero
    # Meanwhile other builder tasks of the current level start, spending the dark elixir the hero
    # waits for.
    others = [
        default[name]
        for name in CURRENT_BUILDER - set(HERO_TASKS)
        if hero[0]["start"] <= default[name]["start"] < waiting[0]["start"]
    ]
    assert others, default


def test_with_the_option_each_hero_level_starts_as_soon_as_the_previous_one_ends(
    tmp_path: Path,
) -> None:
    v = e24_village(tmp_path)
    with spying() as spy:
        plan = post_plan(v, {**DEFENSE, "critical_path": True})
    assert plan["critical_path"] is True
    call = spy.calls[-1]
    found = by_task(plan)
    # The answer is the engine's plan.
    assert {a.task_id: (a.start, a.end) for a in call.plan.assignments} == {
        name: (a["start"], a["end"]) for name, a in found.items() if a["status"] == "planned"
    }
    check_first_accepted_instants(call.problem, call.plan)
    # On this fixture nothing stops the chain: no wait at all.
    hero = [found[name] for name in HERO_TASKS]
    assert hero[0]["start"] == NOW
    for before, after in itertools.pairwise(hero):
        assert after["start"] == before["end"], (before, after)


def test_with_the_option_the_current_level_ends_no_later_and_the_plan_is_valid(
    tmp_path: Path,
) -> None:
    v = e24_village(tmp_path)
    with spying() as spy:
        default = post_plan(v, DEFENSE)
        critical = post_plan(v, {**DEFENSE, "critical_path": True})
    assert len(spy.calls) == 2
    call = spy.calls[-1]
    sim = simulate(call.problem, call.plan)
    assert sim.valid, sim.violations
    assert "heuristic_invalid_plan" not in critical["warnings"]
    assert last_current_end(critical) <= last_current_end(default)
    # The fixture is meant to show a difference: the default plan ends the current level later.
    assert last_current_end(default) > last_current_end(critical)


def test_the_option_raises_the_critical_values_after_the_strategy(tmp_path: Path) -> None:
    v = e24_village(tmp_path)
    with spying() as spy:
        post_plan(v, DEFENSE)
        post_plan(v, {**DEFENSE, "critical_path": True})
    plain, critical = (values(call.problem) for call in spy.calls)
    assert set(plain) == set(critical)
    # A task without float beats every task with float under the value/duration rule.
    durations = {t.id: max(1, t.duration_s) for t in spy.calls[-1].problem.tasks}
    lowest_critical = min(critical[name] / durations[name] for name in HERO_TASKS)
    for name in (CURRENT - set(HERO_TASKS)) | {TOWN_HALL_TASK}:
        assert critical[name] / durations[name] < lowest_critical, name
    # The engine sees the same objective; only task values change.
    assert spy.calls[0].objective == spy.calls[1].objective
    strip = [dataclasses.replace(c.problem, tasks=()) for c in spy.calls]
    assert strip[0] == strip[1]
    tasks = [[dataclasses.replace(t, value=0.0) for t in c.problem.tasks] for c in spy.calls]
    assert tasks[0] == tasks[1]
    for name in HERO_TASKS:
        assert critical[name] > plain[name], name


# --------------------------------------------------------------------------- criterion 5


def test_finish_th_with_the_option_keeps_the_town_hall_last_on_the_builder_lane(
    tmp_path: Path,
) -> None:
    v = e24_village(tmp_path)
    with spying() as spy:
        plan = post_plan(v, {**ENGINE, "strategy": "finish_th", "critical_path": True})
    assert plan["critical_path"] is True and plan["strategy"] == "finish_th"
    assert simulate(spy.calls[-1].problem, spy.calls[-1].plan).valid
    found = by_task(plan)
    assert TOWN_HALL_TASK in found, plan["assignments"]
    town_hall = found[TOWN_HALL_TASK]
    builder_current = [found[name] for name in CURRENT_BUILDER]
    assert len(builder_current) == len(CURRENT_BUILDER)
    assert town_hall["start"] >= max(a["end"] for a in builder_current), plan["assignments"]


# --------------------------------------------------------------------------- criterion 6


def test_a_replanning_keeps_the_critical_path_of_the_plan(tmp_path: Path) -> None:
    v = e24_village(tmp_path)
    with spying() as spy:
        post_plan(v, DEFENSE)
        plain = values(spy.calls[-1].problem)
        first = post_plan(v, {**DEFENSE, "critical_path": True})
        boosted = values(spy.calls[-1].problem)
        assert any(boosted[name] != plain[name] for name in HERO_TASKS)

        event = {"type": "resources_corrected", "resources": {"dark_elixir": 300}}
        answer = ok(
            v.client.post(f"/api/villages/{VILLAGE}/plans/{first['plan_id']}/events", json=event)
        )
        assert len(spy.calls) == 3, "the replanning did not run the engine"
        replanned_values = values(spy.calls[-1].problem)

    now = latest(v)
    assert now["plan_id"] == answer["plan_id"] != first["plan_id"]
    assert now["critical_path"] is True
    assert now["strategy"] == "defense"
    common = [name for name in boosted if name in replanned_values]
    assert set(HERO_TASKS) <= set(common)
    for name in common:
        assert replanned_values[name] == pytest.approx(boosted[name]), name


def test_a_scenario_keeps_the_critical_path_of_the_plan(tmp_path: Path) -> None:
    v = e24_village(tmp_path)
    default = post_plan(v, DEFENSE)
    with spying() as spy:
        critical = post_plan(v, {**DEFENSE, "critical_path": True})
        boosted = values(spy.calls[-1].problem)
        scenario = ok(
            v.client.post(
                f"/api/villages/{VILLAGE}/scenarios",
                json={"name": "Copie", "overlays": [], **ENGINE},
            ),
            201,
        )
        assert len(spy.calls) == 2, "the scenario did not run the engine"
        scenario_values = values(spy.calls[-1].problem)

    assert scenario["status"] == "ok", scenario
    assert scenario_values == pytest.approx(boosted)
    hero = {
        a["task_id"]: (a["start"], a["end"])
        for a in scenario["assignments"]
        if a["task_id"] in HERO_TASKS
    }
    assert hero == {
        name: (a["start"], a["end"]) for name, a in by_task(critical).items() if name in HERO_TASKS
    }
    assert hero != {
        name: (a["start"], a["end"]) for name, a in by_task(default).items() if name in HERO_TASKS
    }


# --------------------------------------------------------------------------- criterion 7


def test_the_ten_strategies_and_the_objective_fields_are_unchanged(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=Clock(NOW)))
    strategies = ok(client.get("/api/meta/strategies"))["strategies"]
    keys = [row["key"] for row in strategies]
    assert len(keys) == len(set(keys)) == 10
    assert set(keys) == TEN_STRATEGIES
    for row in strategies:
        assert set(row["weights"]) <= set(OBJECTIVE_FIELDS), row
    assert tuple(field.name for field in dataclasses.fields(Objective)) == OBJECTIVE_FIELDS
