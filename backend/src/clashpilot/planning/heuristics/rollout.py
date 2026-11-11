"""The rollout planner: every decision is judged by the simulated consequences (SPEC 4, ADR-4/5).

The plan is built from decision point to decision point (see `baseline` for the points). At each
point the candidates are the ready tasks (prerequisites done, free slot, affordable) and, when a
later point exists, waiting until it. A task whose immediate start breaks a rule yields two
candidates: the immediate start, recorded with `INVALID_SCORE` without evaluation and never
chosen, and its next valid start, scored normally. Scoring a candidate means applying it to a copy
of the state, completing the plan with the base policy and taking `score(simulate(...))`.

The evaluations of one run never exceed `config.max_rollouts`: at each point only the K best ready
tasks by ratio are scored, K being the remaining budget spread over the pending tasks; once the
budget is spent, the base policy finishes the plan without further decisions. Every scored
candidate is recorded in the trace, and `chosen` is the best score, ties broken by task id, the
wait candidate last. All sorts are stable: two identical runs give the same plan and trace.

`replay_trace` rebuilds a trace for a plan another engine produced (E10): one decision per distinct
start instant of the plan, the first `MAX_REPLAY_DECISIONS` at most, whose `chosen` is the plan's
own assignment at that instant, scored like any candidate, next to the other ready tasks.
"""

from __future__ import annotations

from clashpilot.planning.heuristics.baseline import State, finish
from clashpilot.planning.model import (
    INVALID_SCORE,
    Candidate,
    Decision,
    Plan,
    Problem,
    Proof,
    Task,
)
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.simulate import simulate

ENGINE = "rollout"
MAX_REPLAY_DECISIONS = 20
_EMPTY_PROOF = Proof(kind="heuristic", objective=0.0)


def _choice_key(candidate: Candidate) -> tuple[float, bool, str]:
    return (-candidate.score, candidate.task_id is None, candidate.task_id or "")


class RolloutPlanner:
    def solve(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None = None,
    ) -> Plan:
        run = _Run(problem, objective, config, reference)
        run.plan()
        return finish(run.state, ENGINE, objective, reference, trace=tuple(run.trace))


class _Run:
    def __init__(
        self, problem: Problem, objective: Objective, config: PlannerConfig, reference: Plan | None
    ) -> None:
        self.problem = problem
        self.objective = objective
        self.reference = reference
        self.budget = max(0, config.max_rollouts)
        self.state = State(problem)
        self.tasks: dict[str, Task] = {task.id: task for task in problem.tasks}
        self.trace: list[Decision] = []

    # ----------------------------------------------------------------- evaluation

    def evaluate(self, candidate: Candidate) -> float:
        """Score of the plan the base policy completes after `candidate`."""
        trial = self.state.clone()
        self.apply(trial, candidate)
        trial.complete()
        plan = Plan(assignments=tuple(trial.assignments), engine=ENGINE, proof=_EMPTY_PROOF)
        self.budget -= 1
        return score(simulate(self.problem, plan, self.reference), self.objective, self.problem)

    def apply(self, state: State, candidate: Candidate) -> None:
        if candidate.task_id is None:
            state.advance(candidate.start)
            return
        task = self.tasks[candidate.task_id]
        state.start(task, candidate.start)

    # ----------------------------------------------------------------- decisions

    def plan(self) -> None:
        state = self.state
        while state.t < self.problem.horizon_end:
            if self.budget <= 0:
                state.complete()
                return
            following = state.next_point(state.t)
            ready = list(state.ready(state.t)) if self.problem.calendar.can_act(state.t) else []
            if not ready:
                if following is None:
                    return
                state.advance(following)
                continue
            candidates = self.candidates(ready, following)
            scored = [c for c in candidates if c.score != INVALID_SCORE]
            if not scored:
                if following is None:
                    return
                state.advance(following)
                continue
            chosen = sorted(scored, key=_choice_key)[0]
            self.trace.append(Decision(at=state.t, candidates=tuple(candidates), chosen=chosen))
            self.apply(state, chosen)

    def candidates(self, ready: list[Task], following: int | None) -> list[Candidate]:
        """The scored candidates of this point, within the budget, plus the refused starts."""
        state = self.state
        now = state.t
        limit = max(1, self.budget // max(1, len(state.pending)))
        candidates: list[Candidate] = []
        for task in ready[:limit]:
            if self.budget <= 0:
                break
            if state.start_allowed(task, now):
                immediate = Candidate(task_id=task.id, start=now, score=0.0)
                candidates.append(self.scored(immediate))
                continue
            candidates.append(Candidate(task_id=task.id, start=now, score=INVALID_SCORE))
            later = state.next_valid_start(task, now)
            if later is not None:
                candidates.append(self.scored(Candidate(task_id=task.id, start=later, score=0.0)))
        if following is not None and self.budget > 0:
            candidates.append(self.scored(Candidate(task_id=None, start=following, score=0.0)))
        return candidates

    def scored(self, candidate: Candidate) -> Candidate:
        return Candidate(
            task_id=candidate.task_id, start=candidate.start, score=self.evaluate(candidate)
        )


def replay_trace(
    problem: Problem, objective: Objective, config: PlannerConfig, plan: Plan
) -> tuple[Decision, ...]:
    """The trace of `plan` as if it had been decided start by start (SPEC section 4, Trace).

    At each of the first `MAX_REPLAY_DECISIONS` distinct start instants, the plan's own task (the
    first by id when several start together) is scored by completing it with the base policy, next
    to at most `config.max_rollouts // MAX_REPLAY_DECISIONS` other ready tasks; `chosen` is the
    plan's task whatever the scores. The state then follows the plan, never the policy.
    """
    tasks = {task.id: task for task in problem.tasks}
    run = _Run(problem, objective, config, None)
    state = run.state
    pending = {task.id for task in state.pending}
    by_start: dict[int, list[Task]] = {}
    for assignment in sorted(plan.assignments, key=lambda a: (a.start, a.task_id)):
        if assignment.task_id in pending and assignment.start >= problem.now:
            by_start.setdefault(assignment.start, []).append(tasks[assignment.task_id])
    others_limit = max(0, config.max_rollouts // MAX_REPLAY_DECISIONS - 1)
    decisions: list[Decision] = []
    for at in sorted(by_start):
        group = by_start[at]
        if len(decisions) < MAX_REPLAY_DECISIONS:
            state.advance(at)
            chosen_task = group[0]
            ready = (
                [
                    t
                    for t in state.ready(at)
                    if t.id != chosen_task.id and state.start_allowed(t, at)
                ]
                if problem.calendar.can_act(at)
                else []
            )
            candidates = [
                run.scored(Candidate(task_id=t.id, start=at, score=0.0))
                for t in (chosen_task, *ready[:others_limit])
                if state.free_slot(t.lane, at) is not None and state.prerequisites_done(t, at)
            ]
            if candidates and candidates[0].task_id == chosen_task.id:
                decisions.append(
                    Decision(at=at, candidates=tuple(candidates), chosen=candidates[0])
                )
        for task in group:
            if task in state.pending and state.free_slot(task.lane, at) is not None:
                state.start(task, at)
    return tuple(decisions)
