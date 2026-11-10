"""Acceptance tests for E08: the baseline and rollout planners (SPEC 4, ADR-4, ADR-5).

Frozen with the task. They describe `clashpilot.planning.heuristics` (`baseline` and `rollout`),
their registration in `clashpilot.planning.registry.PLANNERS` and the benchmark file
`backend/tests/unit/test_rollout_benchmark.py`.

Shapes that the task text leaves open are fixed here:

- The engines register when `clashpilot.planning.heuristics` is imported (registering when
  `clashpilot.planning.registry` is imported is accepted too): the tests import both before
  reading `PLANNERS`. `Plan.engine` is the registry key of the engine that produced the plan.
- `Proof.objective` of a returned plan is `score(simulate(problem, plan, reference), objective,
  problem)` of that plan, for both engines.
- Both engines return a plan that `simulate` finds valid (ADR-4), on every fixture of this module.
- Trace: every non-empty rollout plan has a trace, in chronological order (`at` never decreases).
  A `Decision` lists every candidate scored at that instant plus the immediate starts refused
  without evaluation (`INVALID_SCORE`). `chosen` is one of `candidates`, is never `INVALID_SCORE`,
  and when it names a task the plan starts that task at `chosen.start`. A candidate whose score is
  not `INVALID_SCORE` counts as one evaluation.
- Ties are broken by `task_id` ascending as strings (the fixtures use ids whose string order is
  their numeric order), the wait candidate (`task_id` None) last.
- The benchmark file keeps the literals `300`, `400`, `simulate` and `timeout(0)` in its source.
- `backend/tests/support/problems.py` is loaded here from its path, as the E07 tests do.

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the modules do not exist.
"""

from __future__ import annotations

import dataclasses
import functools
import importlib.util
import json
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HOUR = 3600
DAY = 24 * HOUR
MINUTE = 60
NOW = 1_781_078_400  # 2026-06-10T08:00:00Z, the `NOW` of the support module

TESTS = Path(__file__).resolve().parents[1]
BACKEND = TESTS.parent
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"
BENCHMARK = TESTS / "unit" / "test_rollout_benchmark.py"
PYPROJECT = BACKEND / "pyproject.toml"


# --------------------------------------------------------------------------- feature modules


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def planners() -> dict[str, Any]:
    import clashpilot.planning.heuristics  # noqa: F401  (registers baseline and rollout)

    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


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


def invalid_score() -> float:
    return float(model().INVALID_SCORE)


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e08_acceptance_support_problems"
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
    return support().TEST_CONFIG


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
        duration_s=int(hours * HOUR),
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
    preferred: Sequence[tuple[int, int]] = (),
    policy: Any = None,
    initial: Mapping[str, int] | None = None,
    cap: Mapping[str, int] | None = None,
    income: Mapping[str, int] | None = None,
    days: int = 1,
) -> Any:
    """A problem where funds are plentiful unless the test says otherwise."""
    m = model()
    helpers = support()
    return helpers.make_problem(
        now=NOW,
        horizon_end=NOW + days * DAY,
        lanes=dict(lanes or {"builder": 1}),
        tasks=tuple(tasks),
        frozen=(),
        resources=m.ResourceModel(
            initial=dict(initial if initial is not None else {"gold": 10**7, "elixir": 10**7}),
            cap=dict(cap or {}),
            income_per_hour=dict(income or {}),
        ),
        calendar=helpers.make_calendar(sleep=sleep, preferred=preferred),
        policy=policy if policy is not None else m.Policy(mode="progress"),
    )


def policy(mode: str, **options: Any) -> Any:
    return model().Policy(mode=mode, **options)


TRAP_LONG = "1001:0:2"  # 10 h, value 100: the best ratio, but it delays the unlock
TRAP_KEY = "1002:0:2"  # 1 h, value 1: unlocks TRAP_UNLOCKED
TRAP_UNLOCKED = "1002:0:3"  # 1 h, value 50


def greedy_trap() -> Any:
    """One builder. Baseline runs the 10 h task first; starting the 1 h key first scores more."""
    return problem(
        (
            task(TRAP_LONG, hours=10, value=100.0),
            task(TRAP_KEY, hours=1, value=1.0),
            task(TRAP_UNLOCKED, hours=1, value=50.0, requires=(TRAP_KEY,)),
        )
    )


CHEAP = "1001:0:2"  # 1 h, value 1, costs 100 gold
PRICEY = "1002:0:2"  # 1 h, value 3, costs 1500 gold


