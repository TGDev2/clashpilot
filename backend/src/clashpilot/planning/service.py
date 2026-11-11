"""The plan service (SPEC section 4, Defaults of `POST plans`; ADR-4, ADR-7). Pure module.

It takes inputs already read by the caller (the API reads them from storage, the pack through
`load_effective_pack` so that manual corrections count), applies the defaults, compiles the
problem, runs the engine of `PLANNERS`, judges the plan with `simulate` and builds the documents
the API answers and stores. No I/O and no clock: `now` is a parameter. The caller runs
`create_plan` outside the event loop, since an engine may compute for a while.

`input_hash` is the SHA-256 of the canonical JSON (sorted keys, no spaces) of the problem, the
objective, the engine, the profile and the seed.

The inputs carry the journal of the village (E11): the problem is compiled from the export and the
whole journal, and the objective is the one of the latest `priority_changed`, if any.

A plan may name a strategy (E16), with an army composition, a goal and war windows: they are part
of the stored params, and `apply_strategy` turns the compiled problem and the objective into the
strategy's at every computation. A `priority_changed` still wins over the strategy's weights.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self

from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.state import ManualState, VillageSnapshot
from clashpilot.planning.compile import compile_problem, running_jobs
from clashpilot.planning.explain import consumed_resources, explain_plan, reliability
from clashpilot.planning.journal import JournalEntry, KnownJob, objective_of, replay
from clashpilot.planning.model import Decision, Plan, Problem
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PLANNERS, Planner, PlannerConfig
from clashpilot.planning.simulate import SimResult, Violation, simulate
from clashpilot.planning.strategies import apply_strategy, goal_of, load_strategies
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


class UnknownStrategyError(LookupError):
    def __init__(self, strategy: str) -> None:
        super().__init__(f"unknown strategy: {strategy}")
        self.strategy = strategy


class InvalidGoalError(ValueError):
    """The goal names a level the active pack has no row for."""

    def __init__(self, data_id: int, level: int) -> None:
        super().__init__(f"no pack row for goal {data_id} level {level}")
        self.data_id = data_id
        self.level = level


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
    journal: tuple[JournalEntry, ...] = ()


@dataclass(frozen=True)
class PlanParams:
    """The resolved request parameters, stored with the plan for a later replanning."""

    horizon_days: int
    engine: str
    profile: Profile
    strategy: str | None = None
    composition: tuple[int, ...] = ()
    goal: tuple[int, int] | None = None  # (data_id, level)
    war_windows: tuple[tuple[int, int], ...] = ()  # (start, end) UTC epochs

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> Self:
        """The params stored as JSON; a plan stored before E16 has no strategy."""
        goal = document.get("goal")
        return cls(
            horizon_days=int(document["horizon_days"]),
            engine=str(document["engine"]),
            profile=document["profile"],
            strategy=document.get("strategy"),
            composition=tuple(int(data_id) for data_id in document.get("composition", ())),
            goal=None if goal is None else (int(goal[0]), int(goal[1])),
            war_windows=tuple(
                (int(start), int(end)) for start, end in document.get("war_windows", ())
            ),
        )


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
    horizon_days: int | None,
    engine: str | None,
    profile: Profile | None,
    *,
    strategy: str | None = None,
    composition: Sequence[int] = (),
    goal: tuple[int, int] | None = None,
    war_windows: Sequence[tuple[int, int]] = (),
) -> PlanParams:
    """The SPEC defaults: `auto` if registered else `rollout`, profile `fast`, 90 days.

    Raises `UnknownEngineError` and `UnknownStrategyError`.
    """
    if engine is None:
        engine = AUTO_ENGINE if AUTO_ENGINE in PLANNERS else FALLBACK_ENGINE
    if engine not in PLANNERS:
        raise UnknownEngineError(engine)
    if strategy is not None and strategy not in load_strategies():
        raise UnknownStrategyError(strategy)
    return PlanParams(
        horizon_days=DEFAULT_HORIZON_DAYS if horizon_days is None else horizon_days,
        engine=engine,
        profile=DEFAULT_PROFILE if profile is None else profile,
        strategy=strategy,
        composition=tuple(composition),
        goal=goal,
        war_windows=tuple(sorted(war_windows)),
    )


def strategy_objective(params: PlanParams) -> Objective:
    """The weights of the plan's strategy, or the default objective without strategy."""
    if params.strategy is None:
        return DEFAULT_OBJECTIVE
    strategies = load_strategies()
    if params.strategy not in strategies:
        raise UnknownStrategyError(params.strategy)
    return strategies[params.strategy].objective()


