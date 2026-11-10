"""Unit tests of the plan service: defaults, planner configuration and input hash."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.service import (
    DEFAULT_OBJECTIVE,
    UnknownEngineError,
    input_hash,
    planner_config,
    resolve_params,
)
from clashpilot.settings import Settings
from support.problems import make_problem


@pytest.fixture
def no_auto() -> Iterator[None]:
    saved = PLANNERS.pop("auto", None)
    try:
        yield
    finally:
        if saved is not None:
            PLANNERS["auto"] = saved


def test_defaults_without_auto(no_auto: None) -> None:
    params = resolve_params(None, None, None)

    assert (params.engine, params.profile, params.horizon_days) == ("rollout", "fast", 90)


def test_defaults_with_auto(no_auto: None) -> None:
    PLANNERS["auto"] = PLANNERS["rollout"]
    try:
        assert resolve_params(None, None, None).engine == "auto"
    finally:
        del PLANNERS["auto"]


def test_explicit_parameters_are_kept() -> None:
    params = resolve_params(12, "baseline", "repro")

    assert (params.engine, params.profile, params.horizon_days) == ("baseline", "repro", 12)


def test_an_unknown_engine_is_refused() -> None:
    with pytest.raises(UnknownEngineError):
        resolve_params(None, "nope", None)


def test_planner_config_applies_overrides_but_the_request_names_the_profile() -> None:
    config = planner_config({"max_rollouts": 7, "profile": "repro", "unknown": 1}, "fast")

    assert config.max_rollouts == 7
    assert config.profile == "fast"


def test_input_hash_is_stable_and_follows_every_part() -> None:
    problem = make_problem()
    reference = input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 42)

    assert reference == input_hash(make_problem(), DEFAULT_OBJECTIVE, "rollout", "repro", 42)
    others = {
        input_hash(problem, DEFAULT_OBJECTIVE, "baseline", "repro", 42),
        input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "fast", 42),
        input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 43),
        input_hash(
            problem, dataclasses.replace(DEFAULT_OBJECTIVE, idle=0.5), "rollout", "repro", 42
        ),
        input_hash(
            make_problem(horizon_end=problem.horizon_end + 1),
            DEFAULT_OBJECTIVE,
            "rollout",
            "repro",
            42,
        ),
    }
    assert reference not in others
    assert len(others) == 5


def test_solver_max_time_comes_from_the_environment() -> None:
    settings = Settings.from_env({"CLASHPILOT_SOLVER_MAX_TIME_S": "2.5"})

    assert settings.planner_overrides == {"solver_max_time_s": 2.5}
    assert Settings.from_env({}).planner_overrides == {}
