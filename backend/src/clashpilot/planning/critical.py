"""Critical path of the next town hall, with a work bound per lane (SPEC ADR-CP1).

The date of the next town hall is set either by a chain (hero levels one after another) or by a
saturated lane (a lab with months of research on one slot). `critical_path` computes both:

1. the graph is the tasks of the problem plus its frozen jobs, with the `requires` edges; a
   requirement found in neither is already met;
2. a forward pass in topological order starts each task at the first instant from the end of its
   requirements (never before `now`) that the rules of `simulate` accept: the player can act, no
   hero blackout is crossed, and the end is not asleep (`strict`) nor too far from the next
   instant the player can act (`tolerance`). Durations come from `effective_duration` at that
   start. The calendar wait is summed along each chain;
3. each lane holding a task of the current set gets a work bound: `now` plus its remaining work
   (tasks of the set and running jobs that take a slot) divided by its capacity, rounded up;
4. the set finishes at the latest of its chains and of these work bounds;
5. a backward pass against `finish_parallel` gives each task its float. It runs in continuous
   time, without the calendar: an approximation, the forward pass alone follows the calendar.
   Every task of a critical lane gets a float of 0, since that lane must never stop;
6. `chains` keeps the longest paths of the set, one per `data_id`, at most three.

Capacity and resources do not delay the chains: the work bounds stand for the capacity.

For a stored plan read at a later instant, `start_figures` says how much a start matters to the
date and `stopped_work` which critical chains and lanes stand idle. Pure: no I/O, no clock (`now`
is a parameter).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from operator import itemgetter
from typing import Literal

from clashpilot.planning.compile import TOWN_HALL_TAG
from clashpilot.planning.model import Assignment, Problem, Task
from clashpilot.planning.simulate import (
    calendar_bounds,
    effective_duration,
    hero_blocked,
    next_can_act,
)

MAX_CHAINS = 3
CRITICAL_FLOAT_S = 300  # a task with at most this float is critical
STOPPED_AFTER_S = 15 * 60  # idle critical work is reported when its next start is further than this
_DAY_S = 24 * 3600
_MAX_STEPS = 10_000  # safety cap of the search of a valid start; each step moves forward

Interval = tuple[int, int]


@dataclass(frozen=True)
class Bound:
    """What fixes the end of the current set: a chain, or the work of one lane."""

    kind: Literal["chain", "work"]
    lane: str | None = None


@dataclass(frozen=True)
class Chain:
    """A longest path of the current set, ending with a task of `data_id`."""

    data_id: int
    task_ids: tuple[str, ...]
    finish: int
    calendar_wait_s: int


@dataclass(frozen=True)
class CriticalPath:
    target: Task | None
    work_s: Mapping[str, int]  # remaining work of each lane of `work_finish`
    work_finish: Mapping[str, int]
    set_finish: int | None
    finish_after: int | None  # the town hall started once the set is done
    finish_parallel: int | None  # the town hall run next to the set
    bound: Bound | None
    critical_lanes: tuple[str, ...]
    float_s: Mapping[str, int]
    chains: tuple[Chain, ...]


def current_level_ids(tasks: Sequence[Task], skip_lanes: Iterable[str] = ()) -> tuple[str, ...]:
    """Ids of the tasks that depend on no town hall, outside `skip_lanes`, in task order.

    A task depends on a town hall when it is one, or requires (transitively) one of the tasks.
    """
    skipped = frozenset(skip_lanes)
    after = {task.id for task in tasks if TOWN_HALL_TAG in task.tags}
    grown = bool(after)
    while grown:
        grown = False
        for task in tasks:
            if task.id not in after and after.intersection(task.requires):
                after.add(task.id)
                grown = True
    return tuple(task.id for task in tasks if task.id not in after and task.lane not in skipped)


@dataclass(frozen=True)
class _Early:
    start: int
    finish: int
    wait_s: int  # calendar wait summed along the critical predecessors
    previous: str | None  # the task whose end set the ready instant


def _interval_end(intervals: Sequence[Interval], t: int) -> int | None:
    index = bisect_right(intervals, t, key=itemgetter(0)) - 1
    if index >= 0 and t < intervals[index][1]:
        return intervals[index][1]
    return None


class _Timer:
    """The start rules of `simulate._timing_violations`, as a search of the first valid start."""

    def __init__(self, problem: Problem) -> None:
        self.problem = problem
        self.bounds = calendar_bounds(problem.calendar)

    def first_start(self, task: Task, ready: int) -> int:
        problem, calendar = self.problem, self.problem.calendar
        start = max(ready, problem.now)
        for _ in range(_MAX_STEPS):
            acting = next_can_act(calendar, self.bounds, start)
            if acting > start:
                start = acting
                continue
            if not calendar.can_act(start):
                return start  # no later instant where the player can act
            end = start + effective_duration(problem, task, start)
            later = self._later(task, start, end)
            if later is None:
                return start
            start = later
        return start

    def _later(self, task: Task, start: int, end: int) -> int | None:
        """A later start when `[start, end)` breaks a rule, else None."""
        problem, calendar, policy = self.problem, self.problem.calendar, self.problem.policy
        if hero_blocked(problem, task, start, end):
            return max(
                high for low, high in problem.hero_blackouts if min(end, high) > max(start, low)
            )
        if policy.mode == "strict" and calendar.is_sleep(end):
            wake = _interval_end(calendar.sleep, end)
            if wake is not None:
                return max(start + 1, wake - (end - start))
        if policy.mode == "tolerance":
            acting = next_can_act(calendar, self.bounds, end)
            if acting - end > policy.tolerance_s:
                return max(start + 1, acting - policy.tolerance_s - (end - start))
        return None


def _order(tasks: Sequence[Task]) -> list[Task]:
    """Tasks in topological order of `requires`; tasks on a cycle come last, in task order."""
    known = {task.id: task for task in tasks}
    pending = {task.id: sum(1 for name in set(task.requires) if name in known) for task in tasks}
    followers: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for name in dict.fromkeys(task.requires):
            if name in known:
                followers[name].append(task.id)
    ready = [task.id for task in tasks if pending[task.id] == 0]
    ordered: list[Task] = []
    while ready:
        name = ready.pop(0)
        ordered.append(known[name])
        for follower in followers[name]:
            pending[follower] -= 1
            if pending[follower] == 0:
                ready.append(follower)
    placed = {task.id for task in ordered}
    return ordered + [task for task in tasks if task.id not in placed]


def _forward(problem: Problem) -> dict[str, _Early]:
    timer = _Timer(problem)
    done: dict[str, int] = {job.task_id: job.end for job in problem.frozen}
    early: dict[str, _Early] = {}
    for task in _order(problem.tasks):
        ready, previous = problem.now, None
        for name in task.requires:
            end = early[name].finish if name in early else done.get(name)
            if end is not None and end > ready:
                ready, previous = end, name
        start = timer.first_start(task, ready)
        carried = early[previous].wait_s if previous in early else 0
        finish = start + effective_duration(problem, task, start)
        early[task.id] = _Early(start, finish, carried + start - max(ready, problem.now), previous)
    return early


def _ceil_div(amount: int, divisor: int) -> int:
    return -(-amount // divisor)


def _work(problem: Problem, members: Sequence[Task], early: Mapping[str, _Early]) -> dict[str, int]:
    """Remaining seconds of work of each lane holding a task of the set."""
    work: dict[str, int] = {}
    for task in members:
        work[task.lane] = work.get(task.lane, 0) + early[task.id].finish - early[task.id].start
    for job in problem.frozen:
        if job.lane in work and not job.extra:
            work[job.lane] += max(0, job.end - problem.now)
    return dict(sorted(work.items()))


def _work_finish(problem: Problem, work: Mapping[str, int]) -> dict[str, int]:
    return {
        lane: problem.now + _ceil_div(total, max(1, problem.lanes.get(lane, 1)))
        for lane, total in work.items()
    }


def _floats(
    members: Sequence[Task], target: Task, early: Mapping[str, _Early], finish: int
) -> dict[str, int]:
    """Backward pass in continuous time: latest finish = earliest latest start of a follower."""
    nodes = [*members, target]
    names = {task.id for task in nodes}
    latest_finish = {task.id: finish for task in nodes}
    for task in reversed(_order(nodes)):
        duration = early[task.id].finish - early[task.id].start
        latest_start = latest_finish[task.id] - duration
        for name in task.requires:
            if name in names:
                latest_finish[name] = min(latest_finish[name], latest_start)
    return {task.id: latest_finish[task.id] - early[task.id].finish for task in nodes}


def _chains(members: Sequence[Task], early: Mapping[str, _Early]) -> tuple[Chain, ...]:
    inside = {task.id for task in members}
    last: dict[int, Task] = {}
    for task in members:
        best = last.get(task.data_id)
        if best is None or early[task.id].finish > early[best.id].finish:
            last[task.data_id] = task
    chains: list[Chain] = []
    for data_id, task in last.items():
        path: list[str] = []
        name: str | None = task.id
        while name is not None and name in inside:
            path.append(name)
            name = early[name].previous
        chains.append(
            Chain(data_id, tuple(reversed(path)), early[task.id].finish, early[task.id].wait_s)
        )
    chains.sort(key=lambda chain: (-chain.finish, chain.data_id))
    return tuple(chains[:MAX_CHAINS])


def _empty() -> CriticalPath:
    return CriticalPath(
        target=None,
        work_s={},
        work_finish={},
        set_finish=None,
        finish_after=None,
        finish_parallel=None,
        bound=None,
        critical_lanes=(),
        float_s={},
        chains=(),
    )


def unknown_lanes(problem: Problem, lanes: Iterable[str]) -> tuple[str, ...]:
    """The names of `lanes` the problem does not know, sorted.

    A lane may hold tasks before the village owns its building: it is known all the same.
    """
    known = set(problem.lanes) | {task.lane for task in problem.tasks}
    return tuple(sorted(set(lanes) - known))


def critical_path(problem: Problem, skip_lanes: Iterable[str] = ()) -> CriticalPath:
    """The critical path towards the town hall task of smallest level (see the module text)."""
    town_halls = [task for task in problem.tasks if TOWN_HALL_TAG in task.tags]
    if not town_halls:
        return _empty()
    target = min(town_halls, key=lambda task: task.to_level)
    early = _forward(problem)
    current = frozenset(current_level_ids(problem.tasks, skip_lanes))
    members = [task for task in problem.tasks if task.id in current]

    work = _work(problem, members, early)
    work_finish = _work_finish(problem, work)
    chain_end = max((early[task.id].finish for task in members), default=problem.now)
    work_end = max(work_finish.values(), default=problem.now)
    set_finish = max(chain_end, work_end)
    if work_end > chain_end:
        lane = min(lane for lane, end in work_finish.items() if end == work_end)
        bound = Bound("work", lane)
    else:
        bound = Bound("chain", None)
    critical_lanes = tuple(lane for lane, end in work_finish.items() if end >= set_finish)

    own = early[target.id]
    after_start = _Timer(problem).first_start(target, max(set_finish, own.start))
    finish_after = after_start + effective_duration(problem, target, after_start)
    finish_parallel = max(set_finish, own.finish)

    floats = _floats(members, target, early, finish_parallel)
    for task in members:
        if task.lane in critical_lanes:
            floats[task.id] = 0
    return CriticalPath(
        target=target,
        work_s=work,
        work_finish=work_finish,
        set_finish=set_finish,
        finish_after=finish_after,
        finish_parallel=finish_parallel,
        bound=bound,
        critical_lanes=critical_lanes,
        float_s=floats,
        chains=_chains(members, early),
    )


@dataclass(frozen=True)
class StartFigures:
    """What a start weighs on the date of the next town hall."""

    float_s: int | None  # None: no town hall target, or a task beyond the current level
    critical: bool
    date_gain_s: int  # duration saved by the modifiers at that start, 0 unless critical


def start_figures(problem: Problem, path: CriticalPath, task_id: str, start: int) -> StartFigures:
    """The figures of `task_id` started at `start`; `path` is `critical_path(problem)`."""
    slack = path.float_s.get(task_id)
    if slack is None or slack > CRITICAL_FLOAT_S:
        return StartFigures(slack, False, 0)
    task = next(task for task in problem.tasks if task.id == task_id)
    saved = task.duration_s - effective_duration(problem, task, start)
    return StartFigures(slack, True, max(0, saved))


@dataclass(frozen=True)
class ChainStopped:
    """Nothing of a critical chain runs, and `task`, its next upgrade, is ready but waits."""

    task: Task
    planned_start: int | None  # None: the plan does not hold the task
    delay_s: int | None


@dataclass(frozen=True)
class LaneStopped:
    """Nothing runs on a critical lane that has work waiting."""

    lane: str
    planned_start: int | None  # None: the plan starts nothing more on the lane
    delay_s: int | None


def stopped_work(
    problem: Problem, path: CriticalPath, assignments: Sequence[Assignment], now: int
) -> tuple[ChainStopped | LaneStopped, ...]:
    """The critical chains and lanes standing idle at `now`, chains first.

    `assignments` are those of the plan, running jobs included; `path` is `critical_path(problem)`.
    The plan is taken as followed: an assignment runs over `[start, end)` and is finished at its
    end. A newer export prevails over it: a running job of the problem counts even if the plan
    starts it later, and an assignment found neither in the tasks nor in the running jobs is done.

    - A chain is critical when its last task is. Its next task is the first one not finished; it is
      reported when nothing of the chain runs, its requirements are finished, and the plan starts
      it more than `STOPPED_AFTER_S` after `now`, or not at all.
    - A critical lane with nothing running is reported when its next start is that far away or,
      without any further start, when one of its tasks is ready.
    """
    tasks = {task.id: task for task in problem.tasks}
    outstanding = set(tasks) | {job.task_id for job in problem.frozen}
    kept = [a for a in assignments if a.task_id in outstanding]
    jobs = [(a.task_id, a.lane, a.start, a.end) for a in kept]
    jobs += [(job.task_id, job.lane, job.start, job.end) for job in problem.frozen]
    running = [(name, lane) for name, lane, start, end in jobs if start <= now < end]
    busy_tasks = {name for name, _ in running}
    busy_lanes = {lane for _, lane in running}
    # A job of the problem comes last: its end prevails over the one of the plan.
    ends = {name: end for name, _, _, end in jobs}
    planned = {a.task_id: a.start for a in kept}

    def finished(name: str) -> bool:
        if name not in outstanding:
            return True  # a requirement found nowhere is already met
        return name in ends and ends[name] <= now

    def ready(task: Task) -> bool:
        return all(finished(name) for name in task.requires)

    def far(start: int | None) -> bool:
        return start is None or start - now > STOPPED_AFTER_S

    def delay(start: int | None) -> int | None:
        return None if start is None else start - now

    stopped: list[ChainStopped | LaneStopped] = []
    for chain in path.chains:
        if path.float_s[chain.task_ids[-1]] > CRITICAL_FLOAT_S:
            continue
        if busy_tasks.intersection(chain.task_ids):
            continue
        following = next((name for name in chain.task_ids if not finished(name)), None)
        if following is None:
            continue
        start = planned.get(following)
        if ready(tasks[following]) and far(start):
            stopped.append(ChainStopped(tasks[following], start, delay(start)))

    for lane in path.critical_lanes:
        if lane in busy_lanes:
            continue
        start = min((a.start for a in kept if a.lane == lane and a.start > now), default=None)
        waiting = start is not None or any(
            task.lane == lane and not finished(task.id) and ready(task) for task in problem.tasks
        )
        if waiting and far(start):
            stopped.append(LaneStopped(lane, start, delay(start)))
    return tuple(stopped)


def prioritize_critical(problem: Problem, skip_lanes: Iterable[str] = ()) -> Problem:
    """The problem with each task's value raised by its criticality (plan option `critical_path`).

    Only `Task.value` changes, and only for the tasks `critical_path(problem, skip_lanes)` gives a
    float to: the tasks of `skip_lanes` (plan option `town_hall_skip_lanes`) are never raised.
    With `r` the largest `value / max(1, duration_s)` of the problem, `T` the sum of the values
    and `f` the float of a task in seconds:

    - every task with a float is multiplied by `1 + DAY / (DAY + f)`: twice its value without
      float, almost unchanged with weeks of float, so a smaller float always weighs more;
    - a critical task (`f <= CRITICAL_FLOAT_S`) also gets `2 * r * max(1, duration_s) + T`.

    Its ratio is then above `2 * r`, while a task with more float stays strictly below `2 * r`:
    a task without float dominates every task with float under the value/duration rule of the
    base policy. The `T` term makes each critical task worth more than all the other values
    together, so the simulated score of `rollout` also prefers delaying anything else. Engines
    hold no logic of their own for it. A problem without town hall task, or whose values are all
    zero, comes back unchanged.
    """
    floats = critical_path(problem, skip_lanes).float_s
    total = sum(task.value for task in problem.tasks)
    if not floats or total <= 0:
        return problem
    top_ratio = max(task.value / max(1, task.duration_s) for task in problem.tasks)

    def raised(task: Task) -> Task:
        slack = floats.get(task.id)
        if slack is None:
            return task
        value = task.value * (1 + _DAY_S / (_DAY_S + slack))
        if slack <= CRITICAL_FLOAT_S:
            value += 2 * top_ratio * max(1, task.duration_s) + total
        return replace(task, value=value)

    return replace(problem, tasks=tuple(raised(task) for task in problem.tasks))
