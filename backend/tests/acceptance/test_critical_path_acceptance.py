"""Acceptance tests for E22: the pure critical path module of the town hall.

SPEC (critical path backlog): architecture `planning/critical.py`, ADR-CP1 (chains without capacity
nor resources, a work bound per lane). Frozen with the task.

Shapes that the task text leaves open are fixed here:

- `clashpilot.planning.critical.critical_path(problem, skip_lanes=())` returns a `CriticalPath`, a
  frozen dataclass (like `planning/model.py`). Its fields read by these tests:
  - `target`: the town hall task of smallest `to_level` (the `Task` itself or its id), or None;
  - `work_finish`: a mapping lane -> epoch, one entry per lane with at least one task of the
    current set;
  - `set_finish`, `finish_after`, `finish_parallel`: epochs, or None without a town hall task;
  - `bound`: `{"kind": "chain" | "work", "lane": str | None}`, as a mapping or as an object with
    the attributes `kind` and `lane`;
  - `critical_lanes`: a sequence of lane names;
  - `float_s`: a mapping task id -> seconds, for the tasks of the current set and the target;
  - `chains`: a sequence of at most 3 chains, longest paths of the current set grouped by
    `data_id`, sorted by decreasing finish. A chain holds `data_id`, `task_ids` (the ids of its
    tasks in order, frozen jobs may be listed first), `finish` (epoch of its end) and
    `calendar_wait_s` (seconds lost waiting for the calendar along it).
- `current_level_ids(tasks, skip_lanes=())` returns the ids (or the tasks) of the current set: the
  tasks that depend on no town hall, minus those of the skipped lanes. It is importable from
  `clashpilot.planning.critical` (or from `clashpilot.planning.compile`, where it may live), and
  `_town_hall_last` of `planning/strategies.py` calls it by that name.
- The module is pure: it imports no I/O or clock module and calls no clock.

Fictional data only (ADR-2). The feature imports are local to the helpers below, so that each
guarantee fails on its own for as long as the feature does not exist.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import importlib
import inspect
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

TESTS = Path(__file__).resolve().parents[1]
BACKEND = TESTS.parent
CRITICAL_SOURCE = BACKEND / "src" / "clashpilot" / "planning" / "critical.py"

HOUR = 3600
DAY = 24 * HOUR

H1, H2, H3 = "2001:0:2", "2001:0:3", "2001:0:4"
SHORT = ("3001:0:2", "3002:0:2", "3003:0:2")
TOWN_HALL = "1:0:10"
RESEARCH = ("4001:0:2", "4002:0:2", "4003:0:2")


# --------------------------------------------------------------------------- feature access


def support(name: str) -> ModuleType:
    """`backend/tests/support/<name>.py`, imported as `support.<name>`."""
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    return importlib.import_module(f"support.{name}")


def critical() -> ModuleType:
    return importlib.import_module("clashpilot.planning.critical")


def critical_path(problem: Any, **kwargs: Any) -> Any:
    return critical().critical_path(problem, **kwargs)


def current_level_ids(tasks: Sequence[Any], *args: Any) -> set[str]:
    function = None
    for module_name in ("clashpilot.planning.critical", "clashpilot.planning.compile"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        function = getattr(module, "current_level_ids", None)
        if function is not None:
            break
    assert function is not None, "current_level_ids is missing"
    return {ident(item) for item in function(tasks, *args)}


def ident(item: Any) -> str:
    return item if isinstance(item, str) else str(item.id)


def bound_of(result: Any) -> dict[str, Any]:
    bound = result.bound
    if isinstance(bound, Mapping):
        return {"kind": bound["kind"], "lane": bound["lane"]}
    return {"kind": bound.kind, "lane": bound.lane}


def chain_with(result: Any, task: str) -> Any:
    found = [chain for chain in result.chains if task in list(chain.task_ids)]
    assert found, f"no chain holds {task}: {result.chains!r}"
    return found[0]


# --------------------------------------------------------------------------- fixtures


def task(
    name: str,
    hours: float,
    *,
    lane: str = "builder",
    requires: Iterable[str] = (),
    tags: Iterable[str] = ("building",),
    category: str = "defense",
) -> Any:
    return support("problems").make_task(
        name,
        lane=lane,
        duration_s=int(hours * HOUR),
        requires=requires,
        tags=tags,
        category=category,
    )


def town_hall(name: str = TOWN_HALL, hours: float = 5, requires: Iterable[str] = ()) -> Any:
    return task(name, hours, requires=requires, tags=("town_hall",), category="town_hall")


def hero(name: str, hours: float, requires: Iterable[str] = ()) -> Any:
    return task(name, hours, requires=requires, tags=("hero",), category="hero")


def hero_chain() -> tuple[Any, ...]:
    return (hero(H1, 10), hero(H2, 10, (H1,)), hero(H3, 10, (H2,)))


def base_tasks() -> tuple[Any, ...]:
    """A hero chain of 3 x 10 h, three independent 2 h builder tasks and a 5 h town hall."""
    return (*hero_chain(), *(task(name, 2) for name in SHORT), town_hall())


def researches() -> tuple[Any, ...]:
    return tuple(task(name, 20, lane="lab", tags=("troop",), category="troop") for name in RESEARCH)


def problem(**overrides: Any) -> Any:
    """Always available calendar, no modifier, now = N, two builders."""
    problems = support("problems")
    fields: dict[str, Any] = {
        "horizon_end": problems.NOW + 30 * DAY,
        "lanes": {"builder": 2},
        "tasks": base_tasks(),
        "frozen": (),
        "calendar": problems.make_calendar(),
    }
    fields.update(overrides)
    return problems.make_problem(**fields)


def saturated(**overrides: Any) -> Any:
    """The base example plus a lab of capacity 1 holding three independent 20 h researches."""
    return problem(
        lanes={"builder": 2, "lab": 1}, tasks=(*base_tasks(), *researches()), **overrides
    )


def n() -> int:
    return int(support("problems").NOW)


# --------------------------------------------------------------------------- target


def test_target_is_the_town_hall_task_of_smallest_level() -> None:
    tasks = (
        town_hall("1:0:11", requires=(TOWN_HALL,)),
        *base_tasks(),
    )
    result = critical_path(problem(tasks=tasks))
    assert ident(result.target) == TOWN_HALL


def test_without_town_hall_every_date_is_none_and_nothing_raises() -> None:
    problems = support("problems")
    for tasks in (problems.default_tasks(), base_tasks()[:-1], ()):
        result = critical_path(problem(tasks=tasks, lanes={"builder": 2, "lab": 1}))
        assert result.target is None
        assert result.set_finish is None
        assert result.finish_after is None
        assert result.finish_parallel is None
        assert all(value is None for value in result.work_finish.values())


def test_critical_path_is_a_frozen_dataclass() -> None:
    result = critical_path(problem())
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.target = None


# --------------------------------------------------------------------------- current set


def strategy_tasks() -> tuple[Any, ...]:
    """Two town halls, tasks of the current level on two lanes, tasks after a town hall."""
    return (
        town_hall("1:0:11", requires=(TOWN_HALL,)),
        hero("2001:0:2", 10),
        hero("2001:0:3", 10, ("2001:0:2",)),
        town_hall(TOWN_HALL, requires=("3001:0:2",)),
        task("3001:0:2", 2),
        task("4001:0:2", 20, lane="lab", tags=("troop",), category="troop"),
        task("5001:0:5", 3, requires=(TOWN_HALL,)),
        task("5001:0:6", 3, requires=("5001:0:5",)),
        task("6001:0:2", 4, lane="lab", requires=("1:0:11",), tags=("troop",), category="troop"),
    )


CURRENT = {"2001:0:2", "2001:0:3", "3001:0:2", "4001:0:2"}


def test_current_level_ids_is_the_set_the_town_hall_last_rule_requires() -> None:
    tasks = strategy_tasks()
    assert current_level_ids(tasks) == CURRENT
    assert current_level_ids(tasks, ()) == CURRENT
    without_lab = current_level_ids(tasks, ("lab",))
    lab = {item.id for item in tasks if item.lane == "lab"}
    assert without_lab == CURRENT - lab
    assert not without_lab & lab


def test_town_hall_last_goes_through_current_level_ids() -> None:
    strategies = importlib.import_module("clashpilot.planning.strategies")
    assert "current_level_ids(" in inspect.getsource(strategies._town_hall_last)


def test_apply_strategy_finish_th_is_unchanged() -> None:
    from clashpilot.planning.strategies import apply_strategy, load_strategies

    tasks = strategy_tasks()
    applied, _ = apply_strategy(
        problem(tasks=tasks, lanes={"builder": 2, "lab": 1}), load_strategies()["finish_th"]
    )
    expected = {
        "1:0:11": (TOWN_HALL, "2001:0:2", "2001:0:3", "3001:0:2", "4001:0:2"),
        "2001:0:2": (),
        "2001:0:3": ("2001:0:2",),
        TOWN_HALL: ("3001:0:2", "2001:0:2", "2001:0:3", "4001:0:2"),
        "3001:0:2": (),
        "4001:0:2": (),
        "5001:0:5": (TOWN_HALL,),
        "5001:0:6": ("5001:0:5",),
        "6001:0:2": ("1:0:11",),
    }
    assert {item.id: tuple(item.requires) for item in applied.tasks} == expected
    assert [item.id for item in applied.tasks] == [item.id for item in tasks]
    for before, after in zip(tasks, applied.tasks, strict=True):
        assert dataclasses.replace(after, requires=before.requires) == before


def test_critical_path_set_is_current_level_ids() -> None:
    tasks = strategy_tasks()
    result = critical_path(problem(tasks=tasks, lanes={"builder": 2, "lab": 1}))
    assert set(result.float_s) - {TOWN_HALL} == CURRENT


# --------------------------------------------------------------------------- base example


def test_chain_bound_example_dates() -> None:
    start = n()
    result = critical_path(problem())
    assert ident(result.target) == TOWN_HALL
    assert dict(result.work_finish) == {"builder": start + 18 * HOUR}
    assert result.set_finish == start + 30 * HOUR
    assert result.finish_after == start + 35 * HOUR
    assert result.finish_parallel == start + 30 * HOUR
    assert bound_of(result) == {"kind": "chain", "lane": None}
    assert list(result.critical_lanes) == []


def test_chain_bound_example_floats_against_finish_parallel() -> None:
    result = critical_path(problem())
    floats = dict(result.float_s)
    assert floats[H1] == 0
    assert floats[H2] == 0
    assert floats[H3] == 0
    for name in SHORT:
        assert floats[name] == 28 * HOUR == 100_800
    assert floats[TOWN_HALL] == 25 * HOUR == 90_000


def test_chains_are_at_most_three_grouped_by_data_id_and_sorted() -> None:
    start = n()
    chains = list(critical_path(problem()).chains)
    assert 1 <= len(chains) <= 3
    assert list(chains[0].task_ids) == [H1, H2, H3]
    assert chains[0].finish == start + 30 * HOUR
    assert chains[0].data_id == 2001
    finishes = [chain.finish for chain in chains]
    assert finishes == sorted(finishes, reverse=True)
    data_ids = [chain.data_id for chain in chains]
    assert len(set(data_ids)) == len(data_ids)


# --------------------------------------------------------------------------- saturated lane


def test_saturated_lab_fixes_the_date() -> None:
    start = n()
    result = critical_path(saturated())
    assert result.work_finish["lab"] == start + 60 * HOUR
    assert result.work_finish["builder"] == start + 18 * HOUR
    assert result.set_finish == start + 60 * HOUR
    assert result.finish_after == start + 65 * HOUR
    assert result.finish_parallel == start + 60 * HOUR
    assert bound_of(result) == {"kind": "work", "lane": "lab"}
    assert list(result.critical_lanes) == ["lab"]
    floats = dict(result.float_s)
    for name in RESEARCH:
        assert floats[name] == 0
    for name in (H1, H2, H3):
        assert floats[name] == 30 * HOUR == 108_000


def test_skip_lanes_leaves_the_lab_out() -> None:
    start = n()
    result = critical_path(saturated(), skip_lanes=("lab",))
    assert bound_of(result) == {"kind": "chain", "lane": None}
    assert list(result.critical_lanes) == []
    assert result.set_finish == start + 30 * HOUR
    assert "lab" not in result.work_finish
    assert not set(RESEARCH) & set(result.float_s)
    assert dict(result.float_s)[H1] == 0
    lab_tasks = [item for item in saturated().tasks if item.lane == "lab"]
    assert lab_tasks
    assert not current_level_ids(saturated().tasks, ("lab",)) & {item.id for item in lab_tasks}


# --------------------------------------------------------------------------- work bound


def test_work_finish_counts_remaining_frozen_time_and_rounds_up() -> None:
    from clashpilot.planning.model import FrozenJob

    start = n()
    tasks = (task("3001:0:2", 3), task("3002:0:2", 2 + 1 / HOUR), town_hall())
    frozen = (
        FrozenJob("7001:0:2", "builder", 0, start - HOUR, start + 4 * HOUR),
        FrozenJob("7002:0:2", "builder", 1, start - HOUR, start + 10 * HOUR, extra=True),
        FrozenJob("7003:0:2", "lab", 0, start - HOUR, start + 6 * HOUR),
    )
    result = critical_path(problem(tasks=tasks, frozen=frozen, lanes={"builder": 2, "lab": 1}))
    # (3 h + 2 h 1 s + 4 h left of the running job) / 2 builders, rounded up; the extra job
    # takes no slot; the lab holds no task of the set, so it has no entry.
    assert dict(result.work_finish) == {"builder": start + 16_201}


def test_frozen_running_job_delays_its_successor() -> None:
    from clashpilot.planning.model import FrozenJob

    start = n()
    tasks = (hero(H2, 10, (H1,)), town_hall())
    frozen = (FrozenJob(H1, "builder", 0, start - 3 * HOUR, start + 7 * HOUR),)
    result = critical_path(problem(tasks=tasks, frozen=frozen))
    assert chain_with(result, H2).finish == start + 17 * HOUR
    assert result.set_finish == start + 17 * HOUR
    assert dict(result.float_s)[H2] == 0


# --------------------------------------------------------------------------- simulate's rules


def test_durations_follow_effective_duration_at_the_start_instant() -> None:
    from clashpilot.planning.model import Modifier

    start = n()
    modifier = Modifier(
        start=start, end=start + 6 * HOUR, lane="builder", duration_pct=50, cost_pct=100
    )
    result = critical_path(problem(modifiers=(modifier,)))
    # H1 [N, N+5 h), H2 starts at N+5 h inside the window [N+5 h, N+10 h), H3 starts at N+10 h
    # outside it [N+10 h, N+20 h).
    assert chain_with(result, H3).finish == start + 20 * HOUR
    assert result.finish_parallel == start + 20 * HOUR
    assert result.set_finish == start + 20 * HOUR


def paris(day: datetime.date, hour: int) -> int:
    moment = datetime.datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo("Europe/Paris"))
    return int(moment.timestamp())


def test_calendar_rules_are_those_of_simulate() -> None:
    from clashpilot.planning.model import Policy

    problems = support("problems")
    first = datetime.date(2026, 6, 10)
    sleep = [
        (
            paris(first + datetime.timedelta(days=k), 1),
            paris(first + datetime.timedelta(days=k), 10),
        )
        for k in range(-1, 10)
    ]
    now = paris(first, 20)
    tasks = (hero("2001:0:2", 10), town_hall(hours=1))
    result = critical_path(
        problem(
            now=now,
            horizon_end=now + 20 * DAY,
            tasks=tasks,
            calendar=problems.make_calendar(tz="Europe/Paris", sleep=sleep),
            policy=Policy(mode="strict"),
        )
    )
    chain = chain_with(result, "2001:0:2")
    # Started at 20:00 it would end at 06:00, asleep: it starts at 00:00 and ends at 10:00.
    assert chain.finish == paris(first + datetime.timedelta(days=1), 10)
    assert chain.calendar_wait_s == 4 * HOUR == 14_400
    assert result.set_finish == chain.finish


# --------------------------------------------------------------------------- purity


FORBIDDEN_MODULES = {
    "os",
    "io",
    "pathlib",
    "time",
    "sqlite3",
    "socket",
    "subprocess",
    "random",
    "httpx",
    "urllib",
    "shutil",
    "tempfile",
    "requests",
}
FORBIDDEN_CALLS = {"open", "now", "today", "utcnow", "time", "perf_counter", "monotonic"}


def test_module_is_pure() -> None:
    critical()
    tree = ast.parse(CRITICAL_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FORBIDDEN_MODULES, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in FORBIDDEN_MODULES, node.module
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in FORBIDDEN_CALLS, f"call to {name} at line {node.lineno}"


def test_same_input_same_result() -> None:
    assert critical_path(saturated()) == critical_path(saturated())
    assert critical_path(saturated(), skip_lanes=("lab",)) == critical_path(
        saturated(), skip_lanes=("lab",)
    )


def test_answers_within_two_seconds_on_400_tasks() -> None:
    generated = support("instances").generate_problem(400, seed=1)
    with_town_hall = dataclasses.replace(
        generated, tasks=(*generated.tasks, town_hall("1:0:10", hours=8))
    )
    for instance in (generated, with_town_hall):
        began = time.perf_counter()
        result = critical_path(instance)
        assert time.perf_counter() - began < 2.0
    assert ident(result.target) == "1:0:10"
    assert result.set_finish is not None
    assert critical_path(with_town_hall) == result
