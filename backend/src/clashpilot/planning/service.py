"""The plan service (SPEC section 4, Defaults of `POST plans`; ADR-4, ADR-7). Pure module.

It takes inputs already read by the caller (the API reads them from storage, the pack through
`load_effective_pack` so that manual corrections count), applies the defaults, compiles the
problem, runs the engine of `PLANNERS`, judges the plan with `simulate` and builds the documents
the API answers and stores. No I/O and no clock: `now` is a parameter. The caller runs
`create_plan` outside the event loop, since an engine may compute for a while.

`input_hash` is the SHA-256 of the canonical JSON (sorted keys, no spaces) of the problem, the
objective, the engine, the profile and the seed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.state import ManualState, VillageSnapshot
from clashpilot.planning.compile import compile_problem
from clashpilot.planning.explain import consumed_resources, explain_plan, reliability
from clashpilot.planning.model import Decision, Plan, Problem
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PLANNERS, PlannerConfig
from clashpilot.planning.simulate import SimResult, Violation, simulate
from clashpilot.schedule.profile import ScheduleProfile

Profile = Literal["fast", "repro"]

DEFAULT_OBJECTIVE = Objective(value=1.0, idle=0.1, night=0.2, logins=0.05, spill=0.1, preferred=0.1)
DEFAULT_HORIZON_DAYS = 90
DEFAULT_PROFILE: Profile = "fast"
AUTO_ENGINE = "auto"
FALLBACK_ENGINE = "rollout"
REPRODUCIBLE_ENGINES = frozenset({"baseline", "rollout"})
NO_FARM_RATE = "no_farm_rate"


class UnknownEngineError(LookupError):
    def __init__(self, engine: str) -> None:
        super().__init__(f"unknown engine: {engine}")
        self.engine = engine


class InvalidPlanError(RuntimeError):
    """The engine returned a plan the simulator refuses: it is never stored nor answered."""

    def __init__(self, engine: str, violations: tuple[Violation, ...]) -> None:
        codes = ", ".join(sorted({violation.code for violation in violations}))
        super().__init__(f"engine {engine} returned an invalid plan: {codes}")
        self.engine = engine
        self.violations = violations


@dataclass(frozen=True)
class PlanInputs:
    snapshot: VillageSnapshot
    manual: ManualState
    pack: GamePack
    schedule_profile: ScheduleProfile | None


@dataclass(frozen=True)
class PlanParams:
    """The resolved request parameters, stored with the plan for a later replanning."""

    horizon_days: int
    engine: str
    profile: Profile


@dataclass(frozen=True)
class PlanOutcome:
    params: PlanParams
    problem: Problem
    plan: Plan
    sim: SimResult
    score: float
    input_hash: str
    warnings: tuple[str, ...]
    reproducible: bool
    body: dict[str, Any]  # the plan as the API answers it, without `plan_id`
    explanations: dict[str, dict[str, Any]]  # task id -> explanation
    reliability: dict[str, Any]


def resolve_params(
    horizon_days: int | None, engine: str | None, profile: Profile | None
) -> PlanParams:
    """The SPEC defaults: `auto` if registered else `rollout`, profile `fast`, 90 days."""
    if engine is None:
        engine = AUTO_ENGINE if AUTO_ENGINE in PLANNERS else FALLBACK_ENGINE
    if engine not in PLANNERS:
        raise UnknownEngineError(engine)
    return PlanParams(
        horizon_days=DEFAULT_HORIZON_DAYS if horizon_days is None else horizon_days,
        engine=engine,
        profile=DEFAULT_PROFILE if profile is None else profile,
    )


def planner_config(overrides: Mapping[str, Any], profile: Profile) -> PlannerConfig:
    """`PlannerConfig` with `Settings.planner_overrides` applied; the request names the profile."""
    names = {field.name for field in dataclasses.fields(PlannerConfig)} - {"profile"}
    values = {name: value for name, value in overrides.items() if name in names}
    return PlannerConfig(**values, profile=profile)


def _jsonable(value: Any) -> Any:
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_jsonable
    )


def input_hash(problem: Problem, objective: Objective, engine: str, profile: str, seed: int) -> str:
    document = {
        "problem": dataclasses.asdict(problem),
        "objective": dataclasses.asdict(objective),
        "engine": engine,
        "profile": profile,
        "seed": seed,
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def trace_json(trace: tuple[Decision, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(decision) for decision in trace]


def _data_id(task_id: str) -> int:
    return int(task_id.split(":", 1)[0])


def _name(pack: GamePack, data_id: int) -> str:
    item = pack.item(data_id)
    return item.name if item is not None else str(data_id)


def _assignments(pack: GamePack, problem: Problem, plan: Plan) -> list[dict[str, Any]]:
    tasks = {task.id: task for task in problem.tasks}
    rows: list[dict[str, Any]] = []
    for job in problem.frozen:
        rows.append(
            {
                "task_id": job.task_id,
                "data_id": _data_id(job.task_id),
                "name": _name(pack, _data_id(job.task_id)),
                "lane": job.lane,
                "lane_slot": job.lane_slot,
                "start": job.start,
                "end": job.end,
                "status": "frozen",
                "requires": [],
            }
        )
    for assignment in plan.assignments:
        task = tasks[assignment.task_id]
        rows.append(
            {
                "task_id": task.id,
                "data_id": task.data_id,
                "name": _name(pack, task.data_id),
                "lane": assignment.lane,
                "lane_slot": assignment.lane_slot,
                "start": assignment.start,
                "end": assignment.end,
                "status": "planned",
                "requires": list(task.requires),
            }
        )
    rows.sort(key=lambda row: (row["start"], row["lane"], row["lane_slot"], row["task_id"]))
    return rows


def create_plan(
    inputs: PlanInputs, params: PlanParams, overrides: Mapping[str, Any], now: int
) -> PlanOutcome:
    """Compile, solve, judge and describe a plan.

    Raises `MissingInputError` (from `compile_problem`) and `InvalidPlanError`.
    """
    problem = compile_problem(
        inputs.snapshot,
        inputs.manual,
        inputs.pack,
        inputs.schedule_profile,
        now,
        params.horizon_days,
    )
    objective = DEFAULT_OBJECTIVE
    config = planner_config(overrides, params.profile)
    plan = PLANNERS[params.engine].solve(problem, objective, config)
    sim = simulate(problem, plan)
    if not sim.valid:
        raise InvalidPlanError(params.engine, sim.violations)

    consumed = consumed_resources(problem)
    warnings = list(plan.warnings)
    if any(name not in inputs.manual.farm_rate for name in consumed):
        warnings.append(NO_FARM_RATE)
    warnings = list(dict.fromkeys(warnings))
    reproducible = params.profile == "repro" or params.engine in REPRODUCIBLE_ENGINES
    digest = input_hash(problem, objective, params.engine, params.profile, config.seed)
    value = score(sim, objective, problem)
    pack = inputs.pack
    assert inputs.schedule_profile is not None  # compile_problem refuses a missing profile
    body: dict[str, Any] = {
        "now": now,
        "timezone": inputs.schedule_profile.tz,
        "horizon_days": params.horizon_days,
        "horizon_end": problem.horizon_end,
        "engine": params.engine,
        "profile": params.profile,
        "reproducible": reproducible,
        "input_hash": digest,
        "score": value,
        "proof": dataclasses.asdict(plan.proof),
        "pack": {
            "pack_id": pack.pack_id,
            "game_version": pack.data.game_version,
            "fictional": pack.data.fictional,
        },
        "assignments": _assignments(pack, problem, plan),
        "excluded": [
            {
                "data_id": excluded.data_id,
                "instance": excluded.instance,
                "to_level": excluded.to_level,
                "name": _name(pack, excluded.data_id),
                "reason": excluded.reason,
            }
            for excluded in problem.excluded
        ],
        "warnings": warnings,
    }
    resources = {
        name: (typed.status, typed.as_of) for name, typed in inputs.manual.resources.items()
    }
    return PlanOutcome(
        params=params,
        problem=problem,
        plan=plan,
        sim=sim,
        score=value,
        input_hash=digest,
        warnings=tuple(warnings),
        reproducible=reproducible,
        body=body,
        explanations=explain_plan(problem, plan, sim),
        reliability=reliability(consumed, resources, fictional=pack.data.fictional, now=now),
    )
