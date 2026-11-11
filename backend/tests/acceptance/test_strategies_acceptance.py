"""Acceptance tests for E16: progression strategies and custom goals.

SPEC section 4 (Objective, Simulator: `hero_unavailable`, `Problem.goal`,
`Problem.hero_blackouts`) and section 7 (budget: `TEST_CONFIG`, at most 40 tasks per instance).

Frozen with the task. A strategy is objective weights, value multipliers by category and by tag and
optional constraints: it never ranks tasks, the order always comes out of the engine and the
simulation. The pure tests run the engine `rollout` with `TEST_CONFIG` (profile `repro`) on small
hand-built problems; the HTTP tests use the fictional pack
`backend/tests/fixtures/packs/fictional_pack_items.json`, engine `rollout`, profile `repro` and the
fields of `TEST_CONFIG` as `Settings.planner_overrides`.

Shapes that the task text leaves open are fixed here:

- `backend/src/clashpilot/planning/strategies.json` is a JSON object whose member `strategies` maps
  each of the ten keys to its entry. An entry holds at least `label` (a non-empty French label) and
  `weights` (a mapping of `Objective` field names to numbers; a missing name keeps the `Objective`
  default). Everything else in an entry (multipliers by category and by tag, the composition
  factor, constraints) is the implementation's data: the tests only copy entries whole.
- `clashpilot.planning.strategies.load_strategies(path=None)` returns a mapping from key to
  strategy, read from `path`, or from the packaged `strategies.json` without it.
  `apply_strategy(problem, strategy, composition=(), *, goal=None, war_windows=())` takes a strategy
  of that mapping, an iterable of `data_id`, a `Goal` or None and a sequence of `(start, end)`
  epochs, and returns `(Problem, Objective)`. The objective is `Objective(**weights)`. The problem
  differs from the given one only by task `value`, task `requires`, `goal` and `hero_blackouts`:
  same tasks, lanes, durations, costs, resources, frozen jobs and calendar.
  `custom_goal` with a goal sets `Problem.goal` to it and has a positive `goal` weight; with any
  other strategy the score does not depend on the goal. `war_cwl` sets `hero_blackouts` to the war
  windows. A task whose `data_id` is in the composition gets a higher value.
- The ranking fixtures leave a margin of about ten percent: a favoured task is worth 0.9 times its
  competitors (so a multiplier from 1.2 up is safe). Within one fixture every competing task has
  the same duration, so the engine orders them by value.
- `simulate` reports `hero_unavailable` (with the task id) for a plan assignment whose task has the
  tag `hero` and whose `[start, end)` overlaps a window of `hero_blackouts` by at least one second;
  a frozen job never produces it.
- `POST /api/villages/{id}/plans` accepts `strategy` (a key), `composition` (a list of `data_id`),
  `goal` (`{data_id, level}` or null) and `war_windows` (a list of `{start, end}` UTC epochs). The
  answer and `GET plans/latest` carry these four as top-level fields, as sent. A replanning keeps
  them from the stored params and applies the strategy again. An unknown strategy answers 422
  `unknown_strategy`, a goal whose level has no row in the active pack answers 422 `invalid_goal`;
  a refused request stores nothing.
- `GET /api/meta/strategies` answers `{"strategies": [{"key", "label", "weights"}, ...]}`; `weights`
  stays a free mapping in the OpenAPI schema (the frozen E05 guard refuses a `logins` property).
- A replanning without `priority_changed` gives the engine the strategy's weights; only
  `perturbation` may differ (E11 sets 0.5).

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the feature does not exist.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
TESTS = BACKEND / "tests"
STRATEGIES_JSON = BACKEND / "src" / "clashpilot" / "planning" / "strategies.json"
ITEMS_PACK = TESTS / "fixtures" / "packs" / "fictional_pack_items.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

KEYS = frozenset(
    {
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
)
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

# --------------------------------------------------------------------------- feature modules


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e16_acceptance_support_problems"
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


def strategies_module() -> ModuleType:
    import clashpilot.planning.strategies as module

    return module


def load(path: Path | None = None) -> Mapping[str, Any]:
    module = strategies_module()
    loaded: Mapping[str, Any] = (
        module.load_strategies() if path is None else module.load_strategies(path)
    )
    return loaded


def apply(
    problem: Any,
    key: str,
    *,
    strategies: Mapping[str, Any] | None = None,
    composition: Iterable[int] = (),
    goal: Any = None,
    war_windows: Sequence[tuple[int, int]] = (),
) -> tuple[Any, Any]:
    table = load() if strategies is None else strategies
    result = strategies_module().apply_strategy(
        problem, table[key], tuple(composition), goal=goal, war_windows=tuple(war_windows)
    )
    applied, objective = result
    return applied, objective


def planners() -> dict[str, Any]:
    import clashpilot.planning  # noqa: F401  (registers the engines)
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def objective_class() -> Any:
    from clashpilot.planning.objective import Objective

    return Objective


def judge(problem: Any, plan: Any, reference: Any = None) -> Any:
    from clashpilot.planning.simulate import simulate

    return simulate(problem, plan, reference)


def score(sim: Any, objective: Any, problem: Any) -> float:
    from clashpilot.planning.objective import score as score_of

    return float(score_of(sim, objective, problem))


def default_objective() -> Any:
    from clashpilot.planning.service import DEFAULT_OBJECTIVE

    return DEFAULT_OBJECTIVE


# --------------------------------------------------------------------------- pure helpers

NOW = 1_781_078_400  # 2026-06-10T08:00:00Z, the NOW of support/problems.py


def task(task_id: str, **fields: Any) -> Any:
    made: Any = support().make_task(task_id, **fields)
    return made


def problem(tasks: Iterable[Any], **overrides: Any) -> Any:
    """Seven days, no sleep, policy `progress`, gold and elixir in plenty unless overridden."""
    resources = model().ResourceModel(
        initial={"gold": 1_000_000, "elixir": 1_000_000},
        cap={"gold": 2_000_000, "elixir": 2_000_000},
        income_per_hour={"gold": 1000, "elixir": 1000},
    )
    fields: dict[str, Any] = {
        "tasks": tuple(tasks),
        "lanes": {"builder": 1, "lab": 1},
        "resources": resources,
    }
    fields.update(overrides)
    made: Any = support().make_problem(**fields)
    return made


def solve(prob: Any, objective: Any) -> Any:
    plan = planners()["rollout"].solve(prob, objective, support().TEST_CONFIG)
    sim = judge(prob, plan)
    assert sim.valid, [dataclasses.astuple(v) for v in sim.violations]
    return plan


@dataclasses.dataclass(frozen=True)
class Run:
    problem: Any
    objective: Any
    plan: Any
    sim: Any

    def end(self, task_id: str) -> int:
        ends: Mapping[str, int] = self.sim.metrics.ends
        assert task_id in ends, f"{task_id} is not planned"
        return int(ends[task_id])

    def start(self, task_id: str) -> int:
        for assignment in self.plan.assignments:
            if assignment.task_id == task_id:
                return int(assignment.start)
        raise AssertionError(f"{task_id} is not planned")

    def assignment(self, task_id: str) -> Any:
        for assignment in self.plan.assignments:
            if assignment.task_id == task_id:
                return assignment
        raise AssertionError(f"{task_id} is not planned")


def run(prob: Any, key: str, **options: Any) -> Run:
    """Apply the strategy `key`, solve with rollout, judge the plan on the applied problem."""
    applied, objective = apply(prob, key, **options)
    plan = solve(applied, objective)
    return Run(applied, objective, plan, judge(applied, plan))


def mean(values: Iterable[int]) -> float:
    items = list(values)
    assert items
    return sum(items) / len(items)


def overlaps(start: int, end: int, windows: Iterable[tuple[int, int]]) -> bool:
    return any(min(end, high) - max(start, low) > 0 for low, high in windows)


# --------------------------------------------------------------------------- pure fixtures

# Ranking fixture: one builder, every builder task lasts 2 h. The town hall and the hero are worth
# 0.9 times the others, so a neutral strategy does them last.
TH = "9100:0:2"
HERO = "9101:0:2"
DEFENSES = ("9102:0:2", "9103:0:2")
ARMY = ("9104:0:2", "9105:0:2")
RESOURCE = "9106:0:2"
LAB_TROOP = "9107:0:2"
LAB_SPELL = "9108:0:2"


def ranking_problem() -> Any:
    two_hours = 2 * HOUR
    builder: dict[str, Any] = {"duration_s": two_hours, "cost": {"gold": 1000}}
    lab: dict[str, Any] = {"lane": "lab", "duration_s": 3 * HOUR, "cost": {"elixir": 1000}}
    return problem(
        [
            task(TH, value=1.8, category="town_hall", tags=("building", "town_hall"), **builder),
            task(HERO, value=1.8, category="hero", tags=("hero",), **builder),
            *(
                task(d, value=2.0, category="defense", tags=("building",), **builder)
                for d in DEFENSES
            ),
            *(task(a, value=2.0, category="army", tags=("building",), **builder) for a in ARMY),
            task(RESOURCE, value=2.0, category="resource", tags=("building",), **builder),
            task(LAB_TROOP, value=3.0, category="troop", tags=("troop",), **lab),
            task(LAB_SPELL, value=3.0, category="spell", tags=("spell",), **lab),
        ]
    )


# Composition fixture: one lab, two troop researches of 4 h; the composed troop is worth 0.9 times
# the other one.
COMPOSED = 9201
OTHER_TROOP = 9202


def research_problem() -> Any:
    research: dict[str, Any] = {
        "lane": "lab",
        "duration_s": 4 * HOUR,
        "cost": {"elixir": 1000},
        "category": "troop",
        "tags": ("troop",),
    }
    return problem(
        [
            task(f"{COMPOSED}:0:2", value=3.6, **research),
            task(f"{OTHER_TROOP}:0:2", value=4.0, **research),
        ]
    )


# Hero fixture: two builders; the hero upgrade is by far the best task, a war window covers its
# natural slot.
WAR_HERO = "9301:0:2"
WAR_WINDOWS = ((NOW + HOUR, NOW + 6 * HOUR), (NOW + 2 * DAY, NOW + 2 * DAY + 5 * HOUR))


def war_problem() -> Any:
    return problem(
        [
            task(WAR_HERO, duration_s=3 * HOUR, value=30.0, category="hero", tags=("hero",)),
            task("9302:0:2", duration_s=2 * HOUR, category="defense"),
            task("9303:0:2", duration_s=4 * HOUR, category="defense"),
            task("9304:0:2", duration_s=HOUR, category="army"),
        ],
        lanes={"builder": 2, "lab": 1},
    )


# Finish-the-town-hall fixture: two builders and a lab, everything affordable; the town hall is
# the best task, and one task of the next level needs it.
FINISH_TH = "9401:0:2"
NEXT_LEVEL = "9405:0:1"
CURRENT_LEVEL = ("9402:0:2", "9403:0:2", "9404:0:2", "9406:0:2")


def finish_th_problem() -> Any:
    return problem(
        [
            task(
                FINISH_TH,
                duration_s=3 * HOUR,
                value=40.0,
                category="town_hall",
                tags=("building", "town_hall"),
            ),
            task("9402:0:2", duration_s=2 * HOUR, category="defense"),
            task("9403:0:2", duration_s=4 * HOUR, category="defense"),
            task("9404:0:2", duration_s=HOUR, category="army"),
            task(NEXT_LEVEL, duration_s=2 * HOUR, category="defense", requires=(FINISH_TH,)),
            task(
                "9406:0:2",
                lane="lab",
                duration_s=2 * HOUR,
                cost={"elixir": 1000},
                category="troop",
                tags=("troop",),
            ),
        ],
        lanes={"builder": 2, "lab": 1},
    )


# Goal fixture: one builder; the goal is a chain of two low-value upgrades.
GOAL_TASKS = ("9501:0:2", "9501:0:3")


def goal_problem() -> Any:
    return problem(
        [
            task(GOAL_TASKS[0], duration_s=2 * HOUR, value=0.5, category="defense"),
            task(
                GOAL_TASKS[1],
                duration_s=2 * HOUR,
                value=0.5,
                category="defense",
                requires=(GOAL_TASKS[0],),
            ),
            *(
                task(f"95{n:02d}:0:2", duration_s=2 * HOUR, value=3.0, category="army")
                for n in (2, 3, 4, 5)
            ),
        ]
    )


def goal_of(task_ids: Iterable[str]) -> Any:
    return model().Goal(task_ids=frozenset(task_ids))


# Play fixture: two builders and a lab, uneven durations, a small gold storage filling fast.
def play_problem() -> Any:
    durations = (1, 2, 3, 5, 7, 11, 13, 6)
    builders = [
        task(
            f"96{n:02d}:0:2",
            duration_s=hours * HOUR,
            cost={"gold": 600 * hours},
            value=float(hours),
            category=("defense", "army", "resource", "defense")[n % 4],
        )
        for n, hours in enumerate(durations)
    ]
    labs = [
        task(
            f"97{n:02d}:0:2",
            lane="lab",
            duration_s=hours * HOUR,
            cost={"elixir": 500 * hours},
            value=float(hours),
            category="troop",
            tags=("troop",),
        )
        for n, hours in enumerate((4, 6, 9))
    ]
    resources = model().ResourceModel(
        initial={"gold": 4000, "elixir": 4000},
        cap={"gold": 8000, "elixir": 8000},
        income_per_hour={"gold": 2500, "elixir": 1500},
    )
    calendar = support().make_calendar(
        sleep=[(NOW + 15 * HOUR + day * DAY, NOW + 23 * HOUR + day * DAY) for day in range(7)]
    )
    return problem(
        builders + labs,
        lanes={"builder": 2, "lab": 1},
        resources=resources,
        calendar=calendar,
    )


# --------------------------------------------------------------------------- strategies file


def test_strategies_file_holds_the_ten_strategies_with_a_label_and_weights() -> None:
    assert STRATEGIES_JSON.is_file(), f"{STRATEGIES_JSON} is missing"
    content = json.loads(STRATEGIES_JSON.read_text(encoding="utf-8"))
    entries = content["strategies"]
    assert set(entries) == KEYS
    for key, entry in entries.items():
        assert isinstance(entry["label"], str) and entry["label"].strip(), key
        assert set(entry["weights"]) <= set(OBJECTIVE_FIELDS), key
        assert all(isinstance(v, int | float) for v in entry["weights"].values()), key

    table = load()
    assert set(table) == KEYS
    assert set(load(STRATEGIES_JSON)) == KEYS


@pytest.mark.parametrize("key", sorted(KEYS))
def test_apply_strategy_changes_only_values_weights_and_constraints(key: str) -> None:
    prob = ranking_problem()
    applied, objective = apply(
        prob,
        key,
        composition=(9104, 9107),
        goal=goal_of([TH]),
        war_windows=WAR_WINDOWS,
    )
    content = json.loads(STRATEGIES_JSON.read_text(encoding="utf-8"))
    assert objective == objective_class()(**content["strategies"][key]["weights"])

    assert [t.id for t in applied.tasks] == [t.id for t in prob.tasks]
    for before, after in zip(prob.tasks, applied.tasks, strict=True):
        for name in ("data_id", "instance", "to_level", "lane", "duration_s", "category", "tags"):
            assert getattr(after, name) == getattr(before, name), (key, before.id, name)
        assert dict(after.cost) == dict(before.cost), (key, before.id)
        assert after.value >= 0, (key, before.id)
    for name in ("now", "horizon_end", "frozen", "resources", "calendar", "policy", "modifiers"):
        assert getattr(applied, name) == getattr(prob, name), (key, name)
    assert dict(applied.lanes) == dict(prob.lanes), key


# --------------------------------------------------------------------------- rankings


def test_th_rush_finishes_the_town_hall_earlier_than_balanced() -> None:
    prob = ranking_problem()
    balanced = run(prob, "balanced")
    rush = run(prob, "th_rush")
    assert rush.end(TH) < balanced.end(TH)


def test_heroes_finishes_the_hero_upgrades_earlier_than_balanced() -> None:
    prob = ranking_problem()
    balanced = run(prob, "balanced")
    heroes = run(prob, "heroes")
    assert heroes.end(HERO) < balanced.end(HERO)


def test_defense_finishes_the_defenses_earlier_on_average_than_offense() -> None:
    prob = ranking_problem()
    defense = run(prob, "defense")
    offense = run(prob, "offense")
    assert mean(defense.end(d) for d in DEFENSES) < mean(offense.end(d) for d in DEFENSES)


def test_low_play_logs_in_at_most_as_often_as_balanced() -> None:
    prob = play_problem()
    balanced = run(prob, "balanced")
    low = run(prob, "low_play")
    assert low.sim.metrics.logins <= balanced.sim.metrics.logins


def test_resources_loses_at_most_the_spill_of_balanced() -> None:
    prob = play_problem()
    balanced = run(prob, "balanced")
    thrifty = run(prob, "resources")
    assert sum(thrifty.sim.metrics.spill.values()) <= sum(balanced.sim.metrics.spill.values())


def test_a_composed_troop_finishes_its_research_earlier_than_without_composition() -> None:
    prob = research_problem()
    composed = f"{COMPOSED}:0:2"
    without = run(prob, "balanced")
    with_composition = run(prob, "balanced", composition=(COMPOSED,))
    assert with_composition.end(composed) < without.end(composed)


# --------------------------------------------------------------------------- war windows


def plan_of(prob: Any, rows: Iterable[tuple[str, str, int, int]]) -> Any:
    """A plan from `(task_id, lane, lane_slot, start)`, ends at start + duration."""
    m = model()
    durations = {t.id: t.duration_s for t in prob.tasks}
    assignments = tuple(
        m.Assignment(name, lane, slot, start, start + durations[name])
        for name, lane, slot, start in rows
    )
    return m.Plan(assignments=assignments, engine="test", proof=m.Proof("heuristic", 0.0))


def codes(sim: Any) -> list[str]:
    return [violation.code for violation in sim.violations]


def test_simulate_refuses_a_hero_upgrade_running_during_a_war_window() -> None:
    window = (NOW + HOUR, NOW + 3 * HOUR)
    prob = dataclasses.replace(war_problem(), hero_blackouts=(window,))

    inside = judge(prob, plan_of(prob, [(WAR_HERO, "builder", 0, NOW)]))
    assert not inside.valid
    assert "hero_unavailable" in codes(inside)
    assert any(v.code == "hero_unavailable" and v.task_id == WAR_HERO for v in inside.violations), (
        inside.violations
    )

    straddling = judge(prob, plan_of(prob, [(WAR_HERO, "builder", 0, NOW + 2 * HOUR)]))
    assert "hero_unavailable" in codes(straddling)

    after = judge(prob, plan_of(prob, [(WAR_HERO, "builder", 0, NOW + 3 * HOUR)]))
    assert after.valid, after.violations

    # A hero upgrade ending exactly when the window opens does not overlap it.
    early = dataclasses.replace(prob, hero_blackouts=((NOW + 3 * HOUR, NOW + 5 * HOUR),))
    touching = judge(early, plan_of(early, [(WAR_HERO, "builder", 0, NOW)]))
    assert touching.valid, touching.violations

    # Any other task may run during the window.
    others = judge(
        prob, plan_of(prob, [("9302:0:2", "builder", 0, NOW), ("9303:0:2", "builder", 1, NOW)])
    )
    assert others.valid, others.violations

    # Without windows the same hero upgrade is fine.
    free = dataclasses.replace(prob, hero_blackouts=())
    assert judge(free, plan_of(free, [(WAR_HERO, "builder", 0, NOW)])).valid


def test_simulate_never_blames_a_running_hero_job_for_a_war_window() -> None:
    m = model()
    running = m.FrozenJob(
        task_id="9399:0:2", lane="builder", lane_slot=0, start=NOW - HOUR, end=NOW + 5 * HOUR
    )
    prob = dataclasses.replace(
        war_problem(),
        frozen=(running,),
        hero_blackouts=((NOW + HOUR, NOW + 3 * HOUR),),
    )
    sim = judge(prob, plan_of(prob, [("9302:0:2", "builder", 1, NOW)]))
    assert sim.valid, sim.violations
    assert "hero_unavailable" not in codes(sim)


def test_war_cwl_keeps_every_hero_upgrade_out_of_the_war_windows() -> None:
    prob = war_problem()
    # Without war windows the hero upgrade, the best task, runs during the first window.
    neutral = run(prob, "balanced")
    hero = neutral.assignment(WAR_HERO)
    assert overlaps(hero.start, hero.end, WAR_WINDOWS)

    war = run(prob, "war_cwl", war_windows=WAR_WINDOWS)
    assert tuple(tuple(window) for window in war.problem.hero_blackouts) == WAR_WINDOWS
    assert war.sim.valid
    assert "hero_unavailable" not in codes(war.sim)
    tags = {t.id: t.tags for t in war.problem.tasks}
    planned_heroes = [a for a in war.plan.assignments if "hero" in tags[a.task_id]]
    assert [a.task_id for a in planned_heroes] == [WAR_HERO]
    for assignment in planned_heroes:
        assert not overlaps(assignment.start, assignment.end, WAR_WINDOWS), assignment


# --------------------------------------------------------------------------- finish the town hall


def test_finish_th_starts_no_town_hall_upgrade_before_the_current_level_is_done() -> None:
    prob = finish_th_problem()
    # A neutral strategy starts the town hall, the best task, before the current level is done.
    neutral = run(prob, "balanced")
    current_ends = [neutral.end(name) for name in CURRENT_LEVEL]
    assert neutral.start(FINISH_TH) < max(current_ends)

    finish = run(prob, "finish_th")
    town_halls = [t.id for t in prob.tasks if "town_hall" in t.tags]
    assert town_halls == [FINISH_TH]
    last_current = max(finish.end(name) for name in CURRENT_LEVEL)
    for name in town_halls:
        assert finish.start(name) >= last_current, (name, finish.plan.assignments)
    # It is affordable: the town hall and the next level are planned within the horizon.
    assert finish.end(FINISH_TH) <= prob.horizon_end
    assert finish.end(NEXT_LEVEL) <= prob.horizon_end


# --------------------------------------------------------------------------- custom goal


def test_only_custom_goal_puts_the_goal_term_in_the_score() -> None:
    prob = goal_problem()
    goal = goal_of(GOAL_TASKS)
    table = load()
    for key in sorted(KEYS):
        applied, objective = apply(prob, key, strategies=table, goal=goal)
        plan = solve(applied, objective)
        sim = judge(applied, plan)
        with_goal = score(sim, objective, applied)
        without_goal = score(sim, objective, dataclasses.replace(applied, goal=None))
        if key == "custom_goal":
            assert applied.goal == goal
            assert objective.goal > 0
            assert with_goal != without_goal
        else:
            assert with_goal == without_goal, key


def test_custom_goal_reaches_the_goal_no_later_than_the_end_of_the_balanced_plan() -> None:
    prob = goal_problem()
    balanced = run(prob, "balanced")
    custom = run(prob, "custom_goal", goal=goal_of(GOAL_TASKS))
    assert custom.problem.goal == goal_of(GOAL_TASKS)
    reached = max(custom.end(name) for name in GOAL_TASKS)
    assert reached <= prob.horizon_end
    assert reached <= max(balanced.sim.metrics.ends.values())
    assert reached <= max(balanced.end(name) for name in GOAL_TASKS)


# --------------------------------------------------------------------------- weights from the file


def test_another_strategies_file_changes_the_behaviour_without_code(tmp_path: Path) -> None:
    content = json.loads(STRATEGIES_JSON.read_text(encoding="utf-8"))
    prob = ranking_problem()
    default_balanced = run(prob, "balanced")
    default_rush = run(prob, "th_rush")

    # `balanced` made of the `th_rush` entry: the town hall goes as early as with th_rush.
    swapped = json.loads(json.dumps(content))
    swapped["strategies"]["balanced"] = content["strategies"]["th_rush"]
    path = tmp_path / "swapped_strategies.json"
    path.write_text(json.dumps(swapped), encoding="utf-8")
    table = load(path)
    assert set(table) == KEYS
    applied, objective = apply(prob, "balanced", strategies=table)
    plan = solve(applied, objective)
    custom = Run(applied, objective, plan, judge(applied, plan))
    assert custom.end(TH) == default_rush.end(TH)
    assert custom.end(TH) < default_balanced.end(TH)

    # New weights are read as they are written.
    reweighted = json.loads(json.dumps(content))
    reweighted["strategies"]["balanced"]["weights"] = {"value": 1.0, "idle": 7.0, "night": 0.0}
    path = tmp_path / "reweighted_strategies.json"
    path.write_text(json.dumps(reweighted), encoding="utf-8")
    _, objective = apply(prob, "balanced", strategies=load(path))
    assert objective == objective_class()(value=1.0, idle=7.0, night=0.0)
    assert objective != apply(prob, "balanced")[1]


# --------------------------------------------------------------------------- HTTP

HTTP_PARIS = "Europe/Paris"
# 2026-11-09T09:00 in Paris (CET, UTC+1): a whole day of play before the 23:00 sleep.
HTTP_NOW = int(datetime.datetime(2026, 11, 9, 8, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = HTTP_NOW - HOUR
TAG = "#FICTIF16"
VILLAGE = "FICTIF16"

# Ids of the fictional items pack.
PACK_TOWN_HALL = 9000001
PACK_HUT = 9000002
PACK_LAB = 9000003
PACK_CANNON = 9000004
PACK_HERO = 9000006
PACK_TROOP = 9000007

REPRO = {"horizon_days": 7, "engine": "rollout", "profile": "repro"}
HTTP_WAR_WINDOWS = [
    {"start": HTTP_NOW + DAY, "end": HTTP_NOW + DAY + 4 * HOUR},
    {"start": HTTP_NOW + 3 * DAY, "end": HTTP_NOW + 3 * DAY + 2 * HOUR},
]
STRATEGY_REQUEST = {
    **REPRO,
    "strategy": "th_rush",
    "composition": [PACK_TROOP],
    "goal": {"data_id": PACK_TOWN_HALL, "level": 3},
    "war_windows": HTTP_WAR_WINDOWS,
}


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def set(self, now: int) -> None:
        assert now >= self.now, "the clock never goes back"
        self.now = now


@dataclasses.dataclass
class Village:
    client: TestClient
    clock: Clock


def export_doc() -> dict[str, Any]:
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": [
            {"data": PACK_TOWN_HALL, "lvl": 2},
            {"data": PACK_HUT, "lvl": 1},
            {"data": PACK_HUT, "lvl": 1},
            {"data": PACK_LAB, "lvl": 1},
            {"data": PACK_CANNON, "lvl": 2},
        ],
        "units": [{"data": PACK_TROOP, "lvl": 1}],
        "heroes": [{"data": PACK_HERO, "lvl": 1}],
    }


def resource(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": EXPORTED_AT}


def manual_doc() -> dict[str, Any]:
    rate = {"low": 500, "expected": 1000, "high": 1500}
    return {
        "resources": {
            "gold": resource(100_000),
            "elixir": resource(50_000),
            "dark_elixir": resource(5_000),
        },
        "builders_total": 3,
        "farm_rate": {"gold": rate, "elixir": rate, "dark_elixir": rate},
        "magic_items": {},
    }


def profile_doc() -> dict[str, Any]:
    return {
        "tz": HTTP_PARIS,
        "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def village(tmp_path: Path) -> Village:
    clock = Clock(HTTP_NOW)
    settings = Settings(
        data_dir=tmp_path,
        frontend_dist=tmp_path / "no-dist",
        planner_overrides=dataclasses.asdict(support().TEST_CONFIG),
    )
    client = TestClient(create_app(settings, clock=clock))
    pack = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    response = client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/villages/import",
        content=json.dumps(export_doc()).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE
    response = client.put(f"/api/villages/{VILLAGE}/manual", json=manual_doc())
    assert response.status_code == 200, response.text
    response = client.put(f"/api/villages/{VILLAGE}/schedule", json=profile_doc())
    assert response.status_code == 200, response.text
    return Village(client, clock)


def latest(v: Village) -> dict[str, Any]:
    response = v.client.get(f"/api/villages/{VILLAGE}/plans/latest")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def error_of(response: Any) -> dict[str, Any]:
    detail: dict[str, Any] = response.json()["detail"]
    assert isinstance(detail.get("message"), str) and detail["message"], detail
    return detail


def check_strategy_fields(body: dict[str, Any]) -> None:
    assert body["strategy"] == "th_rush"
    assert body["composition"] == [PACK_TROOP]
    assert body["goal"] == {"data_id": PACK_TOWN_HALL, "level": 3}
    assert [{"start": w["start"], "end": w["end"]} for w in body["war_windows"]] == HTTP_WAR_WINDOWS


@dataclasses.dataclass(frozen=True)
class Call:
    problem: Any
    objective: Any
    config: Any
    reference: Any
    plan: Any


class Spy:
    """Delegates to the real engine and records each call."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Call] = []

    def solve(self, problem: Any, objective: Any, config: Any, reference: Any = None) -> Any:
        plan = self.inner.solve(problem, objective, config, reference)
        self.calls.append(Call(problem, objective, config, reference, plan))
        return plan