def income_race(rate: int) -> Any:
    """One builder, 1000 gold in stock: the pricey task is fundable after 500 / rate hours."""
    return problem(
        (
            task(CHEAP, hours=1, value=1.0, cost={"gold": 100}),
            task(PRICEY, hours=1, value=3.0, cost={"gold": 1500}),
        ),
        initial={"gold": 1000},
        cap={"gold": 10**9},
        income={"gold": rate},
    )


SHORT = "1001:0:2"  # 1 h, value 2
LONG = "1002:0:2"  # 5 h, value 5


def sleep_race(sleep_start_h: int) -> Any:
    """One builder, strict policy, one 8 h night starting `sleep_start_h` hours after now."""
    night = (NOW + sleep_start_h * HOUR, NOW + (sleep_start_h + 8) * HOUR)
    return problem(
        (task(SHORT, hours=1, value=2.0), task(LONG, hours=5, value=5.0)),
        sleep=[night],
        policy=policy("strict"),
    )


def sleep_single() -> Any:
    """One builder, strict policy: the only task would end in the night if started now."""
    return problem(
        (task(LONG, hours=5, value=5.0),),
        sleep=[(NOW + 5 * HOUR, NOW + 13 * HOUR)],
        policy=policy("strict"),
    )


THREE = ("1001:0:2", "1002:0:2", "1003:0:2")  # 1 h, 2 h and 3 h, value 1 each


def three_tasks(mode: str, **options: Any) -> Any:
    """One builder, a night from now + 4 h to now + 12 h, three tasks of equal value."""
    return problem(
        tuple(task(name, hours=index + 1, value=1.0) for index, name in enumerate(THREE)),
        sleep=[(NOW + 4 * HOUR, NOW + 12 * HOUR)],
        policy=policy(mode, **options),
    )


TWENTY = tuple(f"{1000 + index}:0:2" for index in range(20))


