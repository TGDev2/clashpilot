"""Acceptance tests for E29: the town hall does not wait for the lab, and the lab researches the
army composition first (SPEC ADR-CP5). Frozen with the task.

`POST /api/villages/{village_id}/plans` takes two plan options, orthogonal to `strategy`:

- `town_hall_skip_lanes` (list of lanes, empty by default): under the `town_hall_last` constraint
  (strategy `finish_th`) a town hall task requires every current-level task except those of the
  lanes named (`current_level_ids(tasks, skip_lanes)` of `planning/critical.py`);
- `composition_first` (boolean, false by default): on the lab, a research of the composition
  (`composition`, data ids) goes before any research outside it, through `Task.value` only.

Shapes that the task text leaves open are fixed here:

- `PlanOut` (the `POST plans` answer and `GET plans/latest`) carries `town_hall_skip_lanes` as a
  list of strings and `composition_first` as a boolean; the stored params (`plans.params`) carry
  both keys; `PlanParams.from_json` of a document without them gives an empty sequence and false;
- `apply_strategy` takes `town_hall_skip_lanes` as a keyword argument (empty by default);
- `422 unknown_lane` is `{"detail": {"code": "unknown_lane", ...}}`, as for `GET .../critical-path`;
- "ready, fundable and the lane free" is judged by `simulate`: the composition research, started
  at that instant on the lane slot of the research outside the composition, after every
  assignment of the plan that starts earlier, gives a plan `simulate` accepts;
- "today's assignments" are those the code gave before the options existed (`TODAY_*` below);
- "marked critical" is the boost `critical.prioritize_critical` gives a task with a float: with
  `critical_path` and `town_hall_skip_lanes: ["lab"]` no lab task gets one, so (composition first
  off) every lab task value is the one of the same request without `critical_path`.

Every test goes through HTTP, with engine `rollout` and profile `repro`, on the fictional pack
`backend/tests/fixtures/packs/fictional_pack_critical.json` and the export
`backend/tests/fixtures/exports/fictional_export_critical.json` (exported one hour before `NOW` =
2026-11-27T08:00:00Z). The E29 village changes them through the API only: two builders, every
resource plentiful, always available, no sleep; the two lab researches of the scout (levels 2 and 3)
last 40 and 60 days (manual corrections of the pack), so the lab holds 101 days of work, longer than
the 90-day horizon, as the real lab of ADR-CP5. The sapper's 1-day research stays outside the
composition `[SCOUT]`: today's plan starts it first while the scout's level 2 is ready, even with
the composition multiplier of a strategy, because the scout's levels end past the horizon.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.api.app import create_app
from clashpilot.planning.critical import current_level_ids
from clashpilot.planning.model import Assignment, Plan, Problem
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import effective_duration, simulate
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"
STRATEGIES_FILE = BACKEND / "src" / "clashpilot" / "planning" / "strategies.json"
# SHA-256 of the canonical JSON (sorted keys, no spaces) of strategies.json before E29.
STRATEGIES_DIGEST = "503be85f5b2b6b0d06fcdefbe14e533014ff0d6acda2d43bcad386c1faa72cc9"

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
LAB_TASKS = {task(SCOUT, 2), task(SCOUT, 3), task(SAPPER, 2)}
CURRENT_BUILDER = {*HERO_TASKS, task(BALLISTA, 2), task(CATAPULT, 2)}
CURRENT = CURRENT_BUILDER | LAB_TASKS
COMPOSITION = [SCOUT]

ENGINE = {"engine": "rollout", "profile": "repro"}
BALANCED = {**ENGINE, "strategy": "balanced", "composition": COMPOSITION}
FINISH_TH = {**ENGINE, "strategy": "finish_th", "composition": COMPOSITION}
PLAIN = {**ENGINE, "composition": COMPOSITION}
FIRST = {"composition_first": True}
SKIP_LAB = {"town_hall_skip_lanes": ["lab"]}
DEFAULTS = {"composition_first": False, "town_hall_skip_lanes": []}

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

# (task_id, lane, lane_slot, start - NOW, end - NOW, status) given by the code before E29.
Row = tuple[str, str, int, int, int, str]
TODAY_BUILDERS_FIRST: list[Row] = [
    ("9200001:0:2", "builder", 0, 0, 172800, "planned"),
    ("9200003:0:2", "builder", 0, 172800, 259200, "planned"),
    ("9200004:0:2", "builder", 1, 0, 518400, "planned"),
    ("9200004:0:3", "builder", 0, 518400, 1123200, "planned"),
    ("9200004:0:4", "builder", 0, 1123200, 1814400, "planned"),
    ("9200004:0:5", "builder", 0, 1814400, 2592000, "planned"),
    ("9200005:0:2", "builder", 0, 259200, 345600, "planned"),
    ("9200006:0:2", "builder", 0, 345600, 432001, "planned"),
    ("9200007:0:2", "lab", 0, 86400, 3542400, "planned"),
    ("9200007:0:3", "lab", 0, 3542400, 8726400, "planned"),
    ("9200008:0:2", "lab", 0, 0, 86400, "planned"),
]
TODAY_PLAIN = TODAY_BUILDERS_FIRST
TODAY_BALANCED = TODAY_BUILDERS_FIRST
TODAY_FINISH_TH: list[Row] = [
    ("9200004:0:2", "builder", 0, 0, 518400, "planned"),
    ("9200004:0:3", "builder", 0, 518400, 1123200, "planned"),
    ("9200004:0:4", "builder", 0, 1123200, 1814400, "planned"),
    ("9200004:0:5", "builder", 0, 1814400, 2592000, "planned"),
    ("9200005:0:2", "builder", 1, 0, 86400, "planned"),
    ("9200006:0:2", "builder", 1, 86400, 172801, "planned"),
    ("9200007:0:2", "lab", 0, 86400, 3542400, "planned"),
    ("9200007:0:3", "lab", 0, 3542400, 8726400, "planned"),
    ("9200008:0:2", "lab", 0, 0, 86400, "planned"),
]


# --------------------------------------------------------------------------- fixture documents


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CRITICAL_PACK.read_text(encoding="utf-8"))
    return loaded


def export_doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(CRITICAL_EXPORT.read_text(encoding="utf-8"))
    doc["timestamp"] = EXPORTED_AT
    for entry in doc["buildings"]:
        if entry["data"] == HUT:
            entry["cnt"] = 2
    return doc


def stock(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": NOW}


def manual_doc(elixir: int = 1_000_000) -> dict[str, Any]:
    return {
        "resources": {
            "gold": stock(1_000_000),
            "elixir": stock(elixir),
            "dark_elixir": stock(1_000_000),
        }
    }


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


def lab_village(tmp_path: Path) -> Village:
    """Two builders, plentiful resources, the scout's lab levels last 40 and 60 days."""
    clock = Clock(NOW)
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    v = Village(TestClient(create_app(settings, clock=clock)), clock, tmp_path)
    pack = pack_doc()
    ok(v.client.post("/api/gamedata/packs", json=pack), 200, 201)
    ok(v.client.put(f"/api/gamedata/packs/{pack['pack_id']}/active"))
    for level, days in ((2, 40), (3, 60)):
        ok(
            v.client.patch(
                f"/api/gamedata/packs/{pack['pack_id']}/items/{SCOUT}/levels/{level}",
                json={"duration_s": days * DAY},
            )
        )
    ok(
        v.client.post(
            "/api/villages/import",
            content=json.dumps(export_doc()).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
        200,
        201,
    )
    ok(v.client.put(f"/api/villages/{VILLAGE}/schedule", json=PROFILE))
    ok(v.client.put(f"/api/villages/{VILLAGE}/manual", json=manual_doc()))
    return v


def post_plan(v: Village, body: Mapping[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.post(f"/api/villages/{VILLAGE}/plans", json=dict(body)), 201)
    return plan


def latest(v: Village) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.get(f"/api/villages/{VILLAGE}/plans/latest"))
    return plan


def stored_params(v: Village) -> dict[int, dict[str, Any]]:
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        return {
            plan_id: json.loads(text)
            for plan_id, text in connection.execute("SELECT plan_id, params FROM plans")
        }


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


def lab_order(assignments: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        a["task_id"] for a in sorted(assignments, key=lambda a: a["start"]) if a["lane"] == "lab"
    ]


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


def town_hall_requires(problem: Problem) -> set[str]:
    (found,) = [t for t in problem.tasks if t.id == TOWN_HALL_TASK]
    return set(found.requires)


# --------------------------------------------------------------------------- composition check


def composition_overtaken(
    problem: Problem, plan: Plan, composition: Sequence[int]
) -> list[tuple[str, str, int]]:
    """`(outside, composition task, instant)` for each lab research outside the composition that
    starts at an instant where a research of the composition not yet started could start instead
    on the same slot: `simulate` accepts the plan made of every assignment starting earlier plus
    that research at that instant (requirements ended, funds, slot free)."""
    tasks = {t.id: t for t in problem.tasks}
    starts = {a.task_id: a.start for a in plan.assignments}
    candidates = [t for t in problem.tasks if t.lane == "lab" and t.data_id in composition]
    found: list[tuple[str, str, int]] = []
    for placed in plan.assignments:
        if placed.lane != "lab" or tasks[placed.task_id].data_id in composition:
            continue
        instant = placed.start
        earlier = tuple(a for a in plan.assignments if a.start < instant)
        for candidate in candidates:
            if starts.get(candidate.id, instant + 1) <= instant:
                continue
            moved = Assignment(
                task_id=candidate.id,
                lane="lab",
                lane_slot=placed.lane_slot,
                start=instant,
                end=instant + effective_duration(problem, candidate, instant),
            )
            trial = dataclasses.replace(plan, assignments=(*earlier, moved))
            if simulate(problem, trial).valid:
                found.append((placed.task_id, candidate.id, instant))
    return found


# --------------------------------------------------------------------------- criterion 1


def test_post_plans_accepts_both_options_with_their_defaults_and_echoes_them(
    tmp_path: Path,
) -> None:
    v = lab_village(tmp_path)

    without = post_plan(v, BALANCED)
    assert without["town_hall_skip_lanes"] == []
    assert without["composition_first"] is False
    assert latest(v)["town_hall_skip_lanes"] == []
    assert latest(v)["composition_first"] is False

    explicit = post_plan(v, {**BALANCED, **DEFAULTS})
    assert explicit["town_hall_skip_lanes"] == []
    assert explicit["composition_first"] is False

    both = post_plan(v, {**BALANCED, **FIRST, **SKIP_LAB})
    assert both["town_hall_skip_lanes"] == ["lab"]
    assert both["composition_first"] is True
    assert both["strategy"] == "balanced"
    assert both["composition"] == COMPOSITION
    stored = latest(v)
    assert stored["plan_id"] == both["plan_id"]
    assert stored["town_hall_skip_lanes"] == ["lab"]
    assert stored["composition_first"] is True

    # The resolved request is stored with the plan.
    params = stored_params(v)
    assert params[without["plan_id"]]["town_hall_skip_lanes"] == []
    assert params[without["plan_id"]]["composition_first"] is False
    assert params[both["plan_id"]]["town_hall_skip_lanes"] == ["lab"]
    assert params[both["plan_id"]]["composition_first"] is True


def test_plan_params_without_the_keys_read_back_with_their_defaults() -> None:
    from clashpilot.planning.service import PlanParams

    old = {
        "horizon_days": 90,
        "engine": "rollout",
        "profile": "repro",
        "strategy": "finish_th",
        "composition": COMPOSITION,
    }
    params = PlanParams.from_json(old)
    assert tuple(params.town_hall_skip_lanes) == ()
    assert params.composition_first is False
    params = PlanParams.from_json(
        {**old, "town_hall_skip_lanes": ["lab"], "composition_first": True}
    )
    assert tuple(params.town_hall_skip_lanes) == ("lab",)
    assert params.composition_first is True


def test_a_plan_stored_without_the_keys_is_replanned_with_their_defaults(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    plan = post_plan(v, {**FINISH_TH, **FIRST, **SKIP_LAB})

    # A plan stored before E29: its params have neither key.
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        (text,) = connection.execute(
            "SELECT params FROM plans WHERE plan_id = ?", (plan["plan_id"],)
        ).fetchone()
        params = json.loads(text)
        params.pop("town_hall_skip_lanes")
        params.pop("composition_first")
        connection.execute(
            "UPDATE plans SET params = ? WHERE plan_id = ?", (json.dumps(params), plan["plan_id"])
        )

    event = {"type": "resources_corrected", "resources": {"elixir": 900_000}}
    answer = ok(
        v.client.post(f"/api/villages/{VILLAGE}/plans/{plan['plan_id']}/events", json=event)
    )
    replanned = latest(v)
    assert replanned["plan_id"] == answer["plan_id"] != plan["plan_id"]
    assert replanned["town_hall_skip_lanes"] == []
    assert replanned["composition_first"] is False
    assert replanned["strategy"] == "finish_th"


@pytest.mark.parametrize("lanes", [["atelier_inexistant"], ["lab", "atelier_inexistant"]])
def test_a_lane_absent_from_the_problem_is_refused_with_unknown_lane(
    tmp_path: Path, lanes: list[str]
) -> None:
    v = lab_village(tmp_path)
    before = len(stored_params(v))
    response = v.client.post(
        f"/api/villages/{VILLAGE}/plans",
        json={**FINISH_TH, "town_hall_skip_lanes": lanes},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "unknown_lane"
    assert len(stored_params(v)) == before
    # Both lanes of the problem are accepted.
    for known in (["lab"], ["builder"], ["builder", "lab"]):
        post_plan(v, {**FINISH_TH, "town_hall_skip_lanes": known})


# --------------------------------------------------------------------------- criterion 2


def test_finish_th_skipping_the_lab_requires_the_current_level_outside_the_lab(
    tmp_path: Path,
) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        post_plan(v, PLAIN)
        plain = spy.calls[-1].problem
        skipping = post_plan(v, {**FINISH_TH, **SKIP_LAB})
        problem = spy.calls[-1].problem

    expected = set(current_level_ids(plain.tasks, ["lab"]))
    assert expected == CURRENT_BUILDER
    original = {t.id: set(t.requires) for t in plain.tasks}
    assert town_hall_requires(problem) == original[TOWN_HALL_TASK] | expected
    assert not town_hall_requires(problem) & LAB_TASKS
    # Only the town hall's requirements change.
    for t in problem.tasks:
        if t.id != TOWN_HALL_TASK:
            assert set(t.requires) == original[t.id], t.id

    # The town hall no longer waits for the 101 days of the lab: it is planned after the current
    # builder work, while the lab is still busy.
    assert simulate(problem, spy.calls[-1].plan).valid
    found = by_task(skipping)
    assert TOWN_HALL_TASK in found, skipping["assignments"]
    assert found[TOWN_HALL_TASK]["start"] >= max(found[name]["end"] for name in CURRENT_BUILDER)
    assert found[TOWN_HALL_TASK]["start"] < max(found[name]["end"] for name in LAB_TASKS)


@pytest.mark.parametrize("option", [{}, {"town_hall_skip_lanes": []}], ids=["absent", "empty"])
def test_finish_th_with_no_skipped_lane_requires_what_it_does_today(
    tmp_path: Path, option: Mapping[str, Any]
) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        post_plan(v, {**FINISH_TH, **option})
    problem = spy.calls[-1].problem
    # Today: every current-level task, the lab's included.
    assert town_hall_requires(problem) == CURRENT
    assert TOWN_HALL_TASK not in by_task(latest(v))


def test_apply_strategy_takes_the_skipped_lanes_as_a_keyword_with_an_empty_default(
    tmp_path: Path,
) -> None:
    from clashpilot.planning.strategies import apply_strategy, load_strategies

    v = lab_village(tmp_path)
    with spying() as spy:
        post_plan(v, ENGINE)
    plain = spy.calls[-1].problem
    finish_th = load_strategies()["finish_th"]

    today, _ = apply_strategy(plain, finish_th, COMPOSITION)
    empty, _ = apply_strategy(plain, finish_th, COMPOSITION, town_hall_skip_lanes=())
    skipping, _ = apply_strategy(plain, finish_th, COMPOSITION, town_hall_skip_lanes=("lab",))
    assert empty == today
    assert town_hall_requires(today) == CURRENT
    assert town_hall_requires(skipping) == set(current_level_ids(plain.tasks, ("lab",)))
    assert [dataclasses.replace(t, requires=()) for t in skipping.tasks] == [
        dataclasses.replace(t, requires=()) for t in today.tasks
    ]


# --------------------------------------------------------------------------- criteria 3 and 4


@pytest.mark.parametrize(
    "request_body", [PLAIN, BALANCED, FINISH_TH], ids=["no-strategy", "balanced", "finish_th"]
)
def test_without_the_option_a_research_outside_the_composition_overtakes_a_ready_one(
    tmp_path: Path, request_body: Mapping[str, Any]
) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        post_plan(v, request_body)
    call = spy.calls[-1]
    assert simulate(call.problem, call.plan).valid
    overtaken = composition_overtaken(call.problem, call.plan, COMPOSITION)
    assert (task(SAPPER, 2), task(SCOUT, 2), NOW) in overtaken, overtaken


@pytest.mark.parametrize(
    "request_body",
    [PLAIN, BALANCED, FINISH_TH, {**FINISH_TH, **SKIP_LAB}],
    ids=["no-strategy", "balanced", "finish_th", "finish_th-skip-lab"],
)
def test_with_composition_first_no_research_outside_it_overtakes_a_ready_one(
    tmp_path: Path, request_body: Mapping[str, Any]
) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        plan = post_plan(v, {**request_body, **FIRST})
    assert plan["composition_first"] is True
    call = spy.calls[-1]
    sim = simulate(call.problem, call.plan)
    assert sim.valid, sim.violations
    assert "heuristic_invalid_plan" not in plan["warnings"]
    assert composition_overtaken(call.problem, call.plan, COMPOSITION) == []
    # The answer is the engine's plan, and the lab starts with the composition.
    found = by_task(plan)
    assert {a.task_id: (a.start, a.end) for a in call.plan.assignments} == {
        name: (a["start"], a["end"]) for name, a in found.items() if a["status"] == "planned"
    }
    order = lab_order(plan["assignments"])
    assert order and order[0] == task(SCOUT, 2), order
    assert found[task(SCOUT, 2)]["start"] == NOW


def test_composition_first_changes_task_values_only(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        post_plan(v, BALANCED)
        post_plan(v, {**BALANCED, **FIRST})
    plain, first = spy.calls
    assert plain.objective == first.objective
    assert dataclasses.replace(plain.problem, tasks=()) == dataclasses.replace(
        first.problem, tasks=()
    )
    assert [dataclasses.replace(t, value=0.0) for t in plain.problem.tasks] == [
        dataclasses.replace(t, value=0.0) for t in first.problem.tasks
    ]
    raised = values(first.problem)
    assert raised[task(SCOUT, 2)] > values(plain.problem)[task(SCOUT, 2)]


# --------------------------------------------------------------------------- criterion 5


@pytest.mark.parametrize("option", [{}, DEFAULTS], ids=["absent", "defaults"])
@pytest.mark.parametrize(
    ("request_body", "today"),
    [(PLAIN, TODAY_PLAIN), (BALANCED, TODAY_BALANCED), (FINISH_TH, TODAY_FINISH_TH)],
    ids=["no-strategy", "balanced", "finish_th"],
)
def test_without_the_options_the_assignments_are_today_s(
    tmp_path: Path, request_body: Mapping[str, Any], today: list[Row], option: Mapping[str, Any]
) -> None:
    v = lab_village(tmp_path)
    plan = post_plan(v, {**request_body, **option})
    assert plan["now"] == NOW
    assert rows(plan) == today
    assert plan["composition_first"] is False
    assert plan["town_hall_skip_lanes"] == []


# --------------------------------------------------------------------------- criterion 6


def test_the_input_hash_changes_with_each_option(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    base = post_plan(v, BALANCED)["input_hash"]
    assert post_plan(v, {**BALANCED, **DEFAULTS})["input_hash"] == base
    first = post_plan(v, {**BALANCED, **FIRST})["input_hash"]
    skip = post_plan(v, {**BALANCED, **SKIP_LAB})["input_hash"]
    both = post_plan(v, {**BALANCED, **FIRST, **SKIP_LAB})["input_hash"]
    assert len({base, first, skip, both}) == 4


def test_a_replanning_keeps_both_options_of_the_plan(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        first = post_plan(v, {**FINISH_TH, **FIRST, **SKIP_LAB})
        boosted = values(spy.calls[-1].problem)
        event = {"type": "resources_corrected", "resources": {"elixir": 900_000}}
        answer = ok(
            v.client.post(f"/api/villages/{VILLAGE}/plans/{first['plan_id']}/events", json=event)
        )
        assert len(spy.calls) == 2, "the replanning did not run the engine"
        call = spy.calls[-1]

    now = latest(v)
    assert now["plan_id"] == answer["plan_id"] != first["plan_id"]
    assert now["composition_first"] is True
    assert now["town_hall_skip_lanes"] == ["lab"]
    assert now["strategy"] == "finish_th"
    assert now["composition"] == COMPOSITION
    assert stored_params(v)[now["plan_id"]]["composition_first"] is True
    assert stored_params(v)[now["plan_id"]]["town_hall_skip_lanes"] == ["lab"]
    # The engine saw the same options: town hall rule without the lab, composition first.
    assert town_hall_requires(call.problem) == CURRENT_BUILDER
    replanned = values(call.problem)
    for name in LAB_TASKS:
        assert replanned[name] == pytest.approx(boosted[name]), name
    assert composition_overtaken(call.problem, call.plan, COMPOSITION) == []
    assert lab_order(now["assignments"])[0] == task(SCOUT, 2)


def test_a_scenario_keeps_both_options_of_the_plan(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    default = post_plan(v, FINISH_TH)
    with spying() as spy:
        plan = post_plan(v, {**FINISH_TH, **FIRST, **SKIP_LAB})
        boosted = values(spy.calls[-1].problem)
        scenario = ok(
            v.client.post(
                f"/api/villages/{VILLAGE}/scenarios",
                json={"name": "Copie", "overlays": [], **ENGINE},
            ),
            201,
        )
        assert len(spy.calls) == 2, "the scenario did not run the engine"
        call = spy.calls[-1]

    assert scenario["status"] == "ok", scenario
    assert values(call.problem) == pytest.approx(boosted)
    assert town_hall_requires(call.problem) == CURRENT_BUILDER
    assert composition_overtaken(call.problem, call.plan, COMPOSITION) == []
    assert lab_order(scenario["assignments"]) == lab_order(plan["assignments"])
    assert lab_order(scenario["assignments"]) != lab_order(default["assignments"])


# --------------------------------------------------------------------------- criterion 7


def test_with_critical_path_and_the_lab_skipped_no_lab_task_is_critical(tmp_path: Path) -> None:
    v = lab_village(tmp_path)
    # The fixture's lab is the critical lane when it is not skipped.
    path = ok(v.client.get(f"/api/villages/{VILLAGE}/critical-path"))
    assert path["bound"] == {"kind": "work", "lane": "lab"}, path["bound"]
    with spying() as spy:
        post_plan(v, FINISH_TH)
        unskipped_plain = values(spy.calls[-1].problem)
        post_plan(v, {**FINISH_TH, "critical_path": True})
        unskipped_critical = values(spy.calls[-1].problem)
        post_plan(v, {**FINISH_TH, **SKIP_LAB})
        skipped_plain = values(spy.calls[-1].problem)
        post_plan(v, {**FINISH_TH, **SKIP_LAB, "critical_path": True})
        skipped_critical = values(spy.calls[-1].problem)
    # Without the skip the lab tasks get the critical boost, with it none does.
    for name in LAB_TASKS:
        assert unskipped_critical[name] > unskipped_plain[name], name
        assert skipped_critical[name] == pytest.approx(skipped_plain[name]), name
    # The builder lane still gets its criticality.
    assert any(skipped_critical[name] > skipped_plain[name] for name in CURRENT_BUILDER)


def test_with_critical_path_and_the_lab_skipped_the_lab_follows_composition_first(
    tmp_path: Path,
) -> None:
    v = lab_village(tmp_path)
    with spying() as spy:
        without = post_plan(v, {**FINISH_TH, **SKIP_LAB, "critical_path": True})
        call_without = spy.calls[-1]
        plan = post_plan(v, {**FINISH_TH, **SKIP_LAB, **FIRST, "critical_path": True})
        call = spy.calls[-1]
    assert plan["critical_path"] is True and plan["composition_first"] is True
    assert plan["town_hall_skip_lanes"] == ["lab"]
    assert without["critical_path"] is True and without["composition_first"] is False

    assert (task(SAPPER, 2), task(SCOUT, 2), NOW) in composition_overtaken(
        call_without.problem, call_without.plan, COMPOSITION
    )
    sim = simulate(call.problem, call.plan)
    assert sim.valid, sim.violations
    assert composition_overtaken(call.problem, call.plan, COMPOSITION) == []
    assert lab_order(plan["assignments"])[0] == task(SCOUT, 2)
    # The town hall still waits for the current builder work only.
    assert town_hall_requires(call.problem) == CURRENT_BUILDER


# --------------------------------------------------------------------------- criterion 8


def test_the_ten_strategies_and_the_strategies_file_are_unchanged(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=Clock(NOW)))
    strategies = ok(client.get("/api/meta/strategies"))["strategies"]
    keys = [row["key"] for row in strategies]
    assert len(keys) == len(set(keys)) == 10
    assert set(keys) == TEN_STRATEGIES

    content = json.loads(STRATEGIES_FILE.read_text(encoding="utf-8"))
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == STRATEGIES_DIGEST
