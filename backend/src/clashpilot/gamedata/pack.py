"""Game data pack (SPEC section 5): strict schema, cross validation and typed lookups.

Pure module: no I/O, no system clock. `load_pack` either returns a `GamePack` or raises
`PackValidationError` carrying one message per problem found.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
INSTANT_UPGRADE_TAG = "instant_upgrade"
COST_RESOURCES = frozenset(
    {"gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"}
)

Lane = Literal["builder", "lab", "pet_house", "blacksmith", "none"]
Kind = Literal[
    "building",
    "trap",
    "wall",
    "troop",
    "spell",
    "siege",
    "hero",
    "pet",
    "equipment",
    "guardian",
    "helper",
]

_PACK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Source(_Strict):
    id: str
    label: str
    retrieved_at: str
    url: str | None = None
    license: str | None = None


class Requirement(_Strict):
    data_id: int
    level: int


class Item(_Strict):
    data_id: int
    name: str
    kind: Kind
    village: str
    lane: Lane
    category: str
    tags: list[str] = Field(default_factory=list)
    max_count_by_th: dict[str, int] = Field(default_factory=dict)


class LevelRow(_Strict):
    """The upgrade *to* `level` from `level - 1`."""

    data_id: int
    level: int
    duration_s: int
    source: str
    cost: dict[str, int] = Field(default_factory=dict)
    requires: list[Requirement] = Field(default_factory=list)


class StorageRow(_Strict):
    data_id: int
    level: int
    resource: str
    capacity: int
    source: str


class PackData(_Strict):
    schema_version: int
    pack_id: str
    game_version: str
    as_of: str
    fictional: bool
    sources: list[Source]
    items: list[Item]
    levels: list[LevelRow]
    storage: list[StorageRow] = Field(default_factory=list)
    rules: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PackValidationError(Exception):
    """Raised by `load_pack`; `errors` holds one message per problem."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: list[str] = list(errors)
        super().__init__("; ".join(self.errors))


def canonical_json(data: Mapping[str, Any]) -> str:
    """Key-order independent JSON text of a pack, the input of its digest."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha256(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class GamePack:
    """A validated pack with indexed lookups."""

    def __init__(self, data: PackData) -> None:
        self.data = data
        self._items = {item.data_id: item for item in data.items}
        self._levels = {(row.data_id, row.level): row for row in data.levels}
        self._sources = {source.id: source for source in data.sources}
        by_item: dict[int, list[LevelRow]] = {}
        for row in data.levels:
            by_item.setdefault(row.data_id, []).append(row)
        self._by_item = {
            data_id: tuple(sorted(rows, key=lambda row: row.level))
            for data_id, rows in by_item.items()
        }

    @property
    def pack_id(self) -> str:
        return self.data.pack_id

    def item(self, data_id: int) -> Item | None:
        return self._items.get(data_id)

    def level(self, data_id: int, level: int) -> LevelRow | None:
        return self._levels.get((data_id, level))

    def levels_of(self, data_id: int) -> tuple[LevelRow, ...]:
        """Every row of an item, by increasing level."""
        return self._by_item.get(data_id, ())

    def max_level(self, data_id: int) -> int:
        """Highest level present for the item, 0 when it has no row."""
        rows = self._by_item.get(data_id)
        return rows[-1].level if rows else 0

    def source_of(self, data_id: int, level: int) -> Source | None:
        row = self._levels.get((data_id, level))
        return self._sources.get(row.source) if row is not None else None


def _location(loc: Sequence[int | str]) -> str:
    return ".".join(str(part) for part in loc) or "pack"


def _shape_errors(error: ValidationError) -> list[str]:
    return [f"{_location(entry['loc'])}: {entry['msg']}" for entry in error.errors()]


def _duplicates(keys: Sequence[Any]) -> list[Any]:
    return [key for key, seen in Counter(keys).items() if seen > 1]


def _semantic_errors(pack: PackData) -> list[str]:
    errors: list[str] = []
    if pack.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version: expected {SCHEMA_VERSION}, got {pack.schema_version}")
    if not _PACK_ID.fullmatch(pack.pack_id):
        errors.append(f"pack_id: {pack.pack_id!r} must match {_PACK_ID.pattern}")

    source_ids = {source.id for source in pack.sources}
    errors += [
        f"sources: duplicate id {key!r}" for key in _duplicates([s.id for s in pack.sources])
    ]

    items = {item.data_id: item for item in pack.items}
    errors += [
        f"items: duplicate data_id {key}" for key in _duplicates([i.data_id for i in pack.items])
    ]
    for item in pack.items:
        for threshold, count in item.max_count_by_th.items():
            if not threshold.isdecimal() or count < 1:
                errors.append(
                    f"items[{item.data_id}].max_count_by_th: {threshold!r}: {count} is not a "
                    "positive count for a numeric town hall level"
                )

    errors += [
        f"levels: duplicate (data_id, level) {key}"
        for key in _duplicates([(row.data_id, row.level) for row in pack.levels])
    ]
    for row in pack.levels:
        where = f"levels[{row.data_id}, {row.level}]"
        owner = items.get(row.data_id)
        if owner is None:
            errors.append(f"{where}: unknown data_id {row.data_id}")
        if row.level < 1:
            errors.append(f"{where}: level must be at least 1")
        if row.duration_s < 0:
            errors.append(f"{where}: duration_s must not be negative, got {row.duration_s}")
        elif row.duration_s == 0 and not (owner is not None and INSTANT_UPGRADE_TAG in owner.tags):
            errors.append(f"{where}: duration_s 0 is only allowed for {INSTANT_UPGRADE_TAG} items")
        errors += [
            f"{where}: cost {resource!r} must not be negative"
            for resource, amount in row.cost.items()
            if amount < 0
        ]
        if row.source not in source_ids:
            errors.append(f"{where}: unknown source {row.source!r}")
        errors += [
            f"{where}: requires unknown data_id {requirement.data_id}"
            for requirement in row.requires
            if requirement.data_id not in items
        ]

    for entry in pack.storage:
        where = f"storage[{entry.data_id}, {entry.level}]"
        if entry.data_id not in items:
            errors.append(f"{where}: unknown data_id {entry.data_id}")
        if entry.capacity < 0:
            errors.append(f"{where}: capacity must not be negative")
        if entry.source not in source_ids:
            errors.append(f"{where}: unknown source {entry.source!r}")

    for name, rule in pack.rules.items():
        source = rule.get("source")
        if not isinstance(source, str) or not source:
            errors.append(f"rules.{name}: missing source")
        elif source not in source_ids:
            errors.append(f"rules.{name}: unknown source {source!r}")
    return errors


def load_pack(data: Mapping[str, Any]) -> GamePack:
    """Validate a pack dictionary and return it, or raise `PackValidationError`."""
    try:
        pack = PackData.model_validate(data)
    except ValidationError as error:
        raise PackValidationError(_shape_errors(error)) from error
    errors = _semantic_errors(pack)
    if errors:
        raise PackValidationError(errors)
    return GamePack(pack)
