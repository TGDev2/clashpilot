"""CP-SAT refinement of the rollout plan on a near window (SPEC 4, ADR-4 to ADR-7).

The rollout plan is the starting point. Its tasks starting at or after the window end keep their
dates as fixed intervals; the others are modelled on a grid of `solver_grid_s` seconds over the
first `solver_window_days` days: one boolean per task and candidate slot (every `can_act` slot of
the grid, or, beyond `max_candidates` pairs, the slots of the starting plan and its neighbouring
start instants, which turns the proof into `feasible` without bound). The duration and the cost of
each pair come from `effective_duration` and `effective_cost`, so the modifiers hold without any
fallback; durations are rounded up to the grid for the lane intervals only, every other rule uses
the exact instants. Lanes: `add_cumulative` for identical builders, `add_no_overlap` for the lab
and the other unary lanes, the slots being assigned afterwards by interval colouring. Stocks follow
ADR-6: one linear ledger per resource and per step, with an explicit non-negative loss over the cap.
Forbidden ends, availability and frozen prerequisites simply remove candidate slots. The objective
is the score of SPEC section 4 linearised with exact rational coefficients when they fit, the ratios
taking the number of tasks of the starting plan as denominator. Every variable gets a hint from the
starting plan.

The returned plan is always the one the simulator validated (ADR-4): a solver plan the simulator
refuses, or a solve ending without a solution, gives back the rollout plan with a warning. The proof
says `optimal_window` only for an OPTIMAL status with both gap limits at zero on an unrestricted
model, and it only speaks for the discretised window model (ADR-5). Profile `repro` runs one worker
with a fixed seed and a deterministic time limit; `fast` runs in parallel under a wall-clock limit
and is announced as not reproducible (ADR-7).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ortools.sat.python import cp_model

from clashpilot.planning.heuristics.baseline import State
from clashpilot.planning.heuristics.rollout import RolloutPlanner, replay_trace
from clashpilot.planning.model import Assignment, Plan, Problem, Proof, Task
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.simulate import SCALE, effective_cost, effective_duration, simulate

ENGINE = "cpsat"
DAY_S = 86_400
DEFAULT_MAX_CANDIDATES = 200_000
FALLBACK_WARNING = "solver_fallback"
INVALID_PLAN_WARNING = "solver_invalid_plan"
NEIGHBOURS = 2  # start instants of the starting plan kept on each side in restricted mode
SCALE_LIMIT = 10**9  # largest integer scale of the objective
PROBING_SHARE = 20  # presolve probing gets at most 1/20 of the deterministic budget
_SOLUTION_STATUSES = (cp_model.OPTIMAL, cp_model.FEASIBLE)


@dataclass(frozen=True)
class _Candidate:
    task: Task
    slot: int  # grid index from `now`
    start: int  # epoch
    end: int  # epoch: start + effective duration
    size: int  # grid slots, rounded up
    cost: Mapping[str, int]


@dataclass(frozen=True)
class _Group:
    """The candidates of one task sharing one rounded size: one optional interval."""

    size: int
    start: Any  # IntVar, the slot
    present: Any  # BoolVar
    candidates: list[_Candidate]


@dataclass
class _Vars:
    x: dict[tuple[str, int], Any] = field(default_factory=dict)  # (task id, slot) -> bool
    present: dict[str, Any] = field(default_factory=dict)
    groups: dict[str, list[_Group]] = field(default_factory=dict)
    scale: int = 1
    hints: list[tuple[Any, int]] = field(default_factory=list)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _overlap(start: int, end: int, low: int, high: int) -> int:
    return max(0, min(end, high) - max(start, low))


def _text(window_s: int, grid_s: int, restricted: bool) -> str:
    window = f"{window_s // DAY_S} j" if window_s % DAY_S == 0 else f"{window_s / 3600:g} h"
    grid = f"{grid_s // 60} min" if grid_s % 60 == 0 else f"{grid_s} s"
    scope = f"fenêtre {window}, grille {grid}, objectif discrétisé"
    return f"{scope}, créneaux restreints" if restricted else scope


class CpSatPlanner:
    """Engine `cpsat`: rollout first, then the window refined by CP-SAT."""

    def __init__(self, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
        self.max_candidates = max_candidates

    def solve(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None = None,
    ) -> Plan:
        starting = RolloutPlanner().solve(problem, objective, config, reference)
        return self.refine(problem, objective, config, reference, starting)

    def refine(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None,
        starting: Plan,
    ) -> Plan:
        """Refine `starting` (a plan the simulator accepted) on the window."""
        builder = _Builder(problem, objective, config, reference, starting, self.max_candidates)
        model, variables = builder.build()
        solver = cp_model.CpSolver()
        _configure(solver, config)
        status = solver.solve(model)
        metadata = {
            "solver_status": str(solver.status_name(status)),
            "profile": config.profile,
            "reproducible": "true" if config.profile == "repro" else "false",
            "candidates": str(builder.count),
            "restricted": "true" if builder.restricted else "false",
        }
        if status not in _SOLUTION_STATUSES:
            return _fallback(
                problem, objective, config, reference, starting, FALLBACK_WARNING, metadata
            )
        assignments = builder.extract(solver, variables)
        draft = Plan(assignments=assignments, engine=ENGINE, proof=Proof("heuristic", 0.0))
        result = simulate(problem, draft, reference)
        if not result.valid:
            return _fallback(
                problem, objective, config, reference, starting, INVALID_PLAN_WARNING, metadata
            )
        objective_value = score(result, objective, problem)
        if builder.restricted:
            proof = Proof("feasible", objective_value, scope=builder.scope)
        elif status == cp_model.OPTIMAL:
            proof = Proof("optimal_window", objective_value, objective_value, 0.0, builder.scope)
        else:
            bound = solver.best_objective_bound / variables.scale
            gap = max(0.0, bound - objective_value)
            proof = Proof("feasible", objective_value, bound, gap, builder.scope)
        trace = replay_trace(problem, objective, config, draft)
        return Plan(
            assignments=assignments, engine=ENGINE, proof=proof, trace=trace, metadata=metadata
        )


def _configure(solver: cp_model.CpSolver, config: PlannerConfig) -> None:
    parameters = solver.parameters
    parameters.absolute_gap_limit = 0
    parameters.relative_gap_limit = 0
    parameters.random_seed = config.seed
    if config.profile == "repro":
        parameters.num_workers = 1
        parameters.max_deterministic_time = config.solver_max_deterministic_time
        # The LP relaxation costs wall time the deterministic clock barely counts: off here.
        parameters.linearization_level = 0
        # Presolve probing must leave the search its share of a small deterministic budget.
        share = config.solver_max_deterministic_time / PROBING_SHARE
        parameters.probing_deterministic_time_limit = min(
            parameters.probing_deterministic_time_limit, share
        )
        parameters.presolve_probing_deterministic_time_limit = min(
            parameters.presolve_probing_deterministic_time_limit, share
        )
    else:
        parameters.max_time_in_seconds = config.solver_max_time_s


def _fallback(
    problem: Problem,
    objective: Objective,
    config: PlannerConfig,
    reference: Plan | None,
    starting: Plan,
    warning: str,
    metadata: Mapping[str, str],
) -> Plan:
    """The starting rollout plan, re-judged, with the warning that explains the fallback."""
    draft = Plan(assignments=starting.assignments, engine=ENGINE, proof=Proof("heuristic", 0.0))
    result = simulate(problem, draft, reference)
    assignments = starting.assignments if result.valid else ()
    if not result.valid:
        result = simulate(problem, Plan((), ENGINE, Proof("heuristic", 0.0)), reference)
    proof = Proof("heuristic", score(result, objective, problem))
    trace = replay_trace(problem, objective, config, Plan(assignments, ENGINE, proof))
    return Plan(
        assignments=assignments,
        engine=ENGINE,
        proof=proof,
        trace=trace,
        warnings=(warning, *starting.warnings),
        metadata=dict(metadata),
    )


class _Builder:
    """Builds the window model of one problem and reads its solution back."""

    def __init__(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None,
        starting: Plan,
        max_candidates: int,
    ) -> None:
        self.problem = problem
        self.objective = objective
        self.config = config
        self.reference = reference
        self.starting = starting
        self.now = problem.now
        self.grid = max(1, config.solver_grid_s)
        span = max(
            0, min(problem.horizon_end, self.now + config.solver_window_days * DAY_S) - self.now
        )
        self.n_slots = span // self.grid
        self.grid_end = self.now + self.n_slots * self.grid
        self.units = 3600 // math.gcd(3600, self.grid)  # stock units per resource unit
        self.state = State(problem)  # the end rules of the policy
        self.tasks = {task.id: task for task in problem.tasks}
        self.frozen_end = {job.task_id: job.end for job in problem.frozen}
        known = {a.task_id: a for a in starting.assignments if a.task_id in self.tasks}
        self.fixed: dict[str, Assignment] = {
            name: a for name, a in known.items() if a.start >= self.grid_end
        }
        self.hinted_start: dict[str, int] = {
            name: a.start for name, a in known.items() if name not in self.fixed
        }
        self.modelled = [
            task
            for task in problem.tasks
            if task.id not in self.fixed and problem.lanes.get(task.lane, 0) > 0
        ]
        self.modelled_ids = {task.id for task in self.modelled}
        self.spill_vars: dict[str, list[Any]] = {}
        self.reference_starts = (
            {a.task_id: a.start for a in reference.assignments} if reference is not None else {}
        )
        self.candidates: dict[str, list[_Candidate]] = {}
        self.restricted = False
        self.count = 0
        self.scope = ""
        self.max_candidates = max_candidates

    # ----------------------------------------------------------------- candidates

    def slot_start(self, slot: int) -> int:
        return self.now + slot * self.grid

    def slot_ceil(self, t: int) -> int:
        return _ceil_div(t - self.now, self.grid)

    def slot_floor(self, t: int) -> int:
        return (t - self.now) // self.grid

    def _collect(self) -> None:
        calendar = self.problem.calendar
        awake = [k for k in range(self.n_slots) if calendar.can_act(self.slot_start(k))]
        self.restricted = len(self.modelled) * len(awake) > self.max_candidates
        self.scope = _text(self.grid_end - self.now, self.grid, self.restricted)
        slots_of = self._restricted_slots(awake) if self.restricted else None
        self._slots_of = {
            task.id: (awake if slots_of is None else slots_of.get(task.id, []))
            for task in self.modelled
        }
        self._visiting: set[str] = set()
        for task in self.modelled:
            self._candidates_of(task)
        self.count = sum(len(found) for found in self.candidates.values())

    def _candidates_of(self, task: Task) -> list[_Candidate]:
        """The candidates of `task`, from its lower bound on (funds, prerequisites), memoised."""
        found = self.candidates.get(task.id)
        if found is not None:
            return found
        if task.id in self._visiting:  # a cycle: the compiler never builds one
            return []
        self._visiting.add(task.id)
        lower = self._lower_bound(task)
        found = []
        if lower is not None:
            found = [
                candidate
                for k in self._slots_of.get(task.id, [])
                if self.slot_start(k) >= lower
                and (candidate := self._candidate(task, k)) is not None
            ]
        self.candidates[task.id] = found
        self._visiting.discard(task.id)
        return found

    def _lower_bound(self, task: Task) -> int | None:
        """The first instant `task` could start whatever the plan; None when it never can."""
        lower = self.now
        if not self.problem.modifiers:
            funded = self.state.ledger.fundable_at(task.cost, self.now)
            if funded is None:
                return None
            lower = max(lower, funded)
        for required in task.requires:
            if required in self.frozen_end:
                lower = max(lower, self.frozen_end[required])
            elif required in self.modelled_ids:
                ends = [c.end for c in self._candidates_of(self.tasks[required])]
                if not ends:
                    return None
                lower = max(lower, min(ends))
            else:
                return None
        return lower

    def _restricted_slots(self, awake: Sequence[int]) -> dict[str, list[int]]:
        """Each task of the starting plan: its own start slot and the neighbouring instants."""
        awake_set = set(awake)
        instants = sorted({start for start in self.hinted_start.values()})
        slots_of: dict[str, list[int]] = {}
        for name, start in self.hinted_start.items():
            index = instants.index(start)
            near = instants[max(0, index - NEIGHBOURS) : index + NEIGHBOURS + 1]
            slots = sorted({self.slot_ceil(t) for t in near} | {self.slot_floor(start)})
            slots_of[name] = [k for k in slots if 0 <= k < self.n_slots and k in awake_set]
        return slots_of

    def _candidate(self, task: Task, slot: int) -> _Candidate | None:
        start = self.slot_start(slot)
        duration = effective_duration(self.problem, task, start)
        end = start + duration
        if not self.state.end_allowed(end):
            return None
        return _Candidate(
            task=task,
            slot=slot,
            start=start,
            end=end,
            size=max(1, _ceil_div(duration, self.grid)),
            cost=effective_cost(self.problem, task, start),
        )

    # ----------------------------------------------------------------- model

    def build(self) -> tuple[cp_model.CpModel, _Vars]:
        self._collect()
        model = cp_model.CpModel()
        variables = _Vars()
        hinted = self._hinted_candidates()
        for task in self.modelled:
            found = self.candidates[task.id]
            if not found:
                continue
            x_vars = []
            for candidate in found:
                x = model.new_bool_var(f"x[{task.id},{candidate.slot}]")
                variables.x[task.id, candidate.slot] = x
                variables.hints.append((x, int(hinted.get(task.id) is candidate)))
                x_vars.append(x)
            present = model.new_bool_var(f"present[{task.id}]")
            model.add(sum(x_vars) == present)
            variables.hints.append((present, int(task.id in hinted)))
            variables.present[task.id] = present
        self._lanes(model, variables, hinted)
        self._precedence(model, variables)
        self._stocks(model, variables, hinted)
        logins = self._logins(model, variables, hinted)
        self._objective(model, variables, hinted, logins)
        for var, value in variables.hints:
            model.add_hint(var, value)
        return model, variables

    def _hinted_candidates(self) -> dict[str, _Candidate]:
        hinted: dict[str, _Candidate] = {}
        for name, start in self.hinted_start.items():
            if (start - self.now) % self.grid:
                continue
            slot = self.slot_floor(start)
            for candidate in self.candidates.get(name, []):
                if candidate.slot == slot:
                    hinted[name] = candidate
        return hinted

    def _lanes(
        self, model: cp_model.CpModel, variables: _Vars, hinted: Mapping[str, _Candidate]
    ) -> None:
        intervals: dict[str, list[Any]] = {}
        for task in self.modelled:
            found = self.candidates.get(task.id, [])
            if not found:
                continue
            groups: dict[int, list[_Candidate]] = {}
            for candidate in found:
                groups.setdefault(candidate.size, []).append(candidate)
            for size, group in sorted(groups.items()):
                xs = [variables.x[task.id, c.slot] for c in group]
                if len(groups) == 1:
                    present = variables.present[task.id]
                else:
                    present = model.new_bool_var(f"present[{task.id},{size}]")
                    model.add(sum(xs) == present)
                    hint = hinted.get(task.id)
                    variables.hints.append((present, int(any(c is hint for c in group))))
                start = model.new_int_var(0, max(0, self.n_slots - 1), f"start[{task.id},{size}]")
                model.add(start == sum(c.slot * x for c, x in zip(group, xs, strict=True)))
                chosen = hinted.get(task.id)
                in_group = chosen is not None and any(c is chosen for c in group)
                variables.hints.append((start, chosen.slot if in_group and chosen else 0))
                interval = model.new_optional_fixed_size_interval_var(
                    start, size, present, f"interval[{task.id},{size}]"
                )
                intervals.setdefault(task.lane, []).append(interval)
                variables.groups.setdefault(task.id, []).append(
                    _Group(size=size, start=start, present=present, candidates=group)
                )
        for lane, lane_intervals in intervals.items():
            for low, high in self._busy(lane):
                lane_intervals.append(
                    model.new_fixed_size_interval_var(low, high - low, f"busy[{lane},{low}]")
                )
            capacity = self.problem.lanes[lane]
            if capacity == 1:
                model.add_no_overlap(lane_intervals)
            else:
                model.add_cumulative(lane_intervals, [1] * len(lane_intervals), capacity)

    def _busy(self, lane: str) -> list[tuple[int, int]]:
        """Grid spans (rounded outward) that frozen jobs and fixed tasks occupy on `lane`."""
        spans: list[tuple[int, int]] = []
        for job in self.problem.frozen:
            if job.lane == lane and not job.extra and job.end > self.now:
                spans.append((max(0, self.slot_floor(job.start)), self.slot_ceil(job.end)))
        for assignment in self.fixed.values():
            if assignment.lane == lane:
                spans.append((self.slot_floor(assignment.start), self.slot_ceil(assignment.end)))
        return [(start, end) for start, end in spans if end > start]

    def _precedence(self, model: cp_model.CpModel, variables: _Vars) -> None:
        """A task starts once its modelled prerequisites ended.

        Between two modelled tasks the rule reads on the interval starts, with the rounded size of
        the prerequisite (exact when durations are multiples of the grid, conservative otherwise).
        A task fixed beyond the window needs its prerequisite planned and ended, to the second.
        """
        for task in self.modelled:
            groups = variables.groups.get(task.id, [])
            if not groups:
                continue
            for required in task.requires:
                if required not in self.modelled_ids:
                    continue  # frozen jobs are read when the candidates are collected
                required_groups = variables.groups.get(required, [])
                if not required_groups:
                    model.add(variables.present[task.id] == 0)
                    continue
                model.add(variables.present[task.id] <= variables.present[required])
                for group in groups:
                    for before in required_groups:
                        model.add(group.start >= before.start + before.size).only_enforce_if(
                            [group.present, before.present]
                        )
        for assignment in self.fixed.values():
            task = self.tasks[assignment.task_id]
            for required in task.requires:
                if required in self.candidates:
                    early = [
                        variables.x[required, c.slot]
                        for c in self.candidates[required]
                        if c.end <= assignment.start
                    ]
                    model.add(sum(early) == 1)

    def _stocks(
        self, model: cp_model.CpModel, variables: _Vars, hinted: Mapping[str, _Candidate]
    ) -> None:
        """ADR-6: per resource and per step, L[k+1] = L[k] + income - loss - spending."""
        if self.n_slots == 0:
            return
        resources = self.problem.resources
        names = sorted(
            {name for found in self.candidates.values() for c in found for name in c.cost}
        )
        fixed_costs = {
            name
            for assignment in self.fixed.values()
            for name in self.tasks[assignment.task_id].cost
        }
        needed_at_end = self._stock_at_window_end() if fixed_costs else {}
        units, grid = self.units, self.grid
        for name in names:
            income = resources.income_per_hour.get(name, 0) * grid // math.gcd(3600, grid)
            cap = resources.cap.get(name)
            cap_units = cap * units if cap is not None else None
            initial = resources.initial.get(name, 0) * units
            if cap_units is not None:
                initial = min(initial, cap_units)
            upper = cap_units if cap_units is not None else initial + income * self.n_slots
            spending: dict[int, list[tuple[int, Any]]] = {}
            hinted_spend: dict[int, int] = {}
            for found in self.candidates.values():
                for candidate in found:
                    amount = candidate.cost.get(name, 0)
                    if amount:
                        spending.setdefault(candidate.slot, []).append(
                            (amount * units, variables.x[candidate.task.id, candidate.slot])
                        )
            for candidate in hinted.values():
                amount = candidate.cost.get(name, 0) * units
                hinted_spend[candidate.slot] = hinted_spend.get(candidate.slot, 0) + amount
            level: Any = None
            hint_level = initial
            spill_vars: list[Any] = []
            for k in range(self.n_slots + 1):
                if k == 0:
                    before: Any = initial
                    hint_before = initial
                else:
                    before = level + income
                    hint_before = hint_level + income
                    if cap_units is not None:
                        spill = model.new_int_var(0, max(0, income), f"spill[{name},{k - 1}]")
                        model.add(before - spill <= cap_units)
                        hint_spill = max(0, hint_before - cap_units)
                        variables.hints.append((spill, hint_spill))
                        spill_vars.append(spill)
                        before = before - spill
                        hint_before -= hint_spill
                if k == self.n_slots:
                    if name in needed_at_end:
                        model.add(before >= needed_at_end[name])
                    break
                spent = sum(amount * x for amount, x in spending.get(k, []))
                level = model.new_int_var(0, upper, f"stock[{name},{k}]")
                model.add(level == before - spent)
                hint_level = max(0, hint_before - hinted_spend.get(k, 0))
                variables.hints.append((level, hint_level))
            self.spill_vars[name] = spill_vars

    def _stock_at_window_end(self) -> dict[str, int]:
        """Stock (in model units) the starting plan holds when the window ends, before payments."""
        result = simulate(self.problem, self.starting, self.reference)
        resources = self.problem.resources
        last_t, last_stock = result.stock_timeline[0]
        for t, stock in result.stock_timeline:
            if t < self.grid_end:
                last_t, last_stock = t, stock
        divisor = SCALE // self.units
        needed: dict[str, int] = {}
        for name, level in last_stock.items():
            rate = resources.income_per_hour.get(name, 0)
            amount = level + rate * (self.grid_end - last_t)
            cap = resources.cap.get(name)
            if cap is not None:
                amount = min(amount, cap * SCALE)
            needed[name] = _ceil_div(amount, divisor)
        return needed

    def _logins(
        self, model: cp_model.CpModel, variables: _Vars, hinted: Mapping[str, _Candidate]
    ) -> list[Any]:
        """One boolean per slot where something starts: a conservative count of the logins."""
        policy = self.problem.policy
        if policy.max_logins_per_day is None and not self.objective.logins:
            return []
        slots: dict[int, list[Any]] = {}
        for found in self.candidates.values():
            for candidate in found:
                slots.setdefault(candidate.slot, []).append(
                    variables.x[candidate.task.id, candidate.slot]
                )
        hinted_slots = {candidate.slot for candidate in hinted.values()}
        logins: dict[int, Any] = {}
        for k, xs in sorted(slots.items()):
            y = model.new_bool_var(f"login[{k}]")
            for x in xs:
                model.add_implication(x, y)
            variables.hints.append((y, int(k in hinted_slots)))
            logins[k] = y
        limit = policy.max_logins_per_day
        if limit is not None:
            calendar = self.problem.calendar
            by_day: dict[Any, list[Any]] = {}
            for k, y in logins.items():
                by_day.setdefault(calendar.local_day(self.slot_start(k)), []).append(y)
            fixed_by_day: dict[Any, int] = {}
            previous: int | None = None
            for start in sorted(a.start for a in self.fixed.values()):
                if previous is None or start - previous > policy.login_merge_s:
                    day = calendar.local_day(start)
                    fixed_by_day[day] = fixed_by_day.get(day, 0) + 1
                previous = start
            for day, ys in by_day.items():
                model.add(sum(ys) <= max(0, limit - fixed_by_day.get(day, 0)))
        return list(logins.values())

    def _objective(
        self,
        model: cp_model.CpModel,
        variables: _Vars,
        hinted: Mapping[str, _Candidate],
        logins: Sequence[Any],
    ) -> None:
        problem, weights = self.problem, self.objective
        now, horizon_end = problem.now, problem.horizon_end
        horizon = max(1, horizon_end - now)
        total_value = sum(task.value for task in problem.tasks)
        capacity = sum(problem.lanes.values()) * horizon
        planned = max(1, len(self.starting.assignments))
        calendar = problem.calendar
        preferred = calendar.preferred
        in_reference = sum(1 for task in problem.tasks if task.id in self.reference_starts)
        n_ref = max(1, in_reference)
        terms: list[tuple[Fraction, Any]] = []
        constant = Fraction(0)

        def contribution(name: str, value: float, start: int, end: int) -> Fraction:
            total = Fraction(0)
            if weights.value and total_value > 0:
                total += Fraction(weights.value * value * max(0, horizon - (end - now))) / (
                    horizon * Fraction(total_value)
                )
            if weights.idle and capacity > 0:
                total += Fraction(weights.idle * _overlap(start, end, now, horizon_end)) / capacity
            if weights.night and calendar.is_sleep(end):
                total -= Fraction(weights.night) / planned
            if weights.preferred and any(low <= end < high for low, high in preferred):
                total += Fraction(weights.preferred) / planned
            if weights.perturbation and name in self.reference_starts:
                total -= Fraction(
                    weights.perturbation * abs(start - self.reference_starts[name])
                ) / (n_ref * horizon)
            return total

        for found in self.candidates.values():
            for candidate in found:
                coefficient = contribution(
                    candidate.task.id, candidate.task.value, candidate.start, candidate.end
                )
                if coefficient:
                    terms.append((coefficient, variables.x[candidate.task.id, candidate.slot]))
        for assignment in self.fixed.values():
            task = self.tasks[assignment.task_id]
            constant += contribution(task.id, task.value, assignment.start, assignment.end)
        if weights.idle and capacity > 0:
            constant -= Fraction(weights.idle)
            busy = sum(
                _overlap(job.start, job.end, now, horizon_end)
                for job in problem.frozen
                if not job.extra
            )
            constant += Fraction(weights.idle * busy) / capacity
        if weights.spill:
            income_total = sum(
                Fraction(rate * horizon, 3600)
                for rate in problem.resources.income_per_hour.values()
            )
            per_unit = Fraction(weights.spill) / (self.units * max(Fraction(1), income_total))
            for spills in self.spill_vars.values():
                for spill in spills:
                    terms.append((-per_unit, spill))
        if weights.logins:
            per_login = Fraction(weights.logins * 3600) / horizon
            for y in logins:
                terms.append((-per_login, y))
        constant += self._goal_terms(model, variables, hinted, terms, horizon)
        terms = [(coefficient.limit_denominator(10**6), var) for coefficient, var in terms]
        constant = constant.limit_denominator(10**6)

        denominators = {term.denominator for term, _ in terms} | {constant.denominator}
        scale = 1
        for denominator in denominators:
            scale = scale * denominator // math.gcd(scale, denominator)
            if scale > SCALE_LIMIT:
                scale = SCALE_LIMIT
                break
        variables.scale = scale
        expression = sum(round(coefficient * scale) * var for coefficient, var in terms)
        model.maximize(expression + round(constant * scale))

    def _goal_terms(
        self,
        model: cp_model.CpModel,
        variables: _Vars,
        hinted: Mapping[str, _Candidate],
        terms: list[tuple[Fraction, Any]],
        horizon: int,
    ) -> Fraction:
        """Append the goal terms (SPEC goal_term, 2.0 when out of reach); returns the constant."""
        goal, weight = self.problem.goal, self.objective.goal
        if not weight or goal is None or not goal.task_ids:
            return Fraction(0)
        now = self.problem.now
        known_end = 0
        modelled: list[str] = []
        for name in sorted(goal.task_ids):
            if name in self.frozen_end:
                end = self.frozen_end[name]
            elif name in self.fixed:
                end = self.fixed[name].end
            elif self.candidates.get(name):
                modelled.append(name)
                continue
            else:
                return Fraction(-2 * weight)
            if end > self.problem.horizon_end:
                return Fraction(-2 * weight)
            known_end = max(known_end, end - now)
        last = model.new_int_var(known_end, 2 * horizon, "goal_end")
        any_missed = model.new_bool_var("goal_missed")
        hint_last = known_end
        for name in modelled:
            found = self.candidates[name]
            model.add(last >= sum((c.end - now) * variables.x[name, c.slot] for c in found))
            model.add_implication(variables.present[name].negated(), any_missed)
            hint = hinted.get(name)
            if hint is not None:
                hint_last = max(hint_last, hint.end - now)
        variables.hints.append((last, min(hint_last, 2 * horizon)))
        variables.hints.append((any_missed, int(any(name not in hinted for name in modelled))))
        terms.append((Fraction(-weight) / horizon, last))
        terms.append((Fraction(-2 * weight), any_missed))
        return Fraction(0)

    # ----------------------------------------------------------------- solution

    def extract(self, solver: cp_model.CpSolver, variables: _Vars) -> tuple[Assignment, ...]:
        """The assignments of the solution, slots assigned by colouring the real intervals."""
        planned: list[tuple[int, int, str, str]] = []  # (start, end, task id, lane)
        for task in self.modelled:
            for candidate in self.candidates.get(task.id, []):
                if solver.boolean_value(variables.x[task.id, candidate.slot]):
                    planned.append((candidate.start, candidate.end, task.id, task.lane))
                    break
        for assignment in self.fixed.values():
            planned.append((assignment.start, assignment.end, assignment.task_id, assignment.lane))
        occupied: dict[str, list[tuple[int, int]]] = {}  # lane -> [(end, slot)]
        for job in self.problem.frozen:
            if not job.extra and job.end > self.now:
                occupied.setdefault(job.lane, []).append((job.end, job.lane_slot))
        assignments: list[Assignment] = []
        for start, end, name, lane in sorted(planned):
            active = [(e, slot) for e, slot in occupied.get(lane, []) if e > start]
            taken = {slot for _, slot in active}
            free = [slot for slot in range(self.problem.lanes.get(lane, 0)) if slot not in taken]
            slot = free[0] if free else 0
            active.append((end, slot))
            occupied[lane] = active
            assignments.append(
                Assignment(task_id=name, lane=lane, lane_slot=slot, start=start, end=end)
            )
        return tuple(assignments)
