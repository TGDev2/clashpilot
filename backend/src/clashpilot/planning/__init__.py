"""Planning: model, compiler, exact simulator, objective and engines. Pure: no I/O, no clock.

Importing the package registers the heuristic and solver engines in
`clashpilot.planning.registry.PLANNERS`.
"""

from __future__ import annotations

import clashpilot.planning.heuristics
import clashpilot.planning.solver  # noqa: F401
