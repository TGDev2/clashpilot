"""Unit tests of `planning/critical.py` beyond the E22 acceptance cases."""

from __future__ import annotations

from clashpilot.planning.critical import Bound, critical_path, current_level_ids
from clashpilot.planning.model import Assignment, Plan, Policy, Proof, Task
from clashpilot.planning.simulate import simulate
from clashpilot.planning.strategies import _town_hall_last
from support.problems import HOUR, NOW, make_calendar, make_problem, make_task


def town_hall(name: str = "1:0:10", hours: int = 5, requires: tuple[str, ...] = ()) -> Task:
    return make_task(
        name, duration_s=hours * HOUR, requires=requires, tags=("town_hall",), category="town_hall"
    )


def hero(name: str, hours: int, requires: tuple[str, ...] = ()) -> Task:
    return make_task(
        name, duration_s=hours * HOUR, requires=requires, tags=("hero",), category="hero"
    )


def test_town_hall_alone_finishes_from_now() -> None:
    result = critical_path(make_problem(tasks=(town_hall(),), lanes={"builder": 2}))
    assert result.set_finish == NOW
    assert result.finish_after == NOW + 5 * HOUR
    assert result.finish_parallel == NOW + 5 * HOUR
    assert result.work_finish == {}
    assert result.bound == Bound("chain", None)
    assert result.chains == ()
    assert result.float_s == {"1:0:10": 0}


def test_missing_requirement_is_met() -> None:
    tasks = (hero("2001:0:3", 10, ("2001:0:2",)), town_hall())
    result = critical_path(make_problem(tasks=tasks, lanes={"builder": 2}))
    assert result.set_finish == NOW + 10 * HOUR


def test_cycle_does_not_raise() -> None:
    tasks = (hero("2001:0:2", 1, ("2001:0:3",)), hero("2001:0:3", 1, ("2001:0:2",)), town_hall())
    result = critical_path(make_problem(tasks=tasks, lanes={"builder": 2}))
    assert result.target is not None
    assert result.set_finish is not None


def test_hero_blackout_delays_a_hero_start() -> None:
    tasks = (hero("2001:0:2", 2), town_hall())
    blackout = (NOW + HOUR, NOW + 4 * HOUR)
    problem = make_problem(tasks=tasks, lanes={"builder": 2}, hero_blackouts=(blackout,))
    chain = critical_path(problem).chains[0]
    assert chain.finish == NOW + 6 * HOUR
    assert chain.calendar_wait_s == 4 * HOUR


def test_tolerance_keeps_the_end_close_to_an_acting_instant() -> None:
    sleep = [(NOW + 4 * HOUR, NOW + 9 * HOUR)]
    tasks = (hero("2001:0:2", 6), town_hall(hours=1))
    problem = make_problem(
        tasks=tasks,
        lanes={"builder": 2},
        calendar=make_calendar(sleep=sleep),
        policy=Policy(mode="tolerance", tolerance_s=HOUR),
    )
    chain = critical_path(problem).chains[0]
    # Started at now it would end 3 h before the wake-up: the end moves to 1 h before it.
    assert chain.finish == NOW + 8 * HOUR
    assert chain.calendar_wait_s == 2 * HOUR
    start = chain.finish - 6 * HOUR
    plan = Plan(
        (Assignment("2001:0:2", "builder", 0, start, chain.finish),), "test", Proof("heuristic", 0)
    )
    assert simulate(problem, plan).valid


def test_strict_start_is_accepted_by_simulate() -> None:
    sleep = [(NOW + 5 * HOUR, NOW + 13 * HOUR), (NOW + 29 * HOUR, NOW + 37 * HOUR)]
    tasks = (hero("2001:0:2", 8), hero("2001:0:3", 8, ("2001:0:2",)), town_hall(hours=1))
    problem = make_problem(
        tasks=tasks,
        lanes={"builder": 2},
        calendar=make_calendar(sleep=sleep),
        policy=Policy(mode="strict"),
    )
    result = critical_path(problem)
    chain = result.chains[0]
    assert chain.task_ids == ("2001:0:2", "2001:0:3")
    # Each start waits for the wake-up: N+13 h (end N+21 h), then N+37 h (end N+45 h).
    assert chain.finish == NOW + 45 * HOUR
    assert chain.calendar_wait_s == (13 + 16) * HOUR
    assignments = (
        Assignment("2001:0:2", "builder", 0, NOW + 13 * HOUR, NOW + 21 * HOUR),
        Assignment("2001:0:3", "builder", 0, NOW + 37 * HOUR, NOW + 45 * HOUR),
    )
    assert simulate(problem, Plan(assignments, "test", Proof("heuristic", 0))).valid


def test_skip_lanes_in_town_hall_last() -> None:
    tasks = (
        town_hall(),
        make_task("3001:0:2"),
        make_task("4001:0:2", lane="lab", category="troop", tags=("troop",)),
    )
    assert current_level_ids(tasks, ("lab",)) == ("3001:0:2",)
    ruled = {task.id: task.requires for task in _town_hall_last(tasks, ("lab",))}
    assert ruled["1:0:10"] == ("3001:0:2",)
    assert {task.id: task.requires for task in _town_hall_last(tasks)}["1:0:10"] == (
        "3001:0:2",
        "4001:0:2",
    )


def test_without_town_hall_every_task_is_current() -> None:
    tasks = (make_task("3001:0:2"), make_task("3001:0:3", requires=("3001:0:2",)))
    assert current_level_ids(tasks) == ("3001:0:2", "3001:0:3")
