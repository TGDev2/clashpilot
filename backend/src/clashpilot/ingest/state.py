"""The state of a village as read from one export."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

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


@dataclass(frozen=True)
class ManualState:
    """What the export does not give and the player types in: filled by manual input."""
