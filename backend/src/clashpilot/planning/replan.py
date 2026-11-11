"""Replanning after an event (E11; SPEC ADR-4, ADR-7; brief constraint 8). Pure module.

`replan` checks an event against the state before it (the export, the journal and the plan the
event is posted on) and records it as a journal entry holding every value its replay reads. The
village is then compiled again from the export and the whole journal at `now`, which never goes
back: what has started is frozen and never moved, only a `task_finished` or an item on a job may
end it sooner; a task planned in the past but never started is planned again from `now`.

The plan posted on is kept without calling the engine:

- after a `task_started`, if it stays valid on the new problem once the tasks now frozen are taken
  out of it: a start at its planned date changes nothing, although it changes `input_hash`;
- after another event, if `input_hash` is the same.

Otherwise the engine of the stored params runs with `reference` = that plan and the objective of
the latest `priority_changed`, or the weights of the stored strategy (the default objective
without strategy) with `perturbation` 0.5. The stored strategy, composition, goal and war windows
are applied again to the new problem (E16); the event itself is checked on the bare problem.

The report pairs the tasks of both plans by class `(data_id, to_level)`, then by rank (SPEC
section 4, rule 2): an id first meets the same id, the others meet in instance order, so that a new
export ranking the instances otherwise makes no false change. A change is a task whose start or
slot moved, or that is in one plan only; a job that ended by `now` and left the plan is none. The
consequences score the plan posted on with the new objective on the new problem, its violations
aside: it may no longer be feasible.
"""

from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import zip_longest
from typing import Any

from clashpilot.gamedata.rules import BookRule, PotionRule, RuneRule, item_rule
from clashpilot.planning.compile import split_task_id
from clashpilot.planning.journal import (
    BOOK,
    GAME_DATA_UPDATED,
    ITEM_USED,
    POTION,
    PRIORITY_CHANGED,
    RESOURCES_CORRECTED,
    RUNE,
    SCHEDULE_CHANGED,
    SNAPSHOT_IMPORTED,
    TASK_FINISHED,
    TASK_STARTED,
    JournalEntry,
    KnownJob,
    objective_of,
)
from clashpilot.planning.messages import EVENT_LABELS, duration_label, entry
from clashpilot.planning.model import Assignment, Plan, Problem, Proof, Task
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.service import (
    DEFAULT_OBJECTIVE,
    PlanInputs,
    PlanOutcome,
    PlanParams,
    compile_inputs,
    describe,
    engine,
    input_hash,
    planner_config,
    strategy_objective,
    with_strategy,
)
from clashpilot.planning.simulate import effective_cost, effective_duration, simulate

REPLAN_PERTURBATION = 0.5
SCORE_EPSILON = 1e-12

Row = Mapping[str, Any]


