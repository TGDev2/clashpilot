"""The planning engines by name (SPEC section 4, Engines).

`baseline` and `rollout` (E08), `cpsat` and `auto` (E10) register here. Every plan an engine
returns is judged by `clashpilot.planning.simulate.simulate`, whatever the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from clashpilot.planning.model import Plan, Problem
from clashpilot.planning.objective import Objective


@dataclass(frozen=True)
class PlannerConfig:
    profile: Literal["fast", "repro"] = "fast"
    seed: int = 42
    max_rollouts: int = 2000
    solver_window_days: int = 14
    solver_grid_s: int = 300
    solver_max_deterministic_time: float = 20.0
    solver_max_time_s: float = 60.0


class Planner(Protocol):
    def solve(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None = None,
    ) -> Plan: ...


PLANNERS: dict[str, Planner] = {}
