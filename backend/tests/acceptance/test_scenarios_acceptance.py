"""Acceptance tests for E18: what-if scenarios.

SPEC section 4 (Problem, FrozenJob, Policy, simulator metrics, login definition), section 5
(`rules` of the pack: a missing key means "not supported") and section 7 (budget: the HTTP tests
pass the fields of `TEST_CONFIG` as `Settings.planner_overrides`, engine `rollout`, profile
`repro`).

Frozen with the task. A scenario is a set of overlays applied to a copy of the compiled problem of
the real plan: it never writes the real tables (state, plans, journal, schedule, manual input).
The tests run everything through HTTP on the fictional pack
`backend/tests/fixtures/packs/fictional_pack_items.json` and invented exports.

Shapes that the task text leaves open are fixed here:

- `POST /api/villages/{village_id}/scenarios` takes `{"name", "overlays", "engine"?, "profile"?}`.
  `engine` and `profile` are optional (the API defaults to profile `fast`; these tests always send
  `rollout` and `repro`). The scenario starts from the inputs of the latest real plan of the village
  (same `now`, horizon, strategy, composition, goal and war windows, the problem compiled from the
  export and the journal), then applies its overlays in order. An overlay is an object with a
  `type`:
  - `{"type": "start_now", "task_id"}`: the task starts at the earliest instant the simulator
    accepts (lanes, frozen jobs, funds, prerequisites, availability); if resources or prerequisites
    forbid it within the horizon, the scenario is `infeasible`.
  - `{"type": "reserve_builder", "count", "until"}`: `count` builders fewer from `now` until the UTC
    epoch `until`; the running jobs of the export count in the reduced capacity (an overlay never
    moves a FrozenJob). A reservation is never listed in `assignments`.
  - `{"type": "absence", "start_local", "end_local"}`: local date-times `YYYY-MM-DDTHH:MM:SS` in the
    zone of the schedule profile (same format as an absence of the profile); no plan start in
    `[start, end)`, and under the policy `strict` no plan end in `[start, end)` either.
  - `{"type": "policy", "mode", ...}`: `mode` among `strict`, `tolerance`, `progress`,
    `min_logins`; optional `tolerance_minutes` and `max_logins_per_day` as in the schedule profile.
  - `{"type": "strategy", "key"}`: a key of `GET /api/meta/strategies`.
  - `{"type": "reduce_play", "max_logins_per_day"}`: at most that many logins per local day.
  - `{"type": "use_item", "item", "task_id", "at"}`: `item` is a rule key of the active pack,
    `task_id` a task id or null, `at` a UTC epoch.
  An unknown overlay type is a 422 and stores nothing; an unknown village is a 404
  `village_not_found`.
- The answer is 201 with the scenario: `{"scenario_id", "name", "overlays", "status", "reasons",
  "now", "horizon_end", "assignments", "proof"}`. `status` is `ok`, `infeasible` or `unsupported`;
  `reasons` is a list of `{"code", "message"}` (French message), non-empty unless `ok`.
  `assignments` has the shape of the plan assignments (`task_id`, `lane`, `lane_slot`, `start`,
  `end`, `status` `planned` or `frozen`; a task fixed by `start_now` or `use_item` may be listed
  with either status), `proof` the shape of the plan proof or null.
  A scenario holding a `use_item` whose rule is absent from the active pack is `unsupported` with
  a reason of code `unsupported_item`, empty `assignments`, `proof` null, and no engine runs.
- `GET /api/villages/{village_id}/scenarios` answers the list of the village's scenarios, each
  in the shape of the POST answer. `DELETE /api/villages/{village_id}/scenarios/{scenario_id}`
  answers 200 or 204; an unknown scenario is a 404 `scenario_not_found`. Scenario ids and plan ids
  are separate: deleting scenario N never touches plan N.
- `POST /api/villages/{village_id}/scenarios/compare` takes `{"scenario_ids": [...]}` and answers
  `{"plan": row, "scenarios": [row, ...]}`, the real (latest) plan with its `plan_id`, then one row
  per requested id, in the request order, with its `scenario_id`; an unknown id is a 404
  `scenario_not_found`. A row holds, computed by `simulate` on the problem of that plan:
  - `goal_finish`: end of the last task of the goal when every goal task ends by `horizon_end`,
    else null (without a goal the value is left to the implementation);
  - `finished_count`: plan assignments (status `planned`) ending at or before `horizon_end`;
  - `remaining_cost`: a mapping resource -> non-negative amount;
  - `value_gained`: a non-negative number;
  - `hero_unavailable_s`: seconds of `[now, horizon_end)` covered by the jobs of hero tasks;
  - `logins`: number of logins of the plan (maximal groups of starts at most `login_merge_s`,
    900 s by default, apart);
  - `idle_builder_s`: builder capacity times `horizon_end - now` minus the builder busy time in
    `[now, horizon_end]` (when no builder is reserved);
  - `proof`: the proof of that plan.
  The field `logins` must stay out of the OpenAPI schema properties: the frozen E05 guard refuses
  any property named like a login except `..._logins_per_day` (see `next_login_at` in E13).

The feature calls go through HTTP only, so that each guarantee fails on its own for as long as the
feature does not exist.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import importlib.util
import json
import math
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
TESTS = BACKEND / "tests"
ITEMS_PACK = TESTS / "fixtures" / "packs" / "fictional_pack_items.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

PARIS = "Europe/Paris"
# 2026-11-09T09:00 in Paris (CET, UTC+1, no DST change for weeks): a whole day before 23:00 sleep.
HTTP_NOW = int(datetime.datetime(2026, 11, 9, 8, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = HTTP_NOW - HOUR
TAG = "#FICTIF18"
VILLAGE = "FICTIF18"
BUILDERS = 3
LOGIN_MERGE_S = 900

# Ids of the fictional items pack.
PACK_TOWN_HALL = 9000001
PACK_HUT = 9000002
PACK_LAB = 9000003
PACK_CANNON = 9000004
PACK_HERO = 9000006
PACK_TROOP = 9000007

TOWN_HALL_TASK = f"{PACK_TOWN_HALL}:0:3"  # the town hall from level 2 to 3 (5000 gold, 1 h)

PLAN_REQUEST = {"horizon_days": 30, "engine": "rollout", "profile": "repro"}
GOAL_PLAN_REQUEST = {
    **PLAN_REQUEST,
    "strategy": "custom_goal",
    "goal": {"data_id": PACK_TOWN_HALL, "level": 3},
}
ENGINE = {"engine": "rollout", "profile": "repro"}

# A 14-day stay starting two hours after now: 2026-11-09T11:00 to 2026-11-23T11:00 in Paris.
ABSENCE = {
    "type": "absence",
    "start_local": "2026-11-09T11:00:00",
    "end_local": "2026-11-23T11:00:00",
}
ABSENCE_START = HTTP_NOW + 2 * HOUR
ABSENCE_END = ABSENCE_START + 14 * DAY

ALL_OVERLAYS: list[dict[str, Any]] = [
    {"type": "start_now", "task_id": TOWN_HALL_TASK},
    {"type": "reserve_builder", "count": 1, "until": HTTP_NOW + DAY},
    ABSENCE,
    {"type": "policy", "mode": "strict"},
    {"type": "strategy", "key": "balanced"},
    {"type": "reduce_play", "max_logins_per_day": 1},
    {"type": "use_item", "item": "builder_potion", "task_id": None, "at": HTTP_NOW},
]

# --------------------------------------------------------------------------- support


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e18_acceptance_support_problems"
    if name in sys.modules:
        return sys.modules[name]
    assert SUPPORT_PROBLEMS.is_file(), f"{SUPPORT_PROBLEMS} is missing"
    spec = importlib.util.spec_from_file_location(name, SUPPORT_PROBLEMS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses defined there look their module up
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def planners() -> dict[str, Any]:
    import clashpilot.planning  # noqa: F401  (registers the engines)
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


class Counting:
    """Delegates to the real engine and counts its calls."""

    def __init__(self, inner: Any, calls: list[str], key: str) -> None:
        self.inner = inner
        self.calls = calls
        self.key = key

    def solve(self, problem: Any, objective: Any, config: Any, reference: Any = None) -> Any:
        self.calls.append(self.key)
        return self.inner.solve(problem, objective, config, reference)


@contextmanager
def counting_engines() -> Iterator[list[str]]:
    """Every registered engine counts its calls while the block runs."""
    registry = planners()
    saved = dict(registry)
    calls: list[str] = []
    for key, inner in saved.items():
        registry[key] = Counting(inner, calls, key)
    try:
        yield calls
    finally:
        registry.clear()
        registry.update(saved)


# --------------------------------------------------------------------------- village fixtures


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@dataclasses.dataclass
class Village:
    client: TestClient
    data_dir: Path


def items_pack() -> dict[str, Any]:
    pack: dict[str, Any] = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    return pack


def export_doc(running: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    """Town hall 2, two huts, a lab and a cannon, a troop and a hero; `running` adds jobs.

    A running entry replaces the idle building of the same `data` and level when there is one.
    """
    buildings: list[dict[str, Any]] = [
        {"data": PACK_TOWN_HALL, "lvl": 2},
        {"data": PACK_HUT, "lvl": 1},
        {"data": PACK_HUT, "lvl": 1},
        {"data": PACK_LAB, "lvl": 1},
        {"data": PACK_CANNON, "lvl": 2},
    ]
    for job in running:
        for index, idle in enumerate(buildings):
            if idle["data"] == job["data"] and idle["lvl"] == job["lvl"] and "timer" not in idle:
                buildings[index] = dict(job)
                break
        else:
            buildings.append(dict(job))
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": buildings,
        "units": [{"data": PACK_TROOP, "lvl": 1}],
        "heroes": [{"data": PACK_HERO, "lvl": 1}],
    }


def running_job(data_id: int, level: int, finish_at: int) -> dict[str, Any]:
    """An upgrade from `level` in progress at the export, ending at `finish_at`."""
    return {"data": data_id, "lvl": level, "timer": finish_at - EXPORTED_AT}


def resource(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": EXPORTED_AT}


def manual_doc(
    gold: int = 100_000, *, gold_income: bool = True, builders: int = BUILDERS
) -> dict[str, Any]:
    rate = {"low": 500, "expected": 1000, "high": 1500}
    farm_rate = {"elixir": rate, "dark_elixir": rate}
    if gold_income:
        farm_rate["gold"] = rate
    return {
        "resources": {
            "gold": resource(gold),
            "elixir": resource(50_000),
            "dark_elixir": resource(5_000),
        },
        "builders_total": builders,
        "farm_rate": farm_rate,
        "magic_items": {},
    }


def profile_doc() -> dict[str, Any]:
    return {
        "tz": PARIS,
        "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def village(
    data_dir: Path,
    *,
    export: dict[str, Any] | None = None,
    manual: dict[str, Any] | None = None,
    pack: dict[str, Any] | None = None,
    plan: Mapping[str, Any] | None = PLAN_REQUEST,
) -> Village:
    """A village with a pack, an export, a manual input, a schedule and (unless None) a plan."""
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=data_dir,
        frontend_dist=data_dir / "no-dist",
        planner_overrides=dataclasses.asdict(support().TEST_CONFIG),
    )
    client = TestClient(create_app(settings, clock=Clock(HTTP_NOW)))
    content = items_pack() if pack is None else pack
    response = client.post("/api/gamedata/packs", json=content)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{content['pack_id']}/active")
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/villages/import",
        content=json.dumps(export_doc() if export is None else export).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE
    response = client.put(
        f"/api/villages/{VILLAGE}/manual", json=manual_doc() if manual is None else manual
    )
    assert response.status_code == 200, response.text
    response = client.put(f"/api/villages/{VILLAGE}/schedule", json=profile_doc())
    assert response.status_code == 200, response.text
    v = Village(client, data_dir)
    if plan is not None:
        response = client.post(f"/api/villages/{VILLAGE}/plans", json=dict(plan))
        assert response.status_code == 201, response.text
    return v


# --------------------------------------------------------------------------- HTTP helpers

URL = f"/api/villages/{VILLAGE}/scenarios"


def get_json(v: Village, path: str) -> Any:
    response = v.client.get(path)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def latest(v: Village) -> dict[str, Any]:
    body: dict[str, Any] = get_json(v, f"/api/villages/{VILLAGE}/plans/latest")
    return body


def error_of(response: Any) -> dict[str, Any]:
    detail: dict[str, Any] = response.json()["detail"]
    assert isinstance(detail.get("code"), str) and detail["code"], detail
    assert isinstance(detail.get("message"), str) and detail["message"], detail
    return detail


def create(v: Village, name: str, overlays: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    body = {"name": name, "overlays": [dict(o) for o in overlays], **ENGINE}
    response = v.client.post(URL, json=body)
    assert response.status_code == 201, response.text
    scenario: dict[str, Any] = response.json()
    check_scenario(scenario, name, overlays)
    return scenario


def listed(v: Village) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = get_json(v, URL)
    assert isinstance(rows, list), rows
    return rows


def compare(v: Village, scenario_ids: Sequence[int]) -> dict[str, Any]:
    response = v.client.post(f"{URL}/compare", json={"scenario_ids": list(scenario_ids)})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def plan_ids_in_db(v: Village) -> list[int]:
    connection = sqlite3.connect(v.data_dir / "clashpilot.db")
    try:
        rows = connection.execute("SELECT plan_id FROM plans ORDER BY plan_id").fetchall()
    finally:
        connection.close()
    return [int(row[0]) for row in rows]


def check_proof(proof: Any) -> None:
    assert isinstance(proof, dict), proof
    assert proof["kind"] in ("heuristic", "feasible", "optimal_window"), proof
    assert isinstance(proof["objective"], int | float), proof


def check_scenario(scenario: Mapping[str, Any], name: str, overlays: Iterable[Any]) -> None:
    assert isinstance(scenario["scenario_id"], int), scenario
    assert scenario["name"] == name
    assert [o["type"] for o in scenario["overlays"]] == [o["type"] for o in overlays]
    assert scenario["status"] in ("ok", "infeasible", "unsupported"), scenario
    assert isinstance(scenario["reasons"], list), scenario
    for reason in scenario["reasons"]:
        assert isinstance(reason["code"], str) and reason["code"], reason
        assert isinstance(reason["message"], str) and reason["message"].strip(), reason
    if scenario["status"] != "ok":
        assert scenario["reasons"], scenario
    assert isinstance(scenario["assignments"], list), scenario
    if scenario["proof"] is not None:
        check_proof(scenario["proof"])


# --------------------------------------------------------------------------- plan arithmetic


def data_id_of(task_id: str) -> int:
    return int(task_id.split(":")[0])


def planned(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [a for a in body["assignments"] if a["status"] == "planned"]


def assignment_of(body: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    for assignment in body["assignments"]:
        if assignment["task_id"] == task_id:
            found: dict[str, Any] = assignment
            return found
    raise AssertionError(f"{task_id} is not in {body['assignments']}")


def overlap(start: int, end: int, low: int, high: int) -> int:
    return max(0, min(end, high) - max(start, low))


def login_starts(starts: Iterable[int]) -> list[int]:
    """First start of each login: maximal groups of starts at most 900 s apart (SPEC 4)."""
    logins: list[int] = []
    previous: int | None = None
    for start in sorted(starts):
        if previous is None or start - previous > LOGIN_MERGE_S:
            logins.append(start)
        previous = start
    return logins


def local_day(t: int) -> datetime.date:
    return datetime.datetime.fromtimestamp(t, ZoneInfo(PARIS)).date()


@dataclasses.dataclass(frozen=True)
class Expected:
    """The compare figures recomputed from the assignments of a plan without frozen jobs."""

    finished_count: int
    logins: int
    idle_builder_s: int
    hero_unavailable_s: int
    goal_finish: int | None


def expected_figures(body: Mapping[str, Any], goal_tasks: Iterable[str]) -> Expected:
    now, horizon_end = int(body["now"]), int(body["horizon_end"])
    rows = body["assignments"]
    assert all(a["status"] == "planned" for a in rows), "the fixture has no running job"
    busy = sum(
        overlap(a["start"], a["end"], now, horizon_end) for a in rows if a["lane"] == "builder"
    )
    hero = sum(
        overlap(a["start"], a["end"], now, horizon_end)
        for a in rows
        if data_id_of(a["task_id"]) == PACK_HERO
    )
    ends = {a["task_id"]: a["end"] for a in rows}
    goal = list(goal_tasks)
    reached = all(name in ends and ends[name] <= horizon_end for name in goal)
    return Expected(
        finished_count=sum(1 for a in rows if a["end"] <= horizon_end),
        logins=len(login_starts(a["start"] for a in rows)),
        idle_builder_s=BUILDERS * (horizon_end - now) - busy,
        hero_unavailable_s=hero,
        goal_finish=max(ends[name] for name in goal) if goal and reached else None,
    )


ROW_FIELDS = (
    "goal_finish",
    "finished_count",
    "remaining_cost",
    "value_gained",
    "hero_unavailable_s",
    "logins",
    "idle_builder_s",
    "proof",
)
RESOURCES = {"gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"}


def check_row(row: Mapping[str, Any]) -> None:
    for name in ROW_FIELDS:
        assert name in row, (name, row)
    assert row["goal_finish"] is None or isinstance(row["goal_finish"], int), row
    for name in ("finished_count", "hero_unavailable_s", "logins", "idle_builder_s"):
        assert isinstance(row[name], int) and row[name] >= 0, (name, row)
    assert isinstance(row["value_gained"], int | float) and row["value_gained"] >= 0, row
    remaining = row["remaining_cost"]
    assert isinstance(remaining, dict), row
    assert set(remaining) <= RESOURCES, remaining
    assert all(isinstance(v, int | float) and v >= 0 for v in remaining.values()), remaining
    check_proof(row["proof"])


def check_against(row: Mapping[str, Any], expected: Expected, proof: Any) -> None:
    assert row["finished_count"] == expected.finished_count, row
    assert row["logins"] == expected.logins, row
    assert row["idle_builder_s"] == expected.idle_builder_s, row
    assert row["hero_unavailable_s"] == expected.hero_unavailable_s, row
    assert row["goal_finish"] == expected.goal_finish, row
    assert row["proof"] == proof, row


def finish_rank(goal_finish: int | None) -> float:
    """A goal not reached is the worst finish."""
    return math.inf if goal_finish is None else float(goal_finish)


def real_state(v: Village) -> dict[str, Any]:
    """Every read of the real village a scenario must leave untouched."""
    base = f"/api/villages/{VILLAGE}"
    return {
        "state": get_json(v, f"{base}/state"),
        "latest": get_json(v, f"{base}/plans/latest"),
        "journal": get_json(v, f"{base}/journal"),
        "schedule": get_json(v, f"{base}/schedule"),
        "manual": get_json(v, f"{base}/manual"),
        "recommendations": get_json(v, f"{base}/recommendations/now"),
        "plan_ids": plan_ids_in_db(v),
    }


# --------------------------------------------------------------------------- creation and listing


def test_post_scenarios_computes_every_overlay_type_and_lists_it(tmp_path: Path) -> None:
    v = village(tmp_path)
    assert listed(v) == []
    plan = latest(v)

    created: dict[int, dict[str, Any]] = {}
    for overlay in ALL_OVERLAYS:
        scenario = create(v, f"Et si {overlay['type']}", [overlay])
        if overlay["type"] in ("start_now", "use_item"):
            assert scenario["status"] in ("ok", "infeasible"), scenario
        else:
            assert scenario["status"] == "ok", scenario
        created[scenario["scenario_id"]] = scenario
    combined = create(v, "Tout à la fois", ALL_OVERLAYS)
    assert combined["status"] in ("ok", "infeasible"), combined
    created[combined["scenario_id"]] = combined
    assert len(created) == len(ALL_OVERLAYS) + 1, "scenario ids must be distinct"

    for scenario in created.values():
        assert scenario["now"] == plan["now"], scenario
        assert scenario["horizon_end"] == plan["horizon_end"], scenario
        if scenario["status"] == "ok":
            check_proof(scenario["proof"])

    rows = listed(v)
    assert sorted(row["scenario_id"] for row in rows) == sorted(created)
    for row in rows:
        scenario = created[row["scenario_id"]]
        for name in ("name", "status", "now", "horizon_end"):
            assert row[name] == scenario[name], (name, row)
        assert [o["type"] for o in row["overlays"]] == [o["type"] for o in scenario["overlays"]]
        assert [a["task_id"] for a in row["assignments"]] == [
            a["task_id"] for a in scenario["assignments"]
        ]

    paths = v.client.get("/openapi.json").json()["paths"]
    assert {"get", "post"} <= set(paths["/api/villages/{village_id}/scenarios"])
    assert "post" in paths["/api/villages/{village_id}/scenarios/compare"]
    deletable = [
        path
        for path, methods in paths.items()
        if path.startswith("/api/villages/{village_id}/scenarios/{") and "delete" in methods
    ]
    assert deletable, sorted(paths)


def test_post_scenarios_refuses_an_unknown_overlay_and_an_unknown_village(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    body = {"name": "Inconnu", "overlays": [{"type": "teleport", "task_id": TOWN_HALL_TASK}]}
    response = v.client.post(URL, json={**body, **ENGINE})
    assert response.status_code == 422, response.text
    error_of(response)
    assert listed(v) == []

    good = {"name": "Ailleurs", "overlays": [{"type": "policy", "mode": "strict"}], **ENGINE}
    response = v.client.post("/api/villages/NOSUCH99/scenarios", json=good)
    assert response.status_code == 404, response.text
    assert error_of(response)["code"] == "village_not_found"


# --------------------------------------------------------------------------- real state untouched


def test_scenarios_never_change_the_real_state_the_latest_plan_or_the_journal(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    before = real_state(v)

    ids = [
        create(v, f"Scénario {overlay['type']}", [overlay])["scenario_id"]
        for overlay in ALL_OVERLAYS
    ]
    ids.append(create(v, "Tout à la fois", ALL_OVERLAYS)["scenario_id"])
    compare(v, ids)
    assert real_state(v) == before

    response = v.client.delete(f"{URL}/{ids[0]}")
    assert response.status_code in (200, 204), response.text
    assert real_state(v) == before


# --------------------------------------------------------------------------- compare


def test_compare_returns_the_simulated_figures_of_the_real_plan_and_of_each_scenario(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, plan=GOAL_PLAN_REQUEST)
    plan = latest(v)
    same = create(v, "Même politique", [{"type": "policy", "mode": "progress"}])
    fewer = create(v, "Deux connexions", [{"type": "reduce_play", "max_logins_per_day": 2}])
    other = create(v, "Équilibrée", [{"type": "strategy", "key": "balanced"}])
    for scenario in (same, fewer, other):
        assert scenario["status"] == "ok", scenario

    order = [other["scenario_id"], same["scenario_id"], fewer["scenario_id"]]
    body = compare(v, order)
    assert body["plan"]["plan_id"] == plan["plan_id"]
    assert [row["scenario_id"] for row in body["scenarios"]] == order
    for row in [body["plan"], *body["scenarios"]]:
        check_row(row)

    goal = [TOWN_HALL_TASK]
    check_against(body["plan"], expected_figures(plan, goal), plan["proof"])
    rows = {row["scenario_id"]: row for row in body["scenarios"]}
    for scenario in (same, fewer):  # the goal of custom_goal stays in these scenarios
        expected = expected_figures(scenario, goal)
        check_against(rows[scenario["scenario_id"]], expected, scenario["proof"])
    # Another strategy has no goal: every other figure still comes from its plan.
    expected = expected_figures(other, goal)
    row = rows[other["scenario_id"]]
    assert row["finished_count"] == expected.finished_count
    assert row["logins"] == expected.logins
    assert row["idle_builder_s"] == expected.idle_builder_s
    assert row["hero_unavailable_s"] == expected.hero_unavailable_s
    assert row["proof"] == other["proof"]

    # The real plan is reached by the town hall and does real work.
    assert body["plan"]["goal_finish"] is not None
    assert body["plan"]["finished_count"] > 0
    assert body["plan"]["hero_unavailable_s"] > 0

    response = v.client.post(f"{URL}/compare", json={"scenario_ids": [order[0], 999_999]})
    assert response.status_code == 404, response.text
    assert error_of(response)["code"] == "scenario_not_found"


# --------------------------------------------------------------------------- start_now


def test_start_now_starts_the_town_hall_at_the_first_instant_the_simulator_allows(
    tmp_path: Path,
) -> None:
    # One builder, free at now. The real plan upgrades the lab first, the town hall after it.
    one = manual_doc(builders=1)
    free = village(tmp_path / "free", manual=one)
    assert assignment_of(latest(free), TOWN_HALL_TASK)["start"] > HTTP_NOW
    scenario = create(
        free, "Hôtel de Ville maintenant", [{"type": "start_now", "task_id": TOWN_HALL_TASK}]
    )
    assert scenario["status"] == "ok", scenario
    town_hall = assignment_of(scenario, TOWN_HALL_TASK)
    assert town_hall["start"] == HTTP_NOW, town_hall
    assert town_hall["end"] == HTTP_NOW + HOUR, town_hall
    assert town_hall["lane"] == "builder"
    others = [a for a in planned(scenario) if a["lane"] == "builder" and a is not town_hall]
    assert all(a["start"] >= town_hall["end"] for a in others), others

    # The only builder runs a job of the export until twenty minutes after now: the town hall
    # starts when it ends (the real plan upgrades the lab there first).
    first_free = HTTP_NOW + 20 * MINUTE
    export = export_doc([running_job(PACK_CANNON, 2, first_free)])
    busy = village(tmp_path / "busy", manual=one, export=export)
    assert assignment_of(latest(busy), TOWN_HALL_TASK)["start"] > first_free
    scenario = create(
        busy, "Hôtel de Ville au plus tôt", [{"type": "start_now", "task_id": TOWN_HALL_TASK}]
    )
    assert scenario["status"] == "ok", scenario
    town_hall = assignment_of(scenario, TOWN_HALL_TASK)
    assert town_hall["start"] == first_free, town_hall
    assert town_hall["end"] == first_free + HOUR, town_hall
    assert town_hall["lane"] == "builder"


def test_start_now_is_infeasible_with_a_reason_when_the_resources_forbid_it(
    tmp_path: Path,
) -> None:
    # 1000 gold, no gold income: the town hall (5000 gold) can never start.
    v = village(tmp_path, manual=manual_doc(1000, gold_income=False))
    scenario = create(
        v, "Hôtel de Ville sans or", [{"type": "start_now", "task_id": TOWN_HALL_TASK}]
    )
    assert scenario["status"] == "infeasible", scenario
    assert scenario["reasons"], scenario
    assert TOWN_HALL_TASK not in {a["task_id"] for a in planned(scenario)}

    listed_status = {row["scenario_id"]: row["status"] for row in listed(v)}
    assert listed_status[scenario["scenario_id"]] == "infeasible"


# --------------------------------------------------------------------------- reserve_builder


def test_reserve_builder_keeps_the_reduced_builder_capacity_until_the_given_time(
    tmp_path: Path,
) -> None:
    # One builder runs a job of the export until twenty minutes after now.
    running_end = HTTP_NOW + 20 * MINUTE
    running_start = running_end - 1800  # the cannon from level 2 to 3 lasts 1800 s
    v = village(tmp_path, export=export_doc([running_job(PACK_CANNON, 2, running_end)]))
    until = HTTP_NOW + DAY

    def busy_builders(body: Mapping[str, Any], t: int) -> int:
        jobs = [(a["start"], a["end"]) for a in planned(body) if a["lane"] == "builder"]
        jobs.append((running_start, running_end))
        return sum(1 for start, end in jobs if start <= t < end)

    # Without the overlay the real plan uses more than one builder at once before `until`.
    plan = latest(v)
    instants = [a["start"] for a in planned(plan) if a["lane"] == "builder" and a["start"] < until]
    assert max(busy_builders(plan, t) for t in instants) > 1

    reserve = {"type": "reserve_builder", "count": 2, "until": until}
    scenario = create(v, "Deux constructeurs réservés", [reserve])
    assert scenario["status"] == "ok", scenario
    builder_rows = [a for a in planned(scenario) if a["lane"] == "builder"]
    early = [a for a in builder_rows if a["start"] < until]
    assert early, "the remaining builder still works before until"
    for assignment in early:
        assert busy_builders(scenario, assignment["start"]) <= BUILDERS - 2, assignment
    # Nothing starts on a builder while the running job holds the only builder left.
    assert all(a["start"] >= running_end for a in builder_rows), builder_rows


# --------------------------------------------------------------------------- absence


def test_an_absence_of_fourteen_days_has_no_start_inside_and_no_end_inside_under_strict(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)

    away = create(v, "Séjour de 14 jours", [ABSENCE])
    assert away["status"] == "ok", away
    for assignment in planned(away):
        assert not ABSENCE_START <= assignment["start"] < ABSENCE_END, assignment

    strict = create(v, "Séjour strict", [ABSENCE, {"type": "policy", "mode": "strict"}])
    assert strict["status"] == "ok", strict
    rows = planned(strict)
    for assignment in rows:
        assert not ABSENCE_START <= assignment["start"] < ABSENCE_END, assignment
        assert not ABSENCE_START <= assignment["end"] < ABSENCE_END, assignment
    # The hero upgrade (4 h after the lab) cannot end before the stay: work resumes after it.
    assert any(a["start"] >= ABSENCE_END for a in rows), rows


# --------------------------------------------------------------------------- reduce_play


def test_reduce_play_to_one_login_a_day_is_never_better_than_the_real_plan(
    tmp_path: Path,
) -> None:
    # One builder: the real plan logs in at each end, several times on the first day.
    v = village(tmp_path, manual=manual_doc(builders=1), plan=GOAL_PLAN_REQUEST)
    real_logins = login_starts(a["start"] for a in planned(latest(v)))
    assert max(Counter(local_day(t) for t in real_logins).values()) > 1

    scenario = create(
        v, "Une connexion par jour", [{"type": "reduce_play", "max_logins_per_day": 1}]
    )
    assert scenario["status"] == "ok", scenario
    assert planned(scenario), "one login a day still allows some work"

    logins = login_starts(a["start"] for a in planned(scenario))
    per_day = Counter(local_day(t) for t in logins)
    assert all(count <= 1 for count in per_day.values()), per_day

    body = compare(v, [scenario["scenario_id"]])
    real, reduced = body["plan"], body["scenarios"][0]
    check_row(real)
    check_row(reduced)
    assert reduced["logins"] == len(logins)
    assert reduced["idle_builder_s"] >= real["idle_builder_s"] or finish_rank(
        reduced["goal_finish"]
    ) >= finish_rank(real["goal_finish"]), (real, reduced)


# --------------------------------------------------------------------------- use_item


def test_use_item_without_its_rule_in_the_pack_is_unsupported_and_computes_nothing(
    tmp_path: Path,
) -> None:
    v = village(tmp_path / "items")
    # No rule `book_of_heroes` in the pack; any unsupported overlay voids the whole scenario.
    overlays = [
        {"type": "strategy", "key": "balanced"},
        {"type": "use_item", "item": "book_of_heroes", "task_id": TOWN_HALL_TASK, "at": HTTP_NOW},
    ]
    with counting_engines() as calls:
        scenario = create(v, "Livre absent", overlays)
    assert calls == [], f"an engine ran for an unsupported scenario: {calls}"
    assert scenario["status"] == "unsupported", scenario
    assert "unsupported_item" in [reason["code"] for reason in scenario["reasons"]], scenario
    assert scenario["assignments"] == []
    assert scenario["proof"] is None
    listed_status = {row["scenario_id"]: row["status"] for row in listed(v)}
    assert listed_status[scenario["scenario_id"]] == "unsupported"

    # The same item is supported as soon as the active pack has its rule, and only then.
    potion = {"type": "use_item", "item": "builder_potion", "task_id": None, "at": HTTP_NOW}
    supported = create(v, "Potion présente", [potion])
    assert supported["status"] != "unsupported", supported

    pack = copy.deepcopy(items_pack())
    pack["pack_id"] = "fictional-test-pack-items-no-potion"
    del pack["rules"]["builder_potion"]
    without = village(tmp_path / "no-potion", pack=pack)
    with counting_engines() as calls:
        scenario = create(without, "Potion absente", [potion])
    assert calls == [], f"an engine ran for an unsupported scenario: {calls}"
    assert scenario["status"] == "unsupported", scenario
    assert "unsupported_item" in [reason["code"] for reason in scenario["reasons"]], scenario
    assert scenario["assignments"] == []
    assert scenario["proof"] is None


# --------------------------------------------------------------------------- delete


def test_delete_removes_a_scenario_and_never_a_real_plan(tmp_path: Path) -> None:
    v = village(tmp_path)
    response = v.client.post(f"/api/villages/{VILLAGE}/plans", json=PLAN_REQUEST)
    assert response.status_code == 201, response.text
    plans = plan_ids_in_db(v)
    assert len(plans) == 2
    before = real_state(v)

    first = create(v, "Premier", [{"type": "policy", "mode": "strict"}])
    second = create(v, "Second", [{"type": "strategy", "key": "balanced"}])

    # Scenario ids may equal plan ids: deleting scenario N never deletes plan N.
    response = v.client.delete(f"{URL}/{first['scenario_id']}")
    assert response.status_code in (200, 204), response.text
    assert [row["scenario_id"] for row in listed(v)] == [second["scenario_id"]]
    assert real_state(v) == before
    assert plan_ids_in_db(v) == plans

    response = v.client.delete(f"{URL}/{first['scenario_id']}")
    assert response.status_code == 404, response.text
    assert error_of(response)["code"] == "scenario_not_found"

    response = v.client.delete(f"{URL}/{second['scenario_id']}")
    assert response.status_code in (200, 204), response.text
    assert listed(v) == []
    assert real_state(v) == before
    assert plan_ids_in_db(v) == plans
