"""Acceptance tests for E03: community pack builder and manual corrections (SPEC section 5).

Frozen with the task. They use only the fictional tree of `fixtures/community_data` and the
fictional pack. `clashpilot.gamedata.builder` and `clashpilot.storage.packs` are imported inside
the helpers, so that every test fails on its own until the feature exists.

`build_pack_from_dir` returns the pack dictionary and the list of skipped levels, either as a
`(pack, skipped)` tuple or as a dictionary carrying a `skipped` attribute. The dictionary itself
must pass `load_pack`. A skipped level exposes `data_id`, `level` and `reason`, as a mapping or as
attributes.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.settings import Settings
from clashpilot.storage.db import open_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
COMMUNITY_DATA = FIXTURES / "community_data"
FICTIONAL_PACK = FIXTURES / "packs" / "fictional_pack_v1.json"

PACK_ID = "community-test"
GAME_VERSION = "test-version"
AS_OF = "2026-01-02"
RETRIEVED_AT = "2026-01-03"

TOWN_HALL = 1000001
LABORATORY = 1000007
HERO_HALL = 1900071
GOLD_STORAGE = 1900005
CANNON = 1900008
HUT = 1900015
TROOP = 1900026
HERO = 1900081
EQUIPMENT = 1900091
PET = 1900101
CAPITAL_CANNON = 8000001
CAPITAL_DISTRICT = 8000002
UNKNOWN_FOLDER_ITEM = 8100001

CANNON_FILE = "home/defenses/training-cannon.json"
TROOP_FILE = "home/troops/practice-soldier.json"
HERO_FILE = "home/heroes/practice-champion.json"
HERO_HALL_FILE = "home/army-buildings/hero-hall.json"

FICTIONAL_CANNON = 9000004


# --------------------------------------------------------------------------- helpers


def build(path: Path = COMMUNITY_DATA) -> tuple[dict[str, Any], list[Any]]:
    builder = importlib.import_module("clashpilot.gamedata.builder")
    result = builder.build_pack_from_dir(path, PACK_ID, GAME_VERSION, AS_OF, RETRIEVED_AT)
    pack, skipped = result if isinstance(result, tuple) else (result, result.skipped)
    assert isinstance(pack, dict)
    return pack, list(skipped)


def item_of(pack: dict[str, Any], data_id: int) -> dict[str, Any]:
    for item in pack["items"]:
        if item["data_id"] == data_id:
            return item  # type: ignore[no-any-return]
    raise AssertionError(f"pack has no item {data_id}")


def row_or_none(pack: dict[str, Any], data_id: int, level: int) -> dict[str, Any] | None:
    for row in pack["levels"]:
        if row["data_id"] == data_id and row["level"] == level:
            return row  # type: ignore[no-any-return]
    return None


def row_of(pack: dict[str, Any], data_id: int, level: int) -> dict[str, Any]:
    row = row_or_none(pack, data_id, level)
    assert row is not None, f"pack has no row ({data_id}, {level})"
    return row


def entry_field(entry: Any, name: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def is_cited(skipped: list[Any], data_id: int, level: int) -> bool:
    return any(
        entry_field(entry, "data_id") == data_id
        and entry_field(entry, "level") == level
        and isinstance(entry_field(entry, "reason"), str)
        and bool(entry_field(entry, "reason"))
        for entry in skipped
    )


def text_of(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=lambda other: repr(other))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A private copy of the fictional tree, safe to alter."""
    copy = tmp_path / "data"
    shutil.copytree(COMMUNITY_DATA, copy)
    return copy


def edit(tree: Path, relative: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = tree / relative
    content: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    change(content)
    path.write_text(json.dumps(content), encoding="utf-8")


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "clashpilot.gamedata.builder", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=18,
        check=False,
    )


def stored_pack(pack_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FICTIONAL_PACK.read_text(encoding="utf-8"))
    data["pack_id"] = pack_id
    return data


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    return TestClient(create_app(settings))


def store(client: TestClient, pack_id: str) -> None:
    assert client.post("/api/gamedata/packs", json=stored_pack(pack_id)).status_code == 201


def correct(client: TestClient, pack_id: str, level: int, body: dict[str, Any]) -> None:
    url = f"/api/gamedata/packs/{pack_id}/items/{FICTIONAL_CANNON}/levels/{level}"
    response = client.patch(url, json=body)
    assert response.status_code < 300, response.text


def effective(data_dir: Path, pack_id: str) -> Any:
    packs = importlib.import_module("clashpilot.storage.packs")
    with open_db(data_dir) as connection:
        return packs.load_effective_pack(connection, pack_id)


