"""Planning: model, compiler, exact simulator, objective and engines. Pure: no I/O, no clock.

Importing the package registers the heuristic engines in `clashpilot.planning.registry.PLANNERS`.
"""

from __future__ import annotations

import clashpilot.planning.heuristics  # noqa: F401
