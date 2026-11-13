"""Sonde S07 (H07) : aucun ouvrier ne reste inactif quand une amélioration est possible.

Comportement sûr attendu : ressources abondantes, aucune contrainte d'horaire, tâches
indépendantes plus nombreuses que les ouvriers → chaque ouvrier démarre une tâche dès `now`,
et enchaîne sans trou tant qu'il reste des tâches, pour chaque moteur.
Adopted from the audit of 2026-11-12.
"""

from __future__ import annotations

import itertools

import pytest

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.planning.model import ResourceModel
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from support.problems import HOUR, NOW, TEST_CONFIG, make_problem, make_task


@pytest.mark.parametrize("engine", ["baseline", "rollout", "cpsat", "auto"])
def test_every_builder_works_from_now_without_gaps(engine: str) -> None:
    tasks = tuple(
        make_task(f"{3000 + i}:0:2", duration_s=(2 + i % 3) * HOUR, cost={"gold": 100})
        for i in range(6)
    )
    problem = make_problem(
        lanes={"builder": 3},
        tasks=tasks,
        resources=ResourceModel(
            initial={"gold": 1_000_000}, cap={"gold": 2_000_000}, income_per_hour={"gold": 0}
        ),
    )
    plan = PLANNERS[engine].solve(problem, Objective(), TEST_CONFIG)
    assert simulate(problem, plan).valid
    assert len(plan.assignments) == len(tasks)
    by_slot: dict[int, list[tuple[int, int]]] = {}
    for a in plan.assignments:
        by_slot.setdefault(a.lane_slot, []).append((a.start, a.end))
    assert len(by_slot) == 3, f"S07 {engine}: only {len(by_slot)} builders used"
    for slot, spans in by_slot.items():
        spans.sort()
        assert spans[0][0] == NOW, f"S07 {engine}: builder {slot} idle until {spans[0][0] - NOW}s"
        for (_, end), (start, _) in itertools.pairwise(spans):
            assert start == end, f"S07 {engine}: builder {slot} idle {start - end}s"
