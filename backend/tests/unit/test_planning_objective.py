"""Unit tests of the score: the exact formula of SPEC section 4 on hand-computed cases."""

from __future__ import annotations

import pytest

from clashpilot.planning.model import INVALID_SCORE, Assignment, Goal, Plan, Proof, ResourceModel
from clashpilot.planning.objective import Objective, score
from clashpilot.planning.simulate import simulate
from support.problems import DAY, HOUR, NOW, make_calendar, make_problem, make_task


def plan_of(*assignments: Assignment) -> Plan:
    return Plan(assignments=assignments, engine="test", proof=Proof("heuristic", 0.0))


ONE = make_task("1:0:2", duration_s=6 * HOUR, value=3.0, cost={})
TWO = make_task("2:0:2", duration_s=12 * HOUR, value=1.0, cost={})
PLAN = plan_of(
    Assignment(ONE.id, "builder", 0, NOW, NOW + 6 * HOUR),
    Assignment(TWO.id, "builder", 1, NOW + 12 * HOUR, NOW + DAY),
)


def problem(**overrides: object) -> object:
    fields: dict[str, object] = {
        "tasks": (ONE, TWO),
        "horizon_end": NOW + 2 * DAY,
        "lanes": {"builder": 2},
        "calendar": make_calendar(
            sleep=[(NOW + 5 * HOUR, NOW + 7 * HOUR)], preferred=[(NOW + 23 * HOUR, NOW + DAY + 1)]
        ),
        "resources": ResourceModel(
            initial={"gold": 0}, cap={"gold": 1000}, income_per_hour={"gold": 100}
        ),
    }
    fields.update(overrides)
    return make_problem(**fields)


def test_the_default_objective_is_the_discounted_value() -> None:
    prob = problem()
    sim = simulate(prob, PLAN)
    # H = 48 h; ONE ends at 6 h, TWO at 24 h.
    expected = (3.0 * (42 / 48) + 1.0 * (24 / 48)) / 4.0
    assert score(sim, Objective(), prob) == pytest.approx(expected)


def test_every_weighted_term_follows_the_formula() -> None:
    prob = problem(goal=Goal(frozenset({TWO.id})))
    sim = simulate(prob, PLAN, reference=plan_of(Assignment(ONE.id, "builder", 0, NOW + HOUR, 0)))
    weights = Objective(
        value=2.0,
        goal=3.0,
        idle=5.0,
        night=7.0,
        logins=11.0,
        spill=13.0,
        perturbation=17.0,
        preferred=19.0,
    )
    horizon = 48 * HOUR
    discounted = (3.0 * (42 / 48) + 1.0 * (24 / 48)) / 4.0
    goal_term = DAY / horizon
    idle = 1 - (18 * HOUR) / (2 * horizon)
    night = 1 / 2  # ONE ends in sleep
    logins = (2 / 2) / 24  # two logins over two days
    # Income 100/h for 48 h; the cap of 1000 is reached after 10 h.
    spill = (4800 - 1000) / 4800
    perturbation = HOUR / (1 * horizon)
    preferred = 1 / 2  # TWO ends in preferred
    expected = (
        2.0 * discounted
        - 3.0 * goal_term
        - 5.0 * idle
        - 7.0 * night
        - 11.0 * logins
        - 13.0 * spill
        - 17.0 * perturbation
        + 19.0 * preferred
    )
    assert score(sim, weights, prob) == pytest.approx(expected)


def test_a_goal_not_reached_costs_two() -> None:
    prob = problem(goal=Goal(frozenset({TWO.id})))
    only_one = plan_of(PLAN.assignments[0])
    sim = simulate(prob, only_one)
    without_goal = score(sim, Objective(goal=0.0), prob)
    assert score(sim, Objective(goal=1.0), prob) == pytest.approx(without_goal - 2.0)


def test_an_invalid_plan_scores_invalid() -> None:
    prob = problem()
    late = plan_of(Assignment(ONE.id, "builder", 0, NOW - 1, NOW - 1 + 6 * HOUR))
    assert score(simulate(prob, late), Objective(), prob) == INVALID_SCORE
