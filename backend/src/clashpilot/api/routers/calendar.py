"""Calendar route (E21): the latest plan as an iCalendar file to import in a phone calendar."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from clashpilot.api.errors import ApiError
from clashpilot.api.ics import calendar_of
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.storage import plans as store
from clashpilot.storage.db import open_db

router = APIRouter(prefix="/api/villages", tags=["calendar"])


@router.get(
    "/{village_id}/plans/latest/calendar.ics",
    response_class=Response,
    responses={
        200: {"content": {"text/calendar": {"schema": {"type": "string"}}}},
        404: {"model": ErrorBody, "description": "Code village_not_found or no_plan."},
    },
)
def get_calendar(request: Request, village_id: str) -> Response:
    """One event per assignment of the latest plan, in UTC, with an alarm before its end."""
    data_dir: Path = request.app.state.settings.data_dir
    with open_db(data_dir) as connection:
        if not store.village_exists(connection, village_id):
            raise ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")
        stored = store.latest_plan(connection, village_id)
    if stored is None:
        raise ApiError(404, "no_plan", f"Aucun plan n'a encore été calculé pour {village_id}.")
    body = calendar_of(stored.body["assignments"], int(stored.body["now"]))
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="clashpilot.ics"'},
    )