class ReplanError(Exception):
    """An event refused before anything changes: answered with `status` and `code`."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Event:
    type: str
    at: int
    task_id: str | None = None
    item: str | None = None
    resources: Mapping[str, int] | None = None
    objective: Mapping[str, float] | None = None
    pack_id: str | None = None


@dataclass(frozen=True)
class PreviousPlan:
    """The stored plan an event is posted on."""

    plan_id: int
    body: Mapping[str, Any]
    params: PlanParams
    input_hash: str
    explanations: Mapping[str, dict[str, Any]]
    same_snapshot: bool  # computed on the export the replanning reads


@dataclass(frozen=True)
class Replanned:
    outcome: PlanOutcome
    entry: JournalEntry
    report: dict[str, Any]  # the answer without `plan_id`
    kept: bool


def default_objective(params: PlanParams | None = None) -> Objective:
    """The objective of a replanning without `priority_changed`: the weights of the plan's
    strategy (the default ones without strategy) with a perturbation of 0.5."""
    weights = DEFAULT_OBJECTIVE if params is None else strategy_objective(params)
    return dataclasses.replace(weights, perturbation=REPLAN_PERTURBATION)


def replan(
    inputs: PlanInputs,
    previous: PreviousPlan,
    event: Event,
    now: int,
    overrides: Mapping[str, Any],
) -> Replanned:
    """Check, record and apply `event`; return the new plan and its report.

    `inputs.journal` is the journal before the event. Raises `ReplanError`, and `MissingInputError`
    or `InvalidPlanError` as `create_plan` does.
    """
    now = max(now, int(previous.body["now"]))
    if event.at > now:
        raise ReplanError(422, "future_event", "Un événement ne peut pas être daté dans le futur.")
    params = previous.params
    config = planner_config(overrides, params.profile)
    carried = _carried(previous.body) if previous.same_snapshot else {}

    before, known = compile_inputs(inputs, now, params.horizon_days, carried)
    base = default_objective(params)
    payload = _checked(event, before, known, inputs, previous, base)
    recorded = JournalEntry(
        entry_id=max((e.entry_id for e in inputs.journal), default=0) + 1,
        type=event.type,
        at=event.at,
        task_id=event.task_id,
        payload=payload,
    )
    after_inputs = dataclasses.replace(inputs, journal=(*inputs.journal, recorded))
    compiled, _ = compile_inputs(after_inputs, now, params.horizon_days, carried)
    problem, _ = with_strategy(compiled, params)
    objective = objective_of(after_inputs.journal) or base

    reference = _plan_of(previous.body)
    frozen = {job.task_id for job in problem.frozen}
    remaining = dataclasses.replace(
        reference, assignments=tuple(a for a in reference.assignments if a.task_id not in frozen)
    )
    kept = _keeps(event, problem, objective, params, config.seed, previous, remaining)
    if kept:
        value = score(simulate(problem, remaining, reference), objective, problem)
        plan = Plan(remaining.assignments, params.engine, Proof("heuristic", value))
    else:
        plan = engine(params.engine).solve(problem, objective, config, reference)
    outcome = describe(
        after_inputs,
        params,
        problem,
        plan,
        objective,
        config,
        now,
        reference=reference,
        explanations=previous.explanations if kept else None,
    )
    report = _report(event, previous, outcome, objective, remaining, reference, now, kept)
    return Replanned(outcome=outcome, entry=recorded, report=report, kept=kept)


def _keeps(
    event: Event,
    problem: Problem,
    objective: Objective,
    params: PlanParams,
    seed: int,
    previous: PreviousPlan,
    remaining: Plan,
) -> bool:
    if event.type != TASK_STARTED:
        digest = input_hash(problem, objective, params.engine, params.profile, seed)
        if digest != previous.input_hash:
            return False
    return simulate(problem, remaining).valid


def _carried(body: Row) -> dict[str, tuple[int, int]]:
    """`(start, lane_slot)` of every job the plan lists as frozen."""
    return {
        row["task_id"]: (int(row["start"]), int(row["lane_slot"]))
        for row in body["assignments"]
        if row["status"] == "frozen"
    }


def _plan_of(body: Row) -> Plan:
    """The planned assignments of a stored plan, as the reference of the engine."""
    assignments = tuple(
        Assignment(row["task_id"], row["lane"], row["lane_slot"], row["start"], row["end"])
        for row in body["assignments"]
        if row["status"] == "planned"
    )
    proof = body["proof"]
    return Plan(assignments, str(body["engine"]), Proof(proof["kind"], float(proof["objective"])))


# --------------------------------------------------------------------------- checks


def _checked(
    event: Event,
    problem: Problem,
    known: Mapping[str, KnownJob],
    inputs: PlanInputs,
    previous: PreviousPlan,
    base: Objective,
) -> dict[str, Any]:
    """The payload of `event`, or `ReplanError` when it cannot have happened."""
    tasks = {task.id: task for task in problem.tasks}
    if event.type == TASK_STARTED:
        return _started(event, problem, tasks, known, inputs.journal, previous.body)
    if event.type == TASK_FINISHED:
        _job(event, _task_of(event), tasks, known)
        return {}
    if event.type == ITEM_USED:
        return _item(event, problem, tasks, known, inputs)
    if event.type == RESOURCES_CORRECTED:
        return {"resources": dict(sorted((event.resources or {}).items()))}
    if event.type == PRIORITY_CHANGED:
        return {"objective": _weights(event, inputs.journal, base)}
    if event.type == GAME_DATA_UPDATED:
        if event.pack_id != inputs.pack.pack_id:
            raise ReplanError(
                409, "pack_not_active", f"Le pack {event.pack_id} n'est pas le pack actif."
            )
        return {"pack_id": event.pack_id}
    if event.type == SNAPSHOT_IMPORTED:
        return {"exported_at": inputs.snapshot.exported_at}
    if event.type == SCHEDULE_CHANGED:
        return {}
    raise ReplanError(422, "invalid_event", f"Type d'événement inconnu : {event.type}.")


def _task_of(event: Event) -> str:
    if event.task_id is None:
        raise ReplanError(422, "invalid_event", "Cet événement doit nommer une amélioration.")
    return event.task_id


def _not_found(name: str) -> ReplanError:
    return ReplanError(404, "task_not_found", f"L'amélioration {name} est inconnue du plan.")


def _job(
    event: Event, name: str, tasks: Mapping[str, Task], known: Mapping[str, KnownJob]
) -> KnownJob:
    """The started job `name`, refused if it is only planned or unknown."""
    job = known.get(name)
    if job is None:
        if name in tasks:
            raise ReplanError(
                409, "task_not_started", f"L'amélioration {name} n'a pas encore démarré."
            )
        raise _not_found(name)
    if job.start is not None and event.at < job.start:
        raise ReplanError(
            422, "invalid_event", f"L'événement précède le démarrage de l'amélioration {name}."
        )
    return job


def _started(
    event: Event,
    problem: Problem,
    tasks: Mapping[str, Task],
    known: Mapping[str, KnownJob],
    journal: Sequence[JournalEntry],
    body: Row,
) -> dict[str, Any]:
    name = _task_of(event)
    started = {e.task_id for e in journal if e.type == TASK_STARTED}
    if name in started or name in known:
        raise ReplanError(409, "already_started", f"L'amélioration {name} a déjà démarré.")
    task = tasks.get(name)
    if task is None:
        raise _not_found(name)
    done = {job.task_id: job.end for job in problem.frozen}
    for required in task.requires:
        if required not in done or done[required] > event.at:
            raise ReplanError(
                409,
                "task_not_ready",
                f"L'amélioration {name} attend encore {required}.",
            )
    slot = _slot(task, event.at, problem, known, body)
    cost = effective_cost(problem, task, event.at)
    return {
        "lane": task.lane,
        "lane_slot": slot,
        "end": event.at + effective_duration(problem, task, event.at),
        "cost": dict(sorted(cost.items())),
    }


def _slot(task: Task, at: int, problem: Problem, known: Mapping[str, KnownJob], body: Row) -> int:
    """The planned slot if it is free at `at`, else the first free one."""
    capacity = problem.lanes.get(task.lane, 0)
    busy = {
        job.lane_slot
        for job in problem.frozen
        if job.lane == task.lane and not job.extra and job.start <= at < job.end
    }
    busy |= {
        job.lane_slot
        for job in known.values()
        if job.journal and job.lane == task.lane and job.lane_slot is not None and job.runs_at(at)
    }
    planned = next(
        (
            int(row["lane_slot"])
            for row in body["assignments"]
            if row["task_id"] == task.id and row["status"] == "planned" and row["lane"] == task.lane
        ),
        None,
    )
    if planned is not None and planned not in busy and 0 <= planned < capacity:
        return planned
    free = next((slot for slot in range(capacity) if slot not in busy), None)
    if free is None:
        raise ReplanError(
            409, "lane_full", f"Aucun créneau de la file {task.lane} n'est libre à cette date."
        )
    return free


def _item(
    event: Event,
    problem: Problem,
    tasks: Mapping[str, Task],
    known: Mapping[str, KnownJob],
    inputs: PlanInputs,
) -> dict[str, Any]:
    name = event.item or ""
    rule = item_rule(inputs.pack, name)
    if rule is None:
        raise ReplanError(
            422, "unsupported_item", f"Le pack de données ne décrit pas l'objet {name}."
        )
    if isinstance(rule, BookRule):
        job = _job(event, _task_of(event), tasks, known)
        item = inputs.pack.item(split_task_id(job.task_id)[0])
        if item is None or item.kind not in rule.kinds or not job.runs_at(event.at):
            raise ReplanError(
                422,
                "item_not_applicable",
                f"L'objet {name} ne s'applique pas à l'amélioration {job.task_id}.",
            )
        return {"item": name, "effect": BOOK}
    if isinstance(rule, PotionRule):
        return {
            "item": name,
            "effect": POTION,
            "lane": rule.lane,
            "duration_multiplier": rule.duration_multiplier,
            "duration_s": rule.duration_s,
        }
    assert isinstance(rule, RuneRule)
    cap = problem.resources.cap.get(rule.resource)
    if cap is None:
        raise ReplanError(
            422,
            "item_not_applicable",
            f"L'objet {name} demande une réserve de {rule.resource}, et le village n'en a pas.",
        )
    return {"item": name, "effect": RUNE, "resource": rule.resource, "value": cap}


def _weights(event: Event, journal: Sequence[JournalEntry], base: Objective) -> dict[str, float]:
    """The new weights over the current ones: a weight left out keeps its value."""
    given = dict(event.objective or {})
    names = {field.name for field in dataclasses.fields(Objective)}
    unknown = sorted(set(given) - names)
    if unknown:
        raise ReplanError(422, "invalid_event", "Poids inconnu : " + ", ".join(unknown) + ".")
    if not all(math.isfinite(value) for value in given.values()):
        raise ReplanError(422, "invalid_event", "Chaque poids doit être un nombre fini.")
    weights = dataclasses.asdict(objective_of(journal) or base)
    weights.update({name: float(value) for name, value in given.items()})
    return weights


# --------------------------------------------------------------------------- report


def pair_rows(
    old_rows: Sequence[Row], new_rows: Sequence[Row]
) -> list[tuple[Row | None, Row | None]]:
    """Rows of two plans paired by class `(data_id, to_level)`, then by rank."""

    def classes(rows: Sequence[Row]) -> dict[tuple[int, int], list[Row]]:
        grouped: dict[tuple[int, int], list[Row]] = defaultdict(list)
        for row in rows:
            data_id, _, to_level = split_task_id(str(row["task_id"]))
            grouped[(data_id, to_level)].append(row)
        for group in grouped.values():
            group.sort(key=lambda row: split_task_id(str(row["task_id"]))[1])
        return grouped

    old, new = classes(old_rows), classes(new_rows)
    pairs: list[tuple[Row | None, Row | None]] = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key, []), new.get(key, [])
        same = {row["task_id"] for row in before} & {row["task_id"] for row in after}
        by_id = {row["task_id"]: row for row in after}
        pairs += [(row, by_id[row["task_id"]]) for row in before if row["task_id"] in same]
        rest_old = [row for row in before if row["task_id"] not in same]
        rest_new = [row for row in after if row["task_id"] not in same]
        pairs += list(zip_longest(rest_old, rest_new))
    return pairs


def _side(row: Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in ("start", "end", "lane", "lane_slot")}


def changes(
    old_rows: Sequence[Row], new_rows: Sequence[Row], now: int
) -> tuple[list[dict[str, Any]], int]:
    """The tasks whose start or slot changed, or in one plan only; and how many did not."""
    found: list[dict[str, Any]] = []
    unchanged = 0
    for old, new in pair_rows(old_rows, new_rows):
        if new is None and old is not None and old["status"] == "frozen" and old["end"] <= now:
            continue  # a job that ended left the plan
        if (
            old is not None
            and new is not None
            and (old["start"], old["lane_slot"]) == (new["start"], new["lane_slot"])
        ):
            unchanged += 1
            continue
        named = new if new is not None else old
        assert named is not None
        found.append({"task_id": named["task_id"], "before": _side(old), "after": _side(new)})
    found.sort(key=lambda change: change["task_id"])
    return found, unchanged


def _finish(rows: Sequence[Row], problem: Problem, now: int) -> int | None:
    """End of the goal, or of the last upgrade when there is no goal; None if nothing is left."""
    goal = problem.goal.task_ids if problem.goal is not None else None
    ends = [
        int(row["end"])
        for row in rows
        if (row["status"] == "planned" or row["end"] > now)
        and (goal is None or row["task_id"] in goal)
    ]
    return max(ends) if ends else None


def _report(
    event: Event,
    previous: PreviousPlan,
    outcome: PlanOutcome,
    objective: Objective,
    remaining: Plan,
    reference: Plan,
    now: int,
    kept: bool,
) -> dict[str, Any]:
    problem = outcome.problem
    old_rows = list(previous.body["assignments"])
    new_rows = list(outcome.body["assignments"])
    moved, unchanged = changes(old_rows, new_rows, now)

    before = simulate(problem, remaining, reference)
    old_score = score(dataclasses.replace(before, valid=True), objective, problem)
    score_delta = outcome.score - old_score
    idle_delta = before.metrics.busy_s - outcome.sim.metrics.busy_s
    old_finish, new_finish = _finish(old_rows, problem, now), _finish(new_rows, problem, now)
    finish_delta = (
        new_finish - old_finish if old_finish is not None and new_finish is not None else None
    )

    messages = [entry("event_recorded", event.type, label=EVENT_LABELS[event.type])]
    if kept:
        messages.append(entry("plan_kept"))
    if moved:
        messages.append(entry("tasks_moved", len(moved)))
    messages.append(entry("tasks_unchanged", unchanged))
    if finish_delta:
        code = "finish_earlier" if finish_delta < 0 else "finish_later"
        messages.append(entry(code, finish_delta, duration=duration_label(finish_delta)))
    if abs(score_delta) > SCORE_EPSILON:
        messages.append(entry("score_gain" if score_delta > 0 else "score_loss", abs(score_delta)))
    if idle_delta:
        code = "idle_less" if idle_delta < 0 else "idle_more"
        messages.append(entry(code, idle_delta, duration=duration_label(idle_delta)))
    return {
        "previous_plan_id": previous.plan_id,
        "changes": moved,
        "unchanged_count": unchanged,
        "consequences": {
            "goal_finish_delta_s": finish_delta,
            "score_delta": score_delta,
            "idle_delta_s": idle_delta,
            "messages": messages,
        },
        "proof": outcome.body["proof"],
        "input_hash": outcome.input_hash,
    }
