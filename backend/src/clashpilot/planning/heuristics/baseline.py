"""The baseline planner and the planning state shared with the rollout planner (SPEC 4, ADR-5).

Base policy (the "ratio rule"), used by `baseline` for the whole plan and by `rollout` to complete
each candidate:

1. At a decision point `t` where the player can act, scan the pending tasks by decreasing
   `value / max(1, duration_s)` (ties by id). Start the first one that is ready (prerequisites done,
   a free slot on its lane, affordable) and whose start at `t` breaks no rule of the policy
   (end in sleep under `strict`, wait after the end under `tolerance`, daily login limit). Repeat
   at the same instant while a task can start.
2. When nothing can start, advance to the next decision point: a lane slot released, the end of an
   extra frozen job, the next instant where the player can act again, the instant a task becomes
   affordable, a modifier bound, or the next local day when logins are limited. Every wait lands
   strictly later; planning stops when no point remains before `horizon_end`.

The state mirrors the rules of `clashpilot.planning.simulate`, which stays the only judge: the plan
returned is the one the simulator validated. Pure module: no I/O, no clock.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from clashpilot.planning.model import Assignment, Decision, Plan, Problem, Proof, Task
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.registry import PlannerConfig
from clashpilot.planning.simulate import SCALE, effective_cost, effective_duration, simulate

ENGINE = "baseline"
INVALID_PLAN_WARNING = "heuristic_invalid_plan"


def ratio(task: Task) -> float:
    """Value per second of the task; a zero duration counts as one second."""
    return task.value / max(1, task.duration_s)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


class Ledger:
    """Stocks in units x 3600, with the caps and the hourly rates of the problem."""

    __slots__ = ("caps", "clock", "rates", "stock")

    def __init__(self, problem: Problem) -> None:
        model = problem.resources
        names = sorted(
            set(model.initial)
            | set(model.cap)
            | set(model.income_per_hour)
            | {name for task in problem.tasks for name in task.cost}
        )
        self.caps: dict[str, int | None] = {
            name: model.cap[name] * SCALE if name in model.cap else None for name in names
        }
        self.stock: dict[str, int] = {}
        for name in names:
            level = model.initial.get(name, 0) * SCALE
            limit = self.caps[name]
            self.stock[name] = min(level, limit) if limit is not None else level
        self.rates: dict[str, int] = {name: model.income_per_hour.get(name, 0) for name in names}
        self.clock = problem.now

    def clone(self) -> Ledger:
        other = object.__new__(Ledger)
        other.caps = self.caps
        other.rates = self.rates
        other.stock = dict(self.stock)
        other.clock = self.clock
        return other

    def level_at(self, name: str, t: int) -> int:
        """Stock of `name` at `t >= clock` before any payment at `t`."""
        level = self.stock.get(name, 0) + self.rates.get(name, 0) * max(0, t - self.clock)
        limit = self.caps.get(name)
        return min(level, limit) if limit is not None else level

    def advance(self, to: int) -> None:
        if to <= self.clock:
            return
        for name in self.stock:
            self.stock[name] = self.level_at(name, to)
        self.clock = to

    def affordable(self, cost: Mapping[str, int], t: int) -> bool:
        return all(self.level_at(name, t) >= amount * SCALE for name, amount in cost.items())

    def fundable_at(self, cost: Mapping[str, int], t: int) -> int | None:
        """First instant at or after `t` where `cost` is affordable; None if it never is."""
        when = t
        for name, amount in cost.items():
            needed = amount * SCALE
            limit = self.caps.get(name)
            if limit is not None and limit < needed:
                return None
            level = self.level_at(name, t)
            if level >= needed:
                continue
            rate = self.rates.get(name, 0)
            if rate <= 0:
                return None
            when = max(when, t + _ceil_div(needed - level, rate))
        return when

    def pay(self, cost: Mapping[str, int], t: int) -> None:
        self.advance(t)
        for name, amount in cost.items():
            self.stock[name] = max(0, self.stock.get(name, 0) - amount * SCALE)


class State:
    """What has been planned so far, from `problem.now` to the current decision point `t`."""

    __slots__ = (
        "assignments",
        "bounds",
        "done_at",
        "events",
        "free_at",
        "last_start",
        "ledger",
        "logins_by_day",
        "pending",
        "problem",
        "t",
        "zone",
    )

    def __init__(self, problem: Problem) -> None:
        self.problem = problem
        self.t = problem.now
        self.free_at: dict[str, list[int]] = {
            lane: [problem.now] * capacity for lane, capacity in problem.lanes.items()
        }
        self.done_at: dict[str, int] = {}
        events: set[int] = set()
        for job in problem.frozen:
            self.done_at[job.task_id] = max(self.done_at.get(job.task_id, job.end), job.end)
            slots = self.free_at.get(job.lane)
            if job.extra:
                events.add(job.end)
            elif slots is not None and 0 <= job.lane_slot < len(slots):
                slots[job.lane_slot] = max(slots[job.lane_slot], job.end)
        for modifier in problem.modifiers:
            events.update((modifier.start, modifier.end))
        self.events = sorted(events)
        calendar = problem.calendar
        spans = (*calendar.sleep, *calendar.work, *calendar.absence, *calendar.available)
        self.bounds = sorted({bound for span in spans for bound in span})
        self.pending: list[Task] = sorted(
            (task for task in problem.tasks if problem.lanes.get(task.lane, 0) > 0),
            key=lambda task: (-ratio(task), task.id),
        )
        self.ledger = Ledger(problem)
        self.last_start: int | None = None
        self.logins_by_day: Counter[date] = Counter()
        self.assignments: list[Assignment] = []
        self.zone = ZoneInfo(calendar.tz)

    def clone(self) -> State:
        other = object.__new__(State)
        other.problem = self.problem
        other.t = self.t
        other.free_at = {lane: list(slots) for lane, slots in self.free_at.items()}
        other.done_at = dict(self.done_at)
        other.events = self.events
        other.bounds = self.bounds
        other.pending = list(self.pending)
        other.ledger = self.ledger.clone()
        other.last_start = self.last_start
        other.logins_by_day = Counter(self.logins_by_day)
        other.assignments = list(self.assignments)
        other.zone = self.zone
        return other

    # ----------------------------------------------------------------- calendar rules

    def next_can_act(self, t: int) -> int:
        """First instant at or after `t` where the player can act (as the simulator sees it)."""
        calendar = self.problem.calendar
        if calendar.can_act(t):
            return t
        for bound in self.bounds[bisect_right(self.bounds, t) :]:
            if calendar.can_act(bound):
                return bound
        return t

    def end_allowed(self, end: int) -> bool:
        policy = self.problem.policy
        if policy.mode == "strict":
            return not self.problem.calendar.is_sleep(end)
        if policy.mode == "tolerance":
            return self.next_can_act(end) - end <= policy.tolerance_s
        return True

    def login_allowed(self, start: int) -> bool:
        policy = self.problem.policy
        limit = policy.max_logins_per_day
        if limit is None:
            return True
        if self.last_start is not None and start - self.last_start <= policy.login_merge_s:
            return True
        return self.logins_by_day[self.problem.calendar.local_day(start)] < limit

    def next_local_day(self, t: int) -> int:
        """Epoch of the first midnight (local time of the calendar) strictly after `t`."""
        day = self.problem.calendar.local_day(t) + timedelta(days=1)
        midnight = datetime.combine(day, time(0), tzinfo=self.zone)
        return max(t + 1, int(midnight.timestamp()))

    # ----------------------------------------------------------------- task rules

    def free_slot(self, lane: str, t: int) -> int | None:
        slots = self.free_at.get(lane, [])
        for index, free_at in enumerate(slots):
            if free_at <= t:
                return index
        return None

    def free_lanes(self, t: int) -> set[str]:
        return {
            lane for lane, slots in self.free_at.items() if any(free_at <= t for free_at in slots)
        }

    def prerequisites_done(self, task: Task, t: int) -> bool:
        done_at = self.done_at
        for required in task.requires:
            end = done_at.get(required)
            if end is None or end > t:
                return False
        return True

    def duration_of(self, task: Task, start: int) -> int:
        if not self.problem.modifiers:
            return task.duration_s
        return effective_duration(self.problem, task, start)

    def cost_of(self, task: Task, start: int) -> Mapping[str, int]:
        if not self.problem.modifiers:
            return task.cost
        return effective_cost(self.problem, task, start)

    def start_allowed(self, task: Task, start: int) -> bool:
        """Would the simulator accept `task` starting at `start`, in the current state?"""
        problem = self.problem
        if start < problem.now or start >= problem.horizon_end:
            return False
        if not problem.calendar.can_act(start):
            return False
        if not self.end_allowed(start + self.duration_of(task, start)):
            return False
        return self.login_allowed(start)

    def placeable(self, t: int) -> Iterator[Task]:
        """Pending tasks with their prerequisites done and a free slot at `t`, in ratio order."""
        lanes = self.free_lanes(t)
        if not lanes:
            return
        for task in self.pending:
            if task.lane in lanes and self.prerequisites_done(task, t):
                yield task

    def ready(self, t: int) -> Iterator[Task]:
        """Pending tasks placeable and affordable at `t`, by decreasing ratio then id."""
        ledger = self.ledger
        for task in self.placeable(t):
            if ledger.affordable(self.cost_of(task, t), t):
                yield task

    def next_valid_start(self, task: Task, t: int) -> int | None:
        """First instant at or after `t` where `task` can start without breaking a rule.

        Candidates: `t`, the calendar bounds, the instants from which the task ends at a bound
        (with the tolerance), the modifier bounds, the next local day and the funding instant.
        """
        problem = self.problem
        duration = task.duration_s
        tolerance = problem.policy.tolerance_s if problem.policy.mode == "tolerance" else 0
        instants = {t}
        for bound in self.bounds:
            instants.update((bound, bound - duration, bound - tolerance - duration))
        instants.update(self.events)
        if problem.policy.max_logins_per_day is not None:
            instants.add(self.next_local_day(t))
        for start in sorted(instant for instant in instants if instant >= t):
            if start >= problem.horizon_end:
                return None
            cost = self.cost_of(task, start)
            if not self.ledger.affordable(cost, start):
                funded = self.ledger.fundable_at(cost, start)
                if funded is None or funded != start:
                    continue
            if self.start_allowed(task, start):
                return start
        return None

    def next_point(self, t: int) -> int | None:
        """The next decision point strictly after `t`, or None when none remains in the horizon."""
        problem = self.problem
        best: int | None = None

        def consider(instant: int | None) -> None:
            nonlocal best
            if instant is not None and t < instant < problem.horizon_end:
                best = instant if best is None else min(best, instant)

        for slots in self.free_at.values():
            for free_at in slots:
                consider(free_at)
        for bound in self.bounds[bisect_right(self.bounds, t) :]:
            if problem.calendar.can_act(bound):
                consider(bound)
                break
        for event in self.events[bisect_right(self.events, t) :]:
            consider(event)
            break
        ledger = self.ledger
        for task in self.placeable(t):
            cost = self.cost_of(task, t)
            if not ledger.affordable(cost, t):
                consider(ledger.fundable_at(cost, t))
        if problem.policy.max_logins_per_day is not None:
            consider(self.next_local_day(t))
        return best

    # ----------------------------------------------------------------- moves

    def advance(self, to: int) -> None:
        if to > self.t:
            self.ledger.advance(to)
            self.t = to

    def start(self, task: Task, start: int) -> None:
        """Plan `task` at `start >= t` on the first free slot of its lane."""
        self.advance(start)
        slot = self.free_slot(task.lane, start)
        assert slot is not None, task.id
        end = start + self.duration_of(task, start)
        self.ledger.pay(self.cost_of(task, start), start)
        self.free_at[task.lane][slot] = end
        self.done_at[task.id] = end
        self.pending.remove(task)
        policy = self.problem.policy
        if self.last_start is None or start - self.last_start > policy.login_merge_s:
            self.logins_by_day[self.problem.calendar.local_day(start)] += 1
        self.last_start = start
        self.assignments.append(
            Assignment(task_id=task.id, lane=task.lane, lane_slot=slot, start=start, end=end)
        )

    def start_first_allowed(self) -> bool:
        """One step of the base policy at `t`: start the best ready task allowed now."""
        if not self.problem.calendar.can_act(self.t):
            return False
        for task in self.ready(self.t):
            if self.start_allowed(task, self.t):
                self.start(task, self.t)
                return True
        return False

    def complete(self) -> None:
        """Finish the plan with the base policy."""
        while self.t < self.problem.horizon_end:
            if self.start_first_allowed():
                continue
            following = self.next_point(self.t)
            if following is None:
                return
            self.advance(following)


def finish(
    state: State,
    engine: str,
    objective: Objective,
    reference: Plan | None,
    trace: tuple[Decision, ...] = (),
) -> Plan:
    """Judge the planned assignments with the simulator and wrap them in a Plan (ADR-4).

    A plan the simulator refuses is never returned: the empty plan takes its place, with a warning.
    """
    problem = state.problem
    assignments = tuple(state.assignments)
    result = simulate(problem, Plan(assignments, engine, Proof("heuristic", 0.0)), reference)
    warnings: tuple[str, ...] = ()
    if not result.valid:
        assignments, trace, warnings = (), (), (INVALID_PLAN_WARNING,)
        result = simulate(problem, Plan(assignments, engine, Proof("heuristic", 0.0)), reference)
    proof = Proof(kind="heuristic", objective=score(result, objective, problem), bound=None)
    return Plan(assignments=assignments, engine=engine, proof=proof, trace=trace, warnings=warnings)


class BaselinePlanner:
    """The base policy alone, over the whole horizon: the declared adversary of `rollout`."""

    def solve(
        self,
        problem: Problem,
        objective: Objective,
        config: PlannerConfig,
        reference: Plan | None = None,
    ) -> Plan:
        state = State(problem)
        state.complete()
        return finish(state, ENGINE, objective, reference)
