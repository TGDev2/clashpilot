"""Seeded fictional planning instances for the engine tests (SPEC section 7).

`generate_problem(n_tasks, seed=...)` builds a larger instance from a fixed seed: the slow
benchmarks use it with 300 and 400 tasks, the other tests with at most 40. Kept apart from
`problems.py`, which the frozen acceptance tests of E07 and E08 load as it was at their exam.
"""

from __future__ import annotations

import random

from clashpilot.planning.model import Policy, Problem, ResourceModel, Task
from support.problems import DAY, HOUR, NOW, make_calendar, make_problem, make_task


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