# --------------------------------------------------------------------------- pack and source


def test_the_built_dictionary_passes_load_pack() -> None:
    pack, _ = build()
    loaded = load_pack(pack)

    assert isinstance(loaded, GamePack)
    assert pack["pack_id"] == PACK_ID
    assert pack["game_version"] == GAME_VERSION
    assert pack["as_of"] == AS_OF
    assert pack["fictional"] is False


def test_the_pack_cites_the_community_dataset() -> None:
    pack, _ = build()

    sources = [s for s in pack["sources"] if "clash-of-clans-data" in s["label"]]
    assert len(sources) == 1
    assert sources[0]["license"] == "MIT"
    assert sources[0]["retrieved_at"] == RETRIEVED_AT


def test_each_level_row_points_at_the_community_source() -> None:
    pack, _ = build()
    source = load_pack(pack).source_of(CANNON, 2)

    assert source is not None
    assert "clash-of-clans-data" in source.label


def test_an_item_keeps_its_identity() -> None:
    pack, _ = build()
    cannon = item_of(pack, CANNON)

    assert cannon["name"] == "Canon de test"
    assert cannon["village"] == "home"
    assert cannon["category"] == "defense"


# --------------------------------------------------------------------------- conversions


def test_a_time_object_becomes_seconds() -> None:
    pack, _ = build()
    assert row_of(pack, CANNON, 2)["duration_s"] == 93784


@pytest.mark.parametrize(
    ("data_id", "level", "cost"),
    [
        (CANNON, 2, {"gold": 413}),
        (TROOP, 2, {"elixir": 227}),
        (HERO, 2, {"dark_elixir": 1213}),
    ],
)
def test_a_resource_name_is_normalised(data_id: int, level: int, cost: dict[str, int]) -> None:
    pack, _ = build()
    assert row_of(pack, data_id, level)["cost"] == cost


def test_every_positive_ore_field_becomes_a_cost() -> None:
    pack, _ = build()
    assert row_of(pack, EQUIPMENT, 3)["cost"] == {
        "shiny_ore": 203,
        "glowy_ore": 37,
        "starry_ore": 5,
    }


def test_an_ore_field_that_is_null_or_absent_is_left_out() -> None:
    pack, _ = build()
    assert row_of(pack, EQUIPMENT, 2)["cost"] == {"shiny_ore": 101}


# --------------------------------------------------------------------------- requires


def test_a_building_level_requires_the_town_hall() -> None:
    pack, _ = build()
    row = row_of(pack, CANNON, 3)

    assert row["cost"] == {"gold": 919}
    assert row["duration_s"] == 10800
    assert {"data_id": TOWN_HALL, "level": 3} in row["requires"]


def test_a_troop_level_requires_the_laboratory() -> None:
    pack, _ = build()
    row = row_of(pack, TROOP, 2)

    assert row["duration_s"] == 3600
    assert row["cost"] == {"elixir": 227}
    assert {"data_id": LABORATORY, "level": 3} in row["requires"]


def test_a_hero_level_requires_the_hero_hall() -> None:
    pack, _ = build()
    row = row_of(pack, HERO, 2)

    assert row["duration_s"] == 14400
    assert row["cost"] == {"dark_elixir": 1213}
    assert {"data_id": HERO_HALL, "level": 2} in row["requires"]


def test_the_hero_hall_id_is_read_from_its_file(tree: Path) -> None:
    moved = 1900999

    def change(content: dict[str, Any]) -> None:
        content["dataId"] = moved

    edit(tree, HERO_HALL_FILE, change)

    pack, _ = build(tree)
    load_pack(pack)
    assert {"data_id": moved, "level": 2} in row_of(pack, HERO, 2)["requires"]


def test_a_missing_prerequisite_file_never_leaves_a_dangling_reference(tree: Path) -> None:
    (tree / HERO_HALL_FILE).unlink()

    pack, _ = build(tree)

    load_pack(pack)


# --------------------------------------------------------------------------- items


@pytest.mark.parametrize(
    ("data_id", "lane", "kind"),
    [
        (CANNON, "builder", "building"),
        (TROOP, "lab", "troop"),
        (HERO, "builder", "hero"),
        (PET, "pet_house", "pet"),
        (EQUIPMENT, "blacksmith", "equipment"),
        (LABORATORY, "builder", "building"),
        (HERO_HALL, "builder", "building"),
        (GOLD_STORAGE, "builder", "building"),
        (TOWN_HALL, "builder", "building"),
    ],
)
def test_the_folder_decides_lane_and_kind(data_id: int, lane: str, kind: str) -> None:
    pack, _ = build()
    item = item_of(pack, data_id)

    assert item["lane"] == lane
    assert item["kind"] == kind


