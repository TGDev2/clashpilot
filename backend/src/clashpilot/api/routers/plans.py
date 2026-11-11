"""Plan routes: compute a plan, read the latest one and explain what to do now (SPEC ADR-4);
replan after an event and read the journal (E11).

Dates are UTC epochs; `timezone` is the IANA key of the schedule profile, for display only. The
engine runs in the thread pool, never in the event loop. A plan is `stale` once an export newer
than the one it was computed on is imported; the event `snapshot_imported` replans on it.

A plan may name a strategy of `GET /api/meta/strategies` with an army composition, a goal and war
windows (E16): the plan carries them back, and every replanning applies them again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.ingest.state import Resource
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.messages import WAIT_NAME, entry
from clashpilot.planning.replan import Event, PreviousPlan, ReplanError, replan
from clashpilot.planning.service import (
    InvalidGoalError,
    InvalidPlanError,
    PlanInputs,
    UnknownEngineError,
    UnknownStrategyError,
    create_plan,
    resolve_params,
)
from clashpilot.storage import journal as journal_store
from clashpilot.storage import plans as store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["plans"])


class GoalRef(BaseModel):
    """Reach `level` on the item `data_id`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    data_id: int
    level: int = Field(ge=1)


class WarWindow(BaseModel):
    """A war window, UTC epochs: no hero upgrade runs in `[start, end)` under `war_cwl`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    start: int
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> WarWindow:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    horizon_days: int | None = Field(default=None, ge=1, le=365)
    engine: str | None = None
    profile: Literal["fast", "repro"] | None = None
    strategy: str | None = None
    composition: list[int] = Field(default_factory=list)
    goal: GoalRef | None = None
    war_windows: list[WarWindow] = Field(default_factory=list)


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
    stale: bool = False
    strategy: str | None = None
    composition: list[int] = Field(default_factory=list)
    goal: GoalRef | None = None
    war_windows: list[WarWindow] = Field(default_factory=list)


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


class _EventIn(BaseModel):
    """`at` defaults to now; `dry_run` answers the report and stores nothing."""

    model_config = ConfigDict(extra="forbid", strict=True)

    at: int | None = Field(default=None, ge=0)
    dry_run: bool = False


class TaskStartedIn(_EventIn):
    type: Literal["task_started"]
    task_id: str


class TaskFinishedIn(_EventIn):
    type: Literal["task_finished"]
    task_id: str


class PriorityChangedIn(_EventIn):
    """`objective`: weights of the objective by name; a weight left out keeps its value.

    A free mapping, so that no schema property is named like a login (E05 guard).
    """

    type: Literal["priority_changed"]
    objective: dict[str, Annotated[float, Field(strict=False)]]


class ItemUsedIn(_EventIn):
    """`task_id`: the started job of a book, null for a potion or a rune."""

    type: Literal["item_used"]
    item: str
    task_id: str | None = None


class ResourcesCorrectedIn(_EventIn):
    """`resources`: whole units observed at `at`."""

    type: Literal["resources_corrected"]
    resources: dict[Resource, Annotated[int, Field(ge=0)]]


class ScheduleChangedIn(_EventIn):
    type: Literal["schedule_changed"]


class GameDataUpdatedIn(_EventIn):
    type: Literal["game_data_updated"]
    pack_id: str


class SnapshotImportedIn(_EventIn):
    type: Literal["snapshot_imported"]


EventIn = Annotated[
    TaskStartedIn
    | TaskFinishedIn
    | PriorityChangedIn
    | ItemUsedIn
    | ResourcesCorrectedIn
    | ScheduleChangedIn
    | GameDataUpdatedIn
    | SnapshotImportedIn,
    Field(discriminator="type"),
]


class PlacementOut(BaseModel):
    start: int
    end: int
    lane: str
    lane_slot: int


class ChangeOut(BaseModel):
    task_id: str
    before: PlacementOut | None
    after: PlacementOut | None


class ConsequencesOut(BaseModel):
    goal_finish_delta_s: int | None
    score_delta: float
    idle_delta_s: int
    messages: list[Explained]


class ReplanOut(BaseModel):
    """`plan_id` is the new plan, null for a dry run."""

    plan_id: int | None
    previous_plan_id: int
    dry_run: bool
    changes: list[ChangeOut]
    unchanged_count: int
    consequences: ConsequencesOut
    proof: ProofOut
    input_hash: str


class JournalEntryOut(BaseModel):
    entry_id: int
    type: str
    at: int
    task_id: str | None
    payload: dict[str, Any]
    plan_id: int | None


def _missing_input(error: MissingInputError) -> ApiError:
    return ApiError(
        409,
        "missing_input",
        "Des données manquent pour calculer le plan : " + ", ".join(error.missing) + ".",
        extra={"missing": list(error.missing)},
    )


def _invalid_plan(error: InvalidPlanError) -> ApiError:
    return ApiError(
        500,
        "invalid_plan",
        f"Le moteur {error.engine} a produit un plan refusé par le simulateur.",
        [f"{v.code}: {v.task_id}" for v in error.violations],
    )


def _no_active_pack() -> ApiError:
    return ApiError(409, "no_active_pack", "Aucun pack de données n'est actif.")


def _stale(connection: sqlite3.Connection, village_id: str, stored: store.StoredPlan) -> bool:
    """A newer export than the one the plan was computed on has been imported."""
    latest = store.latest_snapshot_row(connection, village_id)
    return stored.snapshot_id is not None and latest is not None and latest[0] != stored.snapshot_id


def _no_plan(village_id: str) -> ApiError:
    return ApiError(404, "no_plan", f"Aucun plan n'a encore été calculé pour {village_id}.")


def _unknown_strategy(error: UnknownStrategyError) -> ApiError:
    return ApiError(422, "unknown_strategy", f"Stratégie inconnue : {error.strategy}.")


def _invalid_goal(error: InvalidGoalError) -> ApiError:
    return ApiError(
        422,
        "invalid_goal",
        f"Le pack actif n'a pas de niveau {error.level} pour l'objet {error.data_id}.",
    )


def _create(request: Request, village_id: str, payload: PlanRequest) -> PlanOut:
    try:
        params = resolve_params(
            payload.horizon_days,
            payload.engine,
            payload.profile,
            strategy=payload.strategy,
            composition=payload.composition,
            goal=None if payload.goal is None else (payload.goal.data_id, payload.goal.level),
            war_windows=[(window.start, window.end) for window in payload.war_windows],
        )
    except UnknownEngineError as error:
        raise ApiError(422, "unknown_engine", f"Moteur inconnu : {error.engine}.") from None
    except UnknownStrategyError as error:
        raise _unknown_strategy(error) from None
    now: int = request.app.state.clock()
    overrides = request.app.state.settings.planner_overrides
    with open_db(_data_dir(request)) as connection:
        found = store.latest_snapshot_row(connection, village_id)
        if found is None:
            raise _village_not_found(village_id)
        snapshot_id, snapshot = found
        pack = store.active_pack(connection)
        if pack is None:
            raise _no_active_pack()
        inputs = PlanInputs(
            snapshot=snapshot,
            manual=store.manual_state(connection, village_id),
            pack=pack,
            schedule_profile=store.schedule_profile(connection, village_id),
            journal=journal_store.entries(connection, village_id),
        )
    try:
        outcome = create_plan(inputs, params, overrides, now)
    except InvalidGoalError as error:
        raise _invalid_goal(error) from None
    except UnknownStrategyError as error:
        raise _unknown_strategy(error) from None
    except MissingInputError as error:
        raise _missing_input(error) from None
    except InvalidPlanError as error:
        raise _invalid_plan(error) from None
    with open_db(_data_dir(request)) as connection:
        plan_id = store.save_plan(connection, village_id, outcome, now, snapshot_id=snapshot_id)
    return PlanOut.model_validate({"plan_id": plan_id, **outcome.body, "stale": False})


@router.post(
    "/{village_id}/plans",
    status_code=201,
    response_model=PlanOut,
    responses={
        404: {"model": ErrorBody, "description": "Unknown village (code village_not_found)."},
        409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
        422: {
            "model": ErrorBody,
            "description": "Code unknown_engine, unknown_strategy, invalid_goal or "
            "invalid_request.",
        },
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
    stored = _latest(request, village_id)
    with open_db(_data_dir(request)) as connection:
        stale = _stale(connection, village_id, stored)
    return PlanOut.model_validate({**stored.body, "stale": stale})


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


# --------------------------------------------------------------------------- replanning (E11)


def _event(payload: Any, now: int) -> Event:
    return Event(
        type=payload.type,
        at=payload.at if payload.at is not None else now,
        task_id=getattr(payload, "task_id", None),
        item=getattr(payload, "item", None),
        resources=getattr(payload, "resources", None),
        objective=getattr(payload, "objective", None),
        pack_id=getattr(payload, "pack_id", None),
    )


def _replan(request: Request, village_id: str, plan_id: int, payload: Any) -> ReplanOut:
    now: int = request.app.state.clock()
    overrides = request.app.state.settings.planner_overrides
    with open_db(_data_dir(request)) as connection:
        found = store.latest_snapshot_row(connection, village_id)
        if found is None:
            raise _village_not_found(village_id)
        snapshot_id, snapshot = found
        if not store.plan_exists(connection, village_id, plan_id):
            raise ApiError(404, "plan_not_found", f"Le plan {plan_id} est inconnu.")
        stored = store.latest_plan(connection, village_id)
        assert stored is not None
        if stored.plan_id != plan_id:
            raise ApiError(
                409,
                "plan_outdated",
                f"Le plan {plan_id} a été remplacé par le plan {stored.plan_id}.",
            )
        pack = store.active_pack(connection)
        if pack is None:
            raise _no_active_pack()
        if isinstance(payload, GameDataUpdatedIn) and not store.pack_exists(
            connection, payload.pack_id
        ):
            raise ApiError(404, "pack_not_found", f"Le pack {payload.pack_id} est inconnu.")
        inputs = PlanInputs(
            snapshot=snapshot,
            manual=store.manual_state(connection, village_id),
            pack=pack,
            schedule_profile=store.schedule_profile(connection, village_id),
            journal=journal_store.entries(connection, village_id),
        )
    previous = PreviousPlan(
        plan_id=stored.plan_id,
        body=stored.body,
        params=stored.params,
        input_hash=stored.input_hash,
        explanations=stored.explanations,
        same_snapshot=stored.snapshot_id == snapshot_id,
    )
    try:
        result = replan(inputs, previous, _event(payload, now), now, overrides)
    except ReplanError as error:
        raise ApiError(error.status, error.code, error.message) from None
    except UnknownStrategyError as error:
        raise _unknown_strategy(error) from None
    except MissingInputError as error:
        raise _missing_input(error) from None
    except InvalidPlanError as error:
        raise _invalid_plan(error) from None
    new_plan_id: int | None = None
    if not payload.dry_run:
        with open_db(_data_dir(request)) as connection:
            new_plan_id = store.save_plan(
                connection,
                village_id,
                result.outcome,
                now,
                snapshot_id=snapshot_id,
                previous_plan_id=stored.plan_id,
            )
            journal_store.add_entry(connection, village_id, result.entry, new_plan_id, now)
    return ReplanOut.model_validate(
        {"plan_id": new_plan_id, "dry_run": payload.dry_run, **result.report}
    )


@router.post(
    "/{village_id}/plans/{plan_id}/events",
    response_model=ReplanOut,
    responses={
        404: {
            "model": ErrorBody,
            "description": "Code village_not_found, plan_not_found, task_not_found or "
            "pack_not_found.",
        },
        409: {
            "model": ErrorBody,
            "description": "Code plan_outdated, already_started, task_not_started, "
            "task_not_ready, lane_full, pack_not_active, missing_input or no_active_pack.",
        },
        422: {
            "model": ErrorBody,
            "description": "Code future_event, unsupported_item, item_not_applicable, "
            "invalid_event or invalid_request.",
        },
        500: {"model": ErrorBody, "description": "The engine plan is invalid (invalid_plan)."},
    },
)
async def post_event(
    request: Request, village_id: str, plan_id: int, payload: EventIn
) -> ReplanOut:
    """Record an event on the latest plan and replan (a dry run records nothing)."""
    return await run_in_threadpool(_replan, request, village_id, plan_id, payload)


@router.get(
    "/{village_id}/journal",
    response_model=list[JournalEntryOut],
    responses={404: {"model": ErrorBody, "description": "Unknown village (village_not_found)."}},
)
def get_journal(request: Request, village_id: str) -> list[JournalEntryOut]:
    """Every recorded event of the village, the most recent first."""
    with open_db(_data_dir(request)) as connection:
        if not store.village_exists(connection, village_id):
            raise _village_not_found(village_id)
        found = journal_store.entries(connection, village_id)
    return [
        JournalEntryOut(
            entry_id=row.entry_id,
            type=row.type,
            at=row.at,
            task_id=row.task_id,
            payload=dict(row.payload),
            plan_id=row.plan_id,
        )
        for row in reversed(found)
    ]
