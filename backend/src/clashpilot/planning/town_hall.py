"""Town hall launch comparator (E26, SPEC "Comparateur", ADR-CP3): when to launch the next town
hall, and what is left to do when it ends.

Pure module: the caller reads the inputs and the latest plan; nothing is stored. The village is
compiled as the latest plan was (its `now`, horizon and params, the export and the whole journal),
with the skip lanes of the request, and each option is planned by the engine asked and judged by
`simulate` (ADR-4):

- `after`: the problem under the `town_hall_last` rule limited by the skip lanes (the town hall
  requires `current_level_ids(tasks, skip_lanes)`), whatever the plan's strategy;
- `no_wait`: on the problem without that rule, the town hall is fixed by the scenario overlay
  `start_at` from T*, the first instant from which the `after` plan keeps at least one builder
  free until its town hall starts. T* is counted in running jobs (plan and frozen ones): the
  slots the engine colours do not matter. The engine plans the rest around the town hall;
- `discount_edge`: when a builder modifier that shortens durations ends after T* and before the
  `after` town hall start, the town hall is fixed from the last instant before the modifier ends
  where it may start (`can_act`, an end awake under `strict`), never before T*.

When `after` cannot place the town hall, it is `infeasible`, `no_wait` starts from `now` and
`discount_edge` is not tried. Without a town hall task there is no target and no option.

Each option reads its own plan at the town hall end (`th_finish`): the builder idle time since
`now`, the current-level tasks (`current_level_ids`, no lane skipped) not finished then with their
remaining work per lane, those of the plan's composition, and the level each hero reached. An
option that cannot happen is `infeasible` with the reasons of the scenarios, never an error.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Any

from clashpilot.gamedata.pack import GamePack
from clashpilot.planning.compile import TOWN_HALL_TAG
from clashpilot.planning.critical import current_level_ids, unknown_lanes
from clashpilot.planning.journal import objective_of
from clashpilot.planning.model import Modifier, Plan, Problem, Task
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.replan import carried_jobs
from clashpilot.planning.scenarios import (
    RESERVED_PREFIX,
    InfeasibleError,
    Reason,
    apply_overlays,
    reason,
)
from clashpilot.planning.service import (
    PlanInputs,
    PlanParams,
    UnknownLaneError,
    compile_inputs,
    engine,
    with_strategy,
)
from clashpilot.planning.simulate import calendar_bounds, effective_duration, simulate
from clashpilot.planning.strategies import town_hall_last

AFTER = "after"
NO_WAIT = "no_wait"
DISCOUNT_EDGE = "discount_edge"
OK = "ok"
INFEASIBLE = "infeasible"

BUILDER = "builder"
HERO = "hero"


@dataclass(frozen=True)
class Context:
    """What every option of one comparison shares."""

    inputs: PlanInputs
    params: PlanParams
    body: Mapping[str, Any]
    config: PlannerConfig
    engine_name: str
    same_snapshot: bool
    plan_id: int


@dataclass(frozen=True)
class Problems:
    """The latest plan's problem without the town hall rule (`base`), with it (`after`)."""

    base: Problem
    after: Problem
    objective: Objective


@dataclass(frozen=True)
class Outcome:
    """One option planned: its problem and plan, and the town hall's span in them."""

    problem: Problem
    plan: Plan
    start: int
    end: int


# --------------------------------------------------------------------------- problems