@contextmanager
def spying(key: str = "rollout") -> Iterator[Spy]:
    registry = planners()
    inner = registry[key]
    spy = Spy(inner)
    registry[key] = spy
    try:
        yield spy
    finally:
        registry[key] = inner


def weights_without_perturbation(objective: Any) -> dict[str, float]:
    weights = dataclasses.asdict(objective)
    weights.pop("perturbation")
    return weights


def test_meta_strategies_lists_the_ten_strategies_with_french_labels_and_weights(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=Clock(HTTP_NOW)))
    response = client.get("/api/meta/strategies")
    assert response.status_code == 200, response.text
    rows = response.json()["strategies"]
    assert isinstance(rows, list)
    keys = [row["key"] for row in rows]
    assert len(keys) == len(set(keys)) == 10
    assert set(keys) == KEYS

    content = json.loads(STRATEGIES_JSON.read_text(encoding="utf-8"))
    labels = set()
    for row in rows:
        label = row["label"]
        assert isinstance(label, str) and label.strip() and label != row["key"], row
        labels.add(label)
        assert set(row["weights"]) <= set(OBJECTIVE_FIELDS), row
        file_weights = content["strategies"][row["key"]]["weights"]
        assert objective_class()(**row["weights"]) == objective_class()(**file_weights), row
    assert len(labels) == 10

    openapi = client.get("/openapi.json").json()
    assert "/api/meta/strategies" in openapi["paths"]


