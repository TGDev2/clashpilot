"""Explained recommendations of a plan (SPEC section 4, Trace). Pure module.

The explanations are built only from the trace of the plan, the problem and the simulator result:
every value they cite is computed here, and their texts come from `clashpilot.planning.messages`.
They are JSON structures (`{code, message, value}` entries) so that a later assistant can read
them, not free text only.

- The action of a task is explained by the trace decision whose `chosen` names that task. Without
  such a decision (an engine without trace), `why.reasons` holds `no_trace` and `counterfactual`
  is None.
- `counterfactual` compares `chosen` with the best other candidate of the decision whose score is
  not `INVALID_SCORE` (first one in the decision order on a tie): `score_delta` is
  `chosen.score - best.score`. `finish_delta_s` is the end the alternative task would have if
  started at its candidate start minus the simulated end of the chosen task; for a wait
  alternative it is the delay the wait puts on the start of the chosen task.
- `reliability` is low on a fictional pack or when a consumed resource is estimated or older than
  7 days, high only when every consumed resource is confirmed, less than 7 days old, on a
  non-fictional pack, and medium otherwise (a resource exactly 7 days old).
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from clashpilot.planning.messages import entry, resource_label
from clashpilot.planning.model import INVALID_SCORE, Candidate, Decision, Plan, Problem, Task
from clashpilot.planning.simulate import SimResult, effective_cost, effective_duration

HOUR_S = 3600
FRESH_LIMIT_S = 7 * 24 * HOUR_S

LOW_FACTORS = frozenset({"fictional_pack", "estimated_resource", "stale_resource"})


def _decisions_by_task(trace: tuple[Decision, ...]) -> dict[str, Decision]:
    found: dict[str, Decision] = {}
    for decision in trace:
        name = decision.chosen.task_id
        if name is not None and name not in found:
            found[name] = decision
    return found


def _best_other(decision: Decision) -> Candidate | None:
    chosen = decision.chosen
    best: Candidate | None = None
    for candidate in decision.candidates:
        if (candidate.task_id, candidate.start) == (chosen.task_id, chosen.start):
            continue
        if candidate.score == INVALID_SCORE:
            continue
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def counterfactual(problem: Problem, decision: Decision, chosen_end: int) -> dict[str, Any] | None:
    """The best valid alternative of `decision`, or None if it has none."""
    best = _best_other(decision)
    if best is None:
        return None
    tasks = {task.id: task for task in problem.tasks}
    alternative = tasks.get(best.task_id) if best.task_id is not None else None
    if alternative is not None:
        finish_delta = best.start + effective_duration(problem, alternative, best.start)
        finish_delta -= chosen_end
    else:
        finish_delta = best.start - decision.chosen.start
    return {
        "alternative_task_id": best.task_id,
        "score_delta": decision.chosen.score - best.score,
        "finish_delta_s": finish_delta,
    }


def _reasons(
    task: Task, decision: Decision | None, alternative: dict[str, Any] | None
) -> list[Any]:
    ratio = task.value / max(1, task.duration_s) * HOUR_S
    if decision is None:
        return [entry("no_trace"), entry("value_per_hour", ratio)]
    valid = sum(1 for candidate in decision.candidates if candidate.score != INVALID_SCORE)
    reasons = [
        entry("value_per_hour", ratio),
        entry("options_compared", valid),
        entry("chosen_score", decision.chosen.score),
    ]
    if alternative is not None:
        reasons.append(entry("ahead_of_alternative", alternative["score_delta"]))
    return reasons


def explain_plan(problem: Problem, plan: Plan, sim: SimResult) -> dict[str, dict[str, Any]]:
    """Task id -> `{why, advantages, drawbacks, counterfactual}` for every planned assignment."""
    tasks = {task.id: task for task in problem.tasks}
    decisions = _decisions_by_task(plan.trace)
    unlocked: dict[str, int] = {}
    for task in problem.tasks:
        for name in task.requires:
            unlocked[name] = unlocked.get(name, 0) + 1
    calendar = problem.calendar
    explained: dict[str, dict[str, Any]] = {}
    for assignment in plan.assignments:
        task = tasks[assignment.task_id]
        end = sim.metrics.ends.get(task.id, assignment.end)
        decision = decisions.get(task.id)
        alternative = counterfactual(problem, decision, end) if decision is not None else None

        advantages: list[dict[str, Any]] = []
        if unlocked.get(task.id):
            advantages.append(entry("unlocks", unlocked[task.id]))
        drawbacks: list[dict[str, Any]] = []
        if calendar.is_sleep(end):
            drawbacks.append(entry("ends_in_sleep", end))
        else:
            advantages.append(entry("ends_awake", end))
        if any(low <= end < high for low, high in calendar.preferred):
            advantages.append(entry("ends_in_preferred", end))
        for name, amount in sorted(effective_cost(problem, task, assignment.start).items()):
            if amount > 0:
                value = {"resource": name, "amount": amount}
                drawbacks.append(entry("spends", value, amount=amount, label=resource_label(name)))

        explained[task.id] = {
            "why": {"reasons": _reasons(task, decision, alternative)},
            "advantages": advantages,
            "drawbacks": drawbacks,
            "counterfactual": alternative,
        }
    return explained


def consumed_resources(problem: Problem) -> set[str]:
    return {name for task in problem.tasks for name, amount in task.cost.items() if amount > 0}


def reliability(
    consumed: Set[str],
    resources: Mapping[Any, tuple[str, int]],
    *,
    fictional: bool,
    now: int,
) -> dict[str, Any]:
    """`{level, factors}` from the pack and the `(status, as_of)` of each consumed resource."""
    factors: list[dict[str, Any]] = []
    if fictional:
        factors.append(entry("fictional_pack", True))
    for name in sorted(consumed):
        if name not in resources:
            continue
        status, as_of = resources[name]
        label = resource_label(name)
        if status != "confirmed":
            factors.append(entry("estimated_resource", name, label=label))
        age = now - as_of
        if age > FRESH_LIMIT_S:
            factors.append(entry("stale_resource", name, label=label))
        elif age == FRESH_LIMIT_S:
            factors.append(entry("aging_resource", name, label=label))
    codes = {factor["code"] for factor in factors}
    if codes & LOW_FACTORS:
        level = "low"
    elif codes:
        level = "medium"
    else:
        level = "high"
    return {"level": level, "factors": factors}
