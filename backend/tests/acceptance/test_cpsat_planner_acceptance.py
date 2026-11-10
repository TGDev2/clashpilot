"""Acceptance tests for E10: the CP-SAT refinement and the `auto` engine (SPEC 4, ADR-4 to ADR-7).

Frozen with the task. They describe `clashpilot.planning.solver` (`cpsat` and `auto`), their
registration in `clashpilot.planning.registry.PLANNERS`, `replay_trace` in
`clashpilot.planning.heuristics.rollout` and the benchmark file
`backend/tests/unit/test_cpsat_benchmark.py`.

Shapes that the task text leaves open are fixed here:

- The four engines are registered once `clashpilot.planning` and `clashpilot.planning.solver` are
  imported. `Plan.engine` of a cpsat plan is `"cpsat"`; a plan of `auto` says `"auto"` or the
  winner. `Proof.objective` is `score(simulate(problem, plan, reference), objective, problem)`.
- The solver is `ortools.sat.python.cp_model.CpSolver`: the status that drives the proof is the
  value returned by `CpSolver.solve`, the parameters are read from `CpSolver.parameters`, and the
  solution is read through the accessors `value`, `boolean_value`, `values`, `boolean_values`,
  `float_value`, `float_values`. The tests wrap these to watch, force or corrupt a solve.
- Profile `repro`: `random_seed` is `PlannerConfig.seed`, `max_deterministic_time` is
  `PlannerConfig.solver_max_deterministic_time`, one worker, and `max_time_in_seconds` keeps its
  infinite default (no wall-clock limit).
- `clashpilot.planning.solver.cpsat.CpSatPlanner(max_candidates=...)` builds a planner whose
  candidate threshold (number of task x slot pairs, 200 000 by default) is the keyword.
- `Proof.scope` is French: it names the window and the grid with the words "fenêtre" and "grille",
  the discretization of the objective with "objectif", and a restricted candidate set with
  "restreint" (as in "créneaux restreints"). "bound et gap_abs nuls" means `None` (ADR-5);
  "non nuls" means not `None`.
- The window ends at `now + solver_window_days * 86400`: a task the starting rollout plan starts at
  or after that instant keeps its start and end. The starting rollout plan is
  `PLANNERS["rollout"].solve(problem, objective, config, reference)`.
- `replay_trace(problem, objective, config, plan)` gives one `Decision` per distinct start instant
  of the plan in chronological order, the first twenty at most; `chosen` is in `candidates` and
  names a task the plan starts at `decision.at`; `plan.trace` of a cpsat or auto plan equals it.
- The benchmark file keeps the literals `100`, `300`, `400`, `simulate` and `timeout(0)`.
- `backend/tests/support/problems.py` is loaded here from its path, as the E07/E08 tests do.

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the modules do not exist.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import importlib.util
import itertools
import json
import math
import random
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HOUR = 3600
DAY = 24 * HOUR
MINUTE = 60
GRID = 300
NOW = 1_781_078_400  # 2026-06-10T08:00:00Z, the `NOW` of the support module
MAX_DECISIONS = 20

TESTS = Path(__file__).resolve().parents[1]
BACKEND = TESTS.parent
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"
BENCHMARK = TESTS / "unit" / "test_cpsat_benchmark.py"
PYPROJECT = BACKEND / "pyproject.toml"


# --------------------------------------------------------------------------- feature modules


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def planners() -> dict[str, Any]:
    import clashpilot.planning.solver

    import clashpilot.planning  # noqa: F401  (registers the engines)
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


def cpsat_planner(**options: Any) -> Any:
    from clashpilot.planning.solver.cpsat import CpSatPlanner

    return CpSatPlanner(**options)


def replay(prob: Any, plan: Any, weights: Any = None, cfg: Any = None) -> Any:
    from clashpilot.planning.heuristics.rollout import replay_trace

    return replay_trace(
        prob,
        weights if weights is not None else objective(),
        cfg if cfg is not None else config(),
        plan,
    )


def objective(**weights: float) -> Any:
    from clashpilot.planning.objective import Objective

    return Objective(**weights)


def judge(prob: Any, plan: Any, reference: Any = None) -> Any:
    from clashpilot.planning.simulate import simulate

    return simulate(prob, plan, reference)


def score_of(prob: Any, plan: Any, weights: Any = None, reference: Any = None) -> float:
    from clashpilot.planning.objective import score

    result = judge(prob, plan, reference)
    return float(score(result, weights if weights is not None else objective(), prob))


def duration_of(prob: Any, task: Any, start: int) -> int:
    from clashpilot.planning.simulate import effective_duration

    return int(effective_duration(prob, task, start))


def invalid_score() -> float:
    return float(model().INVALID_SCORE)


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e10_acceptance_support_problems"
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


def config() -> Any:
    cfg = support().TEST_CONFIG
    assert cfg.profile == "repro"
    assert cfg.solver_grid_s == GRID
    return cfg


def window_end(cfg: Any = None) -> int:
    return NOW + int((cfg if cfg is not None else config()).solver_window_days) * DAY


def solve(
    engine: str, prob: Any, weights: Any = None, *, cfg: Any = None, reference: Any = None
) -> Any:
    planner = planners()[engine]
    return planner.solve(
        prob,
        weights if weights is not None else objective(),
        cfg if cfg is not None else config(),
        reference=reference,
    )


# --------------------------------------------------------------------------- solver spy


@dataclasses.dataclass(frozen=True)
class SolverCall:
    parameters: Any  # a copy of `CpSolver.parameters` at the time of the solve
    status: Any  # the status the real solver returned


class SolverSpy:
    """Records every `CpSolver.solve`; `force` replaces the status the planner sees."""

    def __init__(self) -> None:
        self.calls: list[SolverCall] = []
        self.force: Any = None

    @property
    def last(self) -> SolverCall:
        assert self.calls, "the planner never called CpSolver.solve"
        return self.calls[-1]

    def reset(self) -> None:
        self.calls.clear()
        self.force = None


def cp_model() -> ModuleType:
    from ortools.sat.python import cp_model as module

    return module


@pytest.fixture(scope="module")
def solver_spy() -> Iterator[SolverSpy]:
    spy = SolverSpy()
    solver_class = cp_model().CpSolver
    original = solver_class.solve

    def solve(self: Any, *args: Any, **kwargs: Any) -> Any:
        status = original(self, *args, **kwargs)
        spy.calls.append(SolverCall(parameters=copy.deepcopy(self.parameters), status=status))
        return status if spy.force is None else spy.force

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(solver_class, "solve", solve)
        yield spy


@pytest.fixture(autouse=True)
def _fresh_spy(solver_spy: SolverSpy) -> Iterator[None]:
    solver_spy.reset()
    yield
    solver_spy.reset()


def status_name(status: Any) -> str:
    """`"OPTIMAL"`, `"FEASIBLE"`... whatever the enum type of the status."""
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name
    return str(cp_model().CpSolver.status_name(cp_model().CpSolver(), status))


# --------------------------------------------------------------------------- hand-built problems


def task(
    task_id: str,
    *,
    hours: float,
    value: float | None = None,
    lane: str = "builder",
    cost: Mapping[str, int] | None = None,
    requires: Iterable[str] = (),
) -> Any:
    return support().make_task(
        task_id,
        lane=lane,
        duration_s=round(hours * HOUR),
        cost=dict(cost) if cost is not None else {"gold": 10},
        requires=tuple(requires),
        value=value,
    )


def nights(days: int) -> list[tuple[int, int]]:
    """One sleep window per day, 23:00 to 07:00 UTC; `NOW` is 08:00, awake."""
    return [(NOW + 15 * HOUR + d * DAY, NOW + 23 * HOUR + d * DAY) for d in range(days)]


def problem(
    tasks: Iterable[Any],
    *,
    lanes: Mapping[str, int] | None = None,
    sleep: Sequence[tuple[int, int]] = (),
    policy: Any = None,
    initial: Mapping[str, int] | None = None,
    cap: Mapping[str, int] | None = None,
    income: Mapping[str, int] | None = None,
    frozen: Sequence[Any] = (),
    modifiers: Sequence[Any] = (),
    hours: int = 24,
) -> Any:
    """A problem where funds are plentiful unless the test says otherwise."""
    m = model()
    helpers = support()
    return helpers.make_problem(
        now=NOW,
        horizon_end=NOW + hours * HOUR,
        lanes=dict(lanes or {"builder": 1}),
        tasks=tuple(tasks),
        frozen=tuple(frozen),
        resources=m.ResourceModel(
            initial=dict(initial if initial is not None else {"gold": 10**7, "elixir": 10**7}),
            cap=dict(cap or {}),
            income_per_hour=dict(income or {}),
        ),
        calendar=helpers.make_calendar(sleep=sleep),
        policy=policy if policy is not None else m.Policy(mode="progress"),
        modifiers=tuple(modifiers),
    )


def policy(mode: str, **options: Any) -> Any:
    return model().Policy(mode=mode, **options)


def modifier(start: int, end: int, *, duration_pct: int = 100, cost_pct: int = 100) -> Any:
    return model().Modifier(
        start=start, end=end, lane="builder", duration_pct=duration_pct, cost_pct=cost_pct
    )


def frozen_job(task_id: str, lane: str, slot: int, start: int, end: int) -> Any:
    return model().FrozenJob(task_id=task_id, lane=lane, lane_slot=slot, start=start, end=end)


EXACT_A = "1001:0:2"  # builder, 1 h, value 2, 1000 gold
EXACT_B = "1001:0:3"  # builder, 2 h, value 3, 1500 gold, requires A
EXACT_C = "1002:0:2"  # builder, 1 h, value 1, 500 gold
EXACT_L = "1003:0:2"  # lab, 2 h, value 2, 1000 elixir
EXACT_SLEEP = (NOW + 4 * HOUR, NOW + 5 * HOUR)


def exact() -> Any:
    """Grid-aligned, strict, one builder and one lab: the optimum is enumerated in the test.

    Durations and the 8 h horizon are multiples of 300 s, values are integers, the hourly incomes
    are multiples of 12, and a one-hour sleep window at +4 h makes the end rule bite.
    """
    return problem(
        (
            task(EXACT_A, hours=1, value=2.0, cost={"gold": 1000}),
            task(EXACT_B, hours=2, value=3.0, cost={"gold": 1500}, requires=(EXACT_A,)),
            task(EXACT_C, hours=1, value=1.0, cost={"gold": 500}),
            task(EXACT_L, hours=2, value=2.0, lane="lab", cost={"elixir": 1000}),
        ),
        lanes={"builder": 1, "lab": 1},
        sleep=[EXACT_SLEEP],
        policy=policy("strict"),
        initial={"gold": 1500, "elixir": 2000},
        cap={"gold": 5000, "elixir": 5000},
        income={"gold": 1200, "elixir": 0},
        hours=8,
    )


def off_grid() -> Any:
    """Durations of 50, 70 and 130 minutes: the model rounds them up to the grid."""
    return problem(
        (
            task("1001:0:2", hours=50 / 60, value=1.0),
            task("1002:0:2", hours=70 / 60, value=2.0),
            task("1003:0:2", hours=130 / 60, value=3.0),
        ),
        sleep=[(NOW + 4 * HOUR, NOW + 12 * HOUR)],
    )


MOD_WINDOW = (NOW, NOW + 2 * HOUR)
MOD_FIRST = "1001:0:2"  # builder, 2 h, halved to 1 h when started before +2 h
MOD_SECOND = "1002:0:2"


def modifier_duration() -> Any:
    """One builder, duration_pct 50 on the builder lane for the first two hours."""
    return problem(
        (task(MOD_FIRST, hours=2, value=2.0), task(MOD_SECOND, hours=2, value=2.0)),
        modifiers=[modifier(*MOD_WINDOW, duration_pct=50)],
    )


PRICEY = "1001:0:2"  # 1000 gold, 600 in stock, no income: affordable only at half price


def modifier_cost() -> Any:
    """One builder, cost_pct 50 for the first two hours; the task is fundable only then."""
    return problem(
        (task(PRICEY, hours=1, value=1.0, cost={"gold": 1000}),),
        modifiers=[modifier(*MOD_WINDOW, cost_pct=50)],
        initial={"gold": 600},
        cap={"gold": 5000},
        income={"gold": 0},
    )


def overflow() -> Any:
    """900 gold of 1000, 1200 an hour, asleep one hour: the cap is reached before any start."""
    return problem(
        (
            task("1001:0:2", hours=1, value=1.0, cost={"gold": 100}),
            task("1002:0:2", hours=1, value=1.0, cost={"gold": 100}),
        ),
        sleep=[(NOW, NOW + HOUR)],
        initial={"gold": 900},
        cap={"gold": 1000},
        income={"gold": 1200},
    )


def capped_strict() -> Any:
    """One builder, strict policy with nights, a small cap: spill and sleep rules together."""
    return problem(
        tuple(
            task(f"{1001 + i}:0:2", hours=i + 1, value=float(i + 1), cost={"gold": 800 + 200 * i})
            for i in range(5)
        ),
        sleep=nights(3),
        policy=policy("strict"),
        initial={"gold": 1500},
        cap={"gold": 2000},
        income={"gold": 600},
        hours=48,
    )


FROZEN_BUILDER = frozen_job("9001:0:2", "builder", 0, NOW - HOUR, NOW + 3 * HOUR)
FROZEN_LAB = frozen_job("9002:0:2", "lab", 0, NOW, NOW + 2 * HOUR)
STRICT_SLEEP = (NOW + 4 * HOUR, NOW + 12 * HOUR)


def strict_frozen() -> Any:
    """Two builders and a lab, strict policy, a frozen job on each lane at the window start."""
    return problem(
        (
            task("1001:0:2", hours=2, value=2.0),
            task("1002:0:2", hours=3, value=3.0),
            task("1003:0:2", hours=1, value=1.0),
            task("1004:0:2", hours=2, value=2.0, lane="lab", cost={"elixir": 10}),
            task("1005:0:2", hours=1, value=1.0, lane="lab", cost={"elixir": 10}),
        ),
        lanes={"builder": 2, "lab": 1},
        sleep=[STRICT_SLEEP],
        policy=policy("strict"),
        frozen=[FROZEN_BUILDER, FROZEN_LAB],
    )


def beyond_window() -> Any:
    """One builder, eight 12 h tasks over six days: the last ones start after the 3-day window."""
    return problem(
        tuple(task(f"{1001 + i}:0:2", hours=12, value=float(i + 1)) for i in range(8)),
        hours=6 * 24,
    )


STRONG = "1001:0:2"  # 1 h, value 3
WEAK = "1002:0:2"  # 1 h, value 1


def reference_race() -> Any:
    """One builder: by value alone STRONG goes first; the reference plan starts WEAK first."""
    return problem((task(STRONG, hours=1, value=3.0), task(WEAK, hours=1, value=1.0)))


def reference_plan() -> Any:
    m = model()
    return m.Plan(
        assignments=(
            m.Assignment(task_id=WEAK, lane="builder", lane_slot=0, start=NOW, end=NOW + HOUR),
            m.Assignment(
                task_id=STRONG, lane="builder", lane_slot=0, start=NOW + HOUR, end=NOW + 2 * HOUR
            ),
        ),
        engine="test",
        proof=m.Proof(kind="heuristic", objective=0.0),
    )


def generated(n_tasks: int = 20, *, seed: int = 42, days: int = 10) -> Any:
    """Fictional tasks in chains of two levels, a fifth of them in the lab, nights every day."""
    rng = random.Random(seed)
    tasks: list[Any] = []
    chain = 0
    while len(tasks) < n_tasks:
        data_id = 1000 + chain
        lane = "lab" if chain % 5 == 0 else "builder"
        resource = "elixir" if lane == "lab" else "gold"
        previous: str | None = None
        for to_level in (2, 3):
            if len(tasks) >= n_tasks:
                break
            name = f"{data_id}:0:{to_level}"
            duration_s = 5 * MINUTE * rng.randint(6, 96)  # 30 min to 8 h
            tasks.append(
                support().make_task(
                    name,
                    lane=lane,
                    duration_s=duration_s,
                    cost={resource: 100 * rng.randint(5, 50)},
                    requires=() if previous is None else (previous,),
                    value=round(duration_s / HOUR * rng.uniform(0.5, 2.0), 3),
                )
            )
            previous = name
        chain += 1
    return problem(
        tasks,
        lanes={"builder": 2, "lab": 1},
        sleep=nights(days + 1),
        initial={"gold": 20_000, "elixir": 20_000},
        cap={"gold": 10**6, "elixir": 10**6},
        income={"gold": 5000, "elixir": 5000},
        hours=days * 24,
    )


FIXTURES: dict[str, Callable[[], Any]] = {
    "exact": exact,
    "off_grid": off_grid,
    "modifier_duration": modifier_duration,
    "modifier_cost": modifier_cost,
    "overflow": overflow,
    "capped_strict": capped_strict,
    "strict_frozen": strict_frozen,
    "beyond_window": beyond_window,
    "reference": reference_race,
    "generated_20": functools.partial(generated, 20),
}
SMALL = [name for name in FIXTURES if name != "generated_20"]
AUTO_FIXTURES = ["exact", "overflow", "strict_frozen", "beyond_window"]

_PLANS: dict[tuple[str, str], Any] = {}


def plan_for(engine: str, name: str) -> Any:
    """The plan of `engine` on the fixture `name` with `Objective()` and `TEST_CONFIG`, cached."""
    key = (engine, name)
    if key not in _PLANS:
        _PLANS[key] = solve(engine, FIXTURES[name]())
    return _PLANS[key]


# --------------------------------------------------------------------------- reading results


def codes(result: Any) -> list[str]:
    return sorted(str(v["code"] if isinstance(v, Mapping) else v.code) for v in result.violations)


def assert_clean(result: Any) -> None:
    assert codes(result) == []
    assert result.valid


def metric(result: Any, name: str) -> Any:
    metrics = result.metrics
    return metrics[name] if isinstance(metrics, Mapping) else getattr(metrics, name)


def assignment_of(plan: Any, task_id: str) -> Any:
    found = [a for a in plan.assignments if a.task_id == task_id]
    assert len(found) == 1, f"{task_id} planned {len(found)} times"
    return found[0]


def start_of(plan: Any, task_id: str) -> int:
    return int(assignment_of(plan, task_id).start)


def starts(plan: Any) -> dict[str, int]:
    return {a.task_id: int(a.start) for a in plan.assignments}


def keyed(plan: Any) -> set[tuple[str, int, int]]:
    return {(a.task_id, int(a.start), int(a.end)) for a in plan.assignments}


def scope_words(proof: Any, *words: str) -> None:
    scope = str(proof.scope).lower()
    for word in words:
        assert word in scope, (word, proof.scope)


def serialized(plan: Any) -> bytes:
    return json.dumps(dataclasses.asdict(plan), sort_keys=True, default=repr).encode("utf-8")


def collected(marker: str) -> list[str]:
    """Node ids that pytest collects in the benchmark file for the marker expression."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(PYPROJECT),
        "-p",
        "no:cacheprovider",
        "--collect-only",
        "-q",
        "-m",
        marker,
        str(BENCHMARK),
    ]
    done = subprocess.run(
        command, capture_output=True, text=True, cwd=BACKEND.parent, timeout=60, check=False
    )
    return [line.strip() for line in done.stdout.splitlines() if "::" in line]


