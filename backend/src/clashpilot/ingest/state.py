"""The state of a village as read from one export."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Village = Literal["home", "builder"]


@dataclass(frozen=True)
class SnapshotItem:
    """One entry of the export: `count` identical instances of `data_id`.

    `level` is None when the entry has none, as defense entries made of modules. Those are kept
    untouched in `raw` and are not planned.
    """

    data_id: int
    level: int | None
    count: int
    section: str
    village: Village
    raw: Any = None


@dataclass(frozen=True)
class RunningJob:
    """An upgrade in progress on one instance, ending at the absolute epoch `finish_at`."""

    data_id: int
    finish_at: int
    to_level: int
    section: str
    village: Village
    extra: bool = False
    helper: bool = False


@dataclass(frozen=True)
class VillageSnapshot:
    """A parsed export. `tag` is uppercase without `#`; `format_version` names its adapter."""

    tag: str
    exported_at: int
    imported_at: int
    items: tuple[SnapshotItem, ...] = ()
    running: tuple[RunningJob, ...] = ()
    unknown_sections: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    format_version: str = ""


Resource = Literal["gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"]
RESOURCES: tuple[Resource, ...] = get_args(Resource)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ManualResource(_Strict):
    """A typed-in stock. Never `unknown`: an absent entry is what means unknown."""

    value: int = Field(ge=0)
    status: Literal["confirmed", "estimated"]
    as_of: int


class FarmRate(_Strict):
    """Income per hour, in three scenarios."""

    low: int = Field(ge=0)
    expected: int = Field(ge=0)
    high: int = Field(ge=0)


class ManualEvent(_Strict):
    """A bonus or reduction active on one lane between `start` and `end` (100 = no effect)."""

    label: str
    start: int
    end: int
    lane: str
    duration_pct: int = Field(ge=0)
    cost_pct: int = Field(ge=0)


class ManualState(_Strict):
    """What neither the export nor the official API give and the player types in."""

    resources: dict[Resource, ManualResource] = Field(default_factory=dict)
    builders_total: int | None = Field(default=None, ge=0)
    magic_items: dict[str, int] = Field(default_factory=dict)
    gold_pass: dict[str, Any] = Field(default_factory=dict)
    farm_rate: dict[Resource, FarmRate] = Field(default_factory=dict)
    storage_override: dict[Resource, int] = Field(default_factory=dict)
    events: list[ManualEvent] = Field(default_factory=list)
