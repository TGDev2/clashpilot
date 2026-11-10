"""Unit tests of the explanations: counterfactual, reliability and the message templates."""

from __future__ import annotations

import re

import pytest

from clashpilot.planning.explain import counterfactual, explain_plan, reliability
from clashpilot.planning.messages import TEMPLATES, entry
from clashpilot.planning.model import (
    INVALID_SCORE,
    Assignment,
    Candidate,
    Decision,
    Plan,
    Proof,
)
from clashpilot.planning.simulate import simulate
from support.problems import HOUR, NOW, make_problem

WEEK = 7 * 24 * HOUR


def decision(chosen: Candidate, *others: Candidate) -> Decision:
    return Decision(at=chosen.start, candidates=(chosen, *others), chosen=chosen)


def test_counterfactual_is_the_best_other_valid_candidate() -> None:
    chosen = Candidate("1001:0:2", NOW, 0.9)
    made = decision(
        chosen,
        Candidate("1002:0:2", NOW, 0.4),
        Candidate("1003:0:2", NOW, 0.7),
        Candidate("1001:0:3", NOW, INVALID_SCORE),
    )

    found = counterfactual(make_problem(), made, chosen_end=NOW + 2 * HOUR)

    assert found is not None
    assert found["alternative_task_id"] == "1003:0:2"
    assert found["score_delta"] == pytest.approx(0.2, abs=1e-12)
    # 1003:0:2 lasts 3 h, the chosen task ends after 2 h.
    assert found["finish_delta_s"] == HOUR


def test_counterfactual_keeps_the_first_of_tied_candidates() -> None:
    chosen = Candidate("1001:0:2", NOW, 0.9)
    made = decision(chosen, Candidate("1002:0:2", NOW, 0.5), Candidate("1003:0:2", NOW, 0.5))

    found = counterfactual(make_problem(), made, chosen_end=NOW + 2 * HOUR)

    assert found is not None and found["alternative_task_id"] == "1002:0:2"


def test_a_wait_alternative_gives_the_delay_of_the_wait() -> None:
    chosen = Candidate("1001:0:2", NOW, 0.9)
    made = decision(chosen, Candidate(None, NOW + 600, 0.8))

    found = counterfactual(make_problem(), made, chosen_end=NOW + 2 * HOUR)

    assert found is not None
    assert found["alternative_task_id"] is None
    assert found["finish_delta_s"] == 600


def test_no_valid_alternative_gives_no_counterfactual() -> None:
    chosen = Candidate("1001:0:2", NOW, 0.9)
    made = decision(chosen, Candidate("1002:0:2", NOW, INVALID_SCORE))

    assert counterfactual(make_problem(), made, chosen_end=NOW + 2 * HOUR) is None


def test_a_plan_without_trace_is_explained_with_no_trace() -> None:
    problem = make_problem()
    assignment = Assignment("1002:0:2", "builder", 0, NOW, NOW + HOUR)
    plan = Plan((assignment,), "test", Proof(kind="feasible", objective=0.0))

    explained = explain_plan(problem, plan, simulate(problem, plan))

    entry_of = explained["1002:0:2"]
    assert "no_trace" in {reason["code"] for reason in entry_of["why"]["reasons"]}
    assert entry_of["counterfactual"] is None
    spends = [d for d in entry_of["drawbacks"] if d["code"] == "spends"]
    assert spends == [
        entry("spends", {"resource": "gold", "amount": 1000}, amount=1000, label="or")
    ]


@pytest.mark.parametrize(
    ("fictional", "status", "age", "level", "codes"),
    [
        (False, "confirmed", HOUR, "high", set()),
        (True, "confirmed", HOUR, "low", {"fictional_pack"}),
        (False, "estimated", HOUR, "low", {"estimated_resource"}),
        (False, "confirmed", WEEK + 1, "low", {"stale_resource"}),
        (False, "confirmed", WEEK, "medium", {"aging_resource"}),
        (
            True,
            "estimated",
            WEEK + 1,
            "low",
            {"fictional_pack", "estimated_resource", "stale_resource"},
        ),
    ],
)
def test_reliability_levels(
    fictional: bool, status: str, age: int, level: str, codes: set[str]
) -> None:
    found = reliability({"gold"}, {"gold": (status, NOW - age)}, fictional=fictional, now=NOW)

    assert found["level"] == level
    assert {factor["code"] for factor in found["factors"]} == codes
    for factor in found["factors"]:
        if factor["code"] != "fictional_pack":
            assert factor["value"] == "gold"


def test_reliability_ignores_resources_that_are_not_consumed() -> None:
    found = reliability(
        {"gold"},
        {"gold": ("confirmed", NOW), "elixir": ("estimated", NOW - 2 * WEEK)},
        fictional=False,
        now=NOW,
    )

    assert found == {"level": "high", "factors": []}


def test_no_template_says_optimal() -> None:
    for code, template in TEMPLATES.items():
        assert not re.search(r"optim", template, re.IGNORECASE), code
