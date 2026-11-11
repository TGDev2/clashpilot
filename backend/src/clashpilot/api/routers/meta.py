"""Meta routes: what this server can do: engines and progression strategies (E16)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.service import UnknownEngineError, resolve_params
from clashpilot.planning.strategies import load_strategies

router = APIRouter(prefix="/api/meta", tags=["meta"])


class StrategyOut(BaseModel):
    key: str
    label: str
    # A free mapping of `Objective` weights: a `logins` schema property is refused (E05 guard).
    weights: dict[str, float]


class StrategiesOut(BaseModel):
    strategies: list[StrategyOut]


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


@router.get("/strategies", response_model=StrategiesOut)
def list_strategies() -> StrategiesOut:
    """The progression strategies a plan may name, with their French label and their weights."""
    return StrategiesOut(
        strategies=[
            StrategyOut(key=strategy.key, label=strategy.label, weights=dict(strategy.weights))
            for strategy in load_strategies().values()
        ]
    )