# --------------------------------------------------------------------------- exhaustive optimum


def plan_of(assignments: Sequence[Any]) -> Any:
    m = model()
    return m.Plan(
        assignments=tuple(assignments),
        engine="test",
        proof=m.Proof(kind="heuristic", objective=0.0),
    )


def earliest_grid_start(prob: Any, placed: list[Any], item: Any, lower: int) -> Any | None:
    """The first grid instant at or after `lower` where `item` can start after `placed`.

    Cheap rules first (calendar, one slot per lane, prerequisites, strict ends), then the simulator
    judges the funds: the partial plan with the task at that instant must be valid.
    """
    calendar, mode = prob.calendar, prob.policy.mode
    ends = {a.task_id: a.end for a in placed}
    same_lane = [(a.start, a.end) for a in placed if a.lane == item.lane]
    for start in range(lower, prob.horizon_end, GRID):
        end = start + duration_of(prob, item, start)
        if not calendar.can_act(start):
            continue
        if mode == "strict" and calendar.is_sleep(end):
            continue
        if any(ends.get(required, prob.horizon_end + 1) > start for required in item.requires):
            continue
        if any(start < busy_end and busy_start < end for busy_start, busy_end in same_lane):
            continue
        trial = model().Assignment(
            task_id=item.id, lane=item.lane, lane_slot=0, start=start, end=end
        )
        if judge(prob, plan_of([*placed, trial])).valid:
            return trial
    return None


