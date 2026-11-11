"""Unit tests of the progression strategies (E16) beyond the acceptance tests."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from clashpilot.planning.objective import Objective
from clashpilot.planning.replan import REPLAN_PERTURBATION, default_objective
from clashpilot.planning.service import DEFAULT_OBJECTIVE, PlanParams, resolve_params
from clashpilot.planning.simulate import simulate
from clashpilot.planning.solver.cpsat import CpSatPlanner
from clashpilot.planning.strategies import (
    StrategyFileError,
    apply_strategy,
    goal_of,
    load_strategies,
    parse_strategies,
)
from support.problems import HOUR, NOW, TEST_CONFIG, make_problem, make_task

HERO = "9001:0:2"
WINDOW = (NOW, NOW + 5 * HOUR)


def entry(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"label": "Essai", "weights": {"value": 1.0}}
    base.update(fields)
    return base


@pytest.mark.parametrize(
    "fields",
    [
        {"weights": {"value": 1.0, "speed": 2.0}},
        {"constraints": ["no_such_constraint"]},
        {"category_multipliers": {"defense": -1.0}},
        {"tag_multipliers": {"hero": "high"}},
        {"label": ""},
    ],
)
def test_a_malformed_strategy_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(StrategyFileError):
        parse_strategies({"strategies": {"broken": entry(**fields)}})


def test_load_strategies_reads_the_file_it_is_given(tmp_path: Path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(json.dumps({"strategies": {"only": entry(weights={"idle": 3.0})}}), "utf-8")
    strategies = load_strategies(path)
    assert list(strategies) == ["only"]
    assert strategies["only"].objective() == Objective(idle=3.0)


def test_the_balanced_strategy_keeps_the_default_objective() -> None:
    assert load_strategies()["balanced"].objective() == DEFAULT_OBJECTIVE


def test_goal_of_names_every_task_of_the_item_up_to_the_level() -> None:
    problem = make_problem()
    assert goal_of(problem, 1001, 3).task_ids == {"1001:0:2", "1001:0:3"}
    assert goal_of(problem, 1001, 2).task_ids == {"1001:0:2"}


def test_params_stored_before_strategies_read_without_strategy() -> None:
    params = PlanParams.from_json({"horizon_days": 7, "engine": "rollout", "profile": "repro"})
    assert params == PlanParams(horizon_days=7, engine="rollout", profile="repro")


def test_params_with_a_strategy_survive_their_json_form() -> None:
    params = resolve_params(
        7,
        "rollout",
        "repro",
        strategy="war_cwl",
        composition=[9000007],
        goal=(9000001, 3),
        war_windows=[(NOW + 10, NOW + 20), (NOW, NOW + 5)],
    )
    assert params.war_windows == ((NOW, NOW + 5), (NOW + 10, NOW + 20))
    stored = json.loads(json.dumps(dataclasses.asdict(params)))
    assert PlanParams.from_json(stored) == params


def test_a_replanning_starts_from_the_strategy_weights() -> None:
    params = PlanParams(horizon_days=7, engine="rollout", profile="repro", strategy="low_play")
    expected = load_strategies()["low_play"].objective()
    assert default_objective(params) == dataclasses.replace(
        expected, perturbation=REPLAN_PERTURBATION
    )
    bare = PlanParams(horizon_days=7, engine="rollout", profile="repro")
    assert default_objective(bare) == default_objective()


def test_cpsat_keeps_hero_upgrades_out_of_war_windows() -> None:
    problem = make_problem(
        tasks=(
            make_task(HERO, duration_s=3 * HOUR, value=30.0, category="hero", tags=("hero",)),
            make_task("9002:0:2", duration_s=2 * HOUR),
        )
    )
    applied, objective = apply_strategy(problem, load_strategies()["war_cwl"], war_windows=[WINDOW])
    plan = CpSatPlanner().solve(applied, objective, TEST_CONFIG)
    sim = simulate(applied, plan)
    assert sim.valid, sim.violations
    assert "solver_invalid_plan" not in plan.warnings
    hero = next(a for a in plan.assignments if a.task_id == HERO)
    assert hero.start >= WINDOW[1]


def test_plan_request_refuses_a_war_window_ending_before_it_starts() -> None:
    from clashpilot.api.routers.plans import PlanRequest

    with pytest.raises(ValueError):
        PlanRequest.model_validate({"war_windows": [{"start": NOW + 10, "end": NOW}]})
