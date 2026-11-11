"""Acceptance tests for E17: resource forecast, wall sink and observed farm rate.

SPEC section 4 (Provenance, Simulator, Compilation rule 7), ADR-4 (`simulate` is the single judge)
and section 7 (budget: `TEST_CONFIG` through `Settings.planner_overrides`).

Frozen with the task. No probability is computed: the three scenarios come from the three declared
incomes `farm_rate.low`, `farm_rate.expected` and `farm_rate.high`. Every test goes through HTTP
with the fictional pack `backend/tests/fixtures/packs/fictional_pack_forecast.json`, engine
`rollout`, profile `repro`, `horizon_days` 30, a schedule without sleep, and a plan created with
`POST /api/villages/{id}/plans` before the forecast is read. The wall item of that pack has
`duration_s` 0 on every level; it carries the tag `instant_upgrade` so that the pack validates, and
the compiler still excludes its levels as `wall_sink` (its kind is `wall`).

The fixture village (`now` = 2026-11-11T08:00:00Z): two builders, a lab, gold 5000 with a gold cap
of 5000, elixir 1000 with an elixir cap of 100000, both incomes low 500, expected 1000, high 2000
per hour. Its plan starts the cannon (`9100003:0:2`, 2000 gold, 3 h) at `now` and the troop research
(`9100005:0:2`, 10000 elixir, 2 h) once 9000 more elixir are earned, at `now + 9 h`.

Shapes that the task text leaves open are fixed here. A provenance object is the SPEC one:
`{"value", "status", "source", "as_of"}`.

- `GET /api/villages/{id}/forecast?horizon_days=30` reads the latest plan and answers 200 with at
  least `series`, `funding`, `saturation`, `blocking`, `walls` and `effort`, over
  `[now, now + horizon_days]`.
- `series` maps each resource to a list of points `{"t", "low", "expected", "high"}`: `t` starts
  at `now` and goes up by one regular step, the last point lies within one step of the horizon end,
  and a 30-day horizon gives at most 1441 points. `low`, `expected` and `high` are provenance
  objects holding the stock (in units) under the matching income, the plan's payments taken off.
- `funding` holds one entry `{"task_id", "funded_at": {"low", "expected", "high"}}` per task of the
  compiled problem (the plan's tasks and the ones it leaves out; walls are not tasks). Each
  `funded_at` member is a provenance object whose value is the epoch at which the stock first covers
  the task's cost. The three members are a range of dates: `low` is the earliest (income high),
  `expected` uses income expected, `high` is the latest (income low).
- `saturation` lists windows `{"resource", "start", "end", "spilled"}` (plain numbers, units) where
  the stock sits at its cap under income expected and the plan without wall purchases; the
  `spilled` of a resource add up to `simulate(...).metrics.spill` of that plan over the horizon.
- `blocking` is `{"resource", "delayed_s", "tasks"}` (or null when nothing is delayed): the resource
  whose unlimited supply (infinite stock and cap) moves the plan earlier the most, `delayed_s` that
  delay in seconds, `tasks` the ids of the delayed tasks.
- `walls` lists purchases `{"data_id", "level", "cost", "at"}`: one wall instance upgraded to
  `level` at the epoch `at`, `cost` being the pack row of that level. The excess is what the stock
  would lose over its cap under income expected (the saturation); without saturation `walls` is
  empty. Walls are not tasks: with the plan unchanged, the simulated score with the purchases
  differs from the one without them only by the spill. So "the score is never lower and no planned
  task is delayed" is checked as: every payment of the plan and of the walls is covered at its
  instant, and the spill with the purchases is at most the spill without them.
- `effort` is `{"scope", "cost", "farm_hours"}`. `scope` is `"goal"` when the latest plan has a goal
  (then the goal's tasks still to do), else `"plan"` (every task of the compiled problem). `cost`
  maps a resource to the total cost of those tasks. `farm_hours` maps each resource of `cost` to
  `{"low", "expected", "high"}`, provenance objects whose value is
  `max(0, cost - current stock) / income` in hours under the income of that name.
- Every provenance object of `series`, `funding` and `effort.farm_hours` carries status `estimated`
  and source `model`. Without `farm_rate` the values that need an income are null with status
  `unknown` (a `funded_at` may also be null as a whole); no income is assumed.
- Each `PUT /api/villages/{id}/manual` adds one row per resource of `resources` to the SQLite table
  `manual_history (village_id, resource, value, as_of)` of `data_dir/clashpilot.db`.
- `GET /api/villages/{id}/farm-rate` answers `{"declared", "observed"}`: `declared` is the stored
  `farm_rate` mapping, `observed` maps each resource with at least one history row to a provenance
  object whose value is per hour: (last value - previous value + costs of `task_started` journal
  entries dated between the two `as_of`) / hours between them, status `estimated`; null with status
  `unknown` while the resource has fewer than two rows.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import json
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
TESTS = BACKEND / "tests"
FORECAST_PACK = TESTS / "fixtures" / "packs" / "fictional_pack_forecast.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

NOW = 1_794_384_000  # 2026-11-11T08:00:00Z
EXPORTED_AT = NOW - HOUR
HORIZON_DAYS = 30
HORIZON_END = NOW + HORIZON_DAYS * DAY
TAG = "#2FC0RE"
VILLAGE = "2FC0RE"

TOWN_HALL = 9100001
HUT = 9100002
CANNON = 9100003
LAB = 9100004
TROOP = 9100005
WALL = 9100006
CANNON_TASK = f"{CANNON}:0:2"
TROOP_TASK = f"{TROOP}:0:2"
WALL_COUNT = 4

RATES = {"low": 500, "expected": 1000, "high": 2000}
SCENARIOS = ("low", "expected", "high")
REPRO = {"engine": "rollout", "profile": "repro", "horizon_days": HORIZON_DAYS}


# --------------------------------------------------------------------------- support


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e17_acceptance_support_problems"
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


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FORECAST_PACK.read_text(encoding="utf-8"))
    return loaded


def pack_cost(data_id: int, level: int) -> dict[str, int]:
    for row in pack_doc()["levels"]:
        if row["data_id"] == data_id and row["level"] == level:
            cost: dict[str, int] = row.get("cost", {})
            return cost
    raise AssertionError(f"no pack row for {data_id} level {level}")


def task_cost(task_id: str) -> dict[str, int]:
    data_id, _, to_level = task_id.split(":")
    return pack_cost(int(data_id), int(to_level))


# --------------------------------------------------------------------------- HTTP fixture


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def set(self, now: int) -> None:
        assert now >= self.now, "the clock never goes back"
        self.now = now


@dataclasses.dataclass
class Village:
    client: TestClient
    clock: Clock
    data_dir: Path


def export_doc() -> dict[str, Any]:
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 1},
            {"data": HUT, "lvl": 1, "cnt": 2},
            {"data": CANNON, "lvl": 1},
            {"data": LAB, "lvl": 1},
            {"data": WALL, "lvl": 1, "cnt": WALL_COUNT},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
    }


def stock(value: int, as_of: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": as_of}


def manual_doc(
    *,
    gold: int = 5000,
    elixir: int = 1000,
    as_of: int = NOW,
    rates: Mapping[str, int] | None = RATES,
    caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "resources": {"gold": stock(gold, as_of), "elixir": stock(elixir, as_of)},
        "builders_total": 2,
        "storage_override": dict(caps) if caps is not None else {"gold": 5000, "elixir": 100_000},
    }
    if rates is not None:
        doc["farm_rate"] = {"gold": dict(rates), "elixir": dict(rates)}
    return doc


def profile_doc() -> dict[str, Any]:
    return {
        "tz": "Europe/Paris",
        "weekly": [],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def village(tmp_path: Path, manual: dict[str, Any] | None = None) -> Village:
    """The fixture village, imported with its manual input and schedule; no plan yet."""
    clock = Clock(NOW)
    settings = Settings(
        data_dir=tmp_path,
        frontend_dist=tmp_path / "no-dist",
        planner_overrides=dataclasses.asdict(support().TEST_CONFIG),
    )
    client = TestClient(create_app(settings, clock=clock))
    pack = pack_doc()
    response = client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/villages/import",
        content=json.dumps(export_doc()).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE
    response = client.put(
        f"/api/villages/{VILLAGE}/manual", json=manual if manual is not None else manual_doc()
    )
    assert response.status_code == 200, response.text
    response = client.put(f"/api/villages/{VILLAGE}/schedule", json=profile_doc())
    assert response.status_code == 200, response.text
    return Village(client, clock, tmp_path)


def create_plan(v: Village, **extra: Any) -> dict[str, Any]:
    response = v.client.post(f"/api/villages/{VILLAGE}/plans", json={**REPRO, **extra})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def forecast(v: Village) -> dict[str, Any]:
    response = v.client.get(
        f"/api/villages/{VILLAGE}/forecast", params={"horizon_days": HORIZON_DAYS}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    for key in ("series", "funding", "saturation", "blocking", "walls", "effort"):
        assert key in body, f"forecast has no {key}: {sorted(body)}"
    return body


def planned(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {a["task_id"]: a for a in plan["assignments"]}


def check_fixture_plan(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The plan the fixture relies on: the cannon at `now`, the research once it is funded."""
    rows = planned(plan)
    assert set(rows) == {CANNON_TASK, TROOP_TASK}, sorted(rows)
    assert rows[CANNON_TASK]["start"] == NOW
    assert rows[TROOP_TASK]["start"] == NOW + 9 * HOUR
    return rows


