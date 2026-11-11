"""Engine `auto`: rollout and cpsat on the same problem, the best simulated score wins (SPEC 4).

The rollout plan is computed once and refined by the solver; the two plans are compared on the
score the simulator gives them. The solver wins ties when it actually produced its plan (no
fallback warning), because it carries a proof; the plan returned says who won and why.
"""

from __future__ import annotations

from clashpilot.planning.heuristics.rollout import RolloutPlanner, replay_trace
from clashpilot.planning.model import Plan, Problem
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.solver.cpsat import FALLBACK_WARNING, INVALID_PLAN_WARNING, CpSatPlanner

ENGINE = "auto"


class AutoPlanner:
    def __init__(self, cpsat: CpSatPlanner | None = None) -> None:
        self.cpsat = cpsat if cpsat is not None else CpSatPlanner()

    def solve(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None = None,
    ) -> Plan:
        rollout = RolloutPlanner().solve(problem, objective, config, reference)
        refined = self.cpsat.refine(problem, objective, config, reference, rollout)
        solver_plan = not {FALLBACK_WARNING, INVALID_PLAN_WARNING} & set(refined.warnings)
        rollout_score, cpsat_score = rollout.proof.objective, refined.proof.objective
        if solver_plan and cpsat_score >= rollout_score:
            winner, chosen = "cpsat", refined
            reason = (
                f"score simulé cpsat {cpsat_score:.6f} >= rollout {rollout_score:.6f}, "
                f"preuve {refined.proof.kind}"
            )
        elif solver_plan:
            winner, chosen = "rollout", rollout
            reason = f"score simulé rollout {rollout_score:.6f} > cpsat {cpsat_score:.6f}"
        else:
            winner, chosen = "rollout", rollout
            reason = f"solveur en repli ({', '.join(refined.warnings)}), plan rollout conservé"
        metadata = {**chosen.metadata, "auto_winner": winner, "auto_reason": reason}
        return Plan(
            assignments=chosen.assignments,
            engine=ENGINE,
            proof=chosen.proof,
            trace=replay_trace(problem, objective, config, chosen),
            warnings=chosen.warnings,
            metadata=metadata,
        )