def test_a_storage_building_produces_storage_rows() -> None:
    pack, _ = build()
    rows = {
        (row["level"], row["resource"], row["capacity"])
        for row in pack["storage"]
        if row["data_id"] == GOLD_STORAGE
    }

    assert rows == {(1, "gold", 1517), (2, "gold", 5023)}


def test_available_per_town_hall_drops_null_counts() -> None:
    pack, _ = build()
    assert item_of(pack, CANNON)["max_count_by_th"] == {"2": 1, "3": 2}


def test_the_town_hall_carries_its_tag() -> None:
    pack, _ = build()
    assert "town_hall" in item_of(pack, TOWN_HALL)["tags"]


def test_the_builder_hut_carries_its_tag() -> None:
    pack, _ = build()
    assert "builder_hut" in item_of(pack, HUT)["tags"]


def test_a_pet_level_is_either_read_faithfully_or_skipped() -> None:
    pack, skipped = build()
    row = row_or_none(pack, PET, 1)

    assert item_of(pack, PET)["lane"] == "pet_house"
    if row is None:
        assert is_cited(skipped, PET, 1)
    else:
        assert row["duration_s"] == 7200
        assert row["cost"] == {"dark_elixir": 401}


# --------------------------------------------------------------------------- equipment


def test_equipment_levels_are_instant_upgrades_with_ore_costs() -> None:
    pack, _ = build()
    item = item_of(pack, EQUIPMENT)

    assert item["lane"] == "blacksmith"
    assert "instant_upgrade" in item["tags"]
    for level in (2, 3):
        row = row_of(pack, EQUIPMENT, level)
        assert row["duration_s"] == 0
        assert row["cost"]


# --------------------------------------------------------------------------- skipped levels


@pytest.mark.parametrize(
    ("relative", "data_id", "absent_field"),
    [
        (CANNON_FILE, CANNON, "buildCost"),
        (CANNON_FILE, CANNON, "buildCostResource"),
        (CANNON_FILE, CANNON, "buildTime"),
        (TROOP_FILE, TROOP, "researchCost"),
        (TROOP_FILE, TROOP, "researchTime"),
        (HERO_FILE, HERO, "upgradeCost"),
        (HERO_FILE, HERO, "upgradeTime"),
    ],
)
def test_a_level_missing_a_cost_or_a_time_is_skipped_and_cited(
    tree: Path, relative: str, data_id: int, absent_field: str
) -> None:
    def change(content: dict[str, Any]) -> None:
        del content["levels"][1][absent_field]

    edit(tree, relative, change)

    pack, skipped = build(tree)

    load_pack(pack)
    assert row_or_none(pack, data_id, 2) is None
    assert row_or_none(pack, data_id, 1) is not None
    assert is_cited(skipped, data_id, 2)


def test_a_zero_time_outside_hero_equipment_is_skipped(tree: Path) -> None:
    def change(content: dict[str, Any]) -> None:
        content["levels"][1]["buildTime"] = {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}

    edit(tree, CANNON_FILE, change)

    pack, skipped = build(tree)

    load_pack(pack)
    assert row_or_none(pack, CANNON, 2) is None
    assert "instant_upgrade" not in item_of(pack, CANNON)["tags"]
    assert is_cited(skipped, CANNON, 2)


def test_no_replacement_zero_is_ever_created(tree: Path) -> None:
    def change(content: dict[str, Any]) -> None:
        del content["levels"][1]["buildCost"]

    edit(tree, CANNON_FILE, change)

    pack, _ = build(tree)

    instant = {item["data_id"] for item in pack["items"] if "instant_upgrade" in item["tags"]}
    for row in pack["levels"]:
        assert all(amount > 0 for amount in row["cost"].values()), row
        if row["data_id"] not in instant:
            assert row["duration_s"] > 0, row


# --------------------------------------------------------------------------- excluded content


def test_the_output_has_no_image_and_no_description() -> None:
    pack, skipped = build()
    output = text_of([pack, skipped])

    assert ".png" not in output
    assert "DESCRIPTION-SENTINEL" not in output


def test_clan_capital_folders_are_ignored() -> None:
    pack, _ = build()
    identifiers = {item["data_id"] for item in pack["items"]}

    assert CAPITAL_CANNON not in identifiers
    assert CAPITAL_DISTRICT not in identifiers


