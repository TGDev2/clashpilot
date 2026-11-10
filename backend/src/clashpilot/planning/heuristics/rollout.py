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
