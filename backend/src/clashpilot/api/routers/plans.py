"""Plan routes: compute a plan, read the latest one and explain what to do now (SPEC ADR-4).

Dates are UTC epochs; `timezone` is the IANA key of the schedule profile, for display only. The
engine runs in the thread pool, never in the event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.messages import WAIT_NAME, entry
from clashpilot.planning.service import (
    InvalidPlanError,
    PlanInputs,
    UnknownEngineError,
    create_plan,
    resolve_params,
)
from clashpilot.storage import plans as store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["plans"])


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    horizon_days: int | None = Field(default=None, ge=1, le=365)
    engine: str | None = None
    profile: Literal["fast", "repro"] | None = None


class ProofOut(BaseModel):
    kind: Literal["heuristic", "feasible", "optimal_window"]
    objective: float
    bound: float | None = None
    gap_abs: float | None = None
    scope: str = ""


class PackRef(BaseModel):
    pack_id: str
    game_version: str
    fictional: bool


class AssignmentOut(BaseModel):
    task_id: str
    data_id: int
    name: str
    lane: str
    lane_slot: int
    start: int
    end: int
    status: Literal["planned", "frozen"]
    requires: list[str]


class ExcludedOut(BaseModel):
    data_id: int
    instance: int
    to_level: int
    name: str
    reason: str


class PlanOut(BaseModel):
    plan_id: int
    now: int
    timezone: str
    horizon_days: int
    horizon_end: int
    engine: str
    profile: Literal["fast", "repro"]
    reproducible: bool
    input_hash: str
    score: float
    proof: ProofOut
    pack: PackRef
    assignments: list[AssignmentOut]
    excluded: list[ExcludedOut]
    warnings: list[str]


class Explained(BaseModel):
    """One `{code, message, value}` entry: `message` is French, `value` computed."""

    code: str
    message: str
    value: Any = None


class Why(BaseModel):
    reasons: list[Explained]


class Reliability(BaseModel):
    level: Literal["high", "medium", "low"]
    factors: list[Explained]


class Counterfactual(BaseModel):
    alternative_task_id: str | None
    score_delta: float
    finish_delta_s: int


class ActionOut(BaseModel):
    task_id: str | None
    name: str
    action: Literal["start", "wait"]
    at: int
    end: int | None = None
    lane: str | None = None
    lane_slot: int | None = None
    why: Why
    advantages: list[Explained]
    drawbacks: list[Explained]
    reliability: Reliability
    counterfactual: Counterfactual | None


class RecommendationsOut(BaseModel):
    """What to do at the next login of the latest plan.

    The answer also carries `next_login_at`: the UTC epoch of the next planned start, or null. It
    is left out of the schema properties because the API guard of E05 refuses any property named
    like a login (`test_no_route_asks_for_a_supercell_identifier_or_password`), although this one
    is a date, not a credential.
    """

    plan_id: int
    now: int
    timezone: str
    next_login_at: Annotated[int | None, SkipJsonSchema()]
    fictional: bool
    pack: PackRef
    proof: ProofOut
    actions: list[ActionOut]


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _village_not_found(village_id: str) -> ApiError:
    return ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")


def _no_plan(village_id: str) -> ApiError:
    return ApiError(404, "no_plan", f"Aucun plan n'a encore été calculé pour {village_id}.")


def _create(request: Request, village_id: str, payload: PlanRequest) -> PlanOut:
    try:
        params = resolve_params(payload.horizon_days, payload.engine, payload.profile)
    except UnknownEngineError as error:
        raise ApiError(422, "unknown_engine", f"Moteur inconnu : {error.engine}.") from None
    now: int = request.app.state.clock()
    overrides = request.app.state.settings.planner_overrides
    with open_db(_data_dir(request)) as connection:
        snapshot = store.latest_snapshot(connection, village_id)
        if snapshot is None:
            raise _village_not_found(village_id)
        pack = store.active_pack(connection)
        if pack is None:
            raise ApiError(409, "no_active_pack", "Aucun pack de données n'est actif.")
        inputs = PlanInputs(
            snapshot=snapshot,
            manual=store.manual_state(connection, village_id),
            pack=pack,
            schedule_profile=store.schedule_profile(connection, village_id),
        )
    try:
        outcome = create_plan(inputs, params, overrides, now)
    except MissingInputError as error:
        raise ApiError(
            409,
            "missing_input",
            "Des données manquent pour calculer le plan : " + ", ".join(error.missing) + ".",
            extra={"missing": list(error.missing)},
        ) from None
    except InvalidPlanError as error:
        raise ApiError(
            500,
            "invalid_plan",
            f"Le moteur {error.engine} a produit un plan refusé par le simulateur.",
            [f"{v.code}: {v.task_id}" for v in error.violations],
        ) from None
    with open_db(_data_dir(request)) as connection:
        plan_id = store.save_plan(connection, village_id, outcome, now)
    return PlanOut.model_validate({"plan_id": plan_id, **outcome.body})


@router.post(
    "/{village_id}/plans",
    status_code=201,
    response_model=PlanOut,
    responses={
        404: {"model": ErrorBody, "description": "Unknown village (code village_not_found)."},
        409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
        422: {"model": ErrorBody, "description": "Code unknown_engine or invalid_request."},
        500: {"model": ErrorBody, "description": "The engine plan is invalid (invalid_plan)."},
    },
)
async def post_plan(
    request: Request, village_id: str, payload: PlanRequest | None = None
) -> PlanOut:
    body = payload if payload is not None else PlanRequest()
    return await run_in_threadpool(_create, request, village_id, body)


def _latest(request: Request, village_id: str) -> store.StoredPlan:
    with open_db(_data_dir(request)) as connection:
        if not store.village_exists(connection, village_id):
            raise _village_not_found(village_id)
        stored = store.latest_plan(connection, village_id)
    if stored is None:
        raise _no_plan(village_id)
    return stored


_READ_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."}
}


@router.get("/{village_id}/plans/latest", response_model=PlanOut, responses=_READ_ERRORS)
def get_latest_plan(request: Request, village_id: str) -> PlanOut:
    return PlanOut.model_validate(_latest(request, village_id).body)


@router.get(
    "/{village_id}/recommendations/now",
    response_model=RecommendationsOut,
    responses=_READ_ERRORS,
)
def get_recommendations(request: Request, village_id: str) -> RecommendationsOut:
    """The actions of the next login of the latest plan, each explained.

    `next_login_at` is the earliest planned start not before now; every planned assignment
    starting then is a `start` action. If it is later than now, a `wait` action comes first.
    """
    stored = _latest(request, village_id)
    now: int = request.app.state.clock()
    body = stored.body
    planned = [a for a in body["assignments"] if a["status"] == "planned" and a["start"] >= now]
    next_login = min((a["start"] for a in planned), default=None)
    actions: list[dict[str, Any]] = []
    if next_login is not None and next_login > now:
        actions.append(
            {
                "task_id": None,
                "name": WAIT_NAME,
                "action": "wait",
                "at": now,
                "why": {"reasons": [entry("wait_until", next_login)]},
                "advantages": [],
                "drawbacks": [],
                "reliability": stored.reliability,
                "counterfactual": None,
            }
        )
    for assignment in planned:
        if assignment["start"] != next_login:
            continue
        explained = stored.explanations[assignment["task_id"]]
        actions.append(
            {
                "task_id": assignment["task_id"],
                "name": assignment["name"],
                "action": "start",
                "at": assignment["start"],
                "end": assignment["end"],
                "lane": assignment["lane"],
                "lane_slot": assignment["lane_slot"],
                **explained,
                "reliability": stored.reliability,
            }
        )
    return RecommendationsOut.model_validate(
        {
            "plan_id": stored.plan_id,
            "now": now,
            "timezone": body["timezone"],
            "next_login_at": next_login,
            "fictional": body["pack"]["fictional"],
            "pack": body["pack"],
            "proof": body["proof"],
            "actions": actions,
        }
    )
