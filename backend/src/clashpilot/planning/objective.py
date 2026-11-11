"""The score of a simulated plan, to maximize (SPEC section 4, Objective). Pure module."""

from __future__ import annotations

from dataclasses import dataclass

from clashpilot.planning.model import INVALID_SCORE, Problem
from clashpilot.planning.simulate import SimResult

# Goal term of a plan that does not reach its goal within the horizon.
GOAL_MISSED = 2.0


@dataclass(frozen=True)
class Objective:
    """Weights of the terms of the score."""

    value: float = 1.0
    goal: float = 0.0
    idle: float = 0.0
    night: float = 0.0
    logins: float = 0.0
    spill: float = 0.0
    perturbation: float = 0.0
    preferred: float = 0.0


def _discounted_value(sim: SimResult, problem: Problem, horizon: int) -> float:
    values = {task.id: task.value for task in problem.tasks}
    total = sum(values.values())
    if total <= 0:
        return 0.0
    earned = sum(
        values[name] * max(0.0, (horizon - (end - problem.now)) / horizon)
        for name, end in sim.metrics.ends.items()
        if name in values
    )
    return earned / total


def _goal_term(sim: SimResult, problem: Problem, horizon: int) -> float:
    if problem.goal is None or not problem.goal.task_ids:
        return 0.0
    ends = {job.task_id: job.end for job in problem.frozen}
    ends.update(sim.metrics.ends)
    reached = [ends.get(name) for name in problem.goal.task_ids]
    last = max((end for end in reached if end is not None), default=None)
    if last is None or None in reached or last > problem.horizon_end:
        return GOAL_MISSED
    return (last - problem.now) / horizon


def score(sim: SimResult, objective: Objective, problem: Problem) -> float:
    """The weighted score of SPEC section 4; INVALID_SCORE for a plan that breaks a rule."""
    if not sim.valid:
        return INVALID_SCORE
    metrics = sim.metrics
    horizon = max(1, problem.horizon_end - problem.now)
    capacity = sum(problem.lanes.values()) * horizon
    planned = max(1, metrics.tasks_planned)

    idle_ratio = 1 - metrics.busy_s / capacity if capacity > 0 else 0.0
    night_ratio = metrics.ends_in_sleep / planned
    logins_term = metrics.logins_per_day / 24
    spill_ratio = sum(metrics.spill.values()) / max(1.0, sum(metrics.income.values()))
    preferred_ratio = metrics.ends_in_preferred / planned
    return (
        objective.value * _discounted_value(sim, problem, horizon)
        - objective.goal * _goal_term(sim, problem, horizon)
        - objective.idle * idle_ratio
        - objective.night * night_ratio
        - objective.logins * logins_term
        - objective.spill * spill_ratio
        - objective.perturbation * metrics.perturbation
        + objective.preferred * preferred_ratio
    )