# --------------------------------------------------------------------------- provenance


def is_provenance(value: Any) -> bool:
    return isinstance(value, dict) and {"value", "status", "source"} <= set(value)


def is_unknown(value: Any) -> bool:
    return value is None or (
        is_provenance(value) and value["value"] is None and value["status"] == "unknown"
    )


def forecast_values(body: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """Every predicted value of `series`, `funding` and `effort.farm_hours`, with a label."""
    for resource, points in body["series"].items():
        for point in points:
            for scenario in SCENARIOS:
                yield f"series.{resource}@{point['t']}.{scenario}", point[scenario]
    for entry in body["funding"]:
        funded_at = entry["funded_at"]
        if funded_at is None:
            yield f"funding.{entry['task_id']}", None
            continue
        for scenario in SCENARIOS:
            yield f"funding.{entry['task_id']}.{scenario}", funded_at[scenario]
    for resource, hours in body["effort"]["farm_hours"].items():
        for scenario in SCENARIOS:
            yield f"effort.farm_hours.{resource}.{scenario}", hours[scenario]


def series_of(body: Mapping[str, Any], resource: str) -> list[Mapping[str, Any]]:
    points: list[Mapping[str, Any]] = body["series"][resource]
    return points


def funding_of(body: Mapping[str, Any], task: str) -> Any:
    entries = [entry for entry in body["funding"] if entry["task_id"] == task]
    assert len(entries) == 1, f"funding of {task}: {entries}"
    return entries[0]["funded_at"]


def value_of(provenance: Any) -> Any:
    assert is_provenance(provenance), provenance
    return provenance["value"]


# --------------------------------------------------------------------------- reference ledger

SCALE = 3600  # stock units per resource unit, as in the simulator


def ledger(
    initial: int,
    cap: int,
    rate: int,
    payments: Iterable[tuple[int, int]],
    end: int,
) -> tuple[list[tuple[int, int]], int]:
    """Integer replay of one resource from `NOW` to `end`, payments `(at, amount)` in order.

    Returns the payments the stock could not cover and the units lost over the cap.
    """
    level, limit, clock, lost = initial * SCALE, cap * SCALE, NOW, 0
    uncovered: list[tuple[int, int]] = []
    for at, amount in sorted(payments, key=lambda p: p[0]):
        level += rate * (at - clock)
        if level > limit:
            lost += level - limit
            level = limit
        if level < amount * SCALE:
            uncovered.append((at, amount))
        level -= amount * SCALE
        clock = at
    level += rate * (end - clock)
    if level > limit:
        lost += level - limit
    return uncovered, lost // SCALE


def plan_payments(plan: Mapping[str, Any], resource: str) -> list[tuple[int, int]]:
    payments = []
    for assignment in plan["assignments"]:
        amount = task_cost(assignment["task_id"]).get(resource, 0)
        if amount > 0 and NOW <= assignment["start"] <= HORIZON_END:
            payments.append((int(assignment["start"]), amount))
    return payments


def simulated_spill(plan: Mapping[str, Any]) -> dict[str, float]:
    """`simulate` on the fixture problem rebuilt by hand, with the plan's assignments."""
    from clashpilot.planning.model import Assignment, Plan, Proof, ResourceModel
    from clashpilot.planning.simulate import simulate

    sup = support()
    tasks = (
        sup.make_task(CANNON_TASK, duration_s=3 * HOUR, cost={"gold": 2000}),
        sup.make_task(TROOP_TASK, lane="lab", duration_s=2 * HOUR, cost={"elixir": 10000}),
    )
    problem = sup.make_problem(
        now=NOW,
        horizon_end=HORIZON_END,
        lanes={"builder": 2, "lab": 1},
        tasks=tasks,
        resources=ResourceModel(
            initial={"gold": 5000, "elixir": 1000},
            cap={"gold": 5000, "elixir": 100_000},
            income_per_hour={"gold": RATES["expected"], "elixir": RATES["expected"]},
        ),
        calendar=sup.make_calendar(),
    )
    assignments = tuple(
        Assignment(
            task_id=a["task_id"],
            lane=a["lane"],
            lane_slot=a["lane_slot"],
            start=a["start"],
            end=a["end"],
        )
        for a in plan["assignments"]
    )
    sim = simulate(problem, Plan(assignments, "reference", Proof("heuristic", 0.0)))
    return {name: float(lost) for name, lost in sim.metrics.spill.items()}


# --------------------------------------------------------------------------- forecast shape


def test_forecast_answers_series_funding_saturation_blocking_walls_and_effort_as_estimates(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    check_fixture_plan(create_plan(v))
    body = forecast(v)

    assert set(body["series"]) >= {"gold", "elixir"}
    for resource, points in body["series"].items():
        times = [point["t"] for point in points]
        assert len(times) >= 2, resource
        assert len(times) <= 1441, f"{resource}: {len(times)} points for 30 days"
        assert times[0] == NOW, resource
        step = times[1] - times[0]
        assert step > 0, resource
        assert all(b - a == step for a, b in itertools.pairwise(times)), resource
        assert times[-1] <= HORIZON_END < times[-1] + step, resource

    labelled = list(forecast_values(body))
    assert labelled
    for label, provenance in labelled:
        assert is_provenance(provenance), f"{label}: {provenance}"
        assert provenance["status"] == "estimated", f"{label}: {provenance}"
        assert provenance["source"] == "model", f"{label}: {provenance}"
        assert provenance["value"] is not None, f"{label}: {provenance}"

    # Before the research is paid at now + 9 h, each scenario earns its own income.
    early = [p for p in series_of(body, "elixir") if NOW < p["t"] <= NOW + 8 * HOUR]
    assert early, "no series point within the first 8 hours"
    for point in early:
        elapsed = point["t"] - NOW
        for scenario in SCENARIOS:
            expected_elixir = 1000 + RATES[scenario] * elapsed // HOUR
            assert abs(value_of(point[scenario]) - expected_elixir) <= 1, (point, scenario)
    for point in (p for p in series_of(body, "gold") if NOW < p["t"] <= NOW + 8 * HOUR):
        elapsed = point["t"] - NOW
        for scenario in SCENARIOS:
            expected_gold = min(5000, 3000 + RATES[scenario] * elapsed // HOUR)
            assert abs(value_of(point[scenario]) - expected_gold) <= 1, (point, scenario)


# --------------------------------------------------------------------------- funding


def test_funded_at_is_a_range_of_dates_from_the_three_incomes(tmp_path: Path) -> None:
    v = village(tmp_path)
    check_fixture_plan(create_plan(v))
    body = forecast(v)

    for entry in body["funding"]:
        funded_at = entry["funded_at"]
        assert funded_at is not None, entry
        low, expected, high = (value_of(funded_at[s]) for s in SCENARIOS)
        assert low <= expected <= high, entry

    # 9000 elixir to earn: 4.5 h at 2000/h, 9 h at 1000/h, 18 h at 500/h.
    troop = funding_of(body, TROOP_TASK)
    low, expected, high = (value_of(troop[s]) for s in SCENARIOS)
    assert low < expected < high
    assert abs(low - (NOW + 9 * HOUR // 2)) <= HOUR, low - NOW
    assert abs(expected - (NOW + 9 * HOUR)) <= HOUR, expected - NOW
    assert abs(high - (NOW + 18 * HOUR)) <= HOUR, high - NOW

    cannon = funding_of(body, CANNON_TASK)
    for scenario in SCENARIOS:
        assert NOW <= value_of(cannon[scenario]) <= NOW + HOUR, (scenario, cannon)


# --------------------------------------------------------------------------- saturation


def test_saturation_reports_the_windows_and_the_spill_of_the_simulator(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    check_fixture_plan(plan)
    body = forecast(v)
    reference = simulated_spill(plan)
    assert reference["gold"] > 0 and reference["elixir"] > 0  # the fixture does overflow

    windows = body["saturation"]
    for window in windows:
        assert {"resource", "start", "end", "spilled"} <= set(window), window
        assert NOW <= window["start"] < window["end"] <= HORIZON_END, window
        assert window["spilled"] > 0, window
    for resource, lost in reference.items():
        spilled = sum(w["spilled"] for w in windows if w["resource"] == resource)
        assert abs(spilled - lost) <= 1, (resource, spilled, lost)

    # Gold refills from 3000 to its cap of 5000 in 2 h; elixir from 0 to 100000 by now + 109 h.
    gold = [w for w in windows if w["resource"] == "gold"]
    elixir = [w for w in windows if w["resource"] == "elixir"]
    assert len(gold) == 1 and len(elixir) == 1, windows
    assert abs(gold[0]["start"] - (NOW + 2 * HOUR)) <= HOUR, gold
    assert abs(elixir[0]["start"] - (NOW + 109 * HOUR)) <= HOUR, elixir
    assert HORIZON_END - HOUR <= gold[0]["end"] <= HORIZON_END, gold
    assert HORIZON_END - HOUR <= elixir[0]["end"] <= HORIZON_END, elixir


# --------------------------------------------------------------------------- blocking


def test_blocking_names_the_resource_that_delays_the_plan_the_most(tmp_path: Path) -> None:
    v = village(tmp_path)
    check_fixture_plan(create_plan(v))
    body = forecast(v)

    blocking = body["blocking"]
    assert blocking is not None
    assert blocking["resource"] == "elixir"
    # With unlimited elixir the research starts at now instead of now + 9 h.
    assert abs(blocking["delayed_s"] - 9 * HOUR) <= HOUR, blocking
    assert TROOP_TASK in blocking["tasks"]
    assert CANNON_TASK not in blocking["tasks"]


# --------------------------------------------------------------------------- walls


def test_walls_spend_the_excess_without_delaying_the_plan_nor_adding_spill(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    check_fixture_plan(plan)
    body = forecast(v)

    walls = body["walls"]
    assert walls, "the gold overflow leaves walls to buy"
    by_level: dict[int, list[int]] = {}
    for wall in walls:
        assert wall["data_id"] == WALL, wall
        assert 2 <= wall["level"] <= 5, wall
        cost = {name: amount for name, amount in wall["cost"].items() if amount}
        assert cost == pack_cost(WALL, wall["level"]), wall
        assert NOW <= wall["at"] <= HORIZON_END, wall
        by_level.setdefault(wall["level"], []).append(wall["at"])
    for level, times in by_level.items():
        assert len(times) <= WALL_COUNT, (level, times)
        if level > 2:
            below = sorted(by_level.get(level - 1, []))
            # The k-th upgrade to `level` needs k upgrades to `level - 1` at or before it.
            for k, at in enumerate(sorted(times)):
                assert len(below) > k and below[k] <= at, (level, times, below)

    caps = {"gold": 5000, "elixir": 100_000}
    initial = {"gold": 5000, "elixir": 1000}
    for resource in ("gold", "elixir"):
        tasks = plan_payments(plan, resource)
        bought = [(w["at"], w["cost"].get(resource, 0)) for w in walls]
        bought = [(at, amount) for at, amount in bought if amount > 0]
        rate = RATES["expected"]
        uncovered_before, lost_before = ledger(
            initial[resource], caps[resource], rate, tasks, HORIZON_END
        )
        assert not uncovered_before, (resource, uncovered_before)
        uncovered, lost = ledger(
            initial[resource], caps[resource], rate, [*tasks, *bought], HORIZON_END
        )
        assert not uncovered, f"{resource}: payments not covered with the walls: {uncovered}"
        assert lost <= lost_before, (resource, lost, lost_before)


def test_walls_is_empty_without_excess(tmp_path: Path) -> None:
    big = {"gold": 10_000_000, "elixir": 10_000_000}
    v = village(tmp_path, manual_doc(caps=big))
    check_fixture_plan(create_plan(v))
    body = forecast(v)

    assert body["saturation"] == []
    assert body["walls"] == []


# --------------------------------------------------------------------------- effort


def check_hours(hours: Mapping[str, Any], expected: Mapping[str, float]) -> None:
    for scenario in SCENARIOS:
        value = value_of(hours[scenario])
        assert abs(value - expected[scenario]) <= 0.01, (scenario, value, expected)


def test_effort_gives_the_plan_cost_and_the_farm_hours_of_each_income(tmp_path: Path) -> None:
    v = village(tmp_path)
    check_fixture_plan(create_plan(v))
    effort = forecast(v)["effort"]

    assert effort["scope"] == "plan"
    cost = {name: amount for name, amount in effort["cost"].items() if amount}
    assert cost == {"gold": 2000, "elixir": 10000}
    # Elixir: 10000 - 1000 in stock = 9000 to farm; gold: 2000 is already in stock.
    check_hours(effort["farm_hours"]["elixir"], {"low": 18.0, "expected": 9.0, "high": 4.5})
    check_hours(effort["farm_hours"]["gold"], {"low": 0.0, "expected": 0.0, "high": 0.0})


def test_effort_is_limited_to_the_goal_when_the_plan_has_one(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v, strategy="custom_goal", goal={"data_id": TROOP, "level": 2})
    effort = forecast(v)["effort"]

    assert effort["scope"] == "goal"
    cost = {name: amount for name, amount in effort["cost"].items() if amount}
    assert cost == {"elixir": 10000}
    check_hours(effort["farm_hours"]["elixir"], {"low": 18.0, "expected": 9.0, "high": 4.5})


# --------------------------------------------------------------------------- no farm rate


def test_without_farm_rate_the_values_that_need_an_income_are_unknown(tmp_path: Path) -> None:
    v = village(tmp_path, manual_doc(rates=None))
    plan = create_plan(v)
    assert "no_farm_rate" in plan["warnings"]
    body = forecast(v)

    # The research needs 9000 more elixir than the stock: without income it is never funded.
    troop = funding_of(body, TROOP_TASK)
    if troop is not None:
        for scenario in SCENARIOS:
            assert is_unknown(troop[scenario]), (scenario, troop)

    hours = body["effort"]["farm_hours"]["elixir"]
    for scenario in SCENARIOS:
        assert is_unknown(hours[scenario]), (scenario, hours)

    for resource, points in body["series"].items():
        later = [p for p in points if p["t"] > NOW]
        assert later, resource
        for point in later:
            for scenario in SCENARIOS:
                assert is_unknown(point[scenario]), (resource, point)

    assert body["saturation"] == []
    assert body["walls"] == []


# --------------------------------------------------------------------------- manual history


def history(v: Village) -> list[tuple[str, int, int]]:
    database = v.data_dir / "clashpilot.db"
    assert database.is_file(), database
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT resource, value, as_of FROM manual_history WHERE village_id = ?",
            (VILLAGE,),
        ).fetchall()
    finally:
        connection.close()
    return sorted((str(r), int(value), int(as_of)) for r, value, as_of in rows)


def put_manual(v: Village, doc: dict[str, Any]) -> None:
    response = v.client.put(f"/api/villages/{VILLAGE}/manual", json=doc)
    assert response.status_code == 200, response.text
    response = v.client.get(f"/api/villages/{VILLAGE}/manual")
    assert response.status_code == 200, response.text
    resources = response.json()["resources"]
    assert {
        name: {"value": r["value"], "status": r["status"], "as_of": r["as_of"]}
        for name, r in resources.items()
    } == doc["resources"]


def test_each_manual_put_adds_one_history_row_per_typed_resource(tmp_path: Path) -> None:
    v = village(tmp_path)  # first PUT: gold 5000 and elixir 1000 at NOW
    v.clock.set(NOW + HOUR)
    gold_only = manual_doc()
    gold_only["resources"] = {"gold": stock(5200, NOW + HOUR)}
    put_manual(v, gold_only)
    v.clock.set(NOW + 2 * HOUR)
    both = manual_doc()
    both["resources"] = {
        "gold": stock(4100, NOW + 2 * HOUR),
        "elixir": stock(900, NOW + 110 * MINUTE),
    }
    put_manual(v, both)

    assert history(v) == sorted(
        [
            ("gold", 5000, NOW),
            ("elixir", 1000, NOW),
            ("gold", 5200, NOW + HOUR),
            ("gold", 4100, NOW + 2 * HOUR),
            ("elixir", 900, NOW + 110 * MINUTE),
        ]
    )


# --------------------------------------------------------------------------- farm rate


def farm_rate(v: Village) -> dict[str, Any]:
    response = v.client.get(f"/api/villages/{VILLAGE}/farm-rate")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_farm_rate_gives_the_declared_rate_and_the_observed_one_net_of_journal_spending(
    tmp_path: Path,
) -> None:
    declared = {"low": 300, "expected": 400, "high": 500}
    v = village(tmp_path, manual_doc(gold=1000, elixir=1000, rates=declared))
    plan = create_plan(v)

    body = farm_rate(v)
    assert body["declared"] == {"gold": declared, "elixir": declared}
    for resource in ("gold", "elixir"):
        observed = body["observed"][resource]
        assert is_provenance(observed), observed
        assert observed["value"] is None and observed["status"] == "unknown", observed

    # The cannon (2000 gold) is started between the two entries.
    v.clock.set(NOW + 5 * HOUR)
    response = v.client.post(
        f"/api/villages/{VILLAGE}/plans/{plan['plan_id']}/events",
        json={"type": "task_started", "task_id": CANNON_TASK, "at": NOW + 5 * HOUR},
    )
    assert response.status_code in (200, 201), response.text
    v.clock.set(NOW + 10 * HOUR)
    put_manual(v, manual_doc(gold=1500, elixir=3000, as_of=NOW + 10 * HOUR, rates=declared))

    body = farm_rate(v)
    assert body["declared"] == {"gold": declared, "elixir": declared}
    gold = body["observed"]["gold"]
    assert gold["status"] == "estimated", gold
    assert abs(gold["value"] - 250) < 1e-6, gold  # (1500 - 1000 + 2000) / 10 h
    elixir = body["observed"]["elixir"]
    assert elixir["status"] == "estimated", elixir
    assert abs(elixir["value"] - 200) < 1e-6, elixir  # (3000 - 1000) / 10 h, nothing spent
