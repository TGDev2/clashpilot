"""Magic items and gems advisor routes (E19): rank the uses of an item or of gems.

Each comparison runs in the thread pool on copies of the latest plan's problem
(`planning/items.py`) and writes nothing. The ranking is indicative: the answer carries the proof
of the reference plan, its id and the scope of the comparison.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.api.routers.plans import ProofOut, Reliability
from clashpilot.api.routers.scenarios import (
    ReasonOut,
    base_inputs,
    missing_input_error,
    unknown_strategy_error,
)
from clashpilot.planning import items
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.service import (
    UnknownEngineError,
    UnknownStrategyError,
    planner_config,
    resolve_params,
)

router = APIRouter(prefix="/api/villages", tags=["items"])


# --------------------------------------------------------------------------- request


class _Request(BaseModel):
    """Without `engine` and `profile`, the defaults of `POST plans` (profile `fast`)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_tasks: list[str] | None = None
    engine: str | None = None
    profile: Literal["fast", "repro"] | None = None


class ItemCompareRequest(_Request):
    """`item` is a rule key of the active pack; `candidate_tasks` the tasks it may act on."""

    item: str = Field(min_length=1, max_length=100)


class GemsCompareRequest(_Request):
    """`candidate_tasks` limits the running jobs a gem finish is tried on."""


# --------------------------------------------------------------------------- answers


class ConsequenceOut(BaseModel):
    """`job_shortened` carries the job and its two ends, `resource_filled` the two stocks."""

    code: str
    message: str
    task_id: str | None = None
    end_before: int | None = None
    end_after: int | None = None
    resource: str | None = None
    before: int | None = None
    after: int | None = None


class OptionOut(BaseModel):
    """One ranked option; `score_delta` is against the plan without item (0 for `keep`)."""

    kind: Literal["keep", "use", "gem_finish"]
    task_id: str | None
    at: int | None
    score_delta: float
    finish_delta_s: int
    consequences: list[ConsequenceOut]
    reliability: Reliability
    gems: int | None = None


class HelperOptionOut(BaseModel):
    """An upgrade priced by `helper_upgrade`; its effect has no rule, so no score."""

    data_id: int
    from_level: int
    to_level: int
    cost: dict[str, int]
    score_delta: float | None
    consequences: list[ConsequenceOut]
    reliability: Reliability


class HelpersOut(BaseModel):
    status: Literal["ok", "unsupported"]
    reasons: list[ReasonOut]
    options: list[HelperOptionOut]


class ComparisonOut(BaseModel):
    """The ranking, the proof of the reference plan, its id and the comparison's scope."""

    status: Literal["ok", "unsupported", "unavailable"]
    reasons: list[ReasonOut]
    options: list[OptionOut]
    proof: ProofOut | None
    reference_plan_id: int
    scope: str


class GemsComparisonOut(ComparisonOut):
    helpers: HelpersOut


_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."},
    409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
    422: {
        "model": ErrorBody,
        "description": "Code unknown_engine, unknown_strategy or invalid_request.",
    },
}


# --------------------------------------------------------------------------- routes


def _context(request: Request, village_id: str, payload: _Request) -> items.Context:
    try:
        resolved = resolve_params(None, payload.engine, payload.profile)
    except UnknownEngineError as error:
        raise ApiError(422, "unknown_engine", f"Moteur inconnu : {error.engine}.") from None
    inputs, stored, same_snapshot = base_inputs(request, village_id)
    config = planner_config(request.app.state.settings.planner_overrides, resolved.profile)
    return items.Context(
        inputs=inputs,
        params=stored.params,
        body=stored.body,
        config=config,
        engine_name=resolved.engine,
        same_snapshot=same_snapshot,
        plan_id=stored.plan_id,
    )


def _item(request: Request, village_id: str, payload: ItemCompareRequest) -> ComparisonOut:
    context = _context(request, village_id, payload)
    try:
        answer = items.compare_item(context, payload.item, payload.candidate_tasks)
    except MissingInputError as error:
        raise missing_input_error(error) from None
    except UnknownStrategyError as error:
        raise unknown_strategy_error(error.strategy) from None
    return ComparisonOut.model_validate(answer)


def _gems(request: Request, village_id: str, payload: GemsCompareRequest) -> GemsComparisonOut:
    context = _context(request, village_id, payload)
    try:
        answer = items.compare_gems(context, payload.candidate_tasks)
    except MissingInputError as error:
        raise missing_input_error(error) from None
    except UnknownStrategyError as error:
        raise unknown_strategy_error(error.strategy) from None
    return GemsComparisonOut.model_validate(answer)


@router.post("/{village_id}/items/compare", response_model=ComparisonOut, responses=_ERRORS)
async def post_items_compare(
    request: Request, village_id: str, payload: ItemCompareRequest
) -> ComparisonOut:
    """Rank keeping `item` against each of its uses, simulated on the latest plan's inputs."""
    return await run_in_threadpool(_item, request, village_id, payload)


@router.post("/{village_id}/gems/compare", response_model=GemsComparisonOut, responses=_ERRORS)
async def post_gems_compare(
    request: Request, village_id: str, payload: GemsCompareRequest
) -> GemsComparisonOut:
    """Rank keeping the gems against finishing each running job; price the helpers' upgrades."""
    return await run_in_threadpool(_gems, request, village_id, payload)
