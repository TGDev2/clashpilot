"""Benchmarks of the rollout planner on 300 and 400 tasks (SPEC section 7: slow tests only).

Run by `npm run test:slow`, never by `verify`. Each test prints its timing with `-s`. The budget
is the one of `TEST_CONFIG` (200 rollouts): the time measured is the cost of one rollout on a
large instance, the default budget of 2000 rollouts scales it by ten.
"""

from __future__ import annotations

import time

import pytest

from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from support.instances import generate_problem
from support.problems import TEST_CONFIG

BENCHMARK_CONFIG = TEST_CONFIG


@pytest.mark.slow
@pytest.mark.timeout(0)
@pytest.mark.parametrize("n_tasks", [300, 400])
def test_rollout_plans_a_large_instance_validly(n_tasks: int) -> None:
    problem = generate_problem(n_tasks, seed=42, builders=4)
    assert len(problem.tasks) == n_tasks

    started = time.perf_counter()
    plan = PLANNERS["rollout"].solve(problem, Objective(), BENCHMARK_CONFIG)
    elapsed = time.perf_counter() - started

    result = simulate(problem, plan)
    assert result.valid, [violation.code for violation in result.violations]
    assert plan.warnings == ()
    assert plan.trace
    planned = result.metrics.tasks_planned
    decisions = len(plan.trace)
    print(
        f"\nrollout on {n_tasks} tasks: {elapsed:.1f} s, {planned} planned, {decisions} decisions"
    )
    assert planned == n_tasks