def test_a_file_in_an_unknown_folder_is_ignored_and_noted() -> None:
    pack, skipped = build()

    assert UNKNOWN_FOLDER_ITEM not in {item["data_id"] for item in pack["items"]}
    assert "oddity" in text_of(skipped)


def test_a_file_without_item_shape_is_ignored_and_noted(tree: Path) -> None:
    (tree / "home/defenses/not-an-item.json").write_text("[1, 2]", encoding="utf-8")

    pack, skipped = build(tree)

    load_pack(pack)
    assert "not-an-item" in text_of(skipped)


# --------------------------------------------------------------------------- command line


def test_the_command_line_writes_the_pack(tmp_path: Path) -> None:
    output = tmp_path / "pack.json"

    result = run_cli(
        "--input",
        str(COMMUNITY_DATA),
        "--output",
        str(output),
        "--pack-id",
        PACK_ID,
        "--game-version",
        GAME_VERSION,
        "--as-of",
        AS_OF,
        "--retrieved-at",
        RETRIEVED_AT,
    )

    assert result.returncode == 0, result.stderr
    written = json.loads(output.read_text(encoding="utf-8"))
    assert load_pack(written).pack_id == PACK_ID
    assert written["as_of"] == AS_OF
    assert written["sources"][0]["retrieved_at"] == RETRIEVED_AT


def test_the_command_line_reports_a_missing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"
    output = tmp_path / "pack.json"

    result = run_cli(
        "--input",
        str(missing),
        "--output",
        str(output),
        "--pack-id",
        PACK_ID,
        "--game-version",
        GAME_VERSION,
        "--as-of",
        AS_OF,
        "--retrieved-at",
        RETRIEVED_AT,
    )

    assert result.returncode == 1
    assert str(missing) in result.stdout + result.stderr
    assert not output.exists()


# --------------------------------------------------------------------------- corrections


def test_the_effective_pack_applies_a_duration_correction(
    client: TestClient, tmp_path: Path
) -> None:
    store(client, "pack-a")
    correct(client, "pack-a", 3, {"duration_s": 4321, "note": "mesure en jeu"})

    pack = effective(tmp_path, "pack-a")

    assert isinstance(pack, GamePack)
    row = pack.level(FICTIONAL_CANNON, 3)
    assert row is not None
    assert row.duration_s == 4321
    assert dict(row.cost) == {"gold": 2000}


def test_a_corrected_line_is_sourced_as_manual(client: TestClient, tmp_path: Path) -> None:
    store(client, "pack-a")
    correct(client, "pack-a", 3, {"duration_s": 4321, "note": "mesure en jeu"})

    source = effective(tmp_path, "pack-a").source_of(FICTIONAL_CANNON, 3)

    assert source is not None
    assert source.id == "manual"


def test_a_cost_correction_is_applied_too(client: TestClient, tmp_path: Path) -> None:
    store(client, "pack-a")
    correct(client, "pack-a", 3, {"duration_s": 4321, "cost": {"gold": 77}, "note": "relevé"})

    row = effective(tmp_path, "pack-a").level(FICTIONAL_CANNON, 3)

    assert row is not None
    assert row.duration_s == 4321
    assert dict(row.cost) == {"gold": 77}


def test_other_levels_of_the_pack_are_left_alone(client: TestClient, tmp_path: Path) -> None:
    store(client, "pack-a")
    correct(client, "pack-a", 3, {"duration_s": 4321, "note": "mesure en jeu"})

    pack = effective(tmp_path, "pack-a")

    row = pack.level(FICTIONAL_CANNON, 2)
    assert row is not None
    assert row.duration_s == 300
    source = pack.source_of(FICTIONAL_CANNON, 2)
    assert source is not None
    assert source.id == "s1"


def test_another_pack_is_not_affected(client: TestClient, tmp_path: Path) -> None:
    store(client, "pack-a")
    store(client, "pack-b")
    correct(client, "pack-a", 3, {"duration_s": 4321, "note": "mesure en jeu"})

    other = effective(tmp_path, "pack-b")

    row = other.level(FICTIONAL_CANNON, 3)
    assert row is not None
    assert row.duration_s == 1800
    source = other.source_of(FICTIONAL_CANNON, 3)
    assert source is not None
    assert source.id == "s1"


def test_a_pack_without_correction_is_returned_as_stored(
    client: TestClient, tmp_path: Path
) -> None:
    store(client, "pack-a")

    row = effective(tmp_path, "pack-a").level(FICTIONAL_CANNON, 3)

    assert row is not None
    assert row.duration_s == 1800
    assert dict(row.cost) == {"gold": 2000}
