"""Solver engines (E10): `cpsat` and `auto`, registered in `PLANNERS` on import."""

from __future__ import annotations

from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.solver.auto import AutoPlanner
from clashpilot.planning.solver.cpsat import CpSatPlanner

PLANNERS.setdefault("cpsat", CpSatPlanner())
PLANNERS.setdefault("auto", AutoPlanner())

__all__ = ["AutoPlanner", "CpSatPlanner"]