def exhaustive_best(prob: Any, weights: Any) -> tuple[float, Any]:
    """Best score of a plan on the grid, over every sequence of every subset of the tasks.

    Lanes of capacity one. For a given start order, placing each task at its earliest feasible
    grid instant (not before the previous start) dominates every other placement: the score only
    rewards earlier ends, an earlier payment never leaves less in stock later (less is lost over the
    cap), and a lane freed earlier offers a superset of starts. Every plan has a start order, so
    the maximum over all orders and subsets is the optimum of the grid.
    """
    assert all(capacity == 1 for capacity in prob.lanes.values())
    best_score, best_plan = score_of(prob, plan_of([]), weights), plan_of([])
    for size in range(1, len(prob.tasks) + 1):
        for sequence in itertools.permutations(prob.tasks, size):
            placed: list[Any] = []
            lower = prob.now
            for item in sequence:
                found = earliest_grid_start(prob, placed, item, lower)
                if found is None:
                    break
                placed.append(found)
                lower = found.start
            else:
                candidate = plan_of(placed)
                value = score_of(prob, candidate, weights)
                if value > best_score:
                    best_score, best_plan = value, candidate
    return best_score, best_plan


# --------------------------------------------------------------------------- registry


def test_cpsat_and_auto_are_registered_next_to_the_heuristics() -> None:
    registry = planners()
    assert {"baseline", "rollout", "cpsat", "auto"} <= set(registry)
    prob = exact()
    produced = registry["cpsat"].solve(prob, objective(), config(), reference=None)
    assert isinstance(produced, model().Plan)
    assert produced.engine == "cpsat"
    assert produced.assignments
    assert_clean(judge(prob, produced))
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))


