"""Unit tests of the plan options of E29 (ADR-CP5) beyond the acceptance tests."""

from __future__ import annotations

from clashpilot.planning.critical import unknown_lanes
from clashpilot.planning.model import Problem
from clashpilot.planning.service import (
    DEFAULT_OBJECTIVE,
    PlanParams,
    hash_options,
    input_hash,
    resolve_params,
)
from clashpilot.planning.strategies import prioritize_composition
from support.problems import HOUR, make_problem, make_task

SCOUT = "9101:0:2"
SAPPER = "9102:0:2"
HERO = "9103:0:2"
WALL = "9104:0:2"


def lab_problem() -> Problem:
    return make_problem(
        tasks=(
            make_task(SCOUT, lane="lab", duration_s=10 * HOUR, category="troop"),
            make_task(SAPPER, lane="lab", duration_s=HOUR, category="troop"),
            make_task(HERO, duration_s=5 * HOUR, category="hero"),
            make_task(WALL, duration_s=HOUR),
        )
    )


def values_of(problem: Problem) -> dict[str, float]:
    return {task.id: task.value for task in problem.tasks}


def test_composition_first_doubles_the_composition_and_zeroes_the_rest_of_its_lane() -> None:
    problem = lab_problem()
    raised = values_of(prioritize_composition(problem, [9101]))
    before = values_of(problem)
    assert raised[SCOUT] == 2 * before[SCOUT]
    assert raised[SAPPER] == 0.0
    # The builder lane keeps its values.
    assert raised[HERO] == before[HERO]
    assert raised[WALL] == before[WALL]


def test_composition_first_never_touches_the_builder_lane() -> None:
    problem = lab_problem()
    assert prioritize_composition(problem, []) == problem
    # A hero in the composition: only builder tasks hold it, nothing changes.
    assert prioritize_composition(problem, [9103]) == problem
    # An id the problem does not hold.
    assert prioritize_composition(problem, [4242]) == problem


def test_unknown_lanes_knows_the_lanes_of_the_capacities_and_of_the_tasks() -> None:
    problem = make_problem(
        lanes={"builder": 2},
        tasks=(make_task(SCOUT, lane="lab"), make_task(WALL)),
    )
    assert unknown_lanes(problem, ["builder", "lab"]) == ()
    assert unknown_lanes(problem, ["pets", "lab", "forge"]) == ("forge", "pets")


def test_the_options_leave_the_hash_of_a_plan_without_them_unchanged() -> None:
    problem = make_problem()
    plain = input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0)
    params = PlanParams(horizon_days=90, engine="rollout", profile="repro")
    assert (
        input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0, **hash_options(params))
        == plain
    )
    skip = input_hash(
        problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0, town_hall_skip_lanes=("lab",)
    )
    first = input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0, composition_first=True)
    assert len({plain, skip, first}) == 3


def test_resolve_params_keeps_each_skipped_lane_once_in_order() -> None:
    params = resolve_params(
        90, "rollout", "repro", town_hall_skip_lanes=["lab", "pets", "lab"], composition_first=True
    )
    assert params.town_hall_skip_lanes == ("lab", "pets")
    assert params.composition_first is True
    assert PlanParams.from_json(
        {"horizon_days": 90, "engine": "rollout", "profile": "repro"}
    ) == PlanParams(horizon_days=90, engine="rollout", profile="repro")