def with_strategy(problem: Problem, params: PlanParams) -> tuple[Problem, Objective]:
    """The compiled problem and the objective as the plan's strategy sees them."""
    if params.strategy is None:
        return problem, DEFAULT_OBJECTIVE
    strategies = load_strategies()
    if params.strategy not in strategies:
        raise UnknownStrategyError(params.strategy)
    goal = None if params.goal is None else goal_of(problem, *params.goal)
    return apply_strategy(
        problem,
        strategies[params.strategy],
        params.composition,
        goal=goal,
        war_windows=params.war_windows,
    )


def check_goal(pack: GamePack, params: PlanParams) -> None:
    """Raise `InvalidGoalError` when the goal names a level the pack has no row for."""
    if params.goal is not None and pack.level(*params.goal) is None:
        raise InvalidGoalError(*params.goal)


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


def engine(name: str) -> Planner:
    """The engine registered under `name`, looked up when a plan is computed."""
    return PLANNERS[name]


def compile_inputs(
    inputs: PlanInputs,
    now: int,
    horizon_days: int,
    carried: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[Problem, dict[str, KnownJob]]:
    """The problem of the export and the journal at `now`, and every started job known then.

    `carried` keeps the `(start, lane_slot)` an earlier plan gave the jobs of the export.
    Raises `MissingInputError`.
    """
    running = running_jobs(inputs.snapshot, inputs.pack, now)
    state, known = replay(inputs.journal, running, inputs.snapshot.exported_at, carried)
    problem = compile_problem(
        inputs.snapshot,
        inputs.manual,
        inputs.pack,
        inputs.schedule_profile,
        now,
        horizon_days,
        journal=state,
    )
    return problem, known


def create_plan(
    inputs: PlanInputs, params: PlanParams, overrides: Mapping[str, Any], now: int
) -> PlanOutcome:
    """Compile, solve, judge and describe a plan.

    Raises `InvalidGoalError`, `UnknownStrategyError`, `MissingInputError` (from
    `compile_problem`) and `InvalidPlanError`.
    """
    check_goal(inputs.pack, params)
    compiled, _ = compile_inputs(inputs, now, params.horizon_days)
    problem, weights = with_strategy(compiled, params)
    objective = objective_of(inputs.journal) or weights
    config = planner_config(overrides, params.profile)
    plan = engine(params.engine).solve(problem, objective, config)
    return describe(inputs, params, problem, plan, objective, config, now)


def describe(
    inputs: PlanInputs,
    params: PlanParams,
    problem: Problem,
    plan: Plan,
    objective: Objective,
    config: PlannerConfig,
    now: int,
    *,
    reference: Plan | None = None,
    explanations: Mapping[str, dict[str, Any]] | None = None,
) -> PlanOutcome:
    """Judge `plan` with `simulate` and build the documents the API answers and stores.

    `reference` feeds the perturbation term of the score; `explanations` (of an earlier plan)
    replace those of the tasks they name. Raises `InvalidPlanError`.
    """
    sim = simulate(problem, plan, reference)
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
        "strategy": params.strategy,
        "composition": list(params.composition),
        "goal": None
        if params.goal is None
        else {"data_id": params.goal[0], "level": params.goal[1]},
        "war_windows": [{"start": start, "end": end} for start, end in params.war_windows],
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
        explanations=_explanations(problem, plan, sim, explanations),
        reliability=reliability(consumed, resources, fictional=pack.data.fictional, now=now),
    )


def _explanations(
    problem: Problem,
    plan: Plan,
    sim: SimResult,
    kept: Mapping[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    explained = explain_plan(problem, plan, sim)
    if kept:
        explained.update({name: kept[name] for name in explained if name in kept})
    return explained