# --------------------------------------------------------------------------- exhaustive optimum


def test_the_small_grid_aligned_instance_reaches_the_enumerated_optimum(
    solver_spy: SolverSpy,
) -> None:
    prob = exact()
    assert len(prob.tasks) <= 6
    assert (prob.horizon_end - prob.now) % GRID == 0
    assert all(t.duration_s % GRID == 0 and float(t.value).is_integer() for t in prob.tasks)
    assert all(rate % 12 == 0 for rate in prob.resources.income_per_hour.values())
    weights = objective(value=1.0)

    produced = solve("cpsat", prob, weights)
    assert status_name(solver_spy.last.status) == "OPTIMAL"
    assert produced.engine == "cpsat"
    assert produced.warnings == ()
    assert produced.proof.kind == "optimal_window"
    scope_words(produced.proof, "fenêtre", "grille", "objectif")
    assert_clean(judge(prob, produced))

    best_score, best_plan = exhaustive_best(prob, weights)
    assert best_plan.assignments
    assert best_score > 0
    assert score_of(prob, produced, weights) == pytest.approx(best_score, abs=1e-6)
    assert produced.proof.objective == pytest.approx(best_score, abs=1e-6)


def test_off_grid_durations_give_a_valid_plan_and_a_discretized_objective_scope() -> None:
    prob = off_grid()
    produced = plan_for("cpsat", "off_grid")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 3
    assert produced.proof.kind in ("optimal_window", "feasible")
    scope_words(produced.proof, "fenêtre", "grille", "objectif")
    for assignment in produced.assignments:
        item = next(t for t in prob.tasks if t.id == assignment.task_id)
        assert assignment.end - assignment.start == item.duration_s


