"""The planning model shared by the compiler, the simulator and every planner (SPEC section 4).

Frozen dataclasses only. Each class holds at least the fields of the SPEC; a later task may add
fields, always with a default value. Times are integer UTC epochs in seconds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from clashpilot.schedule.expand import AvailabilityCalendar

Resource = Literal["gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"]
PolicyMode = Literal["strict", "tolerance", "progress", "min_logins"]
ProofKind = Literal["heuristic", "feasible", "optimal_window"]

# Score of a candidate that breaks a rule: below any reachable score.
INVALID_SCORE = -1e18


def task_id(data_id: int, instance: int, to_level: int) -> str:
    """The identity of the upgrade of one instance to one level."""
    return f"{data_id}:{instance}:{to_level}"


@dataclass(frozen=True)
class Task:
    id: str  # "{data_id}:{instance}:{to_level}"
    data_id: int
    instance: int
    to_level: int
    lane: str  # "builder" | "lab" | "pet_house" | "blacksmith" | ...
    duration_s: int
    cost: Mapping[str, int]
    requires: tuple[str, ...]  # ids of tasks or frozen jobs
    value: float
    category: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenJob:
    """An upgrade already running: it occupies its lane and is never moved."""

    task_id: str
    lane: str
    lane_slot: int
    start: int
    end: int
    extra: bool = False  # done by an extra worker: takes no capacity of the lane


@dataclass(frozen=True)
class ResourceModel:
    """Whole units. A resource without `cap` has no storage limit."""

    initial: Mapping[str, int]
    cap: Mapping[str, int]
    income_per_hour: Mapping[str, int]


@dataclass(frozen=True)
class Policy:
    mode: PolicyMode
    tolerance_s: int = 0
    max_logins_per_day: int | None = None
    login_merge_s: int = 900


@dataclass(frozen=True)
class Goal:
    task_ids: frozenset[str]


@dataclass(frozen=True)
class Modifier:
    """An event or bonus on one lane for the tasks started in `[start, end)` (100 = no effect)."""

    start: int
    end: int
    lane: str
    duration_pct: int
    cost_pct: int


@dataclass(frozen=True)
class ExcludedTask:
    """A level left out of the problem, with why."""

    data_id: int
    to_level: int
    reason: str
    instance: int = 0


@dataclass(frozen=True)
class Problem:
    now: int
    horizon_end: int
    lanes: Mapping[str, int]  # capacity of each lane
    tasks: tuple[Task, ...]
    frozen: tuple[FrozenJob, ...]
    resources: ResourceModel
    calendar: AvailabilityCalendar
    policy: Policy
    modifiers: tuple[Modifier, ...] = ()
    excluded: tuple[ExcludedTask, ...] = ()
    goal: Goal | None = None
    hero_blackouts: tuple[tuple[int, int], ...] = ()  # windows where no hero upgrades (E16)


@dataclass(frozen=True)
class Assignment:
    task_id: str
    lane: str
    lane_slot: int
    start: int
    end: int


@dataclass(frozen=True)
class Proof:
    kind: ProofKind
    objective: float
    bound: float | None = None
    gap_abs: float | None = None
    scope: str = ""  # e.g. "fenêtre 14 j, grille 5 min"; empty for heuristic


@dataclass(frozen=True)
class Candidate:
    task_id: str | None  # None = wait
    start: int
    score: float  # INVALID_SCORE when the candidate breaks a rule


@dataclass(frozen=True)
class Decision:
    at: int
    candidates: tuple[Candidate, ...]
    chosen: Candidate


@dataclass(frozen=True)
class Plan:
    assignments: tuple[Assignment, ...]
    engine: str
    proof: Proof
    trace: tuple[Decision, ...] = ()
    warnings: tuple[str, ...] = ()  # e.g. "solver_fallback", "solver_invalid_plan"
    metadata: Mapping[str, str] = field(default_factory=dict)  # e.g. "auto_winner"