def test_post_plans_accepts_a_strategy_a_composition_a_goal_and_war_windows(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    response = v.client.post(f"/api/villages/{VILLAGE}/plans", json=STRATEGY_REQUEST)
    assert response.status_code == 201, response.text
    check_strategy_fields(response.json())
    check_strategy_fields(latest(v))


def test_post_plans_refuses_an_unknown_strategy_and_an_impossible_goal(tmp_path: Path) -> None:
    v = village(tmp_path)
    url = f"/api/villages/{VILLAGE}/plans"

    response = v.client.post(url, json={**REPRO, "strategy": "no_such_strategy"})
    assert response.status_code == 422, response.text
    assert error_of(response)["code"] == "unknown_strategy"

    goal = {"data_id": PACK_TOWN_HALL, "level": 9}
    response = v.client.post(url, json={**REPRO, "strategy": "custom_goal", "goal": goal})
    assert response.status_code == 422, response.text
    assert error_of(response)["code"] == "invalid_goal"

    response = v.client.get(f"/api/villages/{VILLAGE}/plans/latest")
    assert response.status_code == 404, response.text
    assert error_of(response)["code"] == "no_plan"

    # A goal on a level the pack has is accepted.
    good = {"data_id": PACK_TOWN_HALL, "level": 3}
    response = v.client.post(url, json={**REPRO, "strategy": "custom_goal", "goal": good})
    assert response.status_code == 201, response.text
    assert response.json()["goal"] == good


def test_a_replanning_keeps_the_strategy_its_inputs_and_its_weights(tmp_path: Path) -> None:
    v = village(tmp_path)
    content = json.loads(STRATEGIES_JSON.read_text(encoding="utf-8"))
    rush = objective_class()(**content["strategies"]["th_rush"]["weights"])
    assert weights_without_perturbation(rush) != weights_without_perturbation(default_objective())

    with spying() as spy:
        response = v.client.post(f"/api/villages/{VILLAGE}/plans", json=STRATEGY_REQUEST)
        assert response.status_code == 201, response.text
        first = response.json()
        assert len(spy.calls) == 1
        assert weights_without_perturbation(spy.calls[0].objective) == weights_without_perturbation(
            rush
        )

        planned = sorted(
            (a for a in first["assignments"] if a["status"] == "planned"),
            key=lambda a: (a["start"], a["task_id"]),
        )
        assert len(planned) >= 2, first["assignments"]
        started, skipped = planned[0], planned[1]
        # The second task was due before the new now and never started: the engine runs again.
        v.clock.set(max(HTTP_NOW, skipped["start"]) + MINUTE)
        event = {"type": "task_started", "task_id": started["task_id"], "at": started["start"]}
        response = v.client.post(
            f"/api/villages/{VILLAGE}/plans/{first['plan_id']}/events", json=event
        )
        assert response.status_code == 200, response.text
        assert len(spy.calls) == 2, "the replanning did not run the engine"
        call = spy.calls[-1]

    now = latest(v)
    assert now["plan_id"] == response.json()["plan_id"] != first["plan_id"]
    for key in ("engine", "profile", "horizon_days"):
        assert now[key] == first[key], key
    check_strategy_fields(now)

    # The engine got the weights of th_rush, not the default objective.
    assert weights_without_perturbation(call.objective) == weights_without_perturbation(rush)
    # The stored score is the score of that objective on that problem.
    expected = score(judge(call.problem, call.plan, call.reference), call.objective, call.problem)
    assert now["score"] == pytest.approx(expected)
    # The strategy and the composition are applied again: same values as in the first problem.
    first_values = {t.id: t.value for t in spy.calls[0].problem.tasks}
    common = [t for t in call.problem.tasks if t.id in first_values]
    assert common
    for t in common:
        assert t.value == pytest.approx(first_values[t.id]), t.id
