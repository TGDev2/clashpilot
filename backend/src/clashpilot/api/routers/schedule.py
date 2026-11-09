"""Schedule routes: store a village's schedule profile and preview its UTC windows."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, ValidationError

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.schedule.expand import expand_windows
from clashpilot.schedule.profile import ScheduleProfile
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["schedule"])

SECONDS_PER_DAY = 86400

# First match wins: these codes are the error types raised by the profile validators.
_ERROR_MESSAGES = {
    "invalid_timezone": "Le fuseau horaire est inconnu.",
    "invalid_window": "Une fenêtre de l'horaire est invalide.",
}


class SchedulePreview(BaseModel):
    tz: str
    sleep: list[tuple[int, int]]
    work: list[tuple[int, int]]
    absence: list[tuple[int, int]]
    available: list[tuple[int, int]]
    preferred: list[tuple[int, int]]


def _validated(body: dict[str, Any]) -> ScheduleProfile:
    try:
        return ScheduleProfile.model_validate(body)
    except ValidationError as error:
        entries = error.errors()
        problems = [
            f"{'.'.join(str(p) for p in entry['loc'])}: {entry['msg']}" for entry in entries
        ]
        types = {entry["type"] for entry in entries}
        code = next((name for name in _ERROR_MESSAGES if name in types), "invalid_schedule")
        message = _ERROR_MESSAGES.get(code, "L'horaire est invalide.")
        raise ApiError(422, code, message, problems) from None


def _stored(connection: sqlite3.Connection, village_id: str) -> ScheduleProfile:
    row = connection.execute(
        "SELECT body_json FROM schedule_profile WHERE village_id = ?", (village_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "schedule_not_found", f"Aucun horaire pour le village {village_id}.")
    return ScheduleProfile.model_validate_json(row["body_json"])


@router.put(
    "/{village_id}/schedule",
    response_model=ScheduleProfile,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ScheduleProfile.model_json_schema()}},
        }
    },
    responses={
        422: {
            "model": ErrorBody,
            "description": "Code invalid_timezone, invalid_window ou invalid_schedule.",
        },
    },
)
def put_schedule(
    request: Request, village_id: str, body: Annotated[dict[str, Any], Body()]
) -> ScheduleProfile:
    """Replace the schedule profile of a village."""
    profile = _validated(body)
    now: int = request.app.state.clock()
    with open_db(request.app.state.settings.data_dir) as connection:
        connection.execute(
            "INSERT INTO schedule_profile (village_id, body_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (village_id) DO UPDATE SET "
            "body_json = excluded.body_json, updated_at = excluded.updated_at",
            (village_id, profile.model_dump_json(), now),
        )
    return profile


@router.get(
    "/{village_id}/schedule",
    response_model=ScheduleProfile,
    responses={404: {"model": ErrorBody, "description": "Code schedule_not_found."}},
)
def get_schedule(request: Request, village_id: str) -> ScheduleProfile:
    with open_db(request.app.state.settings.data_dir) as connection:
        return _stored(connection, village_id)


@router.get(
    "/{village_id}/schedule/preview",
    response_model=SchedulePreview,
    responses={404: {"model": ErrorBody, "description": "Code schedule_not_found."}},
)
def preview_schedule(
    request: Request, village_id: str, days: Annotated[int, Query(ge=1, le=366)] = 7
) -> SchedulePreview:
    """The UTC windows of the stored profile over the next `days` days."""
    with open_db(request.app.state.settings.data_dir) as connection:
        profile = _stored(connection, village_id)
    now: int = request.app.state.clock()
    calendar = expand_windows(profile, now, now + days * SECONDS_PER_DAY)
    return SchedulePreview(**asdict(calendar))
