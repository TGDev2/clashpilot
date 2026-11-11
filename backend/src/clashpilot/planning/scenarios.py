"""What-if scenarios (E18): overlays applied to a copy of the compiled problem of the real plan.

Pure module: the caller reads the inputs and stores the result. A scenario never writes the real
state: it compiles the village exactly as the latest plan did (same `now`, horizon and params, the
export and the whole journal), applies its overlays to that copy, runs an engine of `PLANNERS` and
judges the plan with `simulate` (ADR-4).

Overlays, as validated mappings with a `type`:

- `strategy {key}`: the plan's strategy is replaced, so are its weights.
- `policy {mode, tolerance_minutes?, max_logins_per_day?}`, `reduce_play {max_logins_per_day}`:
  the login policy of the problem, in the order given.
- `absence {start_local, end_local}`: local date-times of the profile zone added to the absences;
  under the policy `strict` they are also sleep, so that no end falls in them.
- `reserve_builder {count, until}`: `count` builder slots are taken by reservation jobs (frozen
  jobs named `reserved:...`, never shown) from when their running job ends until `until`.
- `start_now {task_id}` and `use_item {item, task_id, at}`: the task is fixed without the engine,
  at the earliest start the simulator accepts (from `now`, or from `at`), as a virtual frozen job;
  its cost is taken off the initial stock so that the engine places the rest around it. A book
  ends its job at once, a potion shortens the jobs of its lane running at its instant (SPEC 5), a
  rune fills its resource up to the cap (now only).

A `use_item` whose rule the active pack lacks makes the scenario `unsupported` before anything is
compiled: no engine runs and no result is made up. An overlay that cannot happen (funds, a
prerequisite, no slot in the horizon) makes it `infeasible`, with its reasons.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from clashpilot.gamedata.pack import GamePack
from clashpilot.gamedata.rules import BookRule, PotionRule, RuneRule, item_rule
from clashpilot.planning.journal import objective_of
from clashpilot.planning.model import (
    Assignment,
    FrozenJob,
    Plan,
    Policy,
    Problem,
    Proof,
    Task,
)
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.replan import carried_jobs, stored_plan
from clashpilot.planning.service import (
    PlanInputs,
    PlanParams,
    assignment_rows,
    compile_inputs,
    engine,
    with_strategy,
)
from clashpilot.planning.simulate import (
    calendar_bounds,
    effective_cost,
    effective_duration,
    simulate,
)
from clashpilot.schedule.expand import local_to_epoch

OK = "ok"
INFEASIBLE = "infeasible"
UNSUPPORTED = "unsupported"

RESERVED_PREFIX = "reserved:"
BUILDER = "builder"
HERO = "hero"
HOUR_S = 3600

MESSAGES = {
    "unsupported_item": "Le pack actif n'a pas de règle pour l'objet {item} : scénario non simulé.",
    "task_not_found": "La tâche {task} ne fait pas partie du plan à améliorer.",
    "already_started": "La tâche {task} est déjà en cours.",
    "missing_prerequisite": "Un prérequis de {task} n'est ni fait ni en cours : {detail}.",
    "insufficient_funds": "Les ressources ({detail}) ne suffiront pas à lancer {task}.",
    "no_slot": "Aucun instant de l'horizon ne permet de lancer {task}.",
    "item_timing": "L'objet {item} ne peut être simulé qu'à partir de maintenant{detail}.",
    "item_target_missing": "L'objet {item} doit viser une tâche.",
    "item_not_applicable": "L'objet {item} ne s'applique pas à {task}.",
    "invalid_plan": "Le moteur a produit un plan refusé par le simulateur ({detail}).",
}


@dataclass(frozen=True)
class Reason:
    code: str
    message: str


def reason(code: str, **values: Any) -> Reason:
    fields = {"item": "", "task": "", "detail": ""} | {k: str(v) for k, v in values.items()}
    return Reason(code, MESSAGES[code].format(**fields))


class InfeasibleError(Exception):
    def __init__(self, found: Reason) -> None:
        super().__init__(found.message)
        self.reason = found


# --------------------------------------------------------------------------- unsupported items


def unsupported(pack: GamePack, overlays: Sequence[Mapping[str, Any]]) -> list[Reason]:
    """One reason per `use_item` whose rule is absent (or malformed) in the pack."""
    missing = [
        str(overlay["item"])
        for overlay in overlays
        if overlay["type"] == "use_item" and item_rule(pack, str(overlay["item"])) is None
    ]
    return [reason("unsupported_item", item=item) for item in dict.fromkeys(missing)]


def scenario_params(params: PlanParams, overlays: Sequence[Mapping[str, Any]]) -> PlanParams:
    """The plan's params with the strategy of the last `strategy` overlay."""
    for overlay in overlays:
        if overlay["type"] == "strategy":
            params = replace(params, strategy=str(overlay["key"]))
    return params


