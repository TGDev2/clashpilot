"""Magic items and gems advisor (E19): each option is a what-if scenario judged by `simulate`.

Pure module: the caller reads the inputs and the latest plan; nothing is stored. The village is
compiled as the latest plan was (`scenarios.compiled`), the engine plans it once without item (the
reference, option `keep`), then once per option on a copy where the item acts as the `use_item`
overlay of E18 (a book ends a job at once, a potion shortens the running jobs of its lane, a rune
fills its storage) or where gems finish a running job. `score_delta` is the simulated score of the
option's plan minus the reference's.

Every value comes from `pack.rules` (SPEC 5): an item or a gem cost without its rule answers
`unsupported` with a reason and runs no engine. The ranking is indicative: it never claims more
than the heuristic or solver proof of the plans it compares.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from clashpilot.gamedata.rules import (
    BookRule,
    GemFinishRule,
    PotionRule,
    RuneRule,
    gem_finish_rule,
    helper_upgrade_rule,
    item_rule,
)
from clashpilot.planning.explain import reliability
from clashpilot.planning.journal import objective_of
from clashpilot.planning.model import FrozenJob, Plan, Problem
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.scenarios import (
    RESERVED_PREFIX,
    InfeasibleError,
    apply_overlays,
    compiled,
)
from clashpilot.planning.service import PlanInputs, PlanParams, engine
from clashpilot.planning.simulate import simulate

OK = "ok"
UNSUPPORTED = "unsupported"
UNAVAILABLE = "unavailable"

KEEP = "keep"
USE = "use"
GEM_FINISH = "gem_finish"
HELPERS_SECTION = "helpers"

# Planned tasks a book is tried on when no candidate is given, besides the running jobs.
MAX_PLANNED_BOOK_TARGETS = 3

MESSAGES = {
    "unsupported_item": (
        "Le pack actif n'a pas de règle sourcée pour l'objet {item} : comparaison non prise en "
        "charge."
    ),
    "item_unavailable": "La saisie manuelle indique 0 exemplaire de {item} : rien à comparer.",
    "unsupported_gem_finish": (
        "Le pack actif n'a pas de règle sourcée de coût en gemmes : terminer une amélioration "
        "n'est pas comparé."
    ),
    "unsupported_helper_upgrade": (
        "Le pack actif n'a pas de règle sourcée d'amélioration des assistants : non comparé."
    ),
    "option_skipped": "Option écartée pour {task} : {detail}",
    "not_enough_gems": (
        "Option écartée pour {task} : {detail} gemmes nécessaires, stock saisi {stock}."
    ),
    "job_shortened": "{task} finit {minutes} min plus tôt.",
    "resource_filled": "Le stock de {resource} passe de {before} à {after} (plafond).",
    "helper_effect_unknown": (
        "L'effet de l'assistant n'est pas décrit par le pack : seul le coût est connu."
    ),
}


def message(code: str, **values: Any) -> str:
    return MESSAGES[code].format(**{k: str(v) for k, v in values.items()})


def _reason(code: str, **values: Any) -> dict[str, str]:
    return {"code": code, "message": message(code, **values)}


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
class Reference:
    problem: Problem
    objective: Objective
    plan: Plan
    score: float
    reliability: dict[str, Any]


# --------------------------------------------------------------------------- shared steps


def _answer(context: Context, status: str, reasons: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": status,
        "reasons": reasons,
        "options": [],
        "proof": None,
        "reference_plan_id": context.plan_id,
        "scope": _scope(context, 0),
    }


def _scope(context: Context, count: int) -> str:
    days = context.params.horizon_days
    return (
        f"Plan n° {context.plan_id}, horizon {days} j, moteur {context.engine_name} : "
        f"{count} option(s) simulée(s) une à une, classement indicatif."
    )


def _reference(context: Context) -> Reference:
    inputs = context.inputs
    problem, weights = compiled(inputs, context.params, context.body, context.same_snapshot)
    objective = objective_of(inputs.journal) or weights
    plan = engine(context.engine_name).solve(problem, objective, context.config)
    value = score(simulate(problem, plan), objective, problem)
    return Reference(problem, objective, plan, value, _reliability(context, problem))


def _reliability(context: Context, problem: Problem) -> dict[str, Any]:
    """`{level, factors}` from the pack and the resources the compiled tasks consume."""
    inputs = context.inputs
    consumed = {name for task in problem.tasks for name, amount in task.cost.items() if amount}
    resources = {
        name: (typed.status, typed.as_of) for name, typed in inputs.manual.resources.items()
    }
    return reliability(consumed, resources, fictional=inputs.pack.data.fictional, now=problem.now)


def _real(problem: Problem) -> list[FrozenJob]:
    return [job for job in problem.frozen if not job.task_id.startswith(RESERVED_PREFIX)]


def _running(problem: Problem) -> list[FrozenJob]:
    return [job for job in _real(problem) if job.start <= problem.now < job.end]


def _ends(problem: Problem, plan: Plan) -> dict[str, int]:
    ends = {job.task_id: job.end for job in _real(problem)}
    ends.update({a.task_id: a.end for a in plan.assignments})
    return ends


def _shortened(
    reference: Reference, problem: Problem, plan: Plan, labels: Callable[[str], str]
) -> list[dict[str, Any]]:
    before = _ends(reference.problem, reference.plan)
    found = []
    for job in _real(problem):
        end_before = before.get(job.task_id)
        if end_before is not None and job.end < end_before:
            minutes = (end_before - job.end) // 60
            found.append(
                {
                    "code": "job_shortened",
                    "message": message("job_shortened", task=labels(job.task_id), minutes=minutes),
                    "task_id": job.task_id,
                    "end_before": end_before,
                    "end_after": job.end,
                }
            )
    return found


def _option(
    context: Context,
    reference: Reference,
    problem: Problem,
    *,
    kind: str,
    task_id: str | None,
    at: int,
    extra: Mapping[str, Any] | None = None,
    consequences: Sequence[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    """The option planned by the engine on `problem`, or None when its plan breaks a rule."""
    plan = engine(context.engine_name).solve(problem, reference.objective, context.config)
    sim = simulate(problem, plan)
    if not sim.valid:
        return None
    labels = _labeller(context)
    finish_delta = 0
    if task_id is not None:
        before = _ends(reference.problem, reference.plan).get(task_id)
        after = _ends(problem, plan).get(task_id)
        if before is not None and after is not None:
            finish_delta = after - before
    return {
        "kind": kind,
        "task_id": task_id,
        "at": at,
        "score_delta": score(sim, reference.objective, problem) - reference.score,
        "finish_delta_s": finish_delta,
        "consequences": [*_shortened(reference, problem, plan, labels), *consequences],
        "reliability": reference.reliability,
        **(extra or {}),
    }


def _keep(reference: Reference) -> dict[str, Any]:
    return {
        "kind": KEEP,
        "task_id": None,
        "at": None,
        "score_delta": 0.0,
        "finish_delta_s": 0,
        "consequences": [],
        "reliability": reference.reliability,
    }


def _labeller(context: Context) -> Callable[[str], str]:
    pack = context.inputs.pack

    def label(task_id: str) -> str:
        parts = task_id.split(":")
        item = pack.item(int(parts[0])) if parts[0].isdecimal() else None
        if item is None or len(parts) != 3:
            return task_id
        return f"{item.name} niveau {parts[2]}"

    return label


def _ranked(
    context: Context, reference: Reference, options: list[dict[str, Any]], reasons: list[Any]
) -> dict[str, Any]:
    ranked = sorted([_keep(reference), *options], key=lambda o: -float(o["score_delta"]))
    return {
        "status": OK,
        "reasons": reasons,
        "options": ranked,
        "proof": dataclasses.asdict(reference.plan.proof),
        "reference_plan_id": context.plan_id,
        "scope": _scope(context, len(ranked)),
    }


# --------------------------------------------------------------------------- items


def _book_targets(
    context: Context, reference: Reference, rule: BookRule, candidates: Sequence[str] | None
) -> list[tuple[str, int]]:
    """`(task_id, at)` pairs: running jobs at now, else the planned start of the task."""
    problem, pack = reference.problem, context.inputs.pack
    running = {job.task_id for job in _running(problem)}
    starts = {a.task_id: a.start for a in reference.plan.assignments}

    def kind_of(task_id: str) -> str | None:
        item = pack.item(int(task_id.split(":", 1)[0]))
        return None if item is None else item.kind

    if candidates is not None:
        return [
            (task_id, problem.now if task_id in running else starts.get(task_id, problem.now))
            for task_id in dict.fromkeys(candidates)
        ]
    targets = [(job, problem.now) for job in sorted(running) if kind_of(job) in rule.kinds]
    durations = {task.id: task.duration_s for task in problem.tasks}
    planned = sorted(
        (
            a
            for a in reference.plan.assignments
            if kind_of(a.task_id) in rule.kinds and a.task_id in durations
        ),
        key=lambda a: (-durations[a.task_id], a.task_id),
    )
    targets += [(a.task_id, a.start) for a in planned[:MAX_PLANNED_BOOK_TARGETS]]
    return targets


def _item_overlays(
    context: Context,
    reference: Reference,
    rule: BookRule | PotionRule | RuneRule,
    candidates: Sequence[str] | None,
) -> list[dict[str, Any]]:
    now = reference.problem.now
    item = rule.item
    pairs: list[tuple[str | None, int]]
    if isinstance(rule, BookRule):
        pairs = list(_book_targets(context, reference, rule, candidates))
    elif isinstance(rule, PotionRule) and candidates is not None:
        running = {job.task_id for job in _running(reference.problem)}
        starts = {a.task_id: a.start for a in reference.plan.assignments}
        pairs = [
            (None, now) if task in running else (task, starts.get(task, now))
            for task in dict.fromkeys(candidates)
        ]
    else:
        pairs = [(None, now)]
    unique = dict.fromkeys(pairs)
    return [{"type": "use_item", "item": item, "task_id": t, "at": at} for t, at in unique]


def compare_item(context: Context, item: str, candidates: Sequence[str] | None) -> dict[str, Any]:
    """The ranking of the uses of `item` against keeping it."""
    rule = item_rule(context.inputs.pack, item)
    if rule is None:
        return _answer(context, UNSUPPORTED, [_reason("unsupported_item", item=item)])
    if context.inputs.manual.magic_items.get(item) == 0:
        return _answer(context, UNAVAILABLE, [_reason("item_unavailable", item=item)])

    reference = _reference(context)
    options: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    for overlay in _item_overlays(context, reference, rule, candidates):
        target = overlay["task_id"]
        try:
            problem, _ = apply_overlays(reference.problem, context.inputs.pack, [overlay])
        except InfeasibleError as error:
            detail = error.reason.message
            reasons.append(_reason("option_skipped", task=target or item, detail=detail))
            continue
        filled: list[dict[str, Any]] = []
        if isinstance(rule, RuneRule):
            filled = _filled(reference.problem, problem, rule.resource)
        found = _option(
            context,
            reference,
            problem,
            kind=USE,
            task_id=target,
            at=int(overlay["at"]),
            consequences=filled,
        )
        if found is not None:
            options.append(found)
    return _ranked(context, reference, options, reasons)


def _filled(before: Problem, after: Problem, resource: str) -> list[dict[str, Any]]:
    old = before.resources.initial.get(resource, 0)
    new = after.resources.initial.get(resource, 0)
    if new == old:
        return []
    text = message("resource_filled", resource=resource, before=old, after=new)
    return [
        {
            "code": "resource_filled",
            "message": text,
            "resource": resource,
            "before": old,
            "after": new,
        }
    ]


# --------------------------------------------------------------------------- gems and helpers


def gems_for(rule: GemFinishRule, left: int) -> int | None:
    """The gems of the first rate whose `up_to_s` covers `left` seconds, or None beyond them."""
    for rate in rule.rates:
        if left <= rate.up_to_s:
            return rate.gems
    return None


def _finished(problem: Problem, job: FrozenJob, gems: int) -> Problem:
    """`job` ends at `now`; the gems are paid now when their stock is modelled."""
    frozen = tuple(replace(j, end=problem.now) if j is job else j for j in problem.frozen)
    initial = dict(problem.resources.initial)
    if "gems" in initial:
        initial["gems"] -= gems
    return replace(problem, frozen=frozen, resources=replace(problem.resources, initial=initial))


def _gem_options(
    context: Context, rule: GemFinishRule, candidates: Sequence[str] | None
) -> tuple[Reference, list[dict[str, Any]], list[dict[str, str]]]:
    reference = _reference(context)
    problem = reference.problem
    typed = context.inputs.manual.resources.get("gems")
    stock = None if typed is None else typed.value
    options: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    for job in _running(problem):
        if candidates is not None and job.task_id not in candidates:
            continue
        gems = gems_for(rule, job.end - problem.now)
        if gems is None:
            continue
        if stock is not None and gems > stock:
            reasons.append(_reason("not_enough_gems", task=job.task_id, detail=gems, stock=stock))
            continue
        found = _option(
            context,
            reference,
            _finished(problem, job, gems),
            kind=GEM_FINISH,
            task_id=job.task_id,
            at=problem.now,
            extra={"gems": gems},
        )
        if found is not None:
            options.append(found)
    return reference, options, reasons


def _helpers(context: Context) -> dict[str, Any]:
    """One upgrade per helper of the export, from its imported level, priced by the pack."""
    rule = helper_upgrade_rule(context.inputs.pack)
    if rule is None:
        return {
            "status": UNSUPPORTED,
            "reasons": [_reason("unsupported_helper_upgrade")],
            "options": [],
        }
    inputs = context.inputs
    problem, _ = compiled(inputs, context.params, context.body, context.same_snapshot)
    found = _reliability(context, problem)
    costs = {level.level: dict(level.cost) for level in rule.levels}
    options = []
    for entry in context.inputs.snapshot.items:
        if entry.section != HELPERS_SECTION or entry.level is None:
            continue
        cost = costs.get(entry.level + 1)
        if cost is None:
            continue
        options.append(
            {
                "data_id": entry.data_id,
                "from_level": entry.level,
                "to_level": entry.level + 1,
                "cost": cost,
                "score_delta": None,
                "consequences": [_reason("helper_effect_unknown")],
                "reliability": found,
            }
        )
    return {"status": OK, "reasons": [], "options": options}


def compare_gems(context: Context, candidates: Sequence[str] | None) -> dict[str, Any]:
    """Keeping the gems against finishing each running job, then the helpers' upgrades."""
    rule = gem_finish_rule(context.inputs.pack)
    if rule is None:
        answer = _answer(context, UNSUPPORTED, [_reason("unsupported_gem_finish")])
    else:
        reference, options, reasons = _gem_options(context, rule, candidates)
        answer = _ranked(context, reference, options, reasons)
    return {**answer, "helpers": _helpers(context)}
