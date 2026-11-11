"""Unit tests of the typed reading of the pack rules (`gamedata/rules.py`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.gamedata.rules import (
    BookRule,
    GemRate,
    PotionRule,
    RuneRule,
    gem_finish_rule,
    helper_upgrade_rule,
    item_rule,
)

ITEMS_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_items.json"
)


def items_pack(**rules: Any) -> GamePack:
    data = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    for name, rule in rules.items():
        if rule is None:
            data["rules"].pop(name, None)
        else:
            data["rules"][name] = {**rule, "source": "s2"}
    return load_pack(data)


def test_the_fictional_pack_rules_are_read_with_their_types() -> None:
    pack = items_pack()

    assert item_rule(pack, "book_of_building") == BookRule(
        "book_of_building", frozenset({"building"})
    )
    assert item_rule(pack, "builder_potion") == PotionRule("builder_potion", "builder", 10, 3600)
    assert item_rule(pack, "research_potion") == PotionRule("research_potion", "lab", 10, 3600)
    assert item_rule(pack, "rune_of_gold") == RuneRule("rune_of_gold", "gold")
    gem = gem_finish_rule(pack)
    assert gem is not None
    assert gem.rates == (GemRate(3600, 7), GemRate(86400, 120))
    helper = helper_upgrade_rule(pack)
    assert helper is not None
    assert [(level.level, dict(level.cost)) for level in helper.levels] == [(2, {"gems": 75})]


@pytest.mark.parametrize("name", ["book_of_heroes", "rune_of_elixir", "pet_potion", "hammer"])
def test_an_item_without_rule_is_not_supported(name: str) -> None:
    assert item_rule(items_pack(), name) is None


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("book_of_building", {"kinds": []}),
        ("book_of_building", {"kinds": ["castle"]}),
        ("builder_potion", {"duration_multiplier": 0, "duration_s": 3600}),
        ("builder_potion", {"duration_multiplier": "10", "duration_s": 3600}),
        ("research_potion", {"duration_s": 3600}),
        ("rune_of_gold", {"resource": "mana"}),
    ],
)
def test_a_malformed_rule_is_not_supported(name: str, rule: dict[str, Any]) -> None:
    assert item_rule(items_pack(**{name: rule}), name) is None


def test_malformed_or_absent_e19_rules_are_not_supported() -> None:
    assert gem_finish_rule(items_pack(gem_finish={"rates": []})) is None
    assert gem_finish_rule(items_pack(gem_finish=None)) is None
    bad_cost = {"levels": [{"level": 2, "cost": {"mana": 3}}]}
    assert helper_upgrade_rule(items_pack(helper_upgrade=bad_cost)) is None
    assert helper_upgrade_rule(items_pack(helper_upgrade=None)) is None


@pytest.mark.parametrize(
    ("left", "end_after"),
    [
        (36_000, 3_600),  # r = D * m: ceil(r / m)
        (35_999, 3_600),  # ceil(35999 / 10)
        (72_000, 39_600),  # r - D * (m - 1)
        (10, 1),
    ],
)
def test_a_potion_follows_the_spec_formula(left: int, end_after: int) -> None:
    potion = PotionRule("builder_potion", "builder", 10, 3600)
    at = 1_000_000
    assert potion.end_after(at, at + left) == at + end_after


def test_a_potion_leaves_an_ended_job_alone() -> None:
    potion = PotionRule("research_potion", "lab", 10, 3600)
    assert potion.end_after(500, 400) == 400
