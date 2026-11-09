from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clashpilot.gamedata.builder import build_pack_from_dir
from clashpilot.gamedata.pack import load_pack

MINUTE = {"days": 0, "hours": 0, "minutes": 1, "seconds": 0}


def write(root: Path, relative: str, content: Any) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content), encoding="utf-8")


def source(data_id: int, levels: list[Any]) -> dict[str, Any]:
    return {"dataId": data_id, "name": f"Objet de test {data_id}", "levels": levels}


def gold_level(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "level": number,
        "buildCost": 10,
        "buildCostResource": "Gold",
        "buildTime": MINUTE,
        **extra,
    }


def build(root: Path) -> tuple[dict[str, Any], list[Any]]:
    pack, skipped = build_pack_from_dir(root, "unit-pack", "v", "2026-01-01", "2026-01-02")
    load_pack(pack)
    return pack, list(skipped)


def reasons(skipped: list[Any]) -> dict[Any, str]:
    return {(entry["data_id"], entry["level"]): entry["reason"] for entry in skipped}


@pytest.mark.parametrize(
    ("stem", "resource"),
    [("elixir-storage", "elixir"), ("dark-elixir-storage", "dark_elixir")],
)
def test_each_storage_file_gives_its_resource(tmp_path: Path, stem: str, resource: str) -> None:
    write(
        tmp_path,
        f"home/resource-buildings/{stem}.json",
        source(7, [gold_level(1, capacity=500)]),
    )

    pack, _ = build(tmp_path)

    assert [(row["resource"], row["capacity"]) for row in pack["storage"]] == [(resource, 500)]


def test_a_storage_level_without_capacity_is_cited(tmp_path: Path) -> None:
    write(tmp_path, "home/resource-buildings/gold-storage.json", source(7, [gold_level(1)]))

    pack, skipped = build(tmp_path)

    assert pack["storage"] == []
    assert reasons(skipped) == {(7, 1): "missing_capacity"}


def test_town_hall_storage_capacity_gives_positive_rows(tmp_path: Path) -> None:
    capacity = {"gold": 900, "elixir": 800, "darkElixir": 0}
    write(
        tmp_path,
        "home/town-hall/town-hall.json",
        source(1, [gold_level(1, storageCapacity=capacity)]),
    )

    pack, _ = build(tmp_path)

    assert {(row["resource"], row["capacity"]) for row in pack["storage"]} == {
        ("gold", 900),
        ("elixir", 800),
    }


def test_an_unknown_resource_is_skipped(tmp_path: Path) -> None:
    write(
        tmp_path,
        "home/defenses/odd.json",
        source(7, [gold_level(1, buildCostResource="Mithril")]),
    )

    pack, skipped = build(tmp_path)

    assert pack["levels"] == []
    assert reasons(skipped) == {(7, 1): "unknown_resource"}


def test_a_null_cost_is_skipped_rather_than_recorded_as_free(tmp_path: Path) -> None:
    write(tmp_path, "home/defenses/odd.json", source(7, [gold_level(1, buildCost=0)]))

    pack, skipped = build(tmp_path)

    assert pack["levels"] == []
    assert reasons(skipped) == {(7, 1): "missing_cost"}


def test_equipment_without_any_ore_is_skipped(tmp_path: Path) -> None:
    write(tmp_path, "home/hero-equipment/odd.json", source(7, [{"level": 1}]))

    pack, skipped = build(tmp_path)

    assert pack["levels"] == []
    assert reasons(skipped) == {(7, 1): "missing_cost"}


def test_a_duplicate_level_keeps_the_first_row(tmp_path: Path) -> None:
    write(tmp_path, "home/defenses/odd.json", source(7, [gold_level(1), gold_level(1)]))

    pack, skipped = build(tmp_path)

    assert len(pack["levels"]) == 1
    assert reasons(skipped) == {(7, 1): "invalid_level"}


def test_a_duplicate_data_id_is_noted_once(tmp_path: Path) -> None:
    write(tmp_path, "home/defenses/a.json", source(7, [gold_level(1)]))
    write(tmp_path, "home/traps/b.json", source(7, [gold_level(1)]))

    pack, skipped = build(tmp_path)

    assert [item["data_id"] for item in pack["items"]] == [7]
    assert [entry["reason"] for entry in skipped] == ["duplicate_data_id"]
    assert skipped[0]["file"] == "home/traps/b.json"


def test_a_file_that_is_not_json_is_noted(tmp_path: Path) -> None:
    path = tmp_path / "home/defenses/broken.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    pack, skipped = build(tmp_path)

    assert pack["items"] == []
    assert skipped[0]["file"] == "home/defenses/broken.json"


def test_a_folder_without_home_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="home"):
        build_pack_from_dir(tmp_path, "unit-pack", "v", "2026-01-01", "2026-01-02")