# --------------------------------------------------------------------------- validity


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_gives_a_valid_cpsat_plan_without_violation(name: str) -> None:
    prob = FIXTURES[name]()
    produced = plan_for("cpsat", name)
    result = judge(prob, produced)
    assert result.valid, codes(result)
    assert list(result.violations) == []
    assert produced.assignments
    assert "solver_invalid_plan" not in produced.warnings
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))


@pytest.mark.parametrize("name", SMALL)
def test_small_fixtures_are_solved_by_the_model_itself(name: str) -> None:
    produced = plan_for("cpsat", name)
    assert produced.warnings == ()
    assert produced.proof.kind in ("optimal_window", "feasible")
    scope_words(produced.proof, "fenêtre", "grille")


@pytest.mark.parametrize("name", AUTO_FIXTURES)
def test_every_fixture_gives_a_valid_auto_plan_without_violation(name: str) -> None:
    prob = FIXTURES[name]()
    produced = plan_for("auto", name)
    result = judge(prob, produced)
    assert result.valid, codes(result)
    assert produced.assignments
    assert "solver_invalid_plan" not in produced.warnings


# --------------------------------------------------------------------------- trace


def check_replayed_trace(prob: Any, produced: Any) -> None:
    invalid = invalid_score()
    by_start: dict[int, set[str]] = {}
    for assignment in produced.assignments:
        by_start.setdefault(int(assignment.start), set()).add(assignment.task_id)
    instants = sorted(by_start)
    assert produced.trace
    assert len(produced.trace) <= MAX_DECISIONS
    assert len(produced.trace) == min(MAX_DECISIONS, len(instants))
    assert [d.at for d in produced.trace] == instants[: len(produced.trace)]
    for decision in produced.trace:
        assert decision.candidates
        chosen = decision.chosen
        assert chosen in tuple(decision.candidates)
        assert chosen.task_id is not None
        assert chosen.start == decision.at
        assert chosen.task_id in by_start[decision.at]
        assert chosen.score != invalid
        for candidate in decision.candidates:
            assert candidate.start >= decision.at


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_cpsat_plan_has_one_replayed_decision_per_start_instant(name: str) -> None:
    check_replayed_trace(FIXTURES[name](), plan_for("cpsat", name))


