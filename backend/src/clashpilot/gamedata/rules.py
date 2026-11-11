"""Typed reading of the pack rules (SPEC section 5, Règles). Pure module.

The key of a rule is the name of the item in `item_used` (E11) and `use_item` (E19). A key absent
from the pack, or a rule of the wrong shape, reads as "not supported": the readers answer None and
never guess a value (ADR-2).

- `book_*`: `{"kinds": [kind, ...]}` ends at once a started job of one of these kinds.
- `builder_potion` and `research_potion`: `{"duration_multiplier", "duration_s"}` speed up the
  jobs running on the `builder` and `lab` lanes.
- `rune_*`: `{"resource"}` fills the storage of that resource.
- `gem_finish` and `helper_upgrade`: read by E19 only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from clashpilot.gamedata.pack import COST_RESOURCES, GamePack, Kind

BOOK_PREFIX = "book_"
RUNE_PREFIX = "rune_"
POTION_LANES = {"builder_potion": "builder", "research_potion": "lab"}
GEM_FINISH = "gem_finish"
HELPER_UPGRADE = "helper_upgrade"


@dataclass(frozen=True)
class BookRule:
    item: str
    kinds: frozenset[str]


@dataclass(frozen=True)
class PotionRule:
    item: str
    lane: str
    duration_multiplier: int
    duration_s: int

    def end_after(self, at: int, end: int) -> int:
        """End of a job running at `at` until `end` once the potion acts from `at` (SPEC 5).

        With `r` seconds left, `m` the multiplier and `D` the duration: `at + ceil(r / m)` if
        `r <= D * m`, else `end - D * (m - 1)`.
        """
        left = end - at
        if left <= 0:
            return end
        m, d = self.duration_multiplier, self.duration_s
        if left <= d * m:
            return at + math.ceil(left / m)
        return end - d * (m - 1)


@dataclass(frozen=True)
class RuneRule:
    item: str
    resource: str


ItemRule = BookRule | PotionRule | RuneRule


@dataclass(frozen=True)
class GemRate:
    up_to_s: int
    gems: int


@dataclass(frozen=True)
class GemFinishRule:
    rates: tuple[GemRate, ...]  # by increasing `up_to_s`


@dataclass(frozen=True)
class HelperLevel:
    level: int
    cost: Mapping[str, int]


@dataclass(frozen=True)
class HelperUpgradeRule:
    levels: tuple[HelperLevel, ...]  # by increasing level


class _Shape(BaseModel):
    # `source` and any later field are allowed; the fields read here must have their exact type.
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class _Book(_Shape):
    kinds: list[Kind] = Field(min_length=1)


class _Potion(_Shape):
    duration_multiplier: int = Field(ge=1)
    duration_s: int = Field(ge=1)


class _Rune(_Shape):
    resource: str


class _Rate(_Shape):
    up_to_s: int = Field(ge=1)
    gems: int = Field(ge=0)


class _GemFinish(_Shape):
    rates: list[_Rate] = Field(min_length=1)


class _HelperLevel(_Shape):
    level: int = Field(ge=1)
    cost: dict[str, int]


class _HelperUpgrade(_Shape):
    levels: list[_HelperLevel] = Field(min_length=1)


def _raw(pack: GamePack, key: str) -> dict[str, Any] | None:
    return pack.data.rules.get(key)


def item_rule(pack: GamePack, item: str) -> ItemRule | None:
    """The rule of a book, potion or rune named `item`, or None when not supported."""
    raw = _raw(pack, item)
    if raw is None:
        return None
    try:
        if item.startswith(BOOK_PREFIX):
            book = _Book.model_validate(raw)
            return BookRule(item, frozenset(book.kinds))
        if item in POTION_LANES:
            potion = _Potion.model_validate(raw)
            return PotionRule(
                item, POTION_LANES[item], potion.duration_multiplier, potion.duration_s
            )
        if item.startswith(RUNE_PREFIX):
            rune = _Rune.model_validate(raw)
            return RuneRule(item, rune.resource) if rune.resource in COST_RESOURCES else None
    except ValidationError:
        return None
    return None


def gem_finish_rule(pack: GamePack) -> GemFinishRule | None:
    raw = _raw(pack, GEM_FINISH)
    if raw is None:
        return None
    try:
        rule = _GemFinish.model_validate(raw)
    except ValidationError:
        return None
    rates = sorted((GemRate(rate.up_to_s, rate.gems) for rate in rule.rates), key=_up_to)
    return GemFinishRule(tuple(rates))


def helper_upgrade_rule(pack: GamePack) -> HelperUpgradeRule | None:
    raw = _raw(pack, HELPER_UPGRADE)
    if raw is None:
        return None
    try:
        rule = _HelperUpgrade.model_validate(raw)
    except ValidationError:
        return None
    if any(
        name not in COST_RESOURCES or amount < 0
        for level in rule.levels
        for name, amount in level.cost.items()
    ):
        return None
    levels = sorted(
        (HelperLevel(level.level, dict(level.cost)) for level in rule.levels),
        key=lambda level: level.level,
    )
    return HelperUpgradeRule(tuple(levels))


def _up_to(rate: GemRate) -> int:
    return rate.up_to_s
