"""Unit tests of the heuristic planners: the cases the acceptance tests leave out."""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from clashpilot.planning.heuristics.baseline import INVALID_PLAN_WARNING, State, ratio
from clashpilot.planning.model import FrozenJob, Modifier, Policy
from clashpilot.planning.model import ResourceModel as Resources
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from support.problems import (
    DAY,
    HOUR,
    NOW,
    TEST_CONFIG,
    generate_problem,
    make_calendar,
    make_problem,
    make_task,
)


def solve(engine: str, problem: Any, objective: Objective | None = None) -> Any:
    return PLANNERS[engine].solve(problem, objective or Objective(), TEST_CONFIG)


def starts(plan: Any) -> dict[str, int]:
    return {assignment.task_id: assignment.start for assignment in plan.assignments}


# --------------------------------------------------------------------------- registry


def test_importing_the_planning_package_registers_both_engines() -> None:
    import clashpilot.planning  # noqa: F401

    assert {"baseline", "rollout"} <= set(PLANNERS)


def test_ratio_uses_at_least_one_second() -> None:
    instant = make_task("1:0:2", duration_s=0, value=5.0)
    assert ratio(instant) == 5.0
    assert ratio(make_task("2:0:2", duration_s=HOUR, value=2.0)) == 2.0 / HOUR


# --------------------------------------------------------------------------- frozen jobs and lanes


def test_a_frozen_job_holds_its_slot_and_satisfies_requires() -> None:
    running = FrozenJob(
        task_id="1001:0:2", lane="builder", lane_slot=0, start=NOW - HOUR, end=NOW + HOUR
    )
    following = make_task("1001:0:3", duration_s=HOUR, requires=("1001:0:2",))
    other = make_task("1002:0:2", duration_s=HOUR)
    problem = make_problem(
        lanes={"builder": 1}, tasks=(following, other), frozen=(running,), calendar=make_calendar()
    )
    for engine in ("baseline", "rollout"):
        plan = solve(engine, problem)
        assert simulate(problem, plan).valid
        assert starts(plan)[following.id] >= NOW + HOUR
        assert starts(plan)[other.id] >= NOW + HOUR


def test_an_extra_frozen_job_leaves_its_lane_free() -> None:
    running = FrozenJob(
        task_id="1001:0:2",
        lane="builder",
        lane_slot=0,
        start=NOW - HOUR,
        end=NOW + HOUR,
        extra=True,
    )
    other = make_task("1002:0:2", duration_s=HOUR)
    problem = make_problem(lanes={"builder": 1}, tasks=(other,), frozen=(running,))
    plan = solve("rollout", problem)
    assert simulate(problem, plan).valid
    assert starts(plan)[other.id] == NOW


def test_a_task_on_a_lane_without_capacity_is_never_planned() -> None:
    pet = make_task("1001:0:2", lane="pet_house", duration_s=HOUR)
    problem = make_problem(lanes={"builder": 1}, tasks=(pet, make_task("1002:0:2")))
    plan = solve("rollout", problem)
    assert simulate(problem, plan).valid
    assert set(starts(plan)) == {"1002:0:2"}


def test_a_modifier_bound_is_a_decision_point() -> None:
    sale = Modifier(
        start=NOW + HOUR, end=NOW + 2 * HOUR, lane="builder", duration_pct=100, cost_pct=50
    )
    pricey = make_task("1001:0:2", duration_s=HOUR, cost={"gold": 2000})
    problem = make_problem(
        lanes={"builder": 1},
        tasks=(pricey,),
        modifiers=(sale,),
        resources=Resources(
            initial={"gold": 1000}, cap={"gold": 10**6}, income_per_hour={"gold": 0}
        ),
    )
    plan = solve("rollout", problem)
    assert simulate(problem, plan).valid
    assert starts(plan) == {pricey.id: NOW + HOUR}


def test_the_login_limit_waits_for_the_next_local_day() -> None:
    tasks = tuple(make_task(f"{1000 + index}:0:2", duration_s=10 * 60) for index in range(3))
    problem = make_problem(
        lanes={"builder": 1},
        tasks=tasks,
        calendar=make_calendar(tz="Europe/Paris"),
        policy=Policy(mode="progress", max_logins_per_day=1, login_merge_s=0),
    )
    plan = solve("rollout", problem)
    result = simulate(problem, plan)
    assert result.valid
    assert result.metrics.tasks_planned == 3
    days = {problem.calendar.local_day(start) for start in starts(plan).values()}
    assert len(days) == 3