@pytest.mark.parametrize("name", AUTO_FIXTURES)
def test_an_auto_plan_has_one_replayed_decision_per_start_instant(name: str) -> None:
    check_replayed_trace(FIXTURES[name](), plan_for("auto", name))


def test_the_trace_is_what_replay_trace_builds_with_candidates_scored_by_simulate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import clashpilot.planning.heuristics.rollout as rollout_module

    prob = exact()
    produced = plan_for("cpsat", "exact")
    calls = 0
    original = rollout_module.simulate

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(rollout_module, "simulate", counting)
    replayed = replay(prob, produced)
    assert tuple(replayed) == tuple(produced.trace)
    scored = sum(1 for d in replayed for c in d.candidates if c.score != invalid_score())
    assert scored >= len(replayed) >= 1
    assert calls >= scored


# --------------------------------------------------------------------------- modifiers


def test_a_duration_modifier_shortens_the_task_started_in_its_window() -> None:
    prob = modifier_duration()
    produced = plan_for("cpsat", "modifier_duration")
    assert_clean(judge(prob, produced))
    assert "solver_invalid_plan" not in produced.warnings
    inside = [a for a in produced.assignments if MOD_WINDOW[0] <= a.start < MOD_WINDOW[1]]
    assert inside
    for assignment in inside:
        item = next(t for t in prob.tasks if t.id == assignment.task_id)
        expected = duration_of(prob, item, assignment.start)
        assert expected < item.duration_s
        assert assignment.end - assignment.start == expected


