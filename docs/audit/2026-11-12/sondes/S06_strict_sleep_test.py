"""Sonde S06 (H06) : en mode « Interdire les fins pendant le sommeil », chaque moteur rend un plan.

Comportement sûr attendu : sur des problèmes générés (graines fixes) avec une nuit par jour,
chaque moteur rend un plan que le simulateur juge valide, sans aucune fin pendant le sommeil.
Un plan invalide se traduirait par une erreur de l'API au lieu d'un plan.
A placer dans backend/tests/unit/ pour l'exécuter (utilise tests/support).
"""

from __future__ import annotations

import dataclasses

import pytest

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.planning.model import Policy
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import simulate
from support.instances import generate_problem
from support.problems import TEST_CONFIG


@pytest.mark.parametrize("seed", [1, 7, 19])
@pytest.mark.parametrize("engine", ["baseline", "rollout", "cpsat", "auto"])
def test_strict_mode_plans_are_valid_and_never_end_in_sleep(engine: str, seed: int) -> None:
    problem = dataclasses.replace(
        generate_problem(14, seed=seed, days=10), policy=Policy(mode="strict")
    )
    plan = PLANNERS[engine].solve(problem, Objective(), TEST_CONFIG)
    sim = simulate(problem, plan)
    assert sim.valid, f"S06 {engine} seed {seed}: invalid plan {sim.violations[:3]}"
    assert sim.metrics.ends_in_sleep == 0, (
        f"S06 {engine} seed {seed}: {sim.metrics.ends_in_sleep} ends in sleep"
    )
    assert plan.assignments, f"S06 {engine} seed {seed}: empty plan"
