"""Critical path route: what fixes the date of the next town hall.

The village is compiled as `POST .../plans` compiles it (latest export, manual input and events,
journal, schedule, active pack with its corrections), at the application clock, over 365 days and
without strategy; `critical_path` judges that problem. Nothing is stored. `?base=` (E32) names the
village compiled: `home` (default) or `builder`, whose target is its builder hall.

Every predicted date is a provenance object of source `model`; without a town hall task the dates
are `unknown`. The computation runs in the thread pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.forecast import Estimate
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.state import Village
from clashpilot.planning.compile import MissingInputError
from clashpilot.planning.critical import (
    CRITICAL_FLOAT_S,
    CriticalPath,
    critical_path,
    unknown_lanes,
)
from clashpilot.planning.forecast import estimate
from clashpilot.planning.model import Problem
from clashpilot.planning.service import PlanInputs, compile_inputs
from clashpilot.storage import journal as journal_store
from clashpilot.storage import plans as store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["critical-path"])

# `?base=` is validated but left out of the OpenAPI document: the frozen E23 guard pins the query
# parameters of this route to `skip_lanes`.
HiddenBaseQuery = Annotated[Village, Query(include_in_schema=False)]

HORIZON_DAYS = 365


class CriticalTargetOut(BaseModel):
    """The next town hall upgrade."""

    task_id: str
    data_id: int
    name: str
    to_level: int


class LaneWorkOut(BaseModel):
    """The remaining work of a lane and the earliest date its capacity allows."""

    lane: str
    capacity: int
    work_s: int
    finish: Estimate


class CriticalBoundOut(BaseModel):
    """What fixes the date: a chain of upgrades, or the work of `lane`."""

    kind: Literal["chain", "work"]
    lane: str | None


class CriticalChainOut(BaseModel):
    """A longest path, ending with the upgrades of `data_id` from `from_level` to `to_level`."""

    data_id: int
    name: str
    from_level: int
    to_level: int
    task_ids: list[str]
    finish: Estimate


class CriticalTaskOut(BaseModel):
    """`float_s`: how long the task may slip without moving the date."""

    task_id: str
    name: str
    to_level: int
    lane: str
    float_s: int
    critical: bool


class CriticalPathOut(BaseModel):
    """`finish_after`: the town hall started once the set is done; `finish_parallel`: run next to
    it. `calendar_wait_s` is the time the first chain waits for the schedule, null without chain.
    """

    target: CriticalTargetOut | None
    set_finish: Estimate
    finish_after: Estimate
    finish_parallel: Estimate
    work_finish: list[LaneWorkOut]
    bound: CriticalBoundOut | None
    critical_lanes: list[str]
    calendar_wait_s: int | None
    chains: list[CriticalChainOut]
    tasks: list[CriticalTaskOut]
    skip_lanes: list[str]


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _village_not_found(village_id: str) -> ApiError:
    return ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")


def _inputs(request: Request, village_id: str, base: Village) -> PlanInputs:
    """The inputs `POST .../plans` reads for `base`, with its errors."""
    with open_db(_data_dir(request)) as connection:
        found = store.latest_snapshot_row(connection, village_id)
        if found is None:
            raise _village_not_found(village_id)
        pack = store.active_pack(connection)
        if pack is None:
            raise ApiError(409, "no_active_pack", "Aucun pack de données n'est actif.")
        return PlanInputs(
            snapshot=found[1],
            manual=store.manual_state(connection, village_id),
            pack=pack,
            schedule_profile=store.schedule_profile(connection, village_id),
            journal=journal_store.entries(connection, village_id, base),
        )


def _name(pack: GamePack, data_id: int) -> str:
    item = pack.item(data_id)
    return item.name if item is not None else str(data_id)


def _date(value: int | None, now: int) -> Estimate:
    return Estimate.model_validate(estimate(value, known=value is not None, as_of=now))


def _document(
    pack: GamePack, problem: Problem, path: CriticalPath, skip_lanes: list[str]
) -> CriticalPathOut:
    """`path` with the names of the pack."""
    now = problem.now
    tasks = {task.id: task for task in problem.tasks}
    target = path.target
    chains: list[CriticalChainOut] = []
    for chain in path.chains:
        # A chain may start with the upgrades another item requires: the levels are its own.
        own = [tasks[name] for name in chain.task_ids if tasks[name].data_id == chain.data_id]
        chains.append(
            CriticalChainOut(
                data_id=chain.data_id,
                name=_name(pack, chain.data_id),
                from_level=own[0].to_level - 1,
                to_level=own[-1].to_level,
                task_ids=list(chain.task_ids),
                finish=_date(chain.finish, now),
            )
        )
    return CriticalPathOut(
        target=None
        if target is None
        else CriticalTargetOut(
            task_id=target.id,
            data_id=target.data_id,
            name=_name(pack, target.data_id),
            to_level=target.to_level,
        ),
        set_finish=_date(path.set_finish, now),
        finish_after=_date(path.finish_after, now),
        finish_parallel=_date(path.finish_parallel, now),
        work_finish=[
            LaneWorkOut(
                lane=lane,
                capacity=problem.lanes.get(lane, 0),
                work_s=path.work_s[lane],
                finish=_date(finish, now),
            )
            for lane, finish in path.work_finish.items()
        ],
        bound=None
        if path.bound is None
        else CriticalBoundOut(kind=path.bound.kind, lane=path.bound.lane),
        critical_lanes=list(path.critical_lanes),
        calendar_wait_s=path.chains[0].calendar_wait_s if path.chains else None,
        chains=chains,
        tasks=[
            CriticalTaskOut(
                task_id=task.id,
                name=_name(pack, task.data_id),
                to_level=task.to_level,
                lane=task.lane,
                float_s=path.float_s[task.id],
                critical=path.float_s[task.id] <= CRITICAL_FLOAT_S,
            )
            for task in problem.tasks
            if task.id in path.float_s
        ],
        skip_lanes=skip_lanes,
    )


def _critical_path(
    request: Request, village_id: str, skip_lanes: list[str], base: Village
) -> CriticalPathOut:
    now: int = request.app.state.clock()
    inputs = _inputs(request, village_id, base)
    try:
        problem, _ = compile_inputs(inputs, now, HORIZON_DAYS, base=base)
    except MissingInputError as error:
        raise ApiError(
            409,
            "missing_input",
            "Des données manquent pour le chemin critique : " + ", ".join(error.missing) + ".",
            extra={"missing": list(error.missing)},
        ) from None
    unknown = unknown_lanes(problem, skip_lanes)
    if unknown:
        raise ApiError(422, "unknown_lane", f"File inconnue : {', '.join(unknown)}.")
    return _document(inputs.pack, problem, critical_path(problem, skip_lanes), skip_lanes)


@router.get(
    "/{village_id}/critical-path",
    response_model=CriticalPathOut,
    responses={
        404: {"model": ErrorBody, "description": "Unknown village (code village_not_found)."},
        409: {"model": ErrorBody, "description": "Code missing_input or no_active_pack."},
        422: {"model": ErrorBody, "description": "Code unknown_lane or invalid_request."},
    },
)
async def get_critical_path(
    request: Request,
    village_id: str,
    skip_lanes: Annotated[list[str] | None, Query()] = None,
    base: HiddenBaseQuery = "home",
) -> CriticalPathOut:
    """The chains and lanes that fix the date of the next town hall, and the float of each task.

    `skip_lanes` (repeatable) names the lanes the town hall does not wait for.
    """
    return await run_in_threadpool(_critical_path, request, village_id, skip_lanes or [], base)