def problems(context: Context, skip_lanes: Sequence[str]) -> Problems:
    """The problems of the options, compiled as the latest plan was with `skip_lanes`.

    Raises `UnknownLaneError` (before any engine run), `MissingInputError` and
    `UnknownStrategyError`.
    """
    params = replace(context.params, town_hall_skip_lanes=tuple(skip_lanes))
    body = context.body
    carried = carried_jobs(body) if context.same_snapshot else {}
    raw, _ = compile_inputs(context.inputs, int(body["now"]), params.horizon_days, carried)
    unknown = unknown_lanes(raw, params.town_hall_skip_lanes)
    if unknown:
        raise UnknownLaneError(unknown)
    shaped, weights = with_strategy(raw, params)
    # The plan's strategy may hold `town_hall_last`: the town halls get their own requirements
    # back, the only change that rule makes.
    own = {task.id: task.requires for task in raw.tasks}
    base = replace(
        shaped,
        tasks=tuple(
            replace(task, requires=own[task.id]) if TOWN_HALL_TAG in task.tags else task
            for task in shaped.tasks
        ),
    )
    after = replace(base, tasks=town_hall_last(base.tasks, params.town_hall_skip_lanes))
    return Problems(base, after, objective_of(context.inputs.journal) or weights)


def target_of(problem: Problem) -> Task | None:
    """The town hall task of smallest level, as the critical path targets it."""
    town_halls = [task for task in problem.tasks if TOWN_HALL_TAG in task.tags]
    return min(town_halls, key=lambda task: (task.to_level, task.id), default=None)


# --------------------------------------------------------------------------- launch instants


def t_star(problem: Problem, plan: Plan, town_hall: str, until: int) -> int:
    """The first instant from which fewer builder jobs than the lane holds run until `until`.

    Jobs are the plan's builder assignments other than `town_hall` and the frozen builder jobs
    that take a slot. It is `now` when the lane is never full before `until`.
    """
    jobs = [
        (a.start, a.end) for a in plan.assignments if a.lane == BUILDER and a.task_id != town_hall
    ]
    jobs += [
        (job.start, job.end) for job in problem.frozen if job.lane == BUILDER and not job.extra
    ]
    capacity = problem.lanes.get(BUILDER, 0)
    now = problem.now
    inner = {t for job in jobs for t in job if now < t < until}
    points = sorted({now, until, *inner})
    found = now
    for low, high in pairwise(points):
        if sum(1 for start, end in jobs if start <= low < end) >= capacity:
            found = high
    return found


def _may_start(problem: Problem, task: Task, t: int) -> bool:
    if not problem.calendar.can_act(t):
        return False
    if problem.policy.mode != "strict":
        return True
    return not problem.calendar.is_sleep(t + effective_duration(problem, task, t))


def edge_start(problem: Problem, task: Task, modifier: Modifier, lowest: int) -> int | None:
    """The last whole second before `modifier` ends where `task` may start under it.

    Not before `lowest`, nor before the modifier starts. "May start" is `can_act`, and under the
    policy `strict` an end that is not asleep. Whether it may start only changes at a calendar
    bound, a modifier bound, or where its end crosses a calendar bound: the last instant of each
    of those stretches is tried, latest first.
    """
    low = max(lowest, modifier.start, problem.now)
    if low >= modifier.end:
        return None
    bounds = calendar_bounds(problem.calendar)
    modifier_bounds = {bound for m in problem.modifiers for bound in (m.start, m.end)}
    starts = {low, *(b for b in modifier_bounds if low <= b < modifier.end)}
    durations = {effective_duration(problem, task, t) for t in starts}
    changes = {
        modifier.end,
        *bounds,
        *modifier_bounds,
        *(bound - duration for bound in bounds for duration in durations),
    }
    for change in sorted((c for c in changes if low < c <= modifier.end), reverse=True):
        if _may_start(problem, task, change - 1):
            return change - 1
    return None


def discount_instant(problem: Problem, task: Task, star: int, after_start: int) -> int | None:
    """Where `discount_edge` starts the town hall, or None when no builder reduction ends after
    T* and before the `after` town hall start (the one ending last is used)."""
    shortening = sorted(
        (
            m
            for m in problem.modifiers
            if m.lane == BUILDER and m.duration_pct < 100 and star < m.end < after_start
        ),
        key=lambda m: m.end,
        reverse=True,
    )
    for modifier in shortening:
        found = edge_start(problem, task, modifier, star)
        if found is not None:
            return found
    return None


# --------------------------------------------------------------------------- figures


