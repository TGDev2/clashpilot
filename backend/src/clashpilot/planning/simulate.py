"""The exact simulator, single judge of every plan whatever its engine (SPEC section 4, ADR-4).

It works to the second in integer arithmetic: a stock is kept in units x 3600, so an hourly income
adds exactly `income_per_hour` per second. The cost of a task is paid when it starts; the income
accrues continuously and what goes over the cap is lost and counted. Pure module: `now` and every
instant come from the problem and the plan.

A frozen job occupies its lane and satisfies `requires`, but the rules about starts and ends
(availability, sleep, tolerance, past start, logins, hero windows) only judge the assignments of
the plan.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from operator import itemgetter

from clashpilot.planning.model import Assignment, Modifier, Plan, Problem, Task
from clashpilot.schedule.expand import AvailabilityCalendar

SCALE = 3600  # stock units per resource unit
DAY_S = 86_400
HERO_TAG = "hero"

Interval = tuple[int, int]
Accepted = tuple[Assignment, Task]


@dataclass(frozen=True)
class Violation:
    code: str
    task_id: str | None = None
    at: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class SimMetrics:
    tasks_planned: int = 0
    tasks_completed: int = 0  # planned tasks ending at or before horizon_end
    ends: Mapping[str, int] = field(default_factory=dict)  # task id -> end
    busy_s: int = 0  # lane time used in [now, horizon_end], frozen jobs included
    ends_in_sleep: int = 0
    ends_in_preferred: int = 0
    logins: int = 0
    logins_per_day: float = 0.0
    spill: Mapping[str, float] = field(default_factory=dict)  # units lost over the horizon
    income: Mapping[str, float] = field(default_factory=dict)  # units earned over the horizon
    perturbation: float = 0.0


@dataclass(frozen=True)
class Purchase:
    """A payment outside the lanes at `at` (a wall bought with the excess, E17).

    It takes no lane, no duration and no login: only the stock pays it, after the tasks starting
    at the same instant. An uncovered purchase is an `insufficient_funds` without task id.
    """

    at: int
    cost: Mapping[str, int]


@dataclass(frozen=True)
class SimResult:
    valid: bool
    violations: tuple[Violation, ...]
    metrics: SimMetrics
    # (t, stocks): stocks in units x 3600 after the income and the payments at t.
    stock_timeline: tuple[tuple[int, Mapping[str, int]], ...]


# --------------------------------------------------------------------------- modifiers


def _scaled_up(amount: int, pct: int) -> int:
    """`amount * pct / 100`, rounded up to the next integer."""
    return -(-amount * pct // 100)


def _active(problem: Problem, lane: str, start: int) -> list[Modifier]:
    return [m for m in problem.modifiers if m.lane == lane and m.start <= start < m.end]


def effective_duration(problem: Problem, task: Task, start: int) -> int:
    """Duration of `task` started at `start`, with the modifiers active at that instant."""
    duration = task.duration_s
    for modifier in _active(problem, task.lane, start):
        duration = _scaled_up(duration, modifier.duration_pct)
    return duration


def effective_cost(problem: Problem, task: Task, start: int) -> dict[str, int]:
    """Cost of `task` started at `start`, with the modifiers active at that instant."""
    cost = dict(task.cost)
    for modifier in _active(problem, task.lane, start):
        cost = {name: _scaled_up(amount, modifier.cost_pct) for name, amount in cost.items()}
    return cost


def hero_blocked(problem: Problem, task: Task, start: int, end: int) -> bool:
    """Does `task`, a hero upgrade running over `[start, end)`, overlap a hero blackout window?"""
    if HERO_TAG not in task.tags:
        return False
    return any(min(end, high) > max(start, low) for low, high in problem.hero_blackouts)


# --------------------------------------------------------------------------- calendar helpers


def _contains(intervals: Sequence[Interval], t: int) -> bool:
    index = bisect_right(intervals, t, key=itemgetter(0)) - 1
    return index >= 0 and t < intervals[index][1]


def calendar_bounds(calendar: AvailabilityCalendar) -> list[int]:
    spans = (*calendar.sleep, *calendar.work, *calendar.absence, *calendar.available)
    return sorted({bound for span in spans for bound in span})


def next_can_act(calendar: AvailabilityCalendar, bounds: Sequence[int], t: int) -> int:
    """First instant at or after `t` where the player can act.

    `can_act` only changes at an interval bound, and holds after the last one.
    """
    if calendar.can_act(t):
        return t
    for bound in bounds[bisect_right(bounds, t) :]:
        if calendar.can_act(bound):
            return bound
    return t


# --------------------------------------------------------------------------- checks


def _screen(problem: Problem, plan: Plan) -> tuple[list[Accepted], list[Violation]]:
    """Keep the assignments that name a task of the problem once; report the others."""
    tasks = {task.id: task for task in problem.tasks}
    frozen = {job.task_id for job in problem.frozen}
    accepted: list[Accepted] = []
    violations: list[Violation] = []
    seen: set[str] = set()
    ordered = sorted(
        plan.assignments, key=lambda a: (a.start, a.lane, a.lane_slot, a.task_id, a.end)
    )
    for assignment in ordered:
        name, start = assignment.task_id, assignment.start
        task = tasks.get(name)
        if name in frozen:
            violations.append(Violation("modified_frozen", name, start))
        elif task is None:
            violations.append(Violation("unknown_task", name, start))
        elif name in seen:
            violations.append(Violation("duplicate_task", name, start))
        else:
            seen.add(name)
            if assignment.lane != task.lane:
                violations.append(Violation("wrong_lane", name, start, task.lane))
            expected = start + effective_duration(problem, task, start)
            if assignment.end != expected:
                violations.append(Violation("duration_mismatch", name, start, str(expected)))
            accepted.append((assignment, task))
    return accepted, violations


def _timing_violations(problem: Problem, accepted: Sequence[Accepted]) -> list[Violation]:
    calendar, policy = problem.calendar, problem.policy
    bounds = calendar_bounds(calendar) if policy.mode == "tolerance" else []
    violations: list[Violation] = []
    for assignment, task in accepted:
        name, start, end = assignment.task_id, assignment.start, assignment.end
        if start < problem.now:
            violations.append(Violation("past_start", name, start))
        if hero_blocked(problem, task, start, end):
            violations.append(Violation("hero_unavailable", name, start))
        if not calendar.can_act(start):
            violations.append(Violation("start_outside_availability", name, start))
        if policy.mode == "strict" and calendar.is_sleep(end):
            violations.append(Violation("end_in_sleep", name, end))
        if policy.mode == "tolerance":
            wait = next_can_act(calendar, bounds, end) - end
            if wait > policy.tolerance_s:
                violations.append(Violation("idle_beyond_tolerance", name, end, str(wait)))
    return violations


def _precedence_violations(problem: Problem, accepted: Sequence[Accepted]) -> list[Violation]:
    done_at = {job.task_id: job.end for job in problem.frozen}
    done_at.update((assignment.task_id, assignment.end) for assignment, _ in accepted)
    violations: list[Violation] = []
    for assignment, task in accepted:
        for required in task.requires:
            end = done_at.get(required)
            if end is None or end > assignment.start:
                violations.append(
                    Violation("precedence", assignment.task_id, assignment.start, required)
                )
    return violations


def _capacity_violations(problem: Problem, accepted: Sequence[Accepted]) -> list[Violation]:
    """Slots out of range, two jobs on one slot at once, or more jobs than the lane holds.

    A conflict between frozen jobs only is not the plan's doing and is not reported.
    """
    spans: dict[str, list[tuple[int, int, int, str | None]]] = defaultdict(list)
    for job in problem.frozen:
        if not job.extra:
            spans[job.lane].append((job.start, job.end, job.lane_slot, None))
    for assignment, _ in accepted:
        spans[assignment.lane].append(
            (assignment.start, assignment.end, assignment.lane_slot, assignment.task_id)
        )

    flagged: dict[str, int] = {}
    for lane in sorted(spans):
        capacity = problem.lanes.get(lane, 0)
        lane_spans = sorted(spans[lane], key=lambda s: (s[0], s[1], s[2], s[3] or ""))
        for start, _, slot, name in lane_spans:
            if name is not None and not 0 <= slot < capacity:
                flagged.setdefault(name, start)

        by_slot: dict[int, list[tuple[int, int, int, str | None]]] = defaultdict(list)
        for span in lane_spans:
            by_slot[span[2]].append(span)
        for same_slot in by_slot.values():
            last_end, last_name = None, None
            for start, end, _, name in same_slot:
                if last_end is not None and start < last_end:
                    culprit = name if name is not None else last_name
                    if culprit is not None:
                        flagged.setdefault(culprit, start)
                if last_end is None or end > last_end:
                    last_end, last_name = end, name

        # Ends before starts at the same instant: intervals are half-open.
        events = sorted(
            [(start, 1, name or "") for start, end, _, name in lane_spans if end > start]
            + [(end, 0, name or "") for start, end, _, name in lane_spans if end > start]
        )
        active = 0
        planned: set[str] = set()
        for t, starting, name in events:
            if not starting:
                active -= 1
                planned.discard(name)
                continue
            active += 1
            if name:
                planned.add(name)
            if active > capacity and planned:
                flagged.setdefault(name or min(planned), t)
    return [Violation("lane_capacity", name, at) for name, at in flagged.items()]


def _login_starts(problem: Problem, accepted: Sequence[Accepted]) -> list[int]:
    """First start of each login: a maximal group of starts at most `login_merge_s` apart."""
    logins: list[int] = []
    previous: int | None = None
    for start in sorted(assignment.start for assignment, _ in accepted):
        if previous is None or start - previous > problem.policy.login_merge_s:
            logins.append(start)
        previous = start
    return logins


def _login_violations(problem: Problem, logins: Sequence[int]) -> list[Violation]:
    limit = problem.policy.max_logins_per_day
    if limit is None:
        return []
    first_of_day: dict[date, int] = {}
    per_day: Counter[date] = Counter()
    for start in logins:
        day = problem.calendar.local_day(start)
        first_of_day.setdefault(day, start)
        per_day[day] += 1
    return [
        Violation("logins_exceeded", None, first_of_day[day], f"{day}: {count}")
        for day, count in per_day.items()
        if count > limit
    ]


# --------------------------------------------------------------------------- resources


@dataclass
class _Ledger:
    """Stocks in units x 3600, advanced from one event to the next."""

    stock: dict[str, int]
    caps: dict[str, int | None]
    rates: dict[str, int]
    clock: int
    horizon_end: int
    spill: dict[str, int]
    earned: dict[str, int]

    def advance(self, to: int) -> None:
        if to <= self.clock:
            return
        counted = min(to, self.horizon_end)
        if counted > self.clock:
            self._accrue(counted - self.clock, count=True)
            self.clock = counted
        if to > self.clock:
            self._accrue(to - self.clock, count=False)
            self.clock = to

    def _accrue(self, seconds: int, *, count: bool) -> None:
        for name, rate in self.rates.items():
            gained = rate * seconds
            level = self.stock[name] + gained
            limit = self.caps[name]
            lost = max(0, level - limit) if limit is not None else 0
            self.stock[name] = level - lost
            if count:
                self.earned[name] += gained
                self.spill[name] += lost


def _funds(
    problem: Problem, accepted: Sequence[Accepted], purchases: Sequence[Purchase] = ()
) -> tuple[list[Violation], _Ledger, tuple[tuple[int, Mapping[str, int]], ...]]:
    model = problem.resources
    now = problem.now
    names = sorted(
        set(model.initial)
        | set(model.cap)
        | set(model.income_per_hour)
        | {name for _, task in accepted for name in task.cost}
        | {name for purchase in purchases for name in purchase.cost}
    )
    caps: dict[str, int | None] = {
        name: model.cap[name] * SCALE if name in model.cap else None for name in names
    }
    stock: dict[str, int] = {}
    for name in names:
        level = model.initial.get(name, 0) * SCALE
        limit = caps[name]
        stock[name] = min(level, limit) if limit is not None else level
    ledger = _Ledger(
        stock=stock,
        caps=caps,
        rates={name: model.income_per_hour.get(name, 0) for name in names},
        clock=now,
        horizon_end=problem.horizon_end,
        spill=dict.fromkeys(names, 0),
        earned=dict.fromkeys(names, 0),
    )

    payments: dict[int, list[Accepted]] = defaultdict(list)
    for assignment, task in accepted:
        payments[max(assignment.start, now)].append((assignment, task))
    bought: dict[int, list[Purchase]] = defaultdict(list)
    for purchase in purchases:
        bought[max(purchase.at, now)].append(purchase)
    timeline: list[tuple[int, Mapping[str, int]]] = [(now, dict(ledger.stock))]
    violations: list[Violation] = []

    def pay(cost: Mapping[str, int], task_id: str | None, at: int) -> None:
        short = sorted(name for name, amount in cost.items() if ledger.stock[name] < amount * SCALE)
        if short:
            violations.append(Violation("insufficient_funds", task_id, at, ",".join(short)))
        for name, amount in cost.items():
            ledger.stock[name] = max(0, ledger.stock[name] - amount * SCALE)

    for t in sorted(set(payments) | set(bought) | {problem.horizon_end}):
        ledger.advance(t)
        for assignment, task in sorted(payments.get(t, []), key=lambda p: p[0].task_id):
            pay(effective_cost(problem, task, assignment.start), task.id, assignment.start)
        for purchase in bought.get(t, []):
            pay(purchase.cost, None, purchase.at)
        timeline.append((t, dict(ledger.stock)))
    return violations, ledger, tuple(timeline)


# --------------------------------------------------------------------------- entry point


def _overlap(start: int, end: int, low: int, high: int) -> int:
    return max(0, min(end, high) - max(start, low))


def simulate(
    problem: Problem,
    plan: Plan,
    reference: Plan | None = None,
    *,
    purchases: Sequence[Purchase] = (),
) -> SimResult:
    """Judge `plan` against `problem`; `reference` only feeds the perturbation metric.

    `purchases` are paid by the stock besides the plan (E17), outside every lane.
    """
    accepted, violations = _screen(problem, plan)
    violations += _timing_violations(problem, accepted)
    violations += _precedence_violations(problem, accepted)
    violations += _capacity_violations(problem, accepted)
    logins = _login_starts(problem, accepted)
    violations += _login_violations(problem, logins)
    fund_violations, ledger, timeline = _funds(problem, accepted, purchases)
    violations += fund_violations

    now, horizon_end = problem.now, problem.horizon_end
    horizon = max(1, horizon_end - now)
    calendar = problem.calendar
    busy = sum(
        _overlap(job.start, job.end, now, horizon_end) for job in problem.frozen if not job.extra
    ) + sum(_overlap(a.start, a.end, now, horizon_end) for a, _ in accepted)
    perturbation = 0.0
    if reference is not None:
        reference_starts = {a.task_id: a.start for a in reference.assignments}
        moves = [
            abs(a.start - reference_starts[a.task_id])
            for a, _ in accepted
            if a.task_id in reference_starts
        ]
        perturbation = sum(moves) / (max(1, len(moves)) * horizon)
    metrics = SimMetrics(
        tasks_planned=len(accepted),
        tasks_completed=sum(1 for a, _ in accepted if a.end <= horizon_end),
        ends={a.task_id: a.end for a, _ in accepted},
        busy_s=busy,
        ends_in_sleep=sum(1 for a, _ in accepted if calendar.is_sleep(a.end)),
        ends_in_preferred=sum(1 for a, _ in accepted if _contains(calendar.preferred, a.end)),
        logins=len(logins),
        logins_per_day=len(logins) / max(1.0, horizon / DAY_S),
        spill={name: lost / SCALE for name, lost in ledger.spill.items()},
        income={name: earned / SCALE for name, earned in ledger.earned.items()},
        perturbation=perturbation,
    )
    ordered = tuple(
        sorted(violations, key=lambda v: (v.at or 0, v.code, v.task_id or "", v.detail))
    )
    return SimResult(
        valid=not ordered, violations=ordered, metrics=metrics, stock_timeline=timeline
    )
