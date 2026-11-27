"""Unit tests of `prioritize_critical` (plan option `critical_path`, E24)."""

from __future__ import annotations

import dataclasses

from clashpilot.planning.critical import critical_path, prioritize_critical
from clashpilot.planning.model import Task
from support.problems import HOUR, make_problem, make_task


def town_hall(requires: tuple[str, ...] = ()) -> Task:
    return make_task(
        "1:0:10", duration_s=5 * HOUR, requires=requires, tags=("town_hall",), category="town_hall"
    )


def hero(name: str, hours: int, requires: tuple[str, ...] = ()) -> Task:
    return make_task(
        name, duration_s=hours * HOUR, requires=requires, tags=("hero",), category="hero"
    )


def ratio(task: Task) -> float:
    return task.value / max(1, task.duration_s)


def test_a_problem_without_town_hall_is_unchanged() -> None:
    problem = make_problem(tasks=(hero("2001:0:2", 10), make_task("3001:0:2", duration_s=HOUR)))
    assert prioritize_critical(problem) is problem


def test_a_task_without_float_dominates_every_task_with_float() -> None:
    tasks = (
        hero("2001:0:2", 10),
        hero("2001:0:3", 20, ("2001:0:2",)),
        # Short and worth a lot: the best ratio of the problem before the option.
        make_task("3001:0:2", duration_s=HOUR, value=100.0),
        make_task("3002:0:2", duration_s=2 * HOUR),
        town_hall(),
        # Depends on the town hall: no float, left as it is.
        make_task("4001:0:1", duration_s=HOUR, requires=("1:0:10",)),
    )
    problem = make_problem(tasks=tasks, lanes={"builder": 3})
    floats = critical_path(problem).float_s
    assert floats["2001:0:2"] == floats["2001:0:3"] == 0
    assert floats["3001:0:2"] > 0 and floats["3002:0:2"] > 0

    raised = {task.id: task for task in prioritize_critical(problem).tasks}
    lowest_critical = min(ratio(raised[name]) for name in ("2001:0:2", "2001:0:3"))
    for name in ("3001:0:2", "3002:0:2", "1:0:10"):
        assert ratio(raised[name]) < lowest_critical, name
    assert raised["4001:0:1"] == problem.tasks[-1]
    # Only the values change.
    for before in problem.tasks:
        assert dataclasses.replace(raised[before.id], value=before.value) == before
        assert raised[before.id].value >= before.value


def test_a_smaller_float_weighs_more() -> None:
    tasks = (
        hero("2001:0:2", 40),
        make_task("3001:0:2", duration_s=HOUR),
        make_task("3002:0:2", duration_s=HOUR),
        make_task("3002:0:3", duration_s=30 * HOUR, requires=("3002:0:2",)),
        town_hall(),
    )
    problem = make_problem(tasks=tasks, lanes={"builder": 4})
    floats = critical_path(problem).float_s
    assert floats["3002:0:2"] < floats["3001:0:2"]
    raised = {task.id: task for task in prioritize_critical(problem).tasks}
    assert raised["3002:0:2"].value > raised["3001:0:2"].value
