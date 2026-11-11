"""Unit tests of the resource forecast (E17) and of the purchases judged by `simulate`."""

from __future__ import annotations

import json
from pathlib import Path

from clashpilot.gamedata.pack import load_pack
from clashpilot.planning.forecast import (
    DAY_S,
    blocking,
    observed_rate,
    replay,
    series_step,
    walls,
)
from clashpilot.planning.model import (
    Assignment,
    ExcludedTask,
    Plan,
    Problem,
    Proof,
    ResourceModel,
)
from clashpilot.planning.objective import Objective
from clashpilot.planning.simulate import Purchase, simulate
from support.problems import HOUR, NOW, make_calendar, make_problem, make_task

PACK = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_forecast.json"
WALL = 9100006


def plan_of(*rows: tuple[str, str, int, int]) -> Plan:
    assignments = tuple(Assignment(tid, lane, 0, start, end) for tid, lane, start, end in rows)
    return Plan(assignments, "test", Proof("heuristic", 0.0))


def gold_problem(tasks: tuple[object, ...] = (), **fields: object) -> Problem:
    values: dict[str, object] = {
        "now": NOW,
        "horizon_end": NOW + 2 * DAY_S,
        "lanes": {"builder": 1, "lab": 1},
        "tasks": tasks,
        "resources": ResourceModel(
            initial={"gold": 5000}, cap={"gold": 5000}, income_per_hour={"gold": 1000}
        ),
        "calendar": make_calendar(),
    }
    values.update(fields)
    return make_problem(**values)


def test_a_purchase_pays_from_the_stock_and_an_uncovered_one_is_insufficient_funds() -> None:
    problem = gold_problem()
    empty = plan_of()
    base = simulate(problem, empty)
    bought = simulate(problem, empty, purchases=[Purchase(NOW, {"gold": 3000})])
    assert bought.valid
    assert bought.metrics.spill["gold"] == base.metrics.spill["gold"] - 3000
    short = simulate(problem, empty, purchases=[Purchase(NOW, {"gold": 6000})])
    assert [(v.code, v.task_id) for v in short.violations] == [("insufficient_funds", None)]


def test_series_step_is_a_multiple_of_five_minutes_bounding_the_points() -> None:
    assert series_step(30 * DAY_S) == HOUR
    assert series_step(DAY_S) == 300
    assert 365 * DAY_S // series_step(365 * DAY_S) <= 720


def test_a_payment_waits_for_its_funds_and_the_next_ones_keep_their_order() -> None:
    first = make_task("1:0:2", duration_s=HOUR, cost={"gold": 4000})
    second = make_task("2:0:2", duration_s=HOUR, cost={"gold": 1000})
    problem = gold_problem(
        (first, second),
        resources=ResourceModel(
            initial={"gold": 0}, cap={"gold": 5000}, income_per_hour={"gold": 1000}
        ),
    )
    # Planned for a richer income: both start at once.
    plan = plan_of(("1:0:2", "builder", NOW, NOW + HOUR), ("2:0:2", "lab", NOW, NOW + HOUR))
    funded = replay(problem, plan).funded
    assert funded["1:0:2"] == NOW + 4 * HOUR
    assert funded["2:0:2"] == NOW + 5 * HOUR


def test_spill_windows_add_up_to_the_spill_of_the_simulator() -> None:
    task = make_task("1:0:2", duration_s=HOUR, cost={"gold": 2000})
    problem = gold_problem((task,))
    plan = plan_of(("1:0:2", "builder", NOW + 3 * HOUR, NOW + 4 * HOUR))
    windows = replay(problem, plan).windows
    assert [(w.start, w.end) for w in windows] == [
        (NOW, NOW + 3 * HOUR),
        (NOW + 5 * HOUR, problem.horizon_end),
    ]
    assert sum(w.lost for w in windows) // 3600 == simulate(problem, plan).metrics.spill["gold"]


def test_observed_rate_uses_the_last_two_inputs_net_of_spending() -> None:
    history = [(NOW, 900), (NOW, 1000), (NOW + 10 * HOUR, 1500)]
    spent = [(NOW - HOUR, 700), (NOW + 5 * HOUR, 2000), (NOW + 11 * HOUR, 300)]
    rate = observed_rate(history, spent)
    assert rate["status"] == "estimated"
    assert rate["value"] == 250
    single = observed_rate([(NOW, 1000), (NOW, 1100)], [])
    assert single["value"] is None and single["status"] == "unknown"


def test_blocking_ignores_a_delay_that_no_resource_explains() -> None:
    # The plan waits two hours on its own: no resource is short.
    task = make_task("1:0:2", duration_s=HOUR, cost={"gold": 1000})
    problem = gold_problem((task,))
    assert blocking(problem, plan_of(("1:0:2", "builder", NOW + 2 * HOUR, NOW + 3 * HOUR))) is None


def test_blocking_names_the_short_resource() -> None:
    task = make_task("1:0:2", duration_s=HOUR, cost={"gold": 3000})
    problem = gold_problem(
        (task,),
        resources=ResourceModel(
            initial={"gold": 0}, cap={"gold": 5000}, income_per_hour={"gold": 1000}
        ),
    )
    found = blocking(problem, plan_of(("1:0:2", "builder", NOW + 3 * HOUR, NOW + 4 * HOUR)))
    assert found == {"resource": "gold", "delayed_s": 3 * HOUR, "tasks": ["1:0:2"]}


def test_a_wall_is_never_bought_with_gold_a_planned_task_needs() -> None:
    pack = load_pack(json.loads(PACK.read_text(encoding="utf-8")))
    task = make_task("1:0:2", duration_s=HOUR, cost={"gold": 5000})
    problem = gold_problem(
        (task,), excluded=(ExcludedTask(data_id=WALL, to_level=2, reason="wall_sink"),)
    )
    plan = plan_of(("1:0:2", "builder", NOW + 1800, NOW + 1800 + HOUR))
    bought = walls(problem, plan, Objective(spill=0.1), pack)
    assert len(bought) == 1
    # Stock at the cap before the task, but a wall then would leave the task short.
    assert bought[0]["at"] >= NOW + 1800
    purchase = Purchase(bought[0]["at"], bought[0]["cost"])
    assert simulate(problem, plan, purchases=[purchase]).valid
