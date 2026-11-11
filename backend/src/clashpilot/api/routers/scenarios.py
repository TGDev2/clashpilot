"""What-if scenario routes (E18): create, list, delete and compare scenarios of a village.

A scenario is computed in the thread pool on a copy of the problem of the latest real plan
(`planning/scenarios.py`); only the `scenarios` table is written. The compared figures of a
scenario are computed with it and stored; those of the real plan are computed on each compare.
`logins` stays out of the OpenAPI schema properties: the API guard of E05 refuses any property
named like a login.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.api.routers.plans import AssignmentOut, ProofOut
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.model import PolicyMode
from clashpilot.planning.scenarios import plan_figures, run_scenario
from clashpilot.planning.service import (
    PlanInputs,
    UnknownEngineError,
    UnknownStrategyError,
    planner_config,
    resolve_params,
)
from clashpilot.planning.strategies import load_strategies
from clashpilot.schedule.profile import LocalDateTime
from clashpilot.storage import journal as journal_store
from clashpilot.storage import plans as store
from clashpilot.storage import scenarios as scenario_store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["scenarios"])


# --------------------------------------------------------------------------- request


class _Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartNowIn(_Overlay):
    """Start `task_id` at the earliest instant the simulator accepts."""

    type: Literal["start_now"]
    task_id: str


class ReserveBuilderIn(_Overlay):
    """`count` builders fewer from now until the UTC epoch `until`."""

    type: Literal["reserve_builder"]
    count: int = Field(ge=1)
    until: int


class AbsenceIn(_Overlay):
    """An absence between two local date-times of the profile zone."""

    type: Literal["absence"]
    start_local: LocalDateTime
    end_local: LocalDateTime

    @model_validator(mode="after")
    def _ends_after_its_start(self) -> Self:
        if datetime.fromisoformat(self.end_local) <= datetime.fromisoformat(self.start_local):
            raise PydanticCustomError("invalid_window", "Une absence doit finir après son début.")
        return self


class PolicyIn(_Overlay):
    """The login policy; a field left out keeps the profile's value."""

    type: Literal["policy"]
    mode: PolicyMode
    tolerance_minutes: int | None = Field(default=None, ge=0)
    max_logins_per_day: int | None = Field(default=None, ge=1)


class StrategyIn(_Overlay):
    type: Literal["strategy"]
    key: str


class ReducePlayIn(_Overlay):
    type: Literal["reduce_play"]
    max_logins_per_day: int = Field(ge=1)


class UseItemIn(_Overlay):
    """Use `item` (a rule key of the active pack) at the UTC epoch `at`, on `task_id` if any."""

    type: Literal["use_item"]
    item: str
    task_id: str | None = None
    at: int = Field(ge=0)


OverlayIn = Annotated[
    StartNowIn | ReserveBuilderIn | AbsenceIn | PolicyIn | StrategyIn | ReducePlayIn | UseItemIn,
    Field(discriminator="type"),
]


