"""Small hand-built planning problems and the planner configuration of the tests (SPEC section 7).

`make_problem(**overrides)` returns a valid `Problem` of four fictional tasks, any field of which a
test replaces by keyword. `TEST_CONFIG` is the configuration every non-slow test runs with.
`generate_problem(n_tasks, seed=...)` builds a larger fictional instance from a fixed seed; the
slow benchmarks use it with 300 and 400 tasks, the other tests with at most 40.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from typing import Any

from clashpilot.planning.model import Policy, Problem, ResourceModel, Task
from clashpilot.planning.registry import PlannerConfig
from clashpilot.schedule.expand import AvailabilityCalendar

TEST_CONFIG = PlannerConfig(
    profile="repro",
    seed=42,
    max_rollouts=200,
    solver_window_days=3,
    solver_max_deterministic_time=3.0,
)

HOUR = 3600
DAY = 24 * HOUR
NOW = 1_781_078_400  # 2026-06-10T08:00:00Z


def make_task(
    task_id: str,
    *,
    lane: str = "builder",
    duration_s: int = HOUR,
    cost: Mapping[str, int] | None = None,
    requires: Iterable[str] = (),
    value: float | None = None,
    category: str = "defense",
    tags: Iterable[str] = ("building",),
) -> Task:
    """A task named `"{data_id}:{instance}:{to_level}"`; `value` defaults to its hours."""
    data_id, instance, to_level = (int(part) for part in task_id.split(":"))
    return Task(
        id=task_id,
        data_id=data_id,
        instance=instance,
        to_level=to_level,
        lane=lane,
        duration_s=duration_s,
        cost=dict(cost if cost is not None else {"gold": 1000}),
        requires=tuple(requires),
        value=duration_s / HOUR if value is None else value,
        category=category,
        tags=tuple(tags),
    )


def make_calendar(
    *,
    tz: str = "UTC",
    sleep: Iterable[tuple[int, int]] = (),
    work: Iterable[tuple[int, int]] = (),
    absence: Iterable[tuple[int, int]] = (),
    available: Iterable[tuple[int, int]] = (),
    preferred: Iterable[tuple[int, int]] = (),
) -> AvailabilityCalendar:
    return AvailabilityCalendar(
        tz=tz,
        sleep=sorted(sleep),
        work=sorted(work),
        absence=sorted(absence),
        available=sorted(available),
        preferred=sorted(preferred),
    )


def default_tasks() -> tuple[Task, ...]:
    """Two chained builder upgrades, one more builder upgrade and one research."""
    return (
        make_task("1001:0:2", duration_s=2 * HOUR, cost={"gold": 2000}),
        make_task("1001:0:3", duration_s=4 * HOUR, cost={"gold": 4000}, requires=("1001:0:2",)),
        make_task("1002:0:2", duration_s=HOUR, cost={"gold": 1000}),
        make_task(
            "1003:0:2",
            lane="lab",
            duration_s=3 * HOUR,
            cost={"elixir": 3000},
            category="troop",
            tags=("troop",),
        ),
    )


def make_problem(**overrides: Any) -> Problem:
    """A small valid problem over seven days; keyword arguments replace its fields."""
    fields: dict[str, Any] = {
        "now": NOW,
        "horizon_end": NOW + 7 * DAY,
        "lanes": {"builder": 2, "lab": 1},
        "tasks": default_tasks(),
        "frozen": (),
        "resources": ResourceModel(
            initial={"gold": 5000, "elixir": 5000},
            cap={"gold": 20_000, "elixir": 20_000},
            income_per_hour={"gold": 1000, "elixir": 1000},
        ),
        "calendar": make_calendar(),
        "policy": Policy(mode="progress"),
    }
    fields.update(overrides)
    return Problem(**fields)


def nightly_sleep(days: int, *, start: int = NOW) -> list[tuple[int, int]]:
    """One sleep window per day, from 23:00 to 07:00 UTC; `start` is 08:00 UTC."""
    return [(start + 15 * HOUR + day * DAY, start + 23 * HOUR + day * DAY) for day in range(days)]


def generate_problem(
    n_tasks: int,
    *,
    seed: int = 42,
    builders: int = 2,
    days: int = 90,
    levels: int = 2,
) -> Problem:
    """A fictional instance of `n_tasks` tasks in chains of `levels` upgrades, from a fixed seed.

    One chain in five lives in the lab. Durations go from 30 min to 8 h, costs from 500 to 5000,
    values are the hours scaled by a factor in [0.5, 2]. Nights every day, policy `progress`,
    plentiful income so that every task is fundable within the horizon.
    """
    rng = random.Random(seed)
    tasks: list[Task] = []
    chain = 0
    while len(tasks) < n_tasks:
        data_id = 1000 + chain
        lane = "lab" if chain % 5 == 0 else "builder"
        resource = "elixir" if lane == "lab" else "gold"
        previous: str | None = None
        for to_level in range(2, 2 + levels):
            if len(tasks) >= n_tasks:
                break
            name = f"{data_id}:0:{to_level}"
            duration_s = 300 * rng.randint(6, 96)
            tasks.append(
                make_task(
                    name,
                    lane=lane,
                    duration_s=duration_s,
                    cost={resource: 100 * rng.randint(5, 50)},
                    requires=() if previous is None else (previous,),
                    value=round(duration_s / HOUR * rng.uniform(0.5, 2.0), 3),
                    category="troop" if lane == "lab" else "defense",
                    tags=("troop",) if lane == "lab" else ("building",),
                )
            )
            previous = name
        chain += 1
    return make_problem(
        horizon_end=NOW + days * DAY,
        lanes={"builder": builders, "lab": 1},
        tasks=tuple(tasks),
        resources=ResourceModel(
            initial={"gold": 20_000, "elixir": 20_000},
            cap={"gold": 10**6, "elixir": 10**6},
            income_per_hour={"gold": 5000, "elixir": 5000},
        ),
        calendar=make_calendar(sleep=nightly_sleep(days + 1)),
        policy=Policy(mode="progress"),
    )
