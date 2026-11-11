"""Meta routes: what this server can do."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.service import UnknownEngineError, resolve_params

router = APIRouter(prefix="/api/meta", tags=["meta"])


class EnginesOut(BaseModel):
    engines: list[str]
    default: str | None


@router.get("/engines", response_model=EnginesOut)
def list_engines() -> EnginesOut:
    """The keys of the engine registry and the engine used when a request names none."""
    try:
        default: str | None = resolve_params(None, None, None).engine
    except UnknownEngineError:
        default = None
    return EnginesOut(engines=sorted(PLANNERS), default=default)
