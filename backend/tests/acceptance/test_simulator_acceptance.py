"""Acceptance tests for E07: planning model, exact simulator and compilation (SPEC 4, ADR-4).

Frozen with the task. They describe `clashpilot.planning.model`, `clashpilot.planning.simulate`,
`clashpilot.planning.compile`, `clashpilot.planning.objective`, `clashpilot.planning.registry` and
the test helper module `backend/tests/support/problems.py`.

Shapes that the task text leaves open are fixed here:

- Model objects are built with keyword arguments only, with the field names of SPEC section 4.
  `ExcludedTask` lives in `clashpilot.planning.model` and exposes at least `data_id`, `to_level`
  and `reason`.
- `simulate(problem, plan)` returns an object with `valid`, `violations`, `metrics` and
  `stock_timeline`; `valid` is true exactly when there is no violation. Each violation exposes its
  code as the attribute `code` (a mapping with the key "code" is accepted too).
- `metrics` is a mapping or an object holding `tasks_completed` (number of planned tasks ending at
  or before `horizon_end`) and `spill` (the lost excess in whole resource units: one number for
  all resources, or a mapping resource -> amount).
- `stock_timeline` is a sequence of `(t, stocks)` pairs ordered by `t`; `stocks` maps each resource
  to its integer stock in units x 3600 at `t`, after the income and the payments at `t`. It holds a
  point at `horizon_end`.
- `effective_duration(problem, task, start)` returns an int and `effective_cost(problem, task,
  start)` a mapping resource -> int.
- `MissingInputError` is importable from `clashpilot.planning.compile`; its attribute `missing` is a
  collection of field names (`schedule_profile`, `builders_total` or a resource name).
- `compile_problem` gets its first six arguments positionally, in SPEC order, and `target_th` and
  `max_tasks` by keyword. The lane of the builders is `Problem.lanes["builder"]`.
- `PLANNERS` values are called as `solve(problem, objective, config, reference=None)`, and
  `Objective()` builds with its defaults.
- `backend/tests/support/problems.py` is loaded here from its path, whatever makes it importable
  for the other test files.

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the modules do not exist.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

HOUR = 3600
DAY = 24 * HOUR
UNLIMITED = 10**12


def utc(text: str) -> int:
    """Epoch of an ISO-8601 UTC instant such as `2026-06-10T08:00Z`."""
    return int(datetime.datetime.fromisoformat(text).timestamp())


def paris(local: str) -> int:
    """Epoch of a local date-time in Europe/Paris."""
    naive = datetime.datetime.fromisoformat(local)
    return int(naive.replace(tzinfo=ZoneInfo("Europe/Paris")).timestamp())


NOW = utc("2026-06-10T08:00Z")  # 10:00 in Paris

SUPPORT_PROBLEMS = Path(__file__).resolve().parents[1] / "support" / "problems.py"


# --------------------------------------------------------------------------- feature modules


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def simulator() -> ModuleType:
    import clashpilot.planning.simulate as module

    return module


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e07_acceptance_support_problems"
    if name in sys.modules:
        return sys.modules[name]
    assert SUPPORT_PROBLEMS.is_file(), f"{SUPPORT_PROBLEMS} is missing"
    spec = importlib.util.spec_from_file_location(name, SUPPORT_PROBLEMS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses defined there look their module up
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


# --------------------------------------------------------------------------- hand-built problems


def task(
    task_id: str,
    *,
    lane: str = "builder",
    duration_s: int = 600,
    cost: Mapping[str, int] | None = None,
    requires: Iterable[str] = (),
) -> Any:
    data_id, instance, to_level = (int(part) for part in task_id.split(":"))
    return model().Task(
        id=task_id,
        data_id=data_id,
        instance=instance,
        to_level=to_level,
        lane=lane,
        duration_s=duration_s,
        cost=dict(cost or {}),
        requires=tuple(requires),
        value=duration_s / HOUR,
        category="defense",
        tags=("building",),
    )


def frozen_job(
    task_id: str, start: int, end: int, *, lane: str = "builder", slot: int = 0, extra: bool = False
) -> Any:
    return model().FrozenJob(
        task_id=task_id, lane=lane, lane_slot=slot, start=start, end=end, extra=extra
    )


def calendar(
    *,
    tz: str = "UTC",
    sleep: Sequence[tuple[int, int]] = (),
    work: Sequence[tuple[int, int]] = (),
    absence: Sequence[tuple[int, int]] = (),
    available: Sequence[tuple[int, int]] = (),
    preferred: Sequence[tuple[int, int]] = (),
) -> Any:
    from clashpilot.schedule.expand import AvailabilityCalendar

    return AvailabilityCalendar(
        tz=tz,
        sleep=list(sleep),
        work=list(work),
        absence=list(absence),
        available=list(available),
        preferred=list(preferred),
    )


def policy(mode: str, **options: Any) -> Any:
    return model().Policy(mode=mode, **options)


def problem(
    *,
    tasks: Iterable[Any] = (),
    frozen: Iterable[Any] = (),
    lanes: Mapping[str, int] | None = None,
    initial: Mapping[str, int] | None = None,
    cap: Mapping[str, int] | None = None,
    income: Mapping[str, int] | None = None,
    cal: Any = None,
    rules: Any = None,
    modifiers: Iterable[Any] = (),
    horizon_end: int = NOW + 2 * DAY,
) -> Any:
    """A problem where everything is allowed unless the test says otherwise."""
    m = model()
    names = ("gold", "elixir")
    return m.Problem(
        now=NOW,
        horizon_end=horizon_end,
        lanes=dict(lanes or {"builder": 1, "lab": 1}),
        tasks=tuple(tasks),
        frozen=tuple(frozen),
        resources=m.ResourceModel(
            initial={name: 0 for name in names} | dict(initial or {}),
            cap={name: UNLIMITED for name in names} | dict(cap or {}),
            income_per_hour={name: 0 for name in names} | dict(income or {}),
        ),
        calendar=cal if cal is not None else calendar(),
        policy=rules if rules is not None else policy("progress"),
        modifiers=tuple(modifiers),
    )


def at(task_id: str, start: int, duration: int, *, lane: str = "builder", slot: int = 0) -> Any:
    return model().Assignment(
        task_id=task_id, lane=lane, lane_slot=slot, start=start, end=start + duration
    )


def plan(*assignments: Any) -> Any:
    m = model()
    return m.Plan(
        assignments=tuple(assignments),
        engine="test",
        proof=m.Proof(kind="heuristic", objective=0.0),
    )


def run(prob: Any, chosen: Any) -> Any:
    return simulator().simulate(prob, chosen)


# --------------------------------------------------------------------------- reading results


def code_of(violation: Any) -> str:
    if isinstance(violation, Mapping):
        return str(violation["code"])
    return str(violation.code)


def codes(result: Any) -> set[str]:
    return {code_of(violation) for violation in result.violations}


def assert_violates(result: Any, code: str) -> None:
    found = codes(result)
    assert code in found, f"expected {code}, got {sorted(found)}"
    assert not result.valid


def assert_clean(result: Any) -> None:
    assert codes(result) == set()
    assert result.valid


def metric(result: Any, name: str) -> Any:
    metrics = result.metrics
    return metrics[name] if isinstance(metrics, Mapping) else getattr(metrics, name)


def total_spill(result: Any) -> Any:
    spill = metric(result, "spill")
    return sum(spill.values()) if isinstance(spill, Mapping) else spill


def stock_at(result: Any, t: int, resource: str) -> Any:
    points = [stocks for when, stocks in result.stock_timeline if when == t]
    assert points, f"stock_timeline has no point at {t}"
    return points[-1][resource]


# --------------------------------------------------------------------------- model


REQUIRED_FIELDS: dict[str, set[str]] = {
    "Task": {
        "id",
        "data_id",
        "instance",
        "to_level",
        "lane",
        "duration_s",
        "cost",
        "requires",
        "value",
        "category",
        "tags",
    },
    "FrozenJob": {"task_id", "lane", "lane_slot", "start", "end", "extra"},
    "ResourceModel": {"initial", "cap", "income_per_hour"},
    "Policy": {"mode", "tolerance_s", "max_logins_per_day", "login_merge_s"},
    "Goal": {"task_ids"},
    "Modifier": {"start", "end", "lane", "duration_pct", "cost_pct"},
    "Problem": {
        "now",
        "horizon_end",
        "lanes",
        "tasks",
        "frozen",
        "resources",
        "calendar",
        "policy",
        "modifiers",
        "excluded",
        "goal",
        "hero_blackouts",
    },
    "ExcludedTask": {"data_id", "to_level", "reason"},
    "Assignment": {"task_id", "lane", "lane_slot", "start", "end"},
    "Proof": {"kind", "objective", "bound", "gap_abs", "scope"},
    "Candidate": {"task_id", "start", "score"},
    "Decision": {"at", "candidates", "chosen"},
    "Plan": {"assignments", "engine", "proof", "trace", "warnings", "metadata"},
}


def test_model_exposes_frozen_dataclasses_with_at_least_the_spec_fields() -> None:
    m = model()
    for name, required in REQUIRED_FIELDS.items():
        cls = getattr(m, name)
        names = {field.name for field in dataclasses.fields(cls)}
        assert required <= names, f"{name} lacks {sorted(required - names)}"
        assert cls.__dataclass_params__.frozen, f"{name} is not frozen"
    assert m.INVALID_SCORE == -1e18


def test_model_defaults_follow_the_spec() -> None:
    m = model()
    empty = plan()
    assert empty.warnings == ()
    assert empty.trace == ()
    assert dict(empty.metadata) == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        empty.engine = "other"

    bare = m.Problem(
        now=NOW,
        horizon_end=NOW + DAY,
        lanes={"builder": 1},
        tasks=(),
        frozen=(),
        resources=m.ResourceModel(initial={}, cap={}, income_per_hour={}),
        calendar=calendar(),
        policy=m.Policy(mode="strict"),
    )
    assert bare.hero_blackouts == ()
    assert bare.modifiers == ()
    assert bare.excluded == ()
    assert bare.goal is None

    rules = m.Policy(mode="tolerance")
    assert (rules.tolerance_s, rules.max_logins_per_day, rules.login_merge_s) == (0, None, 900)
    proof = m.Proof(kind="heuristic", objective=1.5)
    assert (proof.bound, proof.gap_abs, proof.scope) == (None, None, "")
    assert m.FrozenJob(task_id="1:0:2", lane="builder", lane_slot=0, start=0, end=1).extra is False
    bare_task = m.Task(
        id="1:0:2",
        data_id=1,
        instance=0,
        to_level=2,
        lane="builder",
        duration_s=60,
        cost={},
        requires=(),
        value=1.0,
        category="defense",
    )
    assert bare_task.tags == ()


def test_support_module_builds_problems_and_holds_the_test_config() -> None:
    helpers = support()
    m = model()
    assert isinstance(helpers.make_problem(), m.Problem)
    custom = helpers.make_problem(now=NOW, horizon_end=NOW + DAY, lanes={"builder": 3})
    assert isinstance(custom, m.Problem)
    assert (custom.now, custom.horizon_end, dict(custom.lanes)) == (NOW, NOW + DAY, {"builder": 3})

    config = helpers.TEST_CONFIG
    assert config.profile == "repro"
    assert config.seed == 42
    assert config.max_rollouts == 200
    assert config.solver_window_days == 3
    assert config.solver_max_deterministic_time == 3.0


# --------------------------------------------------------------------------- simulator


def test_empty_plan_is_valid_and_completes_no_task() -> None:
    result = run(problem(tasks=(task("1:0:2"),)), plan())
    assert result.valid
    assert list(result.violations) == []
    assert metric(result, "tasks_completed") == 0


def test_a_task_ending_inside_the_horizon_counts_as_completed() -> None:
    result = run(problem(tasks=(task("1:0:2"),)), plan(at("1:0:2", NOW, 600)))
    assert_clean(result)
    assert metric(result, "tasks_completed") == 1


@pytest.mark.parametrize("slots", [(0, 0), (0, 1)])
def test_two_simultaneous_tasks_overflow_a_lane_of_capacity_one(slots: tuple[int, int]) -> None:
    tasks = (task("1:0:2"), task("2:0:2"))
    chosen = plan(at("1:0:2", NOW, 600, slot=slots[0]), at("2:0:2", NOW + 60, 600, slot=slots[1]))
    assert_violates(run(problem(tasks=tasks), chosen), "lane_capacity")


def test_a_lane_of_capacity_two_holds_two_simultaneous_tasks() -> None:
    tasks = (task("1:0:2"), task("2:0:2"))
    chosen = plan(at("1:0:2", NOW, 600, slot=0), at("2:0:2", NOW + 60, 600, slot=1))
    assert_clean(run(problem(tasks=tasks, lanes={"builder": 2, "lab": 1}), chosen))


@pytest.mark.parametrize(("extra", "overflow"), [(True, False), (False, True)])
def test_only_a_frozen_job_without_extra_takes_capacity(extra: bool, overflow: bool) -> None:
    running = frozen_job("9:0:2", NOW - 600, NOW + 600, extra=extra)
    prob = problem(tasks=(task("1:0:2"),), frozen=(running,))
    result = run(prob, plan(at("1:0:2", NOW, 600)))
    if overflow:
        assert_violates(result, "lane_capacity")
    else:
        assert_clean(result)


@pytest.mark.parametrize(("offset", "late"), [(599, True), (600, False)])
def test_a_task_waits_for_the_end_of_the_task_it_requires(offset: int, late: bool) -> None:
    first = task("1:0:2", duration_s=600)
    second = task("2:0:2", lane="lab", requires=(first.id,))
    chosen = plan(at(first.id, NOW, 600), at(second.id, NOW + offset, 600, lane="lab"))
    result = run(problem(tasks=(first, second)), chosen)
    if late:
        assert_violates(result, "precedence")
    else:
        assert_clean(result)


@pytest.mark.parametrize(("offset", "late"), [(300, True), (600, False)])
def test_a_task_waits_for_the_end_of_a_required_frozen_job(offset: int, late: bool) -> None:
    second = task("2:0:2", lane="lab", requires=("1:0:2",))
    prob = problem(tasks=(second,), frozen=(frozen_job("1:0:2", NOW - 100, NOW + 600),))
    result = run(prob, plan(at(second.id, NOW + offset, 600, lane="lab")))
    if late:
        assert_violates(result, "precedence")
    else:
        assert_clean(result)


GOLD: dict[str, Any] = {
    "initial": {"gold": 1000},
    "cap": {"gold": 2000},
    "income": {"gold": 3600},
}


def test_a_cost_above_the_capped_stock_is_insufficient() -> None:
    expensive = task("1:0:2", cost={"gold": 2500})
    result = run(problem(tasks=(expensive,), **GOLD), plan(at(expensive.id, NOW + 1500, 600)))
    assert_violates(result, "insufficient_funds")


def test_a_cost_within_the_stock_is_paid() -> None:
    affordable = task("1:0:2", cost={"gold": 1500})
    result = run(problem(tasks=(affordable,), **GOLD), plan(at(affordable.id, NOW + 1800, 600)))
    assert_clean(result)


def test_the_excess_over_the_cap_is_lost_and_counted() -> None:
    result = run(problem(horizon_end=NOW + HOUR, **GOLD), plan())
    assert_clean(result)
    assert total_spill(result) == 2600
    assert stock_at(result, NOW + HOUR, "gold") == 2000 * HOUR


def test_the_stock_is_kept_in_integer_units_times_3600() -> None:
    prob = problem(initial={"gold": 7}, income={"gold": 1}, horizon_end=NOW + 1800)
    stock = stock_at(run(prob, plan()), NOW + 1800, "gold")
    assert isinstance(stock, int)
    assert stock == 7 * HOUR + 1800


SLEEP = (NOW + 4 * HOUR, NOW + 12 * HOUR)


@pytest.mark.parametrize(
    ("windows", "start"),
    [
        ({"sleep": [SLEEP]}, NOW + 5 * HOUR),
        ({"work": [(NOW + HOUR, NOW + 2 * HOUR)]}, NOW + 90 * 60),
        ({"absence": [(NOW + HOUR, NOW + 2 * HOUR)]}, NOW + 90 * 60),
    ],
)
def test_a_start_outside_can_act_is_refused(windows: dict[str, Any], start: int) -> None:
    one = task("1:0:2")
    result = run(problem(tasks=(one,), cal=calendar(**windows)), plan(at(one.id, start, 600)))
    assert_violates(result, "start_outside_availability")


def single_task_result(duration: int, rules: Any) -> Any:
    """One task started at NOW + 3 h, awake, before a night from NOW + 4 h to NOW + 12 h."""
    one = task("1:0:2", duration_s=duration)
    prob = problem(tasks=(one,), cal=calendar(sleep=[SLEEP]), rules=rules)
    return run(prob, plan(at(one.id, NOW + 3 * HOUR, duration)))


def test_strict_policy_refuses_an_end_in_sleep() -> None:
    assert_violates(single_task_result(2 * HOUR, policy("strict")), "end_in_sleep")
    assert_clean(single_task_result(HOUR // 2, policy("strict")))


def test_tolerance_policy_bounds_the_wait_after_an_end() -> None:
    rules = policy("tolerance", tolerance_s=HOUR)
    # Ends at NOW + 10 h: two hours before the next instant where the player can act.
    assert_violates(single_task_result(7 * HOUR, rules), "idle_beyond_tolerance")
    # Ends at NOW + 11 h 30: thirty minutes before it.
    assert_clean(single_task_result(17 * HOUR // 2, rules))


@pytest.mark.parametrize("duration", [2 * HOUR, 7 * HOUR])
def test_progress_policy_accepts_ends_in_sleep(duration: int) -> None:
    assert_clean(single_task_result(duration, policy("progress")))


@pytest.mark.parametrize(
    ("first", "second", "exceeded"),
    [
        ("2026-06-10T11:00:00", "2026-06-10T13:00:00", True),
        ("2026-06-10T11:00:00", "2026-06-10T11:15:00", False),
        ("2026-06-10T11:00:00", "2026-06-10T11:10:00", False),
        # Two local days, although the same UTC day.
        ("2026-06-10T23:50:00", "2026-06-11T00:10:00", False),
        # One local day, although two UTC days.
        ("2026-06-11T01:50:00", "2026-06-11T02:10:00", True),
    ],
)
def test_logins_are_counted_per_local_day(first: str, second: str, exceeded: bool) -> None:
    one, two = task("1:0:2", duration_s=60), task("2:0:2", duration_s=60)
    prob = problem(
        tasks=(one, two),
        lanes={"builder": 2, "lab": 1},
        cal=calendar(tz="Europe/Paris"),
        rules=policy("progress", max_logins_per_day=1),
    )
    result = run(prob, plan(at(one.id, paris(first), 60), at(two.id, paris(second), 60, slot=1)))
    if exceeded:
        assert_violates(result, "logins_exceeded")
    else:
        assert_clean(result)


def test_an_assignment_may_not_touch_a_frozen_job() -> None:
    done = task("1:0:2")
    prob = problem(
        tasks=(done,),
        frozen=(frozen_job(done.id, NOW - 600, NOW + 600),),
        lanes={"builder": 2, "lab": 1},
    )
    assert_violates(run(prob, plan(at(done.id, NOW + 600, 600, slot=1))), "modified_frozen")


def test_an_assignment_may_not_start_in_the_past() -> None:
    late = task("1:0:2")
    assert_violates(run(problem(tasks=(late,)), plan(at(late.id, NOW - 1, 600))), "past_start")


@pytest.mark.parametrize(("mode", "tolerance_s"), [("strict", 0), ("tolerance", 600)])
def test_frozen_jobs_are_never_judged_by_the_start_and_end_rules(
    mode: str, tolerance_s: int
) -> None:
    # Both started before now, in the night, on the same local day and two hours apart; both end
    # in the night, one hour before the player can act again.
    night = (NOW - 8 * HOUR, NOW + 2 * HOUR)
    running = (
        frozen_job("1:0:2", NOW - 5 * HOUR, NOW + HOUR, slot=0),
        frozen_job("2:0:2", NOW - 3 * HOUR, NOW + HOUR, slot=1),
    )
    prob = problem(
        frozen=running,
        lanes={"builder": 2, "lab": 1},
        cal=calendar(sleep=[night]),
        rules=policy(mode, tolerance_s=tolerance_s, max_logins_per_day=1),
    )
    assert_clean(run(prob, plan()))


def test_a_modifier_scales_duration_and_cost_rounding_up_inside_its_period_only() -> None:
    effective_duration = simulator().effective_duration
    effective_cost = simulator().effective_cost
    bonus = model().Modifier(
        start=NOW + HOUR, end=NOW + DAY, lane="builder", duration_pct=50, cost_pct=50
    )
    building = task("1:0:2", duration_s=601, cost={"gold": 1001})
    research = task("2:0:2", lane="lab", duration_s=601, cost={"gold": 1001})
    prob = problem(tasks=(building, research), modifiers=(bonus,))

    inside = NOW + 2 * HOUR
    assert effective_duration(prob, building, inside) == 301
    assert dict(effective_cost(prob, building, inside)) == {"gold": 501}
    for outside in (NOW, NOW + DAY + 1):
        assert effective_duration(prob, building, outside) == 601
        assert dict(effective_cost(prob, building, outside)) == {"gold": 1001}
    assert effective_duration(prob, research, inside) == 601
    assert dict(effective_cost(prob, research, inside)) == {"gold": 1001}


@pytest.mark.parametrize(("cost_pct", "short"), [(50, False), (100, True)])
def test_the_simulator_pays_the_modified_cost(cost_pct: int, short: bool) -> None:
    sale = model().Modifier(
        start=NOW, end=NOW + DAY, lane="builder", duration_pct=100, cost_pct=cost_pct
    )
    one = task("1:0:2", cost={"gold": 1800})
    prob = problem(tasks=(one,), initial={"gold": 1000}, modifiers=(sale,))
    result = run(prob, plan(at(one.id, NOW + 60, 600)))
    if short:
        assert_violates(result, "insufficient_funds")
    else:
        assert_clean(result)


def test_a_registered_planner_is_called_by_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from clashpilot.planning.objective import Objective
    from clashpilot.planning.registry import PLANNERS

    assert isinstance(PLANNERS, dict)
    calls: list[str] = []

    class FakePlanner:
        def solve(self, prob: Any, objective: Any, config: Any, reference: Any = None) -> Any:
            calls.append("solve")
            first = prob.tasks[0]
            return plan(at(first.id, prob.now, first.duration_s, lane=first.lane))

    monkeypatch.setitem(PLANNERS, "fake-e07", FakePlanner())
    prob = problem(tasks=(task("1:0:2"),))
    produced = PLANNERS["fake-e07"].solve(prob, Objective(), support().TEST_CONFIG)
    assert calls == ["solve"]
    result = run(prob, produced)
    assert_clean(result)
    assert metric(result, "tasks_completed") == 1


# --------------------------------------------------------------------------- compilation

TH = 9100
HUT = 9200
LAB = 9300
CANNON = 9400
GAP = 9410
BIG = 9420
STORE = 9450
FIRST = 9510
SECOND = 9520
THIRD = 9530
TROOP = 9600
WALL = 9700
GEAR = 9800
MORTAR = 9900


def item(
    data_id: int,
    *,
    kind: str = "building",
    lane: str = "builder",
    category: str = "defense",
    tags: Iterable[str] = (),
    max_count: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "data_id": data_id,
        "name": f"Objet fictif {data_id}",
        "kind": kind,
        "village": "home",
        "lane": lane,
        "category": category,
        "tags": list(tags),
        "max_count_by_th": dict(max_count or {"1": 1}),
    }


def row(
    data_id: int,
    level: int,
    *,
    duration: int | None = None,
    cost: Mapping[str, int] | None = None,
    requires: Iterable[tuple[int, int]] = ((TH, 1),),
) -> dict[str, Any]:
    return {
        "data_id": data_id,
        "level": level,
        "duration_s": 600 * level + 7 if duration is None else duration,
        "cost": dict(cost or {"gold": 10 * level}),
        "requires": [{"data_id": needed, "level": at_least} for needed, at_least in requires],
        "source": "s1",
    }


def th_rows(top: int) -> list[dict[str, Any]]:
    return [row(TH, level, cost={"gold": 1000 * level}, requires=()) for level in range(1, top + 1)]


def game_pack(
    items: Iterable[dict[str, Any]],
    levels: Iterable[dict[str, Any]],
    storage: Iterable[tuple[int, int, str, int]] = (),
) -> Any:
    from clashpilot.gamedata.pack import load_pack

    return load_pack(
        {
            "schema_version": 1,
            "pack_id": "e07-acceptance",
            "game_version": "test-0",
            "as_of": "2026-01-01",
            "fictional": True,
            "sources": [{"id": "s1", "label": "Source fictive", "retrieved_at": "2026-01-01"}],
            "items": [item(TH, category="town_hall", tags=("town_hall",)), *items],
            "levels": list(levels),
            "storage": [
                {"data_id": d, "level": lvl, "resource": r, "capacity": c, "source": "s1"}
                for d, lvl, r, c in storage
            ],
        }
    )


def village(*entries: tuple[int, int, int], running: Iterable[tuple[int, int, int]] = ()) -> Any:
    """A snapshot of `(data_id, level, count)` entries and `(data_id, finish_at, to_level)` jobs."""
    from clashpilot.ingest.state import RunningJob, SnapshotItem, VillageSnapshot

    return VillageSnapshot(
        tag="E07TEST",
        exported_at=NOW - 60,
        imported_at=NOW - 60,
        items=tuple(
            SnapshotItem(data_id=d, level=lvl, count=n, section="buildings", village="home")
            for d, lvl, n in entries
        ),
        running=tuple(
            RunningJob(data_id=d, finish_at=f, to_level=to, section="buildings", village="home")
            for d, f, to in running
        ),
    )


def stock(value: int, as_of: int = NOW) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": as_of}


def manual(**changes: Any) -> Any:
    """Typed-in state: gold, elixir and dark elixir known, two builders, roomy storages."""
    from clashpilot.ingest.state import ManualState

    document: dict[str, Any] = {
        "resources": {name: stock(0) for name in ("gold", "elixir", "dark_elixir")},
        "builders_total": 2,
        "storage_override": {name: 10**7 for name in ("gold", "elixir", "dark_elixir")},
    }
    document.update(changes)
    return ManualState.model_validate(document)


def schedule() -> Any:
    from clashpilot.schedule.profile import ScheduleProfile

    return ScheduleProfile.model_validate(
        {
            "tz": "UTC",
            "weekly": [],
            "exceptions": [],
            "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
        }
    )


def compiled(
    snapshot: Any,
    game: Any,
    *,
    typed: Any = None,
    target_th: int | None = None,
    max_tasks: int = 400,
) -> Any:
    from clashpilot.planning.compile import compile_problem

    return compile_problem(
        snapshot,
        typed if typed is not None else manual(),
        game,
        schedule(),
        NOW,
        7,
        target_th=target_th,
        max_tasks=max_tasks,
    )


def ids(prob: Any) -> set[str]:
    return {one.id for one in prob.tasks}


def tasks_by_id(prob: Any) -> dict[str, Any]:
    return {one.id: one for one in prob.tasks}


def exclusions(prob: Any) -> set[tuple[int, int, str]]:
    return {(entry.data_id, entry.to_level, entry.reason) for entry in prob.excluded}


def missing_fields(call: Callable[[], Any]) -> set[str]:
    from clashpilot.planning.compile import MissingInputError

    with pytest.raises(MissingInputError) as caught:
        call()
    return set(caught.value.missing)


def test_compile_creates_one_chained_task_per_level_to_reach() -> None:
    game = game_pack(
        items=[
            item(LAB, category="laboratory", tags=("laboratory",)),
            item(CANNON, tags=("splash",), max_count={"1": 3}),
            item(TROOP, kind="troop", lane="lab", category="troop"),
        ],
        levels=[
            *th_rows(3),
            *(row(LAB, lvl, cost={"elixir": 100 * lvl}, requires=[(TH, lvl)]) for lvl in (1, 2, 3)),
            *(
                row(CANNON, lvl, cost={"gold": 100 * lvl}, requires=[(TH, lvl)])
                for lvl in (1, 2, 3)
            ),
            *(
                row(TROOP, lvl, cost={"elixir": 10 * lvl}, requires=[(LAB, lvl)])
                for lvl in (1, 2, 3)
            ),
        ],
    )
    prob = compiled(village((TH, 2, 1), (LAB, 1, 1), (CANNON, 1, 3), (TROOP, 1, 1)), game)

    th3, lab2, lab3 = f"{TH}:0:3", f"{LAB}:0:2", f"{LAB}:0:3"
    expected: dict[str, set[str]] = {
        th3: set(),
        lab2: set(),
        lab3: {lab2, th3},
        f"{TROOP}:0:2": {lab2},
        f"{TROOP}:0:3": {f"{TROOP}:0:2", lab3},
    }
    for instance in range(3):
        expected[f"{CANNON}:{instance}:2"] = set()
        expected[f"{CANNON}:{instance}:3"] = {f"{CANNON}:{instance}:2", th3}
    found = tasks_by_id(prob)
    assert set(found) == set(expected)
    for task_id, requires in expected.items():
        assert set(found[task_id].requires) == requires, task_id

    cannon, source = found[f"{CANNON}:1:3"], game.level(CANNON, 3)
    assert (cannon.data_id, cannon.instance, cannon.to_level) == (CANNON, 1, 3)
    assert cannon.lane == "builder"
    assert cannon.duration_s == source.duration_s
    assert dict(cannon.cost) == dict(source.cost)
    assert cannon.category == "defense"
    assert {"splash", "building"} <= set(cannon.tags)
    assert cannon.value == pytest.approx(source.duration_s / HOUR)
    assert found[f"{TROOP}:0:2"].lane == "lab"
    assert prob.lanes["builder"] == 2
    assert prob.lanes["lab"] == 1


def test_a_job_running_after_now_becomes_frozen_and_its_level_is_not_asked_again() -> None:
    game = game_pack(
        items=[item(CANNON, max_count={"1": 3})],
        levels=[*th_rows(1), row(CANNON, 1), row(CANNON, 2, duration=3000), row(CANNON, 3)],
    )
    snapshot = village(
        (TH, 1, 1), (CANNON, 1, 2), (CANNON, 1, 1), running=[(CANNON, NOW + 1000, 2)]
    )
    prob = compiled(snapshot, game)

    frozen = {job.task_id: job for job in prob.frozen}
    assert set(frozen) == {f"{CANNON}:0:2"}
    job = frozen[f"{CANNON}:0:2"]
    assert (job.lane, job.start, job.end) == ("builder", NOW - 2000, NOW + 1000)
    found = ids(prob)
    assert f"{CANNON}:0:2" not in found
    assert {f"{CANNON}:0:3", f"{CANNON}:1:2", f"{CANNON}:2:2"} <= found


def target_pack(top_th: int) -> Any:
    return game_pack(
        items=[item(CANNON)],
        levels=[
            *th_rows(top_th),
            *(row(CANNON, lvl, requires=[(TH, lvl)]) for lvl in (1, 2, 3, 4)),
        ],
    )


def test_the_default_target_is_the_next_town_hall_level() -> None:
    prob = compiled(village((TH, 2, 1), (CANNON, 2, 1)), target_pack(4))
    assert ids(prob) == {f"{TH}:0:3", f"{CANNON}:0:3"}
    listed = {(data_id, level) for data_id, level, _ in exclusions(prob)}
    assert not listed & {(TH, 4), (CANNON, 4)}


def test_a_target_equal_to_the_current_level_creates_no_town_hall_task() -> None:
    prob = compiled(village((TH, 2, 1), (CANNON, 2, 1)), target_pack(4), target_th=2)
    assert not any(one.data_id == TH for one in prob.tasks)
    assert f"{CANNON}:0:3" not in ids(prob)
    assert (CANNON, 3) not in {(data_id, level) for data_id, level, _ in exclusions(prob)}


def test_without_a_next_town_hall_row_the_target_stays_current() -> None:
    prob = compiled(village((TH, 2, 1), (CANNON, 1, 1)), target_pack(2))
    assert ids(prob) == {f"{CANNON}:0:2"}


def unlock_pack() -> Any:
    return game_pack(
        items=[item(CANNON, max_count={"1": 2, "3": 3})],
        levels=[*th_rows(4), *(row(CANNON, lvl) for lvl in (1, 2, 3))],
    )


def test_a_missing_instance_starts_at_level_zero_and_waits_for_its_town_hall() -> None:
    game = unlock_pack()
    found = tasks_by_id(compiled(village((TH, 2, 1), (CANNON, 3, 2)), game, target_th=3))
    first = found[f"{CANNON}:2:1"]
    assert first.duration_s == game.level(CANNON, 1).duration_s
    assert dict(first.cost) == dict(game.level(CANNON, 1).cost)
    assert f"{TH}:0:3" in first.requires
    assert {f"{CANNON}:2:2", f"{CANNON}:2:3"} <= set(found)


def test_a_missing_instance_needs_no_town_hall_task_once_its_level_is_reached() -> None:
    found = tasks_by_id(compiled(village((TH, 3, 1), (CANNON, 3, 2)), unlock_pack(), target_th=3))
    first = found[f"{CANNON}:2:1"]
    assert not any(needed.startswith(f"{TH}:") for needed in first.requires)


def test_task_ids_are_stable_across_imports() -> None:
    game = game_pack(
        items=[item(MORTAR, max_count={"1": 3})],
        levels=[*th_rows(1), *(row(MORTAR, lvl) for lvl in range(1, 9))],
    )
    before = village((TH, 1, 1), (MORTAR, 5, 3))
    first, again = compiled(before, game), compiled(before, game)
    assert [one.id for one in first.tasks] == [one.id for one in again.tasks]
    assert {f"{MORTAR}:0:6", f"{MORTAR}:1:6", f"{MORTAR}:2:6"} <= ids(first)

    after = village((TH, 1, 1), (MORTAR, 5, 2), (MORTAR, 6, 1))
    second = compiled(after, game)
    assert {f"{MORTAR}:0:7", f"{MORTAR}:1:6", f"{MORTAR}:2:6"} <= ids(second)
    assert f"{MORTAR}:0:6" not in ids(second)
    reordered = village((TH, 1, 1), (MORTAR, 6, 1), (MORTAR, 5, 2))
    assert ids(compiled(reordered, game)) == ids(second)


def test_unplannable_levels_are_excluded_with_their_reason() -> None:
    game = game_pack(
        items=[
            item(GAP),
            item(BIG),
            item(WALL, kind="wall", category="wall", max_count={"1": 4}),
            item(
                GEAR,
                kind="equipment",
                lane="blacksmith",
                category="equipment",
                tags=("instant_upgrade",),
            ),
        ],
        levels=[
            *th_rows(1),
            row(GAP, 1),
            row(GAP, 2),
            row(GAP, 4),
            row(BIG, 1),
            row(BIG, 2, cost={"gold": 10**9}),
            *(row(WALL, lvl) for lvl in (1, 2, 3)),
            *(row(GEAR, lvl, duration=0, cost={"shiny_ore": 50 * lvl}) for lvl in (1, 2)),
        ],
    )
    prob = compiled(village((TH, 1, 1), (GAP, 1, 1), (BIG, 1, 1), (WALL, 1, 4), (GEAR, 1, 1)), game)
    reasons = exclusions(prob)

    assert f"{GAP}:0:2" in ids(prob)
    assert (GAP, 3, "missing_pack_row") in reasons
    assert (GAP, 4, "missing_pack_row") in reasons
    assert not any(one.data_id == GAP and one.to_level >= 3 for one in prob.tasks)
    assert (BIG, 2, "exceeds_storage") in reasons
    assert (WALL, 2, "wall_sink") in reasons
    assert {reason for data_id, _, reason in reasons if data_id == WALL} == {"wall_sink"}
    assert (GEAR, 2, "instant_upgrade") in reasons
    assert not any(one.data_id in (BIG, WALL, GEAR) for one in prob.tasks)


def dependency_order(prob: Any) -> list[Any]:
    placed = {job.task_id for job in prob.frozen}
    pending = sorted(prob.tasks, key=lambda one: one.id)
    ordered: list[Any] = []
    while pending:
        ready = [one for one in pending if set(one.requires) <= placed]
        assert ready, "requires form a cycle or name an absent task"
        ordered += ready
        placed |= {one.id for one in ready}
        pending = [one for one in pending if one.id not in placed]
    return ordered


def test_the_task_cap_cascades_to_dependents_and_leaves_a_consistent_problem() -> None:
    game = game_pack(
        items=[item(FIRST), item(SECOND), item(THIRD)],
        levels=[
            *th_rows(1),
            *(row(FIRST, lvl) for lvl in (1, 2, 3, 4)),
            *(row(SECOND, lvl) for lvl in (1, 2, 3)),
            row(THIRD, 1),
            row(THIRD, 2, requires=[(TH, 1), (SECOND, 3)]),
        ],
    )
    snapshot = village((TH, 1, 1), (FIRST, 1, 1), (SECOND, 1, 1), (THIRD, 1, 1))
    full = compiled(snapshot, game)
    assert f"{THIRD}:0:2" in ids(full)

    capped = compiled(snapshot, game, max_tasks=3)
    kept = ids(capped)
    assert len(kept) <= 3
    assert f"{SECOND}:0:3" not in kept
    assert f"{THIRD}:0:2" not in kept
    reasons = exclusions(capped)
    assert (THIRD, 2, "horizon_limit") in reasons
    for dropped in full.tasks:
        if dropped.id not in kept:
            assert (dropped.data_id, dropped.to_level, "horizon_limit") in reasons, dropped.id
    known = kept | {job.task_id for job in capped.frozen}
    for one in capped.tasks:
        assert set(one.requires) <= known, one.id

    cursor, assignments = capped.now, []
    for one in dependency_order(capped):
        assignments.append(at(one.id, cursor, one.duration_s, lane=one.lane))
        cursor += one.duration_s
    assert assignments
    assert "precedence" not in codes(run(capped, plan(*assignments)))


def simple_pack(*extra_items: dict[str, Any], extra_levels: Iterable[dict[str, Any]] = ()) -> Any:
    return game_pack(
        items=[item(CANNON), *extra_items],
        levels=[
            *th_rows(1),
            *(row(CANNON, lvl, cost={"gold": 100 * lvl}) for lvl in (1, 2)),
            *extra_levels,
        ],
    )


def test_a_consumed_resource_without_value_is_a_missing_input() -> None:
    typed = manual(resources={"elixir": stock(0), "dark_elixir": stock(0)})
    snapshot = village((TH, 1, 1), (CANNON, 1, 1))
    assert "gold" in missing_fields(lambda: compiled(snapshot, simple_pack(), typed=typed))


def test_builders_without_input_nor_hut_are_a_missing_input() -> None:
    snapshot = village((TH, 1, 1), (CANNON, 1, 1))
    typed = manual(builders_total=None)
    assert "builders_total" in missing_fields(
        lambda: compiled(snapshot, simple_pack(), typed=typed)
    )


def test_a_missing_schedule_profile_is_a_missing_input() -> None:
    from clashpilot.planning.compile import compile_problem

    snapshot = village((TH, 1, 1), (CANNON, 1, 1))
    assert "schedule_profile" in missing_fields(
        lambda: compile_problem(snapshot, manual(), simple_pack(), None, NOW, 7)
    )


def test_unconsumed_ores_and_gems_need_no_input() -> None:
    gear = item(
        GEAR, kind="equipment", lane="blacksmith", category="equipment", tags=("instant_upgrade",)
    )
    gear_rows = [row(GEAR, lvl, duration=0, cost={"shiny_ore": 50 * lvl}) for lvl in (1, 2)]
    game = simple_pack(gear, extra_levels=gear_rows)
    prob = compiled(village((TH, 1, 1), (CANNON, 1, 1), (GEAR, 1, 1)), game)
    assert f"{CANNON}:0:2" in ids(prob)


@pytest.mark.parametrize(("typed", "expected"), [(5, 5), (None, 3)])
def test_the_builder_lane_comes_from_the_input_else_from_the_huts(
    typed: int | None, expected: int
) -> None:
    hut = item(HUT, lane="none", category="builder_hut", tags=("builder_hut",), max_count={"1": 3})
    snapshot = village((TH, 1, 1), (CANNON, 1, 1), (HUT, 1, 3))
    prob = compiled(snapshot, simple_pack(hut), typed=manual(builders_total=typed))
    assert prob.lanes["builder"] == expected


def stock_problem(gold: int, as_of: int, rate: int) -> Any:
    game = game_pack(
        items=[item(CANNON), item(STORE, category="resource_storage", tags=("storage",))],
        levels=[
            *th_rows(1),
            row(STORE, 1),
            *(row(CANNON, lvl, cost={"gold": 100 * lvl, "elixir": 50 * lvl}) for lvl in (1, 2)),
        ],
        storage=[(STORE, 1, "gold", 20000)],
    )
    typed = manual(
        resources={"gold": stock(gold, as_of), "elixir": stock(500, NOW - 7200)},
        farm_rate={"gold": {"low": 0, "expected": rate, "high": 2 * rate}},
        storage_override={"elixir": 10**6},
    )
    return compiled(village((TH, 1, 1), (STORE, 1, 1), (CANNON, 1, 1)), game, typed=typed)


def test_the_initial_stock_adds_the_expected_income_since_its_reading() -> None:
    resources = stock_problem(1000, NOW - 7200, 3600).resources
    assert resources.initial["gold"] == 8200
    assert resources.income_per_hour["gold"] == 3600
    assert resources.cap["gold"] == 20000
    assert resources.initial["elixir"] == 500
    assert resources.income_per_hour.get("elixir", 0) == 0


def test_the_initial_stock_is_capped_and_rounded_down() -> None:
    assert stock_problem(19000, NOW - 7200, 3600).resources.initial["gold"] == 20000
    assert stock_problem(1000, NOW - 1, 1800).resources.initial["gold"] == 1000


@pytest.mark.parametrize("finish_offset", [0, -100])
def test_a_finished_running_job_leaves_its_instance_at_the_target_level(finish_offset: int) -> None:
    game = game_pack(
        items=[item(CANNON)], levels=[*th_rows(1), *(row(CANNON, lvl) for lvl in (1, 2, 3))]
    )
    snapshot = village((TH, 1, 1), (CANNON, 1, 1), running=[(CANNON, NOW + finish_offset, 2)])
    prob = compiled(snapshot, game)
    assert not prob.frozen
    found = tasks_by_id(prob)
    assert f"{CANNON}:0:2" not in found
    assert f"{CANNON}:0:3" in found
    assert f"{CANNON}:0:2" not in found[f"{CANNON}:0:3"].requires


def test_manual_events_become_modifiers() -> None:
    event = {
        "label": "Événement fictif",
        "start": NOW + HOUR,
        "end": NOW + 3 * HOUR,
        "lane": "builder",
        "duration_pct": 80,
        "cost_pct": 70,
    }
    prob = compiled(
        village((TH, 1, 1), (CANNON, 1, 1)), simple_pack(), typed=manual(events=[event])
    )
    wanted = (NOW + HOUR, NOW + 3 * HOUR, "builder", 80, 70)
    assert any(
        (bonus.start, bonus.end, bonus.lane, bonus.duration_pct, bonus.cost_pct) == wanted
        for bonus in prob.modifiers
    )
