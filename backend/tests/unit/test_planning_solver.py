"""Unit tests of the solver engines: what the acceptance tests of E10 leave open."""

from __future__ import annotations

import dataclasses
import math

import pytest
from ortools.sat.python import cp_model

from clashpilot.planning.heuristics.rollout import replay_trace
from clashpilot.planning.model import FrozenJob, Modifier
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from clashpilot.planning.solver.auto import AutoPlanner
from clashpilot.planning.solver.cpsat import CpSatPlanner, _configure, _text
from support.problems import DAY, HOUR, NOW, TEST_CONFIG, make_calendar, make_problem, make_task


def test_scope_names_the_window_the_grid_and_the_discretised_objective() -> None:
    assert _text(3 * DAY, 300, False) == "fenêtre 3 j, grille 5 min, objectif discrétisé"
    assert _text(8 * HOUR, 90, True) == (
        "fenêtre 8 h, grille 90 s, objectif discrétisé, créneaux restreints"
    )


def test_fast_profile_runs_under_a_wall_clock_limit_and_says_so() -> None:
    solver = cp_model.CpSolver()
    fast = dataclasses.replace(TEST_CONFIG, profile="fast", solver_max_time_s=7.5)
    _configure(solver, fast)
    assert solver.parameters.max_time_in_seconds == pytest.approx(7.5)
    assert math.isinf(solver.parameters.max_deterministic_time)
    assert solver.parameters.absolute_gap_limit == 0
    assert solver.parameters.relative_gap_limit == 0

    plan = CpSatPlanner().solve(make_problem(), Objective(), fast)
    assert plan.metadata["profile"] == "fast"
    assert plan.metadata["reproducible"] == "false"
    assert simulate(make_problem(), plan).valid


def test_two_builders_share_the_lane_with_a_frozen_job_and_distinct_slots() -> None:
    problem = make_problem(
        tasks=(
            make_task("1001:0:2", duration_s=2 * HOUR),
            make_task("1002:0:2", duration_s=2 * HOUR),
            make_task("1003:0:2", duration_s=HOUR),
        ),
        frozen=(FrozenJob("9001:0:2", "builder", 1, NOW - HOUR, NOW + HOUR),),
        horizon_end=NOW + DAY,
    )
    plan = PLANNERS["cpsat"].solve(problem, Objective(), TEST_CONFIG)
    result = simulate(problem, plan)
    assert result.valid, [v.code for v in result.violations]
    assert result.metrics.tasks_planned == 3
    assert plan.proof.kind == "optimal_window"
    early = [a for a in plan.assignments if a.start < NOW + HOUR]
    assert early and all(a.lane_slot == 0 for a in early)


def test_a_cost_modifier_with_a_bound_off_the_grid_is_read_at_the_exact_instant() -> None:
    problem = make_problem(
        tasks=(make_task("1001:0:2", duration_s=HOUR, cost={"gold": 1000}),),
        modifiers=(Modifier(NOW + 2 * 60, NOW + 7 * 60, "builder", 100, 50),),
        resources=dataclasses.replace(
            make_problem().resources, initial={"gold": 500}, income_per_hour={"gold": 0}
        ),
        horizon_end=NOW + DAY,
    )
    plan = PLANNERS["cpsat"].solve(problem, Objective(), TEST_CONFIG)
    assert simulate(problem, plan).valid
    assert [a.start for a in plan.assignments] == [NOW + 5 * 60]


def test_replay_trace_of_an_empty_plan_is_empty() -> None:
    problem = make_problem()
    empty = PLANNERS["baseline"].solve(make_problem(tasks=()), Objective(), TEST_CONFIG)
    assert replay_trace(problem, Objective(), TEST_CONFIG, empty) == ()


def test_auto_keeps_rollout_when_the_solver_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda self, model: cp_model.UNKNOWN)
    problem = make_problem(calendar=make_calendar(sleep=[(NOW + 10 * HOUR, NOW + 18 * HOUR)]))
    plan = AutoPlanner().solve(problem, Objective(), TEST_CONFIG)
    assert plan.engine == "auto"
    assert plan.metadata["auto_winner"] == "rollout"
    assert "solver_fallback" in plan.metadata["auto_reason"]
    assert plan.proof.kind == "heuristic"
    assert simulate(problem, plan).valid
    assert plan.trace