def test_a_cost_modifier_makes_the_task_affordable_in_the_modelled_stock() -> None:
    prob = modifier_cost()
    produced = plan_for("cpsat", "modifier_cost")
    assert_clean(judge(prob, produced))
    assert "solver_invalid_plan" not in produced.warnings
    assert MOD_WINDOW[0] <= start_of(produced, PRICEY) < MOD_WINDOW[1]


# --------------------------------------------------------------------------- restricted candidates


def test_a_restricted_candidate_set_never_claims_optimal_window(solver_spy: SolverSpy) -> None:
    prob = exact()
    produced = cpsat_planner(max_candidates=1).solve(prob, objective(), config(), reference=None)
    assert status_name(solver_spy.last.status) in ("OPTIMAL", "FEASIBLE")
    assert_clean(judge(prob, produced))
    assert produced.assignments
    assert produced.proof.kind == "feasible"
    assert produced.proof.bound is None
    assert produced.proof.gap_abs is None
    scope_words(produced.proof, "restreint")


# --------------------------------------------------------------------------- stock cap


def test_income_over_the_cap_is_lost_and_never_infeasible() -> None:
    prob = overflow()
    produced = plan_for("cpsat", "overflow")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 2
    assert metric(result, "spill")["gold"] > 0
    assert min(a.start for a in produced.assignments) >= NOW + HOUR


# --------------------------------------------------------------------------- proof


def test_optimal_window_comes_with_zero_gap_limits(solver_spy: SolverSpy) -> None:
    prob = exact()
    produced = solve("cpsat", prob)
    call = solver_spy.last
    assert status_name(call.status) == "OPTIMAL"
    assert call.parameters.absolute_gap_limit == 0
    assert call.parameters.relative_gap_limit == 0
    assert produced.proof.kind == "optimal_window"
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))
    scope_words(produced.proof, "fenêtre", "grille")


def test_a_feasible_status_on_an_unrestricted_model_is_announced_as_feasible(
    solver_spy: SolverSpy,
) -> None:
    prob = exact()
    solver_spy.force = cp_model().FEASIBLE
    produced = solve("cpsat", prob)
    assert status_name(solver_spy.last.status) == "OPTIMAL"  # the real status, hidden
    assert_clean(judge(prob, produced))
    assert produced.assignments
    assert produced.proof.kind == "feasible"
    assert produced.proof.bound is not None
    assert produced.proof.gap_abs is not None
    assert produced.proof.gap_abs >= 0
    scope_words(produced.proof, "fenêtre", "grille")
    assert "solver_fallback" not in produced.warnings


def test_a_limit_reached_without_solution_falls_back_to_rollout(solver_spy: SolverSpy) -> None:
    prob = exact()
    fallback = solve("rollout", prob)
    solver_spy.force = cp_model().UNKNOWN
    produced = solve("cpsat", prob)
    assert solver_spy.calls
    assert "solver_fallback" in produced.warnings
    assert produced.proof.kind == "heuristic"
    assert produced.proof.bound is None
    assert keyed(produced) == keyed(fallback)
    assert_clean(judge(prob, produced))
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))


# --------------------------------------------------------------------------- auto


@pytest.mark.parametrize("name", AUTO_FIXTURES)
def test_auto_keeps_the_best_simulated_plan_of_rollout_and_cpsat(name: str) -> None:
    prob = FIXTURES[name]()
    produced = plan_for("auto", name)
    rollout_plan = solve("rollout", prob)
    cpsat_plan = plan_for("cpsat", name)
    assert_clean(judge(prob, produced))
    winner = produced.metadata["auto_winner"]
    assert winner in ("rollout", "cpsat")
    assert isinstance(produced.metadata["auto_reason"], str)
    assert produced.metadata["auto_reason"].strip()
    assert produced.engine in ("auto", winner)
    scores = {"rollout": score_of(prob, rollout_plan), "cpsat": score_of(prob, cpsat_plan)}
    auto_score = score_of(prob, produced)
    assert auto_score >= scores["rollout"]
    assert auto_score == pytest.approx(max(scores.values()))
    assert keyed(produced) == keyed(rollout_plan if winner == "rollout" else cpsat_plan)
    assert produced.proof.objective == pytest.approx(auto_score)


# --------------------------------------------------------------------------- invalid solution