def test_an_empty_problem_gives_an_empty_valid_plan() -> None:
    problem = make_problem(tasks=())
    for engine in ("baseline", "rollout"):
        plan = solve(engine, problem)
        assert plan.assignments == ()
        assert plan.trace == ()
        assert plan.warnings == ()
        assert simulate(problem, plan).valid


def test_a_zero_budget_still_completes_the_plan_without_trace() -> None:
    from dataclasses import replace

    problem = generate_problem(12, builders=2, days=10)
    plan = PLANNERS["rollout"].solve(problem, Objective(), replace(TEST_CONFIG, max_rollouts=0))
    result = simulate(problem, plan)
    assert result.valid
    assert result.metrics.tasks_planned == 12
    assert plan.trace == ()


def test_the_invalid_plan_warning_names_the_fallback() -> None:
    assert INVALID_PLAN_WARNING == "heuristic_invalid_plan"


# --------------------------------------------------------------------------- generator


def test_generate_problem_is_deterministic_and_sized() -> None:
    first, again = generate_problem(37, seed=7), generate_problem(37, seed=7)
    assert [task.id for task in first.tasks] == [task.id for task in again.tasks]
    assert [task.duration_s for task in first.tasks] == [task.duration_s for task in again.tasks]
    assert len(first.tasks) == 37
    assert len({task.id for task in first.tasks}) == 37
    known = {task.id for task in first.tasks}
    assert all(set(task.requires) <= known for task in first.tasks)
    assert first.lanes == {"builder": 2, "lab": 1}


# --------------------------------------------------------------------------- properties


@st.composite
def random_problems(draw: st.DrawFn) -> Any:
    count = draw(st.integers(min_value=1, max_value=8))
    tasks = []
    for index in range(count):
        lane = draw(st.sampled_from(["builder", "lab"]))
        requires = (tasks[-1].id,) if tasks and draw(st.booleans()) else ()
        tasks.append(
            make_task(
                f"{1000 + index}:0:2",
                lane=lane,
                duration_s=draw(st.integers(min_value=0, max_value=20 * HOUR)),
                cost={draw(st.sampled_from(["gold", "elixir"])): draw(st.integers(0, 20_000))},
                requires=requires,
                value=draw(st.floats(min_value=0.0, max_value=10.0)),
            )
        )
    mode = draw(st.sampled_from(["strict", "tolerance", "progress", "min_logins"]))
    night = draw(st.integers(min_value=0, max_value=20))
    return make_problem(
        lanes={"builder": draw(st.integers(1, 3)), "lab": draw(st.integers(0, 1))},
        tasks=tuple(tasks),
        resources=Resources(
            initial={"gold": draw(st.integers(0, 30_000)), "elixir": draw(st.integers(0, 30_000))},
            cap={"gold": draw(st.integers(0, 40_000)), "elixir": draw(st.integers(0, 40_000))},
            income_per_hour={
                "gold": draw(st.integers(0, 5_000)),
                "elixir": draw(st.integers(0, 5_000)),
            },
        ),
        horizon_end=NOW + draw(st.integers(1, 5)) * DAY,
        calendar=make_calendar(
            tz="Europe/Paris",
            sleep=[
                (NOW + night * HOUR + day * DAY, NOW + (night + 8) * HOUR + day * DAY)
                for day in range(6)
            ],
        ),
        policy=Policy(
            mode=mode,
            tolerance_s=draw(st.integers(0, 2 * HOUR)),
            max_logins_per_day=draw(st.sampled_from([None, 1, 3])),
        ),
    )


@settings(max_examples=40, deadline=None)
@given(random_problems())
def test_both_engines_always_return_a_plan_the_simulator_accepts(problem: Any) -> None:
    for engine in ("baseline", "rollout"):
        plan = solve(engine, problem)
        result = simulate(problem, plan)
        assert result.valid, (engine, [violation.code for violation in result.violations])
        assert plan.warnings == ()


@settings(max_examples=40, deadline=None)
@given(random_problems())
def test_rollout_scores_at_least_as_well_as_baseline(problem: Any) -> None:
    from clashpilot.planning.objective import score

    base = solve("baseline", problem)
    roll = solve("rollout", problem)
    assert (
        score(simulate(problem, roll), Objective(), problem)
        >= score(simulate(problem, base), Objective(), problem) - 1e-12
    )


def test_state_next_point_is_strictly_later() -> None:
    problem = generate_problem(10, builders=2, days=3)
    state = State(problem)
    t = problem.now
    for _ in range(50):
        following = state.next_point(t)
        if following is None:
            break
        assert following > t
        t = following