# --------------------------------------------------------------------------- overlays


def _merged(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _policy(problem: Problem, overlays: Sequence[Mapping[str, Any]]) -> Policy:
    policy = problem.policy
    for overlay in overlays:
        if overlay["type"] == "policy":
            minutes = overlay.get("tolerance_minutes")
            limit = overlay.get("max_logins_per_day")
            policy = replace(
                policy,
                mode=overlay["mode"],
                tolerance_s=policy.tolerance_s if minutes is None else int(minutes) * 60,
                max_logins_per_day=policy.max_logins_per_day if limit is None else int(limit),
            )
        elif overlay["type"] == "reduce_play":
            policy = replace(policy, max_logins_per_day=int(overlay["max_logins_per_day"]))
    return policy


def _absences(problem: Problem, overlays: Sequence[Mapping[str, Any]]) -> Problem:
    calendar = problem.calendar
    spans = [
        (
            local_to_epoch(datetime.fromisoformat(str(overlay["start_local"])), calendar.tz),
            local_to_epoch(datetime.fromisoformat(str(overlay["end_local"])), calendar.tz),
        )
        for overlay in overlays
        if overlay["type"] == "absence"
    ]
    clipped = [
        (max(start, problem.now), min(end, problem.horizon_end))
        for start, end in spans
        if end > problem.now and start < problem.horizon_end
    ]
    if not clipped:
        return problem
    calendar = replace(calendar, absence=_merged([*calendar.absence, *clipped]))
    if problem.policy.mode == "strict":
        calendar = replace(calendar, sleep=_merged([*calendar.sleep, *clipped]))
    return replace(problem, calendar=calendar)


def _real_jobs(problem: Problem) -> list[FrozenJob]:
    return [job for job in problem.frozen if not job.task_id.startswith(RESERVED_PREFIX)]


def _reserve(problem: Problem, count: int, until: int) -> Problem:
    """`count` builder slots, the first free ones, are reserved until `until`."""
    capacity = problem.lanes.get(BUILDER, 0)
    if until <= problem.now or capacity == 0:
        return problem
    free_at = dict.fromkeys(range(capacity), problem.now)
    for job in problem.frozen:
        if job.lane == BUILDER and not job.extra and job.lane_slot in free_at:
            free_at[job.lane_slot] = max(free_at[job.lane_slot], job.end)
    taken = len([j for j in problem.frozen if j.task_id.startswith(RESERVED_PREFIX)])
    slots = sorted(free_at, key=lambda slot: (free_at[slot], slot))[: min(count, capacity)]
    reservations = tuple(
        FrozenJob(f"{RESERVED_PREFIX}{BUILDER}:{taken + n}", BUILDER, slot, free_at[slot], until)
        for n, slot in enumerate(slots)
        if free_at[slot] < until
    )
    return replace(problem, frozen=(*problem.frozen, *reservations))


def _funded_at(problem: Problem, task: Task, after: int) -> int | None:
    """The first instant the stock covers the task's cost if nothing else spends, or None."""
    model = problem.resources
    funded = after
    for name, amount in effective_cost(problem, task, after).items():
        if amount <= 0:
            continue
        cap = model.cap.get(name)
        if cap is not None and amount > cap:
            return None
        initial = model.initial.get(name, 0)
        if initial >= amount:
            continue
        rate = model.income_per_hour.get(name, 0)
        if rate <= 0:
            return None
        funded = max(funded, problem.now + math.ceil((amount - initial) * HOUR_S / rate))
    return funded


def _probe(problem: Problem, assignment: Assignment) -> bool:
    plan = Plan((assignment,), "scenario", Proof("heuristic", 0.0))
    return simulate(problem, plan).valid


def earliest_start(problem: Problem, task: Task, after: int) -> Assignment:
    """The earliest assignment of `task` at or after `after` that `simulate` accepts alone.

    Raises `InfeasibleError` when funds, a prerequisite or the horizon forbid it.
    """
    lower = max(after, problem.now)
    frozen_ends = {job.task_id: job.end for job in problem.frozen}
    pending = {t.id for t in problem.tasks}
    for required in task.requires:
        if required in frozen_ends:
            lower = max(lower, frozen_ends[required])
        elif required in pending:
            raise InfeasibleError(reason("missing_prerequisite", task=task.id, detail=required))
    funded = _funded_at(problem, task, lower)
    if funded is None:
        short = ", ".join(sorted(task.cost))
        raise InfeasibleError(reason("insufficient_funds", task=task.id, detail=short))
    lower = max(lower, funded)
    capacity = problem.lanes.get(task.lane, 0)
    lane_jobs = [job for job in problem.frozen if job.lane == task.lane and not job.extra]
    bounds = [
        *calendar_bounds(problem.calendar),
        *(bound for window in problem.hero_blackouts for bound in window),
        *(bound for m in problem.modifiers for bound in (m.start, m.end)),
    ]
    candidates = {lower, *(job.end for job in lane_jobs)}
    candidates |= set(bounds) | {b - task.duration_s for b in bounds}
    for t in sorted(c for c in candidates if lower <= c < problem.horizon_end):
        end = t + effective_duration(problem, task, t)
        for slot in range(capacity):
            if any(job.lane_slot == slot and job.start < end and t < job.end for job in lane_jobs):
                continue
            assignment = Assignment(task.id, task.lane, slot, t, end)
            if _probe(problem, assignment):
                return assignment
    raise InfeasibleError(reason("no_slot", task=task.id))


def _fix(problem: Problem, task: Task, assignment: Assignment, end: int) -> Problem:
    """`task` becomes a frozen job at `assignment`, ending at `end`, its cost paid now."""
    cost = effective_cost(problem, task, assignment.start)
    initial = dict(problem.resources.initial)
    for name, amount in cost.items():
        initial[name] = initial.get(name, 0) - amount
    job = FrozenJob(task.id, task.lane, assignment.lane_slot, assignment.start, end)
    return replace(
        problem,
        tasks=tuple(t for t in problem.tasks if t.id != task.id),
        frozen=(*problem.frozen, job),
        resources=replace(problem.resources, initial=initial),
    )


def _task(problem: Problem, task_id: str | None) -> Task | None:
    return next((t for t in problem.tasks if t.id == task_id), None)


def _running(problem: Problem, task_id: str | None, at: int) -> FrozenJob | None:
    return next(
        (j for j in _real_jobs(problem) if j.task_id == task_id and j.start <= at < j.end), None
    )


def _start_now(problem: Problem, task_id: str, forced: list[Task]) -> Problem:
    task = _task(problem, task_id)
    if task is None:
        found = any(job.task_id == task_id for job in _real_jobs(problem))
        code = "already_started" if found else "task_not_found"
        raise InfeasibleError(reason(code, task=task_id))
    assignment = earliest_start(problem, task, problem.now)
    forced.append(task)
    return _fix(problem, task, assignment, assignment.end)


def _with_jobs(problem: Problem, jobs: Mapping[int, FrozenJob]) -> Problem:
    frozen = tuple(jobs.get(index, job) for index, job in enumerate(problem.frozen))
    return replace(problem, frozen=frozen)


def _use_item(
    problem: Problem, pack: GamePack, overlay: Mapping[str, Any], forced: list[Task]
) -> Problem:
    item = str(overlay["item"])
    rule = item_rule(pack, item)
    assert rule is not None  # `unsupported` is checked first
    at = int(overlay["at"])
    target = overlay.get("task_id")
    if at < problem.now:
        raise InfeasibleError(reason("item_timing", item=item))
    if isinstance(rule, RuneRule):
        if at > problem.now:
            raise InfeasibleError(reason("item_timing", item=item, detail=", pas plus tard"))
        cap = problem.resources.cap.get(rule.resource)
        if cap is None:
            return problem
        initial = {**problem.resources.initial, rule.resource: cap}
        return replace(problem, resources=replace(problem.resources, initial=initial))

    task = _task(problem, None if target is None else str(target))
    if isinstance(rule, BookRule):
        if target is None:
            raise InfeasibleError(reason("item_target_missing", item=item))
        item_of = pack.item(int(str(target).split(":", 1)[0]))
        if item_of is None or item_of.kind not in rule.kinds:
            raise InfeasibleError(reason("item_not_applicable", item=item, task=target))
        running = _running(problem, str(target), at)
        if running is not None:
            index = problem.frozen.index(running)
            return _with_jobs(problem, {index: replace(running, end=at)})
        if task is None:
            raise InfeasibleError(reason("task_not_found", task=target))
        assignment = earliest_start(problem, task, at)
        forced.append(task)
        return _fix(problem, task, assignment, assignment.start)

    assert isinstance(rule, PotionRule)
    if task is not None:
        if task.lane != rule.lane:
            raise InfeasibleError(reason("item_not_applicable", item=item, task=task.id))
        assignment = earliest_start(problem, task, at)
        forced.append(task)
        problem = _fix(problem, task, assignment, assignment.end)
        at = assignment.start
    elif target is not None and _running(problem, str(target), at) is None:
        raise InfeasibleError(reason("task_not_found", task=target))
    changed = {
        index: replace(job, end=rule.end_after(at, job.end))
        for index, job in enumerate(problem.frozen)
        if job.lane == rule.lane
        and not job.task_id.startswith(RESERVED_PREFIX)
        and job.start <= at < job.end
    }
    return _with_jobs(problem, changed)


def apply_overlays(
    problem: Problem, pack: GamePack, overlays: Sequence[Mapping[str, Any]]
) -> tuple[Problem, tuple[Task, ...]]:
    """The problem with every overlay applied, and the tasks fixed by start_now or use_item.

    Order: login policy, absences, builder reservations, then the fixed tasks as given.
    Raises `InfeasibleError`.
    """
    problem = replace(problem, policy=_policy(problem, overlays))
    problem = _absences(problem, overlays)
    for overlay in overlays:
        if overlay["type"] == "reserve_builder":
            problem = _reserve(problem, int(overlay["count"]), int(overlay["until"]))
    forced: list[Task] = []
    for overlay in overlays:
        if overlay["type"] == "start_now":
            problem = _start_now(problem, str(overlay["task_id"]), forced)
        elif overlay["type"] == "use_item":
            problem = _use_item(problem, pack, overlay, forced)
    return problem, tuple(forced)


# --------------------------------------------------------------------------- figures


def _overlap(start: int, end: int, low: int, high: int) -> int:
    return max(0, min(end, high) - max(start, low))


def _is_hero(pack: GamePack, task_id: str) -> bool:
    item = pack.item(int(task_id.split(":", 1)[0]))
    return item is not None and (item.kind == HERO or HERO in item.tags)


def figures(
    problem: Problem,
    plan: Plan,
    pack: GamePack,
    proof: Mapping[str, Any],
    forced: Sequence[Task] = (),
) -> dict[str, Any]:
    """The compared figures of `plan`, all read from `simulate` on `problem`.

    The tasks fixed by a scenario count as planned ones; reservations are no work.
    """
    sim = simulate(problem, plan)
    now, horizon_end = problem.now, problem.horizon_end
    jobs = _real_jobs(problem)
    reserved = [job for job in problem.frozen if job.task_id.startswith(RESERVED_PREFIX)]
    forced_ids = {task.id: task for task in forced}
    tasks = {task.id: task for task in problem.tasks}

    ends = {job.task_id: job.end for job in jobs if job.task_id in forced_ids}
    ends.update(sim.metrics.ends)
    finished = [
        tasks.get(name) or forced_ids[name] for name, end in ends.items() if end <= horizon_end
    ]
    finished_ids = {task.id for task in finished}

    goal_finish: int | None = None
    if problem.goal is not None:
        goal_ends = {**{job.task_id: job.end for job in jobs}, **ends}
        names = problem.goal.task_ids
        if names and all(goal_ends.get(name, horizon_end + 1) <= horizon_end for name in names):
            goal_finish = max(goal_ends[name] for name in names)
    elif sim.metrics.ends:
        last = max(sim.metrics.ends.values())
        goal_finish = last if last <= horizon_end else None

    remaining: dict[str, int] = {}
    for task in problem.tasks:
        if task.id not in finished_ids:
            for name, amount in task.cost.items():
                if amount > 0:
                    remaining[name] = remaining.get(name, 0) + amount

    spans = [(a.task_id, a.lane, a.start, a.end) for a in plan.assignments]
    spans += [(j.task_id, j.lane, j.start, j.end) for j in jobs if not j.extra]
    hero = sum(
        _overlap(start, end, now, horizon_end)
        for name, _, start, end in spans
        if _is_hero(pack, name)
    )
    busy = sum(
        _overlap(start, end, now, horizon_end) for _, lane, start, end in spans if lane == BUILDER
    )
    held = sum(_overlap(j.start, j.end, now, horizon_end) for j in reserved)
    capacity = problem.lanes.get(BUILDER, 0) * (horizon_end - now) - held
    return {
        "goal_finish": goal_finish,
        "finished_count": len(finished),
        "remaining_cost": dict(sorted(remaining.items())),
        "value_gained": float(sum(task.value for task in finished)),
        "hero_unavailable_s": hero,
        "logins": sim.metrics.logins,
        "idle_builder_s": max(0, capacity - busy),
        "proof": dict(proof),
    }


# --------------------------------------------------------------------------- entry points


def _compiled(
    inputs: PlanInputs, params: PlanParams, body: Mapping[str, Any], same_snapshot: bool
) -> tuple[Problem, Objective]:
    """The problem and objective of the stored plan `body`, as it was computed."""
    now = int(body["now"])
    carried = carried_jobs(body) if same_snapshot else {}
    compiled, _ = compile_inputs(inputs, now, params.horizon_days, carried)
    problem, weights = with_strategy(compiled, params)
    return problem, weights


def plan_figures(
    inputs: PlanInputs, params: PlanParams, body: Mapping[str, Any], *, same_snapshot: bool
) -> dict[str, Any]:
    """The compared figures of the stored real plan. Raises `MissingInputError`."""
    problem, _ = _compiled(inputs, params, body, same_snapshot)
    return figures(problem, stored_plan(body), inputs.pack, body["proof"])


def run_scenario(
    inputs: PlanInputs,
    params: PlanParams,
    body: Mapping[str, Any],
    overlays: Sequence[Mapping[str, Any]],
    config: PlannerConfig,
    *,
    engine_name: str,
    same_snapshot: bool,
) -> dict[str, Any]:
    """The scenario document: status, reasons, assignments, proof and the compared figures.

    `params` and `body` are those of the latest real plan. Raises `MissingInputError` and
    `UnknownStrategyError`.
    """
    assert inputs.schedule_profile is not None
    document: dict[str, Any] = {
        "now": int(body["now"]),
        "horizon_end": int(body["horizon_end"]),
        "timezone": inputs.schedule_profile.tz,
        "engine": engine_name,
        "profile": config.profile,
        "status": OK,
        "reasons": [],
        "assignments": [],
        "proof": None,
        "warnings": [],
        "figures": None,
    }
    missing = unsupported(inputs.pack, overlays)
    if missing:
        return document | {"status": UNSUPPORTED, "reasons": _reasons(missing)}

    changed = scenario_params(params, overlays)
    problem, weights = _compiled(inputs, changed, body, same_snapshot)
    strategy_set = any(overlay["type"] == "strategy" for overlay in overlays)
    objective = weights if strategy_set else (objective_of(inputs.journal) or weights)
    try:
        problem, forced = apply_overlays(problem, inputs.pack, overlays)
    except InfeasibleError as error:
        return document | {"status": INFEASIBLE, "reasons": _reasons([error.reason])}

    plan = engine(engine_name).solve(problem, objective, config)
    sim = simulate(problem, plan)
    if not sim.valid:
        detail = ", ".join(sorted({v.code for v in sim.violations}))
        return document | {
            "status": INFEASIBLE,
            "reasons": _reasons([reason("invalid_plan", detail=detail)]),
        }
    proof = dataclasses.asdict(plan.proof)
    shown = replace(problem, frozen=tuple(_real_jobs(problem)))
    return document | {
        "assignments": assignment_rows(inputs.pack, shown, plan),
        "proof": proof,
        "warnings": list(plan.warnings),
        "figures": figures(problem, plan, inputs.pack, proof, forced),
    }


def _reasons(found: Iterable[Reason]) -> list[dict[str, str]]:
    return [{"code": r.code, "message": r.message} for r in found]