def _is_hero(pack: GamePack, data_id: int) -> bool:
    item = pack.item(data_id)
    return item is not None and (item.kind == HERO or HERO in item.tags)


def _name(pack: GamePack, data_id: int) -> str:
    item = pack.item(data_id)
    return str(data_id) if item is None else item.name


def _levels_of_jobs(problem: Problem, data_id: int) -> list[tuple[int, int]]:
    """`(to_level, end)` of the frozen jobs upgrading `data_id`."""
    found = []
    for job in problem.frozen:
        if job.task_id.startswith(RESERVED_PREFIX):
            continue
        parts = job.task_id.split(":")
        if len(parts) == 3 and parts[0] == str(data_id) and parts[2].isdigit():
            found.append((int(parts[2]), job.end))
    return found


def figures(
    outcome: Outcome, current: Sequence[Task], composition: Iterable[int], pack: GamePack
) -> dict[str, Any]:
    """What `outcome` leaves at its town hall end: builder idle time, unfinished current-level
    work per lane and of the composition, and the level each hero reached."""
    problem, plan, finish = outcome.problem, outcome.plan, outcome.end
    spans = {
        job.task_id: (job.start, job.end)
        for job in problem.frozen
        if not job.task_id.startswith(RESERVED_PREFIX)
    }
    spans.update((a.task_id, (a.start, a.end)) for a in plan.assignments)

    def left(task: Task) -> int:
        span = spans.get(task.id)
        if span is None:
            return task.duration_s
        start, end = span
        return max(0, end - max(start, finish))

    chosen = frozenset(composition)
    by_lane: dict[str, int] = {}
    work_by_lane: dict[str, int] = {}
    composition_left = {"count": 0, "work_s": 0}
    for task in current:
        work = left(task)
        if work == 0:
            continue
        by_lane[task.lane] = by_lane.get(task.lane, 0) + 1
        work_by_lane[task.lane] = work_by_lane.get(task.lane, 0) + work
        if task.data_id in chosen:
            composition_left["count"] += 1
            composition_left["work_s"] += work

    heroes = []
    for data_id in sorted({task.data_id for task in current if _is_hero(pack, task.data_id)}):
        levels = [(t.to_level, left(t) == 0) for t in current if t.data_id == data_id]
        levels += [(level, end <= finish) for level, end in _levels_of_jobs(problem, data_id)]
        done = [level for level, finished in levels if finished]
        reached = max(done, default=min(level for level, _ in levels) - 1)
        heroes.append({"data_id": data_id, "name": _name(pack, data_id), "level_reached": reached})

    now = problem.now
    builder = [(a.start, a.end) for a in plan.assignments if a.lane == BUILDER]
    builder += [
        (job.start, job.end) for job in problem.frozen if job.lane == BUILDER and not job.extra
    ]
    busy = sum(max(0, min(end, finish) - max(start, now)) for start, end in builder)
    return {
        "idle_builder_s": problem.lanes.get(BUILDER, 0) * (finish - now) - busy,
        "unfinished_count": sum(by_lane.values()),
        "unfinished_by_lane": dict(sorted(by_lane.items())),
        "unfinished_work_s_by_lane": dict(sorted(work_by_lane.items())),
        "unfinished_composition": composition_left,
        "heroes": heroes,
    }


# --------------------------------------------------------------------------- options


def _infeasible(kind: str, context: Context, reasons: Iterable[Reason]) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": INFEASIBLE,
        "reasons": [{"code": r.code, "message": r.message} for r in reasons],
        "th_start": None,
        "th_finish": None,
        "idle_builder_s": None,
        "unfinished_count": None,
        "unfinished_by_lane": {},
        "unfinished_work_s_by_lane": {},
        "unfinished_composition": None,
        "heroes": [],
        "engine": context.engine_name,
        "proof": None,
    }


