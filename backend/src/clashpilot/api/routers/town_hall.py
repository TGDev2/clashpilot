"""Town hall launch comparator route (E26): when to launch the next town hall.

The comparison runs in the thread pool on copies of the latest plan's problem
(`planning/town_hall.py`) and writes nothing. Each option is a plan of the engine asked, judged by
`simulate`; dates are UTC epochs read from those plans.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.critical import CriticalTargetOut
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.api.routers.plans import ProofOut
from clashpilot.api.routers.scenarios import (
    ReasonOut,
    base_inputs,
    missing_input_error,
    unknown_strategy_error,
)
from clashpilot.planning import town_hall
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.service import (
    UnknownEngineError,
    UnknownLaneError,
    UnknownStrategyError,
    planner_config,
    resolve_params,
)

router = APIRouter(prefix="/api/villages", tags=["town-hall"])


class TownHallCompareRequest(BaseModel):
    """Without `engine` and `profile`, the defaults of `POST plans` (profile `fast`); without
    `skip_lanes`, the latest plan's `town_hall_skip_lanes`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    engine: str | None = None
    profile: Literal["fast", "repro"] | None = None
    skip_lanes: list[str] | None = None


class CompositionLeftOut(BaseModel):
    """The current-level tasks of the plan's composition not finished, and their work left."""

    count: int
    work_s: int


class HeroLevelOut(BaseModel):
    data_id: int
    name: str
    level_reached: int


class LaunchOptionOut(BaseModel):
    """One moment to launch the town hall; the figures are read at its end (`th_finish`).

    An `infeasible` option carries its reasons and no dates nor figures.
    """

    kind: Literal["after", "no_wait", "discount_edge"]
    status: Literal["ok", "infeasible"]
    reasons: list[ReasonOut]
    th_start: int | None
    th_finish: int | None
    idle_builder_s: int | None
    unfinished_count: int | None
    unfinished_by_lane: dict[str, int]
    unfinished_work_s_by_lane: dict[str, int]
    unfinished_composition: CompositionLeftOut | None
    heroes: list[HeroLevelOut]
    engine: str
    proof: ProofOut | None


class TownHallComparisonOut(BaseModel):
    """The town hall aimed at, the lanes it does not wait for under `after`, and the options."""

    target: CriticalTargetOut | None
    skip_lanes: list[str]
    options: list[LaunchOptionOut]
    reference_plan_id: int
    now: int
    scope: str


_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."},
    409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
    422: {
        "model": ErrorBody,
        "description": "Code unknown_engine, unknown_strategy, unknown_lane or invalid_request.",
    },
}


def _compare(
    request: Request, village_id: str, payload: TownHallCompareRequest
) -> TownHallComparisonOut:
    try:
        resolved = resolve_params(None, payload.engine, payload.profile)
    except UnknownEngineError as error:
        raise ApiError(422, "unknown_engine", f"Moteur inconnu : {error.engine}.") from None
    inputs, stored, same_snapshot = base_inputs(request, village_id)
    config = planner_config(request.app.state.settings.planner_overrides, resolved.profile)
    context = town_hall.Context(
        inputs=inputs,
        params=stored.params,
        body=stored.body,
        config=config,
        engine_name=resolved.engine,
        same_snapshot=same_snapshot,
        plan_id=stored.plan_id,
    )
    try:
        answer = town_hall.compare_town_hall(context, payload.skip_lanes)
    except MissingInputError as error:
        raise missing_input_error(error) from None
    except UnknownStrategyError as error:
        raise unknown_strategy_error(error.strategy) from None
    except UnknownLaneError as error:
        raise ApiError(422, "unknown_lane", f"File inconnue : {', '.join(error.lanes)}.") from None
    return TownHallComparisonOut.model_validate(answer)


@router.post(
    "/{village_id}/town-hall/compare", response_model=TownHallComparisonOut, responses=_ERRORS
)
async def post_town_hall_compare(
    request: Request, village_id: str, payload: TownHallCompareRequest
) -> TownHallComparisonOut:
    """Compare launching the next town hall after the current level, as soon as a builder stays
    free, or at the end of a builder reduction; simulated on the latest plan's inputs."""
    return await run_in_threadpool(_compare, request, village_id, payload)
