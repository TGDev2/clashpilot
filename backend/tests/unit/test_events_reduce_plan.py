"""Sonde S05c (H05) : un événement du jeu saisi réduit bien la durée et le coût dans le plan.

Comportement sûr attendu : pour chaque moteur, une tâche démarrée pendant un événement à 80 %
dure et coûte 80 % de sa valeur nominale (arrondi au supérieur), et le plan est valide.
Adopted from the audit of 2026-11-12.
"""

from __future__ import annotations

import pytest

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.planning.model import Modifier, ResourceModel
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import effective_cost, simulate
from support.problems import HOUR, NOW, TEST_CONFIG, make_problem, make_task


@pytest.mark.parametrize("engine", ["baseline", "rollout", "cpsat", "auto"])
def test_event_shortens_and_cheapens_planned_task(engine: str) -> None:
    task = make_task("2001:0:2", duration_s=10 * HOUR, cost={"gold": 10_000})
    problem = make_problem(
        lanes={"builder": 1},
        tasks=(task,),
        resources=ResourceModel(
            initial={"gold": 100_000}, cap={"gold": 1_000_000}, income_per_hour={"gold": 0}
        ),
        modifiers=(Modifier(NOW, NOW + 2 * 24 * HOUR, "builder", 80, 80),),
    )
    plan = PLANNERS[engine].solve(problem, Objective(), TEST_CONFIG)
    assert simulate(problem, plan).valid
    (assignment,) = [a for a in plan.assignments if a.task_id == task.id]
    assert NOW <= assignment.start < NOW + 2 * 24 * HOUR
    assert assignment.end - assignment.start == 8 * HOUR, f"S05c {engine}: event ignored"
    assert effective_cost(problem, task, assignment.start) == {"gold": 8_000}
