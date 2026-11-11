"""Heuristic planners (E08): `baseline` and `rollout`, registered in `PLANNERS` on import."""

from __future__ import annotations

from clashpilot.planning.heuristics.baseline import BaselinePlanner
from clashpilot.planning.heuristics.rollout import RolloutPlanner
from clashpilot.planning.registry import PLANNERS

PLANNERS.setdefault("baseline", BaselinePlanner())
PLANNERS.setdefault("rollout", RolloutPlanner())

__all__ = ["BaselinePlanner", "RolloutPlanner"]
