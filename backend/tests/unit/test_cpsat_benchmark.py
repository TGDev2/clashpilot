"""Benchmarks of the cpsat planner on 100, 300 and 400 tasks (SPEC section 7: slow tests only).

Run by `npm run test:slow`, never by `verify`. Each test prints, with `-s`, its timing, the solver
status, the simulated score and the bound, from which the budgets of the `fast` profile are fixed.
Profile `repro` with the SPEC window of 14 days and a deterministic budget of 60: no wall-clock
limit, so the figures are comparable from one machine to another.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from support.instances import generate_problem
from support.problems import TEST_CONFIG

BENCHMARK_CONFIG = dataclasses.replace(
    TEST_CONFIG, solver_window_days=14, solver_max_deterministic_time=60.0
)


@pytest.mark.slow
@pytest.mark.timeout(0)
@pytest.mark.parametrize("n_tasks", [100, 300, 400])
def test_cpsat_refines_a_large_instance_validly(n_tasks: int) -> None:
    problem = generate_problem(n_tasks, seed=42, builders=4)
    assert len(problem.tasks) == n_tasks

    started = time.perf_counter()
    plan = PLANNERS["cpsat"].solve(problem, Objective(), BENCHMARK_CONFIG)
    elapsed = time.perf_counter() - started

    result = simulate(problem, plan)
    assert result.valid, [violation.code for violation in result.violations]
    assert plan.assignments
    assert plan.trace
    planned = result.metrics.tasks_planned
    status = plan.metadata.get("solver_status")
    print(
        f"\ncpsat on {n_tasks} tasks: {elapsed:.1f} s, status {status}, "
        f"proof {plan.proof.kind}, score {plan.proof.objective:.4f}, bound {plan.proof.bound}, "
        f"{planned} planned, candidates {plan.metadata.get('candidates')}, "
        f"restricted {plan.metadata.get('restricted')}, warnings {list(plan.warnings)}"
    )