def test_a_solver_plan_refused_by_simulate_is_replaced_by_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prob = exact()
    fallback = solve("rollout", prob)
    solver_class = cp_model().CpSolver

    def one(self: Any, item: Any) -> int:
        return 1

    def true(self: Any, item: Any) -> bool:
        return True

    def ones(self: Any, items: Any) -> list[int]:
        return [1 for _ in items]

    def trues(self: Any, items: Any) -> list[bool]:
        return [True for _ in items]

    monkeypatch.setattr(solver_class, "value", one)
    monkeypatch.setattr(solver_class, "float_value", one)
    monkeypatch.setattr(solver_class, "boolean_value", true)
    monkeypatch.setattr(solver_class, "values", ones)
    monkeypatch.setattr(solver_class, "float_values", ones)
    monkeypatch.setattr(solver_class, "boolean_values", trues)

    produced = solve("cpsat", prob)
    assert "solver_invalid_plan" in produced.warnings
    assert produced.proof.kind == "heuristic"
    assert keyed(produced) == keyed(fallback)
    assert_clean(judge(prob, produced))
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))


# --------------------------------------------------------------------------- strict, capacity


def test_strict_cpsat_plan_ends_outside_sleep_and_shares_lanes_with_frozen_jobs() -> None:
    prob = strict_frozen()
    produced = plan_for("cpsat", "strict_frozen")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 5
    for assignment in produced.assignments:
        assert not prob.calendar.is_sleep(assignment.end), assignment
    builder_starts = [a.start for a in produced.assignments if a.lane == "builder"]
    lab_starts = [a.start for a in produced.assignments if a.lane == "lab"]
    assert min(builder_starts) == NOW  # the second builder is free while the first is frozen
    assert all(
        a.lane_slot == 1
        for a in produced.assignments
        if a.lane == "builder" and a.start < FROZEN_BUILDER.end
    )
    assert min(lab_starts) >= FROZEN_LAB.end
    assert (
        sum(1 for a in produced.assignments if a.lane == "builder" and a.start < FROZEN_BUILDER.end)
        >= 1
    )


def test_the_capped_strict_fixture_is_valid_with_spill() -> None:
    prob = capped_strict()
    produced = plan_for("cpsat", "capped_strict")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") >= 3
    for assignment in produced.assignments:
        assert not prob.calendar.is_sleep(assignment.end), assignment


# --------------------------------------------------------------------------- repro profile


@pytest.mark.parametrize("name", ["exact", "strict_frozen"])
def test_two_identical_repro_runs_give_the_same_plan_without_a_wall_clock_limit(
    solver_spy: SolverSpy, name: str
) -> None:
    prob = FIXTURES[name]()
    first = solve("cpsat", prob)
    parameters = solver_spy.last.parameters
    second = solve("cpsat", prob)
    assert first.assignments
    assert first == second
    assert serialized(first) == serialized(second)
    assert math.isinf(parameters.max_time_in_seconds)  # the default: no wall-clock limit
    assert parameters.max_deterministic_time == pytest.approx(
        config().solver_max_deterministic_time
    )
    assert parameters.random_seed == config().seed
    assert 1 in (parameters.num_workers, parameters.num_search_workers)


# --------------------------------------------------------------------------- window


def test_tasks_beyond_the_window_keep_their_rollout_dates() -> None:
    prob = beyond_window()
    starting = solve("rollout", prob)
    produced = plan_for("cpsat", "beyond_window")
    assert_clean(judge(prob, produced))
    limit = window_end()
    outside = {a.task_id: (a.start, a.end) for a in starting.assignments if a.start >= limit}
    assert len(outside) >= 2, starting.assignments
    assert any(a.start < limit for a in starting.assignments)
    for task_id, (start, end) in outside.items():
        assignment = assignment_of(produced, task_id)
        assert (assignment.start, assignment.end) == (start, end)
    assert len(produced.assignments) == len(starting.assignments)


# --------------------------------------------------------------------------- reference


def test_a_positive_perturbation_weight_keeps_the_reference_starts() -> None:
    prob = reference_race()
    reference = reference_plan()
    with_weight = solve("cpsat", prob, objective(perturbation=100.0), reference=reference)
    without = solve("cpsat", prob, objective(), reference=reference)
    assert_clean(judge(prob, with_weight, reference))
    assert_clean(judge(prob, without, reference))
    assert with_weight.assignments and without.assignments

    wanted = starts(reference)
    kept_with = sum(1 for a in with_weight.assignments if wanted.get(a.task_id) == a.start)
    kept_without = sum(1 for a in without.assignments if wanted.get(a.task_id) == a.start)
    assert kept_with >= kept_without
    assert start_of(without, STRONG) == NOW


# --------------------------------------------------------------------------- benchmark file


def test_the_benchmark_file_holds_slow_tests_only() -> None:
    assert BENCHMARK.is_file(), f"{BENCHMARK} is missing"
    source = BENCHMARK.read_text(encoding="utf-8")
    for literal in ("100", "300", "400", "simulate", "timeout(0)"):
        assert literal in source, literal
    slow = collected("slow")
    assert len(slow) >= 3, slow
    assert all("test_cpsat_benchmark.py::" in node for node in slow), slow
    assert collected("not slow") == []
