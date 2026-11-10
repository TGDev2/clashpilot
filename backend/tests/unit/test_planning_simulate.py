"""Unit tests of the exact simulator: properties and the cases the acceptance tests leave out."""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from clashpilot.planning.model import Assignment, FrozenJob, Modifier, Plan, Policy, Proof
from clashpilot.planning.model import ResourceModel as Resources
from clashpilot.planning.simulate import SCALE, effective_duration, simulate
from support.problems import DAY, HOUR, NOW, make_calendar, make_problem, make_task


def plan_of(*assignments: Assignment) -> Plan:
    return Plan(assignments=assignments, engine="test", proof=Proof("heuristic", 0.0))


def at(task_id: str, start: int, duration: int, lane: str = "builder", slot: int = 0) -> Assignment:
    return Assignment(task_id, lane, slot, start, start + duration)


def codes(result: Any) -> list[str]:
    return [violation.code for violation in result.violations]


# --------------------------------------------------------------------------- properties


@st.composite
def random_runs(draw: st.DrawFn) -> tuple[Any, Plan]:
    count = draw(st.integers(min_value=0, max_value=6))
    tasks = []
    assignments = []
    for index in range(count):
        lane = draw(st.sampled_from(["builder", "lab"]))
        duration = draw(st.integers(min_value=1, max_value=6 * HOUR))
        cost = {draw(st.sampled_from(["gold", "elixir"])): draw(st.integers(0, 30_000))}
        task = make_task(f"{index + 1}:0:2", lane=lane, duration_s=duration, cost=cost)
        tasks.append(task)
        if draw(st.booleans()):
            start = NOW + draw(st.integers(min_value=-HOUR, max_value=2 * DAY))
            slot = draw(st.integers(min_value=0, max_value=2))
            assignments.append(at(task.id, start, duration, lane, slot))
    resources = Resources(
        initial={"gold": draw(st.integers(0, 40_000)), "elixir": draw(st.integers(0, 40_000))},
        cap={"gold": draw(st.integers(0, 30_000)), "elixir": draw(st.integers(0, 30_000))},
        income_per_hour={"gold": draw(st.integers(0, 9_999)), "elixir": draw(st.integers(0, 99))},
    )
    problem = make_problem(
        tasks=tuple(tasks),
        resources=resources,
        horizon_end=NOW + 3 * DAY,
        calendar=make_calendar(sleep=[(NOW + 14 * HOUR, NOW + 22 * HOUR)]),
        policy=Policy(mode="tolerance", tolerance_s=HOUR, max_logins_per_day=2),
    )
    return problem, plan_of(*assignments)


@settings(max_examples=50, deadline=None)
@given(random_runs())
def test_a_stock_never_goes_above_its_cap_nor_below_zero(run: tuple[Any, Plan]) -> None:
    problem, plan = run
    result = simulate(problem, plan)
    for _, stocks in result.stock_timeline:
        for name, cap in problem.resources.cap.items():
            assert 0 <= stocks[name] <= cap * SCALE
            assert isinstance(stocks[name], int)


@settings(max_examples=50, deadline=None)
@given(random_runs())
def test_the_simulation_is_deterministic(run: tuple[Any, Plan]) -> None:
    problem, plan = run
    assert simulate(problem, plan) == simulate(problem, plan)


@settings(max_examples=50, deadline=None)
@given(random_runs(), st.randoms(use_true_random=False))
def test_the_order_of_the_assignments_does_not_matter(run: tuple[Any, Plan], rng: Any) -> None:
    problem, plan = run
    shuffled = list(plan.assignments)
    rng.shuffle(shuffled)
    assert simulate(problem, plan_of(*shuffled)) == simulate(problem, plan)


# --------------------------------------------------------------------------- plan screening


def test_an_end_that_disagrees_with_the_effective_duration_is_refused() -> None:
    task = make_task("1:0:2", duration_s=HOUR)
    result = simulate(make_problem(tasks=(task,)), plan_of(at(task.id, NOW, HOUR - 1)))
    assert codes(result) == ["duration_mismatch"]


def test_the_end_follows_the_modifiers_of_the_start() -> None:
    task = make_task("1:0:2", duration_s=HOUR)
    bonus = Modifier(start=NOW, end=NOW + DAY, lane="builder", duration_pct=80, cost_pct=100)
    problem = make_problem(tasks=(task,), modifiers=(bonus,))
    assert effective_duration(problem, task, NOW) == 2880
    assert simulate(problem, plan_of(at(task.id, NOW, 2880))).valid


def test_unknown_duplicate_and_misplaced_assignments_are_refused() -> None:
    task = make_task("1:0:2")
    problem = make_problem(tasks=(task,), lanes={"builder": 2, "lab": 1})
    result = simulate(
        problem,
        plan_of(
            at(task.id, NOW, HOUR),
            at(task.id, NOW + 2 * HOUR, HOUR, slot=1),
            at("9:0:9", NOW, HOUR),
        ),
    )
    assert sorted(codes(result)) == ["duplicate_task", "unknown_task"]
    wrong_lane = simulate(problem, plan_of(at(task.id, NOW, HOUR, lane="lab")))
    assert "wrong_lane" in codes(wrong_lane)