class ScenarioRequest(BaseModel):
    """Without `engine` and `profile`, the defaults of `POST plans` (profile `fast`)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=200)
    overlays: list[OverlayIn]
    engine: str | None = None
    profile: Literal["fast", "repro"] | None = None


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_ids: list[int]


# --------------------------------------------------------------------------- answers


class ReasonOut(BaseModel):
    code: str
    message: str


class ScenarioOut(BaseModel):
    """`assignments` and `proof` are empty for a scenario that is not `ok`."""

    scenario_id: int
    plan_id: int | None
    name: str
    overlays: list[dict[str, Any]]
    status: Literal["ok", "infeasible", "unsupported"]
    reasons: list[ReasonOut]
    now: int
    horizon_end: int
    timezone: str
    engine: str
    profile: Literal["fast", "repro"]
    assignments: list[AssignmentOut]
    proof: ProofOut | None
    warnings: list[str]


class FiguresOut(BaseModel):
    """Figures read from `simulate`; null for a scenario that is not `ok`.

    The answer also carries `logins`, the number of logins of the plan, left out of the schema
    properties because of the E05 guard.
    """

    goal_finish: int | None = None
    finished_count: int | None = None
    remaining_cost: dict[str, int] | None = None
    value_gained: float | None = None
    hero_unavailable_s: int | None = None
    logins: Annotated[int | None, SkipJsonSchema()] = None
    idle_builder_s: int | None = None
    proof: ProofOut | None = None


class PlanFiguresOut(FiguresOut):
    plan_id: int


class ScenarioFiguresOut(FiguresOut):
    scenario_id: int
    name: str
    status: Literal["ok", "infeasible", "unsupported"]


class CompareOut(BaseModel):
    plan: PlanFiguresOut
    scenarios: list[ScenarioFiguresOut]


# --------------------------------------------------------------------------- helpers


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _village_not_found(village_id: str) -> ApiError:
    return ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")


def _scenario_not_found(scenario_id: int) -> ApiError:
    return ApiError(404, "scenario_not_found", f"Le scénario {scenario_id} est introuvable.")


def _unknown_strategy(strategy: str) -> ApiError:
    return ApiError(422, "unknown_strategy", f"Stratégie inconnue : {strategy}.")


def _missing_input(error: MissingInputError) -> ApiError:
    return ApiError(
        409,
        "missing_input",
        "Des données manquent pour calculer le scénario : " + ", ".join(error.missing) + ".",
        extra={"missing": list(error.missing)},
    )


def _base(request: Request, village_id: str) -> tuple[PlanInputs, store.StoredPlan, bool]:
    """The inputs of the village and its latest plan, and whether it is on the latest export."""
    with open_db(_data_dir(request)) as connection:
        found = store.latest_snapshot_row(connection, village_id)
        if found is None:
            raise _village_not_found(village_id)
        snapshot_id, snapshot = found
        stored = store.latest_plan(connection, village_id)
        if stored is None:
            raise ApiError(404, "no_plan", f"Aucun plan n'a encore été calculé pour {village_id}.")
        pack = store.active_pack(connection)
        if pack is None:
            raise ApiError(409, "no_active_pack", "Aucun pack de données n'est actif.")
        inputs = PlanInputs(
            snapshot=snapshot,
            manual=store.manual_state(connection, village_id),
            pack=pack,
            schedule_profile=store.schedule_profile(connection, village_id),
            journal=journal_store.entries(connection, village_id),
        )
    return inputs, stored, stored.snapshot_id == snapshot_id


def _out(stored: scenario_store.StoredScenario) -> ScenarioOut:
    return ScenarioOut.model_validate(
        {
            **stored.body,
            "scenario_id": stored.scenario_id,
            "name": stored.name,
            "overlays": stored.overlays,
        }
    )


_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."},
    409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
    422: {
        "model": ErrorBody,
        "description": "Code unknown_engine, unknown_strategy or invalid_request.",
    },
}


# --------------------------------------------------------------------------- routes


def _create(request: Request, village_id: str, payload: ScenarioRequest) -> ScenarioOut:
    try:
        resolved = resolve_params(None, payload.engine, payload.profile)
    except UnknownEngineError as error:
        raise ApiError(422, "unknown_engine", f"Moteur inconnu : {error.engine}.") from None
    strategies = load_strategies()
    for overlay in payload.overlays:
        if isinstance(overlay, StrategyIn) and overlay.key not in strategies:
            raise _unknown_strategy(overlay.key)
    overlays = [overlay.model_dump() for overlay in payload.overlays]
    inputs, stored, same_snapshot = _base(request, village_id)
    config = planner_config(request.app.state.settings.planner_overrides, resolved.profile)
    try:
        document = run_scenario(
            inputs,
            stored.params,
            stored.body,
            overlays,
            config,
            engine_name=resolved.engine,
            same_snapshot=same_snapshot,
        )
    except MissingInputError as error:
        raise _missing_input(error) from None
    except UnknownStrategyError as error:
        raise _unknown_strategy(error.strategy) from None
    document["plan_id"] = stored.plan_id
    now: int = request.app.state.clock()
    with open_db(_data_dir(request)) as connection:
        scenario_id = scenario_store.add_scenario(
            connection, village_id, stored.plan_id, payload.name, overlays, document, now
        )
    return _out(scenario_store.StoredScenario(scenario_id, payload.name, overlays, document))


@router.post(
    "/{village_id}/scenarios",
    status_code=201,
    response_model=ScenarioOut,
    responses=_ERRORS,
)
async def post_scenario(request: Request, village_id: str, payload: ScenarioRequest) -> ScenarioOut:
    """Compute a scenario on a copy of the latest plan's problem and store it."""
    return await run_in_threadpool(_create, request, village_id, payload)


@router.get(
    "/{village_id}/scenarios",
    response_model=list[ScenarioOut],
    responses={404: {"model": ErrorBody, "description": "Code village_not_found."}},
)
def get_scenarios(request: Request, village_id: str) -> list[ScenarioOut]:
    """The scenarios of the village, oldest first."""
    with open_db(_data_dir(request)) as connection:
        if not store.village_exists(connection, village_id):
            raise _village_not_found(village_id)
        stored = scenario_store.scenarios(connection, village_id)
    return [_out(scenario) for scenario in stored]


@router.delete(
    "/{village_id}/scenarios/{scenario_id}",
    status_code=204,
    responses={404: {"model": ErrorBody, "description": "Code scenario_not_found."}},
)
def delete_scenario(request: Request, village_id: str, scenario_id: int) -> Response:
    """Delete one scenario; no plan and no other table is touched."""
    with open_db(_data_dir(request)) as connection:
        if not scenario_store.delete_scenario(connection, village_id, scenario_id):
            raise _scenario_not_found(scenario_id)
    return Response(status_code=204)


def _compare(request: Request, village_id: str, payload: CompareRequest) -> CompareOut:
    inputs, stored, same_snapshot = _base(request, village_id)
    with open_db(_data_dir(request)) as connection:
        chosen = []
        for scenario_id in payload.scenario_ids:
            found = scenario_store.scenario(connection, village_id, scenario_id)
            if found is None:
                raise _scenario_not_found(scenario_id)
            chosen.append(found)
    try:
        real = plan_figures(inputs, stored.params, stored.body, same_snapshot=same_snapshot)
    except MissingInputError as error:
        raise _missing_input(error) from None
    except UnknownStrategyError as error:
        raise _unknown_strategy(error.strategy) from None
    rows = [
        {
            **(scenario.body.get("figures") or {}),
            "scenario_id": scenario.scenario_id,
            "name": scenario.name,
            "status": scenario.body["status"],
        }
        for scenario in chosen
    ]
    return CompareOut.model_validate(
        {"plan": {**real, "plan_id": stored.plan_id}, "scenarios": rows}
    )


@router.post(
    "/{village_id}/scenarios/compare",
    response_model=CompareOut,
    responses={
        **_ERRORS,
        404: {
            "model": ErrorBody,
            "description": "Code village_not_found, no_plan or scenario_not_found.",
        },
    },
)
async def post_compare(request: Request, village_id: str, payload: CompareRequest) -> CompareOut:
    """The figures of the latest real plan and of each scenario, in the order asked."""
    return await run_in_threadpool(_compare, request, village_id, payload)
