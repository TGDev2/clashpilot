"""Resource forecast of the latest plan and wall sink (E17; SPEC section 4, ADR-4). Pure module.

No probability is computed: the three scenarios are the three declared incomes `farm_rate.low`,
`farm_rate.expected` and `farm_rate.high`. Each scenario compiles the village with that income and
replays the payments of the stored plan on the stock, in the simulator's integer arithmetic (units
x 3600), from the plan's `now` to `now + horizon_days`. Under an income too low for the plan, a
payment waits until the stock covers it, and the payments after it keep their order.

- `series`: the stock of each known resource every `step_s` seconds (a regular step, at most
  `MAX_STEPS + 1` points), the payments of an instant included.
- `funding`: for each task of the compiled problem, the first instant the stock covers its cost
  (after the plan's earlier payments; a task the plan leaves out comes after all of them). The three
  members are a range of dates: `low` comes from the high income, `high` from the low income.
- `saturation`: the windows where the stock sits at its cap under the expected income; their spill
  adds up to the one of `simulate`.
- `blocking`: the plan shifted as early as its lanes, order, prerequisites, funds and availability
  allow (the stored plan itself when it already starts everything as early as it can), against the
  same shift with one resource the plan pays unlimited (no cap, no shortage), both judged by
  `simulate`; the resource that moves a task's end the most, by how much, and which tasks move.
- `walls`: wall upgrades (levels excluded as `wall_sink`) bought greedily, cheapest level first,
  at the start of a saturation window of their cost; each purchase is kept only if `simulate`
  still accepts the plan with every purchase, the spill does not grow and the score is not lower.
- `effort`: the remaining cost of the goal (its tasks and their prerequisites) or of every task,
  and the farm hours `max(0, cost - stock) / income` of each income.

A resource without `farm_rate` has no income here: its values after `now`, and every value that
depends on it, are null with status `unknown`. Nothing is assumed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.state import FarmRate, ManualState
from clashpilot.planning.compile import WALL_SINK
from clashpilot.planning.journal import JournalEntry, objective_of
from clashpilot.planning.model import Assignment, Plan, Problem, ResourceModel, Task
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.replan import carried_jobs, stored_plan
from clashpilot.planning.service import PlanInputs, PlanParams, compile_inputs, with_strategy
from clashpilot.planning.simulate import (
    SCALE,
    Purchase,
    calendar_bounds,
    effective_cost,
    effective_duration,
    next_can_act,
    simulate,
)

Scenario = Literal["low", "expected", "high"]
SCENARIOS: tuple[Scenario, ...] = ("low", "expected", "high")
HOUR_S = 3600
DAY_S = 86_400
MAX_STEPS = 720
MIN_STEP_S = 300
MAX_WALLS = 100
WINDOW_TRIES = 3
SHIFT_ROUNDS = 4
UNLIMITED = 10**15
TASK_STARTED = "task_started"

Estimate = dict[str, Any]


def estimate(value: float | None, *, known: bool, as_of: int | None) -> Estimate:
    """A provenance object of the model: `estimated`, or `unknown` with a null value."""
    if not known or value is None:
        return {"value": None, "status": "unknown", "source": "model", "as_of": as_of}
    return {"value": value, "status": "estimated", "source": "model", "as_of": as_of}


def series_step(horizon_s: int) -> int:
    """The smallest multiple of 5 minutes giving at most `MAX_STEPS` steps over the horizon."""
    per_step = -(-max(1, horizon_s) // MAX_STEPS)
    return -(-per_step // MIN_STEP_S) * MIN_STEP_S


# --------------------------------------------------------------------------- stock ledger


@dataclass
class Window:
    """`resource` sits at its cap over `[start, end)` and loses `lost` (units x 3600)."""

    resource: str
    start: int
    end: int
    lost: int = 0


class _Stock:
    """Stocks in units x 3600 under one income, with the spill windows within the horizon."""

    def __init__(
        self, resources: ResourceModel, names: Iterable[str], now: int, horizon_end: int
    ) -> None:
        self.caps: dict[str, int | None] = {}
        self.rates: dict[str, int] = {}
        self.level: dict[str, int] = {}
        for name in names:
            cap = resources.cap[name] * SCALE if name in resources.cap else None
            level = resources.initial.get(name, 0) * SCALE
            self.caps[name] = cap
            self.rates[name] = resources.income_per_hour.get(name, 0)
            self.level[name] = min(level, cap) if cap is not None else level
        self.clock = now
        self.horizon_end = horizon_end
        self.windows: list[Window] = []
        self._open: dict[str, Window] = {}

    def advance(self, to: int) -> None:
        if to <= self.clock:
            return
        counted = min(to, self.horizon_end) - self.clock
        for name, rate in self.rates.items():
            cap, level = self.caps[name], self.level[name]
            grown = level + rate * (to - self.clock)
            if cap is None or grown <= cap:
                self.level[name] = grown
                continue
            lost = level + rate * counted - cap if counted > 0 else 0
            if lost > 0:
                window = self._open.get(name)
                if window is None:
                    window = Window(name, self.clock + (cap - level) // rate, self.clock)
                    self._open[name] = window
                    self.windows.append(window)
                window.lost += lost
                window.end = self.clock + counted
            self.level[name] = cap
        self.clock = to

    def pay(self, cost: Mapping[str, int]) -> None:
        for name, amount in cost.items():
            if amount > 0:
                self.level[name] = max(0, self.level[name] - amount * SCALE)
                self._open.pop(name, None)

    def covered_at(self, cost: Mapping[str, int]) -> int | None:
        """First instant from the clock where the stock covers `cost`, no payment in between."""
        at = self.clock
        for name, amount in cost.items():
            need = amount * SCALE
            if amount <= 0 or self.level[name] >= need:
                continue
            rate, cap = self.rates[name], self.caps[name]
            if rate <= 0 or (cap is not None and need > cap):
                return None
            at = max(at, self.clock - (-(need - self.level[name]) // rate))
        return at

    def units(self, name: str) -> int:
        return self.level[name] // SCALE


@dataclass(frozen=True)
class Replay:
    funded: Mapping[str, int | None]  # task id -> first instant the stock covers its cost
    samples: Mapping[str, tuple[int, ...]]  # resource -> units at each sample instant
    windows: tuple[Window, ...]


def resource_names(problem: Problem, purchases: Sequence[Purchase] = ()) -> list[str]:
    model = problem.resources
    return sorted(
        set(model.initial)
        | set(model.cap)
        | set(model.income_per_hour)
        | {name for task in problem.tasks for name in task.cost}
        | {name for purchase in purchases for name in purchase.cost}
    )


def replay(
    problem: Problem,
    plan: Plan,
    times: Sequence[int] = (),
    purchases: Sequence[Purchase] = (),
) -> Replay:
    """The stock of `problem` under its income while the plan (and `purchases`) pays."""
    tasks = {task.id: task for task in problem.tasks}
    now = problem.now
    names = resource_names(problem, purchases)
    stock = _Stock(problem.resources, names, now, problem.horizon_end)
    samples: dict[str, list[int]] = {name: [] for name in names}
    index = 0

    def sample_before(limit: int, *, inclusive: bool) -> None:
        nonlocal index
        while index < len(times) and (
            times[index] < limit or (inclusive and times[index] == limit)
        ):
            stock.advance(times[index])
            for name in names:
                samples[name].append(stock.units(name))
            index += 1

    events: list[tuple[int, int, str, Mapping[str, int]]] = []
    for assignment in plan.assignments:
        task = tasks.get(assignment.task_id)
        if task is not None:
            price = effective_cost(problem, task, assignment.start)
            events.append((max(assignment.start, now), 0, task.id, price))
    for number, purchase in enumerate(purchases):
        events.append((max(purchase.at, now), 1, f"{number:09d}", purchase.cost))
    events.sort(key=lambda event: event[:3])

    funded: dict[str, int | None] = {}
    for at, kind, key, cost in events:
        if kind == 0:
            covered = stock.covered_at(cost)
            funded[key] = covered
            if covered is None:
                continue  # the stock never covers it: never paid
            when = max(at, covered)
        else:
            when = max(at, stock.clock)
        sample_before(when, inclusive=False)
        stock.advance(when)
        stock.pay(cost)
    for task in problem.tasks:
        if task.id not in funded:
            funded[task.id] = stock.covered_at(task.cost)
    sample_before(problem.horizon_end, inclusive=True)
    stock.advance(problem.horizon_end)
    return Replay(
        funded=funded,
        samples={name: tuple(values) for name, values in samples.items()},
        windows=tuple(stock.windows),
    )


# --------------------------------------------------------------------------- blocking


def _unlimited(problem: Problem, name: str) -> Problem:
    model = problem.resources
    resources = ResourceModel(
        initial={**model.initial, name: UNLIMITED},
        cap={key: cap for key, cap in model.cap.items() if key != name},
        income_per_hour=model.income_per_hour,
    )
    return replace(problem, resources=resources)


def shifted_plan(problem: Problem, plan: Plan) -> Plan:
    """The plan started as early as its lanes, order, prerequisites, funds and availability allow.

    Each assignment keeps its lane slot and never starts later than in `plan`; a task `simulate`
    blames keeps its start, and the shift is tried again. Without a valid shift, `plan` itself.
    """
    tasks = {task.id: task for task in problem.tasks}
    bounds = calendar_bounds(problem.calendar)
    ordered = sorted(plan.assignments, key=lambda a: (a.start, a.task_id))
    pinned: set[str] = set()
    for _ in range(SHIFT_ROUNDS):
        stock = _Stock(problem.resources, resource_names(problem), problem.now, problem.horizon_end)
        free: dict[tuple[str, int], int] = {}
        ends: dict[str, int] = {}
        for job in problem.frozen:
            ends[job.task_id] = job.end
            if not job.extra:
                slot = (job.lane, job.lane_slot)
                free[slot] = max(free.get(slot, problem.now), job.end)
        shifted: list[Assignment] = []
        for assignment in ordered:
            task = tasks[assignment.task_id]
            start = assignment.start
            if task.id not in pinned:
                earliest = max(
                    problem.now,
                    stock.clock,
                    free.get((assignment.lane, assignment.lane_slot), problem.now),
                    *(ends.get(required, problem.now) for required in task.requires),
                )
                covered = stock.covered_at(effective_cost(problem, task, earliest))
                candidate = max(earliest, covered if covered is not None else start)
                start = min(start, next_can_act(problem.calendar, bounds, candidate))
            stock.advance(start)
            stock.pay(effective_cost(problem, task, start))
            end = start + effective_duration(problem, task, start)
            free[(assignment.lane, assignment.lane_slot)] = end
            ends[task.id] = end
            shifted.append(replace(assignment, start=start, end=end))
        candidate_plan = replace(plan, assignments=tuple(shifted))
        blamed = {v.task_id for v in simulate(problem, candidate_plan).violations if v.task_id}
        if not blamed:
            return candidate_plan
        if blamed <= pinned:
            break
        pinned |= blamed
    return plan


def blocking(problem: Problem, plan: Plan) -> dict[str, Any] | None:
    """The resource whose shortage delays the plan the most, or None when none does."""
    tasks = {task.id: task for task in problem.tasks}
    # The real plan re-timed by the same shift: only the unlimited resource makes a difference.
    real = {a.task_id: a.end for a in shifted_plan(problem, plan).assignments if a.task_id in tasks}
    paid = sorted({name for tid in real for name, amount in tasks[tid].cost.items() if amount > 0})
    best: tuple[int, str, list[str]] | None = None
    for name in paid:
        shifted = shifted_plan(_unlimited(problem, name), plan)
        delays = {
            a.task_id: real[a.task_id] - a.end
            for a in shifted.assignments
            if a.task_id in real and real[a.task_id] > a.end
        }
        if delays and (best is None or max(delays.values()) > best[0]):
            best = (max(delays.values()), name, sorted(delays))
    if best is None:
        return None
    delayed_s, name, delayed = best
    return {"resource": name, "delayed_s": delayed_s, "tasks": delayed}


# --------------------------------------------------------------------------- walls


@dataclass(frozen=True)
class WallStep:
    data_id: int
    instance: int
    level: int
    cost: Mapping[str, int]


def wall_steps(problem: Problem, pack: GamePack) -> list[WallStep]:
    """The wall levels the compiler left out as `wall_sink`, cheapest level first."""
    steps: list[WallStep] = []
    for excluded in problem.excluded:
        if excluded.reason != WALL_SINK:
            continue
        row = pack.level(excluded.data_id, excluded.to_level)
        if row is None:
            continue
        cost = {name: amount for name, amount in row.cost.items() if amount > 0}
        if cost:
            steps.append(WallStep(excluded.data_id, excluded.instance, excluded.to_level, cost))
    steps.sort(key=lambda s: (s.level, sum(s.cost.values()), s.data_id, s.instance))
    return steps


def walls(
    problem: Problem, plan: Plan, objective: Objective, pack: GamePack
) -> list[dict[str, Any]]:
    """Wall upgrades bought with the expected excess, each one checked by `simulate`."""
    steps = wall_steps(problem, pack)
    if not steps:
        return []
    base = simulate(problem, plan)
    if not base.valid:
        return []
    floor_score = score(base, objective, problem)
    spill = sum(base.metrics.spill.values())
    bought: list[Purchase] = []
    proposed: list[dict[str, Any]] = []
    last: dict[tuple[int, int], int] = {}
    blocked: set[tuple[int, int]] = set()
    for step in steps:
        if len(proposed) >= MAX_WALLS:
            break
        key = (step.data_id, step.instance)
        if key in blocked:
            continue
        earliest = last.get(key, problem.now)
        windows = replay(problem, plan, purchases=bought).windows
        instants = sorted(
            {
                max(window.start, earliest)
                for window in windows
                if window.resource in step.cost and window.end > earliest
            }
        )[:WINDOW_TRIES]
        for at in instants:
            trial = [*bought, Purchase(at, step.cost)]
            sim = simulate(problem, plan, purchases=trial)
            lost = sum(sim.metrics.spill.values())
            if not sim.valid or lost > spill or score(sim, objective, problem) < floor_score:
                continue
            bought, spill, last[key] = trial, lost, at
            item = pack.item(step.data_id)
            proposed.append(
                {
                    "data_id": step.data_id,
                    "instance": step.instance,
                    "level": step.level,
                    "name": item.name if item is not None else str(step.data_id),
                    "cost": dict(step.cost),
                    "at": at,
                }
            )
            break
        else:
            blocked.add(key)
    return proposed


# --------------------------------------------------------------------------- effort


def _scope(problem: Problem) -> tuple[str, list[Task]]:
    tasks = {task.id: task for task in problem.tasks}
    if problem.goal is None or not problem.goal.task_ids:
        return "plan", list(problem.tasks)
    wanted: set[str] = set()
    pending = [tid for tid in problem.goal.task_ids if tid in tasks]
    while pending:
        tid = pending.pop()
        if tid in wanted:
            continue
        wanted.add(tid)
        pending.extend(required for required in tasks[tid].requires if required in tasks)
    return "goal", [task for task in problem.tasks if task.id in wanted]


def effort(problems: Mapping[Scenario, Problem], known: frozenset[str]) -> dict[str, Any]:
    expected = problems["expected"]
    scope, tasks = _scope(expected)
    cost: dict[str, int] = {}
    for task in tasks:
        for name, amount in task.cost.items():
            if amount > 0:
                cost[name] = cost.get(name, 0) + amount
    hours: dict[str, dict[str, Estimate]] = {}
    for name, total in sorted(cost.items()):
        hours[name] = {}
        for scenario in SCENARIOS:
            model = problems[scenario].resources
            stock = model.initial.get(name)
            rate = model.income_per_hour.get(name, 0)
            need = None if stock is None else max(0, total - stock)
            if need == 0:
                value: float | None = 0.0
            elif need is None or name not in known or rate <= 0:
                value = None
            else:
                value = need / rate
            hours[name][scenario] = estimate(value, known=value is not None, as_of=expected.now)
    return {"scope": scope, "cost": dict(sorted(cost.items())), "farm_hours": hours}


# --------------------------------------------------------------------------- forecast


def build_forecast(
    problems: Mapping[Scenario, Problem],
    plan: Plan,
    objective: Objective,
    pack: GamePack,
    known: frozenset[str],
) -> dict[str, Any]:
    """The forecast document; `problems` share their tasks and differ by their income only."""
    expected = problems["expected"]
    now, horizon_end = expected.now, expected.horizon_end
    step = series_step(horizon_end - now)
    times = tuple(range(now, horizon_end + 1, step))
    replays = {scenario: replay(problems[scenario], plan, times) for scenario in SCENARIOS}

    series = {
        name: [
            {
                "t": t,
                **{
                    scenario: estimate(
                        replays[scenario].samples[name][i],
                        known=name in known or t == now,
                        as_of=now,
                    )
                    for scenario in SCENARIOS
                },
            }
            for i, t in enumerate(times)
        ]
        for name in sorted(expected.resources.initial)
    }

    tasks = {task.id: task for task in expected.tasks}
    planned = [a.task_id for a in sorted(plan.assignments, key=lambda a: (a.start, a.task_id))]
    order = [tid for tid in planned if tid in tasks]
    order += sorted(tid for tid in tasks if tid not in set(order))

    def funded(tid: str, scenario: Scenario) -> Estimate:
        at = replays[scenario].funded.get(tid)
        needs = [name for name, amount in tasks[tid].cost.items() if amount > 0]
        sure = at is not None and (at == now or all(name in known for name in needs))
        return estimate(at, known=sure, as_of=now)

    funding = [
        {
            "task_id": tid,
            "funded_at": {
                "low": funded(tid, "high"),
                "expected": funded(tid, "expected"),
                "high": funded(tid, "low"),
            },
        }
        for tid in order
    ]
    saturation = [
        {
            "resource": window.resource,
            "start": window.start,
            "end": window.end,
            "spilled": window.lost / SCALE,
        }
        for window in sorted(replays["expected"].windows, key=lambda w: (w.start, w.resource))
    ]
    return {
        "now": now,
        "horizon_end": horizon_end,
        "step_s": step,
        "series": series,
        "funding": funding,
        "saturation": saturation,
        "blocking": blocking(expected, plan),
        "walls": walls(expected, plan, objective, pack),
        "effort": effort(problems, known),
    }


def _with_income(manual: ManualState, scenario: Scenario) -> ManualState:
    """`manual` whose expected income is the one of `scenario`."""
    rates = {
        name: FarmRate(low=rate.low, expected=getattr(rate, scenario), high=rate.high)
        for name, rate in manual.farm_rate.items()
    }
    return manual.model_copy(update={"farm_rate": rates})


def forecast_plan(
    inputs: PlanInputs,
    params: PlanParams,
    body: Mapping[str, Any],
    horizon_days: int,
    *,
    same_snapshot: bool = True,
) -> dict[str, Any]:
    """The forecast of a stored plan (`body` as stored) over `horizon_days` from its `now`.

    Raises `MissingInputError` and `UnknownStrategyError`.
    """
    now = int(body["now"])
    carried = carried_jobs(body) if same_snapshot else {}
    problems: dict[Scenario, Problem] = {}
    weights: Objective | None = None
    for scenario in SCENARIOS:
        variant = replace(inputs, manual=_with_income(inputs.manual, scenario))
        compiled, _ = compile_inputs(variant, now, params.horizon_days, carried)
        problem, objective = with_strategy(compiled, params)
        problems[scenario] = replace(problem, horizon_end=now + horizon_days * DAY_S)
        if scenario == "expected":
            weights = objective
    assert weights is not None
    objective = objective_of(inputs.journal) or weights
    known = frozenset(inputs.manual.farm_rate)
    return build_forecast(problems, stored_plan(body), objective, inputs.pack, known)


# --------------------------------------------------------------------------- observed rate


def observed_rate(history: Sequence[tuple[int, int]], spent: Sequence[tuple[int, int]]) -> Estimate:
    """The rate per hour between the last two inputs `(as_of, value)`, net of `spent`.

    `spent` holds `(at, amount)` payments; those in `(previous as_of, last as_of]` are added back.
    The last input of an `as_of` wins. Fewer than two inputs: null with status `unknown`.
    """
    latest: dict[int, int] = {}
    for as_of, value in history:
        latest[as_of] = value
    points = sorted(latest.items())
    if len(points) < 2:
        return estimate(None, known=False, as_of=points[-1][0] if points else None)
    (before, first), (after, last) = points[-2], points[-1]
    paid = sum(amount for at, amount in spent if before < at <= after)
    return estimate((last - first + paid) * HOUR_S / (after - before), known=True, as_of=after)


def journal_spending(journal: Iterable[JournalEntry], resource: str) -> list[tuple[int, int]]:
    """`(at, amount)` of the `task_started` costs of `resource` in the journal."""
    spent: list[tuple[int, int]] = []
    for entry in journal:
        if entry.type != TASK_STARTED:
            continue
        cost = entry.payload.get("cost") or {}
        amount = cost.get(resource, 0) if isinstance(cost, Mapping) else 0
        if isinstance(amount, int) and amount > 0:
            spent.append((entry.at, amount))
    return spent