def _ok(kind: str, context: Context, outcome: Outcome, current: Sequence[Task]) -> dict[str, Any]:
    pack = context.inputs.pack
    return {
        "kind": kind,
        "status": OK,
        "reasons": [],
        "th_start": outcome.start,
        "th_finish": outcome.end,
        **figures(outcome, current, context.params.composition, pack),
        "engine": context.engine_name,
        "proof": dataclasses.asdict(outcome.plan.proof),
    }


def _solved(context: Context, problem: Problem, objective: Objective) -> Plan | Reason:
    plan = engine(context.engine_name).solve(problem, objective, context.config)
    sim = simulate(problem, plan)
    if not sim.valid:
        return reason("invalid_plan", detail=", ".join(sorted({v.code for v in sim.violations})))
    return plan


def _after(context: Context, found: Problems, target: Task) -> Outcome | Reason:
    plan = _solved(context, found.after, found.objective)
    if isinstance(plan, Reason):
        return plan
    placed = next((a for a in plan.assignments if a.task_id == target.id), None)
    if placed is None:
        return reason("no_slot", task=target.id)
    return Outcome(found.after, plan, placed.start, placed.end)


def _fixed(context: Context, found: Problems, target: Task, at: int) -> Outcome | Reason:
    """The town hall fixed by the scenario overlay `start_at` from `at`, the rest planned."""
    overlay = {"type": "start_at", "task_id": target.id, "at": at}
    try:
        problem, _ = apply_overlays(found.base, context.inputs.pack, [overlay])
    except InfeasibleError as error:
        return error.reason
    plan = _solved(context, problem, found.objective)
    if isinstance(plan, Reason):
        return plan
    job = next(job for job in problem.frozen if job.task_id == target.id)
    return Outcome(problem, plan, job.start, job.end)


def _option(
    kind: str, context: Context, outcome: Outcome | Reason, current: Sequence[Task]
) -> dict[str, Any]:
    if isinstance(outcome, Reason):
        return _infeasible(kind, context, [outcome])
    return _ok(kind, context, outcome, current)


def _scope(context: Context) -> str:
    return (
        f"Plan n° {context.plan_id}, horizon {context.params.horizon_days} j, moteur "
        f"{context.engine_name} : chaque option est un plan simulé, rien n'est enregistré."
    )


def compare_town_hall(context: Context, skip_lanes: Sequence[str] | None) -> dict[str, Any]:
    """The comparison document: target, skip lanes used and the options.

    `skip_lanes` None means the latest plan's `town_hall_skip_lanes`. Raises `UnknownLaneError`,
    `MissingInputError` and `UnknownStrategyError`.
    """
    chosen = context.params.town_hall_skip_lanes if skip_lanes is None else skip_lanes
    lanes = list(dict.fromkeys(chosen))
    found = problems(context, lanes)
    pack = context.inputs.pack
    target = target_of(found.base)
    answer: dict[str, Any] = {
        "target": None,
        "skip_lanes": lanes,
        "options": [],
        "reference_plan_id": context.plan_id,
        "now": found.base.now,
        "scope": _scope(context),
    }
    if target is None:
        return answer
    answer["target"] = {
        "task_id": target.id,
        "data_id": target.data_id,
        "name": _name(pack, target.data_id),
        "to_level": target.to_level,
    }
    ids = frozenset(current_level_ids(found.base.tasks))
    current = [task for task in found.base.tasks if task.id in ids]

    after = _after(context, found, target)
    # Without an `after` plan to read, `no_wait` launches as soon as the simulator accepts.
    if isinstance(after, Outcome):
        star = t_star(after.problem, after.plan, target.id, after.start)
    else:
        star = found.base.now
    options = [
        _option(AFTER, context, after, current),
        _option(NO_WAIT, context, _fixed(context, found, target, star), current),
    ]
    if isinstance(after, Outcome):
        edge = discount_instant(found.base, target, star, after.start)
        if edge is not None:
            outcome = _fixed(context, found, target, edge)
            options.append(_option(DISCOUNT_EDGE, context, outcome, current))
    answer["options"] = options
    return answer