def test_a_required_task_missing_from_the_plan_breaks_precedence() -> None:
    first = make_task("1:0:2")
    second = make_task("1:0:3", requires=(first.id,))
    result = simulate(make_problem(tasks=(first, second)), plan_of(at(second.id, NOW, HOUR)))
    assert codes(result) == ["precedence"]


def test_two_jobs_on_one_slot_conflict_even_when_the_lane_has_room() -> None:
    one, two = make_task("1:0:2"), make_task("2:0:2")
    problem = make_problem(tasks=(one, two), lanes={"builder": 2, "lab": 1})
    same_slot = plan_of(at(one.id, NOW, HOUR, slot=0), at(two.id, NOW + 60, HOUR, slot=0))
    assert codes(simulate(problem, same_slot)) == ["lane_capacity"]


def test_frozen_jobs_overflowing_their_lane_alone_are_not_the_plan_s_fault() -> None:
    running = (
        FrozenJob("1:0:2", "builder", 0, NOW - HOUR, NOW + HOUR),
        FrozenJob("2:0:2", "builder", 1, NOW - HOUR, NOW + HOUR),
    )
    problem = make_problem(tasks=(), frozen=running, lanes={"builder": 1, "lab": 1})
    assert simulate(problem, plan_of()).valid


# ------------------------------------------------------------------------- availability and logins


def test_tolerance_counts_the_wait_until_work_allows_acting_again() -> None:
    # Work from 2 h to 6 h with a break from 3 h to 4 h: an end at 2 h waits one hour.
    calendar = make_calendar(
        work=[(NOW + 2 * HOUR, NOW + 6 * HOUR)], available=[(NOW + 3 * HOUR, NOW + 4 * HOUR)]
    )
    task = make_task("1:0:2", duration_s=2 * HOUR)
    for tolerance, expected in ((HOUR - 1, ["idle_beyond_tolerance"]), (HOUR, [])):
        problem = make_problem(
            tasks=(task,),
            calendar=calendar,
            policy=Policy(mode="tolerance", tolerance_s=tolerance),
        )
        assert codes(simulate(problem, plan_of(at(task.id, NOW, 2 * HOUR)))) == expected


def test_starts_chained_within_the_merge_delay_are_one_login() -> None:
    tasks = tuple(make_task(f"{n}:0:2", duration_s=60) for n in (1, 2, 3))
    problem = make_problem(
        tasks=tasks,
        lanes={"builder": 3, "lab": 1},
        policy=Policy(mode="progress", max_logins_per_day=1),
    )
    # 0, 900 and 1800 s: each start is within 900 s of the previous one.
    plan = plan_of(*(at(t.id, NOW + 900 * i, 60, slot=i) for i, t in enumerate(tasks)))
    result = simulate(problem, plan)
    assert result.valid
    assert result.metrics.logins == 1


# --------------------------------------------------------------------------- resources and metrics


def test_spill_and_income_are_counted_over_the_horizon_only() -> None:
    task = make_task("1:0:2", cost={"gold": 10})
    problem = make_problem(
        tasks=(task,),
        horizon_end=NOW + HOUR,
        resources=Resources(
            initial={"gold": 0}, cap={"gold": 1000}, income_per_hour={"gold": 3600}
        ),
    )
    # The task starts after the horizon: the stock keeps growing until then, but is not counted.
    result = simulate(problem, plan_of(at(task.id, NOW + 2 * HOUR, HOUR)))
    assert result.valid
    assert result.metrics.spill["gold"] == 2600
    assert result.metrics.income["gold"] == 3600
    times = [t for t, _ in result.stock_timeline]
    assert times == [NOW, NOW + HOUR, NOW + 2 * HOUR]
    assert result.stock_timeline[-1][1]["gold"] == (1000 - 10) * SCALE


def test_a_resource_without_cap_is_unlimited() -> None:
    problem = make_problem(
        tasks=(),
        horizon_end=NOW + HOUR,
        resources=Resources(initial={"gems": 5}, cap={}, income_per_hour={"gems": 1}),
    )
    result = simulate(problem, plan_of())
    assert result.stock_timeline[-1][1]["gems"] == 5 * SCALE + HOUR
    assert result.metrics.spill["gems"] == 0


def test_metrics_describe_the_plan() -> None:
    first = make_task("1:0:2", duration_s=HOUR)
    second = make_task("2:0:2", duration_s=HOUR)
    calendar = make_calendar(
        sleep=[(NOW + 90 * 60, NOW + 8 * HOUR)], preferred=[(NOW + 30 * 60, NOW + 61 * 60)]
    )
    problem = make_problem(
        tasks=(first, second),
        horizon_end=NOW + DAY,
        calendar=calendar,
        frozen=(FrozenJob("3:0:2", "lab", 0, NOW - HOUR, NOW + HOUR),),
    )
    plan = plan_of(at(first.id, NOW, HOUR), at(second.id, NOW + HOUR, HOUR, slot=1))
    reference = plan_of(at(first.id, NOW + 600, HOUR))
    metrics = simulate(problem, plan, reference).metrics
    assert metrics.tasks_planned == 2
    assert metrics.tasks_completed == 2
    assert dict(metrics.ends) == {first.id: NOW + HOUR, second.id: NOW + 2 * HOUR}
    assert metrics.busy_s == 3 * HOUR
    assert metrics.ends_in_sleep == 1
    assert metrics.ends_in_preferred == 1
    assert metrics.logins == 2
    assert metrics.perturbation == 600 / DAY