def twenty_tasks(mode: str = "progress", **options: Any) -> Any:
    """Two builders, 21 days with nights, twenty tasks of 30 min to 2 h."""
    tasks = tuple(
        task(name, hours=(30 + 5 * ((index * 7) % 19)) / 60, cost={"gold": 100})
        for index, name in enumerate(TWENTY)
    )
    return problem(
        tasks,
        lanes={"builder": 2},
        sleep=nights(22),
        policy=policy(mode, **options),
        days=21,
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


SINGLE = "1001:0:2"  # 2 h, value 1
PREFERRED = (NOW + 10 * HOUR, NOW + 11 * HOUR)


def preferred_window() -> Any:
    """One builder: start now (end at + 2 h) or after the night at + 8 h (end at + 10 h)."""
    return problem(
        (task(SINGLE, hours=2, value=1.0),),
        sleep=[(NOW + 3 * HOUR, NOW + 8 * HOUR)],
        preferred=[PREFERRED],
    )


def generated(builders: int, *, seed: int = 42, chains: int = 20, days: int = 30) -> Any:
    """Forty fictional tasks in twenty chains of two levels, a fifth of them in the lab."""
    rng = random.Random(seed)
    tasks: list[Any] = []
    for chain in range(chains):
        data_id = 1000 + chain
        lane = "lab" if chain % 5 == 0 else "builder"
        resource = "elixir" if lane == "lab" else "gold"
        previous: str | None = None
        for to_level in (2, 3):
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
    return problem(
        tasks,
        lanes={"builder": builders, "lab": 1},
        sleep=nights(days + 1),
        initial={"gold": 20_000, "elixir": 20_000},
        cap={"gold": 10**6, "elixir": 10**6},
        income={"gold": 5000, "elixir": 5000},
        days=days,
    )


FIXTURES: dict[str, Callable[[], Any]] = {
    "greedy_trap": greedy_trap,
    "income_low": functools.partial(income_race, 100),
    "income_high": functools.partial(income_race, 10_000),
    "sleep_early": functools.partial(sleep_race, 5),
    "sleep_late": functools.partial(sleep_race, 6),
    "sleep_single": sleep_single,
    "three_strict": functools.partial(three_tasks, "strict"),
    "three_tolerance": functools.partial(three_tasks, "tolerance", tolerance_s=HOUR),
    "three_progress": functools.partial(three_tasks, "progress"),
    "logins_capped": functools.partial(twenty_tasks, max_logins_per_day=2),
    "logins_free": twenty_tasks,
    "logins_min": functools.partial(twenty_tasks, "min_logins"),
    "reference": reference_race,
    "preferred": preferred_window,
    **{f"generated_{b}": functools.partial(generated, b) for b in range(2, 7)},
}

_PLANS: dict[tuple[str, str], Any] = {}


def plan_for(engine: str, name: str) -> Any:
    """The plan of `engine` on the fixture `name` with `Objective()` and `TEST_CONFIG`, cached."""
    key = (engine, name)
    if key not in _PLANS:
        _PLANS[key] = solve(engine, FIXTURES[name]())
    return _PLANS[key]


# --------------------------------------------------------------------------- reading results


def code_of(violation: Any) -> str:
    if isinstance(violation, Mapping):
        return str(violation["code"])
    return str(violation.code)


def codes(result: Any) -> list[str]:
    return sorted(code_of(violation) for violation in result.violations)


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


def end_of(plan: Any, task_id: str) -> int:
    return int(assignment_of(plan, task_id).end)


def candidates_of(decision: Any, task_id: str | None) -> list[Any]:
    return [c for c in decision.candidates if c.task_id == task_id]


def scored(plan: Any) -> int:
    """Number of evaluations recorded in the trace."""
    invalid = invalid_score()
    return sum(1 for d in plan.trace for c in d.candidates if c.score != invalid)


def expected_choice(candidates: Sequence[Any]) -> Any:
    """Highest score; ties by task_id ascending, the wait candidate last."""
    return sorted(candidates, key=lambda c: (-c.score, c.task_id is None, c.task_id or ""))[0]


def wait_after(calendar: Any, t: int) -> int:
    """Seconds from `t` to the next instant where the player can act."""
    if calendar.can_act(t):
        return 0
    spans = (*calendar.sleep, *calendar.work, *calendar.absence, *calendar.available)
    for bound in sorted({bound for span in spans for bound in span}):
        if bound > t and calendar.can_act(bound):
            return bound - t
    raise AssertionError(f"no can_act instant after {t}")


def in_window(windows: Sequence[tuple[int, int]], t: int) -> bool:
    return any(start <= t < end for start, end in windows)


def logins_per_day(prob: Any, plan: Any) -> Counter[Any]:
    """Logins per local day: a login is a maximal group of starts at most login_merge_s apart."""
    merge = prob.policy.login_merge_s
    per_day: Counter[Any] = Counter()
    previous: int | None = None
    for start in sorted(a.start for a in plan.assignments):
        if previous is None or start - previous > merge:
            per_day[prob.calendar.local_day(start)] += 1
        previous = start
    return per_day


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


# --------------------------------------------------------------------------- registry


@pytest.mark.parametrize("engine", ["baseline", "rollout"])
def test_both_engines_are_registered_and_return_a_heuristic_plan(engine: str) -> None:
    prob = greedy_trap()
    planner = planners()[engine]
    produced = planner.solve(prob, objective(), config(), reference=None)
    assert isinstance(produced, model().Plan)
    assert produced.engine == engine
    assert produced.proof.kind == "heuristic"
    assert produced.proof.bound is None
    assert produced.assignments
    assert_clean(judge(prob, produced))
    assert produced.proof.objective == pytest.approx(score_of(prob, produced))


# --------------------------------------------------------------------------- validity


@pytest.mark.parametrize("engine", ["rollout", "baseline"])
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_gives_a_valid_plan_without_violation(engine: str, name: str) -> None:
    prob = FIXTURES[name]()
    produced = plan_for(engine, name)
    result = judge(prob, produced)
    assert result.valid, codes(result)
    assert list(result.violations) == []
    assert produced.assignments


@pytest.mark.parametrize("builders", range(2, 7))
def test_the_generated_forty_task_problem_is_fully_planned(builders: int) -> None:
    prob = generated(builders)
    assert len(prob.tasks) == 40
    assert prob.lanes["builder"] == builders
    produced = plan_for("rollout", f"generated_{builders}")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 40


# --------------------------------------------------------------------------- benchmark file


def test_the_benchmark_file_holds_slow_tests_only() -> None:
    assert BENCHMARK.is_file(), f"{BENCHMARK} is missing"
    source = BENCHMARK.read_text(encoding="utf-8")
    for literal in ("300", "400", "simulate", "timeout(0)"):
        assert literal in source, literal
    slow = collected("slow")
    assert len(slow) >= 2, slow
    assert all("test_rollout_benchmark.py::" in node for node in slow), slow
    assert collected("not slow") == []


# --------------------------------------------------------------------------- greedy trap


def test_baseline_sorts_by_ratio_and_rollout_scores_strictly_more() -> None:
    prob = greedy_trap()
    base = plan_for("baseline", "greedy_trap")
    roll = plan_for("rollout", "greedy_trap")
    assert {a.task_id: a.start for a in base.assignments} == {
        TRAP_LONG: NOW,
        TRAP_KEY: NOW + 10 * HOUR,
        TRAP_UNLOCKED: NOW + 11 * HOUR,
    }
    assert start_of(roll, TRAP_KEY) == NOW
    assert start_of(roll, TRAP_UNLOCKED) < start_of(roll, TRAP_LONG)
    assert score_of(prob, roll) > score_of(prob, base)


# --------------------------------------------------------------------------- trace


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_each_decision_keeps_the_best_scored_candidate(name: str) -> None:
    prob = FIXTURES[name]()
    produced = plan_for("rollout", name)
    invalid = invalid_score()
    assert produced.assignments
    assert produced.trace
    assert scored(produced) <= config().max_rollouts
    starts = {a.task_id: a.start for a in produced.assignments}
    previous = prob.now
    for decision in produced.trace:
        assert decision.at >= previous
        previous = decision.at
        assert decision.candidates
        for candidate in decision.candidates:
            assert candidate.start >= decision.at
        chosen = decision.chosen
        assert chosen in tuple(decision.candidates)
        assert chosen.score != invalid
        best = expected_choice(decision.candidates)
        assert (chosen.task_id, chosen.start) == (best.task_id, best.start)
        assert chosen.score == best.score
        if chosen.task_id is not None:
            assert starts.get(chosen.task_id) == chosen.start


@pytest.mark.parametrize("name", ["preferred", "sleep_single"])
def test_a_decision_where_waiting_is_possible_lists_the_wait_candidate(name: str) -> None:
    produced = plan_for("rollout", name)
    first = produced.trace[0]
    assert first.at == NOW
    assert candidates_of(first, None)


# --------------------------------------------------------------------------- metamorphic


def test_the_income_decides_which_task_starts_first() -> None:
    low = plan_for("rollout", "income_low")
    high = plan_for("rollout", "income_high")
    assert start_of(low, CHEAP) < start_of(low, PRICEY)
    assert start_of(high, PRICEY) < start_of(high, CHEAP)


def test_moving_the_night_reverses_the_order() -> None:
    early = plan_for("rollout", "sleep_early")
    late = plan_for("rollout", "sleep_late")
    assert start_of(early, SHORT) < start_of(early, LONG)
    assert start_of(late, LONG) < start_of(late, SHORT)


# --------------------------------------------------------------------------- strict policy


def test_strict_policy_delays_a_start_that_would_end_in_sleep() -> None:
    prob = sleep_single()
    produced = plan_for("rollout", "sleep_single")
    result = judge(prob, produced)
    assert "end_in_sleep" not in codes(result)
    assert_clean(result)
    assert start_of(produced, LONG) == NOW + 13 * HOUR
    assert not prob.calendar.is_sleep(end_of(produced, LONG))

    first = produced.trace[0]
    assert first.at == NOW
    immediate = [c for c in candidates_of(first, LONG) if c.start == NOW]
    later = [c for c in candidates_of(first, LONG) if c.start == NOW + 13 * HOUR]
    assert len(immediate) == 1
    assert immediate[0].score == invalid_score()
    assert len(later) == 1
    assert later[0].score > invalid_score()
    assert later[0].score == pytest.approx(score_of(prob, produced))
    assert (first.chosen.task_id, first.chosen.start) == (LONG, NOW + 13 * HOUR)


def test_a_shorter_task_is_launched_immediately_instead() -> None:
    prob = sleep_race(5)
    produced = plan_for("rollout", "sleep_early")
    assert_clean(judge(prob, produced))
    assert start_of(produced, SHORT) == NOW
    assert start_of(produced, LONG) == NOW + 13 * HOUR

    first = produced.trace[0]
    assert first.at == NOW
    assert [c.score for c in candidates_of(first, LONG) if c.start == NOW] == [invalid_score()]
    later = [c for c in candidates_of(first, LONG) if c.start == NOW + 13 * HOUR]
    assert len(later) == 1
    assert later[0].score > invalid_score()
    assert any(c.start == NOW and c.score > invalid_score() for c in candidates_of(first, SHORT))


# --------------------------------------------------------------------------- tolerance, progress


def test_tolerance_policy_bounds_the_wait_after_every_end() -> None:
    prob = three_tasks("tolerance", tolerance_s=HOUR)
    produced = plan_for("rollout", "three_tolerance")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 3
    for assignment in produced.assignments:
        assert wait_after(prob.calendar, assignment.end) <= HOUR, assignment


def test_progress_policy_ends_no_later_than_strict() -> None:
    strict = plan_for("rollout", "three_strict")
    progress = plan_for("rollout", "three_progress")
    assert {a.task_id for a in strict.assignments} == set(THREE)
    assert {a.task_id for a in progress.assignments} == set(THREE)
    assert sum(a.end for a in progress.assignments) <= sum(a.end for a in strict.assignments)


# --------------------------------------------------------------------------- logins


def test_the_daily_login_limit_is_respected_on_twenty_tasks() -> None:
    prob = twenty_tasks(max_logins_per_day=2)
    produced = plan_for("rollout", "logins_capped")
    result = judge(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_planned") == 20
    per_day = logins_per_day(prob, produced)
    assert per_day
    assert max(per_day.values()) <= 2, per_day


def test_min_logins_with_a_login_weight_logs_in_no_more_than_progress() -> None:
    prob_min = twenty_tasks("min_logins")
    plan_min = solve("rollout", prob_min, objective(logins=1.0))
    prob_progress = twenty_tasks()
    plan_progress = plan_for("rollout", "logins_free")
    result_min = judge(prob_min, plan_min)
    result_progress = judge(prob_progress, plan_progress)
    assert_clean(result_min)
    assert_clean(result_progress)
    assert metric(result_min, "tasks_planned") == 20
    assert metric(result_progress, "tasks_planned") == 20
    assert metric(result_min, "logins") <= metric(result_progress, "logins")


# --------------------------------------------------------------------------- budget


@pytest.mark.parametrize("budget", [200, 10])
def test_the_rollout_never_scores_more_candidates_than_max_rollouts(budget: int) -> None:
    prob = generated(3)
    cfg = dataclasses.replace(config(), max_rollouts=budget)
    produced = solve("rollout", prob, cfg=cfg)
    assert_clean(judge(prob, produced))
    assert produced.trace
    assert scored(produced) <= budget
    if budget == config().max_rollouts:
        assert scored(produced) >= 1


# --------------------------------------------------------------------------- reference


def test_a_positive_perturbation_weight_keeps_the_reference_starts() -> None:
    prob = reference_race()
    reference = reference_plan()
    with_weight = solve("rollout", prob, objective(perturbation=100.0), reference=reference)
    without = solve("rollout", prob, objective(), reference=reference)
    assert_clean(judge(prob, with_weight, reference))
    assert_clean(judge(prob, without, reference))

    wanted = {a.task_id: a.start for a in reference.assignments}
    kept_with = sum(1 for a in with_weight.assignments if wanted.get(a.task_id) == a.start)
    kept_without = sum(1 for a in without.assignments if wanted.get(a.task_id) == a.start)
    assert kept_with >= kept_without
    assert kept_with == len(wanted)
    assert start_of(without, STRONG) == NOW


# --------------------------------------------------------------------------- determinism


@pytest.mark.parametrize("name", ["generated_4", "logins_capped", "sleep_early"])
def test_two_identical_runs_give_the_same_plan_byte_for_byte(name: str) -> None:
    first = solve("rollout", FIXTURES[name]())
    second = solve("rollout", FIXTURES[name]())
    assert first.assignments
    assert first == second
    assert serialized(first) == serialized(second)


# --------------------------------------------------------------------------- preferred


def test_a_preferred_weight_moves_the_end_into_a_preferred_window() -> None:
    prob = preferred_window()
    with_weight = solve("rollout", prob, objective(preferred=1.0))
    without = plan_for("rollout", "preferred")
    assert_clean(judge(prob, with_weight))
    assert_clean(judge(prob, without))
    assert in_window(prob.calendar.preferred, end_of(with_weight, SINGLE))
    assert start_of(with_weight, SINGLE) == NOW + 8 * HOUR
    assert not in_window(prob.calendar.preferred, end_of(without, SINGLE))
    assert start_of(without, SINGLE) == NOW
