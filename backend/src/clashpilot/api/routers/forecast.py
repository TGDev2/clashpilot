"""Forecast routes (E17): resources of the latest plan under the three farm rates, wall sink, and
the farm rate observed from the manual history.

Every predicted value is a provenance object of source `model`, `estimated` or `unknown` (null).
The forecast is computed in the thread pool: it compiles the village three times and replays the
plan with `simulate`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.forecast import forecast_plan, journal_spending, observed_rate
from clashpilot.planning.service import PlanInputs, UnknownStrategyError
from clashpilot.storage import history as history_store
from clashpilot.storage import journal as journal_store
from clashpilot.storage import plans as store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["forecast"])

DEFAULT_HORIZON_DAYS = 30


class Estimate(BaseModel):
    """A provenance object of the model; `value` is null when `status` is `unknown`."""

    value: int | float | None
    status: Literal["estimated", "unknown"]
    source: str
    as_of: int | None


class ScenarioEstimates(BaseModel):
    low: Estimate
    expected: Estimate
    high: Estimate


class SeriesPoint(BaseModel):
    """The stock (units) at `t` under each income."""

    t: int
    low: Estimate
    expected: Estimate
    high: Estimate


class FundingOut(BaseModel):
    """When the stock first covers the task's cost: `low` earliest (income high), `high` latest."""

    task_id: str
    funded_at: ScenarioEstimates | None


class SaturationOut(BaseModel):
    """The stock sits at its cap over `[start, end)` under the expected income."""

    resource: str
    start: int
    end: int
    spilled: float


class BlockingOut(BaseModel):
    resource: str
    delayed_s: int
    tasks: list[str]


class WallOut(BaseModel):
    """Upgrade wall `instance` of `data_id` to `level` at `at`, paid from the excess."""

    data_id: int
    instance: int
    level: int
    name: str
    cost: dict[str, int]
    at: int


class EffortOut(BaseModel):
    """Remaining cost of the goal (`scope` goal) or of every task (`scope` plan)."""

    scope: Literal["goal", "plan"]
    cost: dict[str, int]
    farm_hours: dict[str, ScenarioEstimates]


class ForecastOut(BaseModel):
    plan_id: int
    now: int
    horizon_end: int
    step_s: int
    series: dict[str, list[SeriesPoint]]
    funding: list[FundingOut]
    saturation: list[SaturationOut]
    blocking: BlockingOut | None
    walls: list[WallOut]
    effort: EffortOut


class DeclaredRate(BaseModel):
    low: int
    expected: int
    high: int


class FarmRateOut(BaseModel):
    """`declared`: the typed-in rates; `observed`: per hour from the last two stock inputs."""

    declared: dict[str, DeclaredRate]
    observed: dict[str, Estimate]


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _village_not_found(village_id: str) -> ApiError:
    return ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")


def _forecast(request: Request, village_id: str, horizon_days: int) -> ForecastOut:
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
    try:
        document = forecast_plan(
            inputs,
            stored.params,
            stored.body,
            horizon_days,
            same_snapshot=stored.snapshot_id == snapshot_id,
        )
    except MissingInputError as error:
        raise ApiError(
            409,
            "missing_input",
            "Des données manquent pour la prévision : " + ", ".join(error.missing) + ".",
            extra={"missing": list(error.missing)},
        ) from None
    except UnknownStrategyError as error:
        raise ApiError(422, "unknown_strategy", f"Stratégie inconnue : {error.strategy}.") from None
    return ForecastOut.model_validate({"plan_id": stored.plan_id, **document})


@router.get(
    "/{village_id}/forecast",
    response_model=ForecastOut,
    responses={
        404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."},
        409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
    },
)
async def get_forecast(
    request: Request,
    village_id: str,
    horizon_days: Annotated[int, Query(ge=1, le=365)] = DEFAULT_HORIZON_DAYS,
) -> ForecastOut:
    """Stocks, funding dates, saturation, blocking resource, walls and effort of the latest plan."""
    return await run_in_threadpool(_forecast, request, village_id, horizon_days)


@router.get(
    "/{village_id}/farm-rate",
    response_model=FarmRateOut,
    responses={404: {"model": ErrorBody, "description": "Code village_not_found."}},
)
def get_farm_rate(request: Request, village_id: str) -> FarmRateOut:
    """The declared farm rates and the ones observed between the last two manual inputs."""
    with open_db(_data_dir(request)) as connection:
        if not store.village_exists(connection, village_id):
            raise _village_not_found(village_id)
        manual = store.manual_state(connection, village_id)
        history = history_store.manual_history(connection, village_id)
        journal = journal_store.entries(connection, village_id)
    observed = {
        name: observed_rate(points, journal_spending(journal, name))
        for name, points in sorted(history.items())
    }
    declared = {name: rate.model_dump() for name, rate in sorted(manual.farm_rate.items())}
    return FarmRateOut.model_validate({"declared": declared, "observed": observed})
