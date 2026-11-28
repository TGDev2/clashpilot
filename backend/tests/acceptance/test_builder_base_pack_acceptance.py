"""Acceptance tests for E31: build the pack with the objects of the builder base.

SPEC (builder base backlog): `gamedata/builder.py` reads `data/builder` besides `data/home`, with
the resources `builder_gold` and `builder_elixir`, the prerequisites towards the builder hall and
the star laboratory, `availablePerBuilderHall`, the builder storages and instant walls; nothing is
guessed (ADR-BB4). Frozen with the task. The frozen E03 test (`test_pack_builder_acceptance.py`)
and its tree `fixtures/community_data` stay as they are.

Fictional data only (ADR-2): the tree `fixtures/community_data_builder` holds a minimal `home`
folder and a `builder` folder shaped like the dataset (same file stems in both villages for the
walls and the two storages).

Shapes that the task text leaves open are fixed here:

- "The same pack as today" is read from `fixtures/built_packs/home_packs_before_builder_base.json`,
  written by the builder of `main` before E31 from the E03 tree and from the new tree without its
  `builder` folder. The pack must be equal; of the skipped list, the (file, data_id, level)
  entries must be equal. The home village keeps that content when the `builder` folder is there,
  including its wall, which today's builder leaves without a level row.
- A skipped builder level is reported like a home one: `data_id`, `level` and a non-empty
  `reason`. The text of the reason is free.
- Builder walls: item tagged `instant_upgrade`, their upgrades are rows lasting 0 s with the cost
  of the dataset. Level 1 costs nothing in the dataset: it may stay out of the pack (then it is
  reported) or be a row lasting 0 s.
- `builder/leagues` holds no object: it gives no item; whether it is reported is free. A file in a
  folder the builder does not know is reported, as in the home village.
- A `dataId` already read in the home folder is not read twice: the home item stays and the
  builder file is reported.
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

from clashpilot.gamedata.pack import load_pack

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TREE = FIXTURES / "community_data_builder"
E03_TREE = FIXTURES / "community_data"
BEFORE = FIXTURES / "built_packs" / "home_packs_before_builder_base.json"

PACK_ID = "community-test"
GAME_VERSION = "test-version"
AS_OF = "2026-01-02"
RETRIEVED_AT = "2026-01-03"

HOME_TH, HOME_LAB, HOME_GOLD, HOME_ELIXIR, HOME_TROOP, HOME_WALL = range(7600001, 7600007)
(
    HALL,
    STAR_LAB,
    TURRET,
    CLOCK,
    GOLD_STORAGE,
    ELIXIR_STORAGE,
    GOLD_MINE,
    SNARE,
    WALL,
    HERO,
    TROOP,
) = range(7700001, 7700012)

HOME_IDS = set(range(7600001, 7600007))
BUILDER_IDS = set(range(7700001, 7700012))
BUILDER_RESOURCES = {"builder_gold", "builder_elixir"}
OR_BOTH = "Builder Gold or Builder Elixir"

HALL_FILE = "builder/builder-hall/builder-hall.json"
STAR_LAB_FILE = "builder/army-buildings/star-laboratory.json"
TURRET_FILE = "builder/defenses/practice-turret.json"
GOLD_STORAGE_FILE = "builder/resource-buildings/gold-storage.json"
WALL_FILE = "builder/walls/wall.json"
HERO_FILE = "builder/heroes/practice-engine.json"
TROOP_FILE = "builder/troops/practice-raider.json"


# --------------------------------------------------------------------------- helpers


def build(path: Path = TREE) -> tuple[dict[str, Any], list[Any]]:
    builder = importlib.import_module("clashpilot.gamedata.builder")
    result = builder.build_pack_from_dir(path, PACK_ID, GAME_VERSION, AS_OF, RETRIEVED_AT)
    pack, skipped = result if isinstance(result, tuple) else (result, result.skipped)
    assert isinstance(pack, dict)
    return pack, list(skipped)


def built(path: Path = TREE) -> tuple[dict[str, Any], list[Any]]:
    """A built pack that passes `load_pack`."""
    pack, skipped = build(path)
    load_pack(pack)
    return pack, skipped


def before(case: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(BEFORE.read_text(encoding="utf-8"))
    found: dict[str, Any] = loaded[case]
    return found


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


def storage_of(pack: dict[str, Any], data_id: int) -> set[tuple[int, str, int]]:
    return {
        (row["level"], row["resource"], row["capacity"])
        for row in pack["storage"]
        if row["data_id"] == data_id
    }


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


def skipped_keys(skipped: list[Any], prefix: str = "") -> list[tuple[str, Any, Any]]:
    keys = [
        (entry_field(e, "file"), entry_field(e, "data_id"), entry_field(e, "level"))
        for e in skipped
        if str(entry_field(e, "file")).startswith(prefix)
    ]
    return sorted(keys, key=repr)


def text_of(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=lambda other: repr(other))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A private copy of the fictional tree, safe to alter."""
    copy = tmp_path / "data"
    shutil.copytree(TREE, copy)
    return copy


def edit(tree: Path, relative: str, change: Callable[[dict[str, Any]], None]) -> None:
    path = tree / relative
    content: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    change(content)
    path.write_text(json.dumps(content), encoding="utf-8")


def level_of(content: dict[str, Any], number: int) -> dict[str, Any]:
    found: dict[str, Any] = next(row for row in content["levels"] if row["level"] == number)
    return found


def edit_level(tree: Path, relative: str, number: int, **fields: Any) -> None:
    edit(tree, relative, lambda content: level_of(content, number).update(fields))


def drop_field(tree: Path, relative: str, number: int, field: str) -> None:
    def change(content: dict[str, Any]) -> None:
        del level_of(content, number)[field]

    edit(tree, relative, change)


# --------------------------------------------------------------------------- both villages


def test_every_object_of_the_builder_folder_enters_the_pack_in_the_builder_village() -> None:
    pack, _ = built()

    identifiers = [item["data_id"] for item in pack["items"]]
    assert sorted(identifiers) == sorted(HOME_IDS | BUILDER_IDS)
    villages = {item["data_id"]: item["village"] for item in pack["items"]}
    assert {data_id for data_id, village in villages.items() if village == "builder"} == BUILDER_IDS
    assert {data_id for data_id, village in villages.items() if village == "home"} == HOME_IDS


def test_a_builder_item_keeps_its_identity_and_the_community_source() -> None:
    pack, _ = built()
    turret = item_of(pack, TURRET)

    assert turret["name"] == "Tourelle d'essai"
    assert turret["category"] == "defense"
    source = load_pack(pack).source_of(TURRET, 2)
    assert source is not None
    assert "clash-of-clans-data" in source.label


def test_a_builder_time_object_becomes_seconds() -> None:
    pack, _ = built()

    assert row_of(pack, TURRET, 2)["duration_s"] == 93784
    assert row_of(pack, HERO, 2)["duration_s"] == 28800
    assert row_of(pack, TROOP, 2)["duration_s"] == 10800


@pytest.mark.parametrize("case", ["community_data", "home_only"])
def test_a_tree_without_a_builder_folder_gives_the_pack_of_today(case: str, tree: Path) -> None:
    shutil.rmtree(tree / "builder")
    source = E03_TREE if case == "community_data" else tree

    pack, skipped = built(source)

    expected = before(case)
    assert pack == expected["pack"]
    assert skipped_keys(skipped) == skipped_keys(expected["skipped"])


def test_the_builder_folder_adds_to_the_home_village_without_changing_it() -> None:
    pack, skipped = built()
    expected = before("home_only")
    reference = expected["pack"]

    assert {item["data_id"] for item in pack["items"]} >= BUILDER_IDS
    assert [i for i in pack["items"] if i["data_id"] in HOME_IDS] == reference["items"]
    assert [r for r in pack["levels"] if r["data_id"] in HOME_IDS] == reference["levels"]
    assert [r for r in pack["storage"] if r["data_id"] in HOME_IDS] == reference["storage"]
    assert skipped_keys(skipped, "home/") == skipped_keys(expected["skipped"])


def test_the_builder_files_bring_no_image_and_no_description() -> None:
    pack, skipped = built()
    output = text_of([pack, skipped])

    assert item_of(pack, TURRET)
    assert ".png" not in output
    assert "DESCRIPTION-SENTINEL" not in output


def test_a_file_in_an_unknown_builder_folder_is_ignored_and_noted(tree: Path) -> None:
    folder = tree / "builder" / "mystery-folder"
    folder.mkdir()
    content = {"dataId": 7799999, "name": "Bizarrerie d'essai", "levels": []}
    (folder / "builder-oddity.json").write_text(json.dumps(content), encoding="utf-8")

    pack, skipped = built(tree)

    assert HALL in {item["data_id"] for item in pack["items"]}
    assert 7799999 not in {item["data_id"] for item in pack["items"]}
    assert "builder-oddity" in text_of(skipped)


def test_a_data_id_already_read_at_home_is_noted_and_not_read_twice(tree: Path) -> None:
    edit(tree, TURRET_FILE, lambda content: content.update(dataId=HOME_LAB))

    pack, skipped = built(tree)

    assert HALL in {item["data_id"] for item in pack["items"]}
    assert item_of(pack, HOME_LAB)["village"] == "home"
    assert "practice-turret" in text_of(skipped)


# --------------------------------------------------------------------------- lanes and tags


@pytest.mark.parametrize(
    ("data_id", "lane", "kind"),
    [
        (HALL, "builder", "building"),
        (STAR_LAB, "builder", "building"),
        (TURRET, "builder", "building"),
        (CLOCK, "builder", "building"),
        (GOLD_STORAGE, "builder", "building"),
        (GOLD_MINE, "builder", "building"),
        (SNARE, "builder", "trap"),
        (WALL, "builder", "wall"),
        (HERO, "builder", "hero"),
        (TROOP, "lab", "troop"),
    ],
)
def test_the_builder_folder_decides_lane_and_kind(data_id: int, lane: str, kind: str) -> None:
    pack, _ = built()
    item = item_of(pack, data_id)

    assert item["village"] == "builder"
    assert item["lane"] == lane
    assert item["kind"] == kind


@pytest.mark.parametrize(
    ("tag", "tagged"),
    [
        ("town_hall", {"home": [HOME_TH], "builder": [HALL]}),
        ("laboratory", {"builder": [STAR_LAB]}),
    ],
)
def test_the_builder_hall_and_the_star_laboratory_carry_their_tags(
    tag: str, tagged: dict[str, list[int]]
) -> None:
    pack, _ = built()

    found: dict[str, list[int]] = {}
    for item in pack["items"]:
        if tag in item["tags"]:
            found.setdefault(item["village"], []).append(item["data_id"])
    assert found == tagged


# --------------------------------------------------------------------------- resources


@pytest.mark.parametrize(
    ("data_id", "level", "cost"),
    [
        (HALL, 2, {"builder_gold": 3100}),
        (STAR_LAB, 2, {"builder_elixir": 4200}),
        (TURRET, 2, {"builder_gold": 5200}),
        (GOLD_STORAGE, 2, {"builder_elixir": 6300}),
        (HERO, 2, {"builder_elixir": 7100}),
        (TROOP, 3, {"builder_elixir": 8100}),
    ],
)
def test_a_builder_resource_name_is_normalised(
    data_id: int, level: int, cost: dict[str, int]
) -> None:
    pack, _ = built()
    assert row_of(pack, data_id, level)["cost"] == cost


def test_no_builder_row_costs_a_home_resource() -> None:
    pack, _ = built()
    rows = [row for row in pack["levels"] if row["data_id"] in BUILDER_IDS]

    assert rows
    for row in rows:
        assert set(row["cost"]) <= BUILDER_RESOURCES, row


def test_a_level_payable_with_either_resource_is_skipped_and_cited() -> None:
    pack, skipped = built()

    assert row_of(pack, WALL, 3)["cost"] == {"builder_gold": 12100}
    assert row_or_none(pack, WALL, 4) is None
    assert is_cited(skipped, WALL, 4)


def test_either_resource_is_never_guessed_outside_the_walls(tree: Path) -> None:
    edit_level(tree, TURRET_FILE, 2, buildCostResource=OR_BOTH)

    pack, skipped = built(tree)

    assert row_or_none(pack, TURRET, 2) is None
    assert row_or_none(pack, TURRET, 3) is not None
    assert is_cited(skipped, TURRET, 2)


@pytest.mark.parametrize(
    ("relative", "data_id", "absent_field"),
    [
        (TURRET_FILE, TURRET, "buildCost"),
        (TURRET_FILE, TURRET, "buildCostResource"),
        (TURRET_FILE, TURRET, "buildTime"),
        (TROOP_FILE, TROOP, "researchCost"),
        (TROOP_FILE, TROOP, "researchTime"),
        (HERO_FILE, HERO, "upgradeCost"),
        (HERO_FILE, HERO, "upgradeTime"),
    ],
)
def test_a_builder_level_missing_a_cost_or_a_time_is_skipped_and_cited(
    tree: Path, relative: str, data_id: int, absent_field: str
) -> None:
    drop_field(tree, relative, 2, absent_field)

    pack, skipped = built(tree)

    assert row_or_none(pack, data_id, 2) is None
    assert is_cited(skipped, data_id, 2)
    assert row_or_none(pack, HALL, 2) is not None


def test_a_zero_time_outside_the_builder_walls_is_skipped(tree: Path) -> None:
    zero = {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}
    edit_level(tree, TURRET_FILE, 2, buildTime=zero)

    pack, skipped = built(tree)

    assert row_or_none(pack, TURRET, 2) is None
    assert "instant_upgrade" not in item_of(pack, TURRET)["tags"]
    assert is_cited(skipped, TURRET, 2)


# --------------------------------------------------------------------------- prerequisites


@pytest.mark.parametrize(
    ("data_id", "level", "required"),
    [
        (STAR_LAB, 3, {"data_id": HALL, "level": 3}),
        (TURRET, 2, {"data_id": HALL, "level": 2}),
        (WALL, 3, {"data_id": HALL, "level": 2}),
        (HERO, 2, {"data_id": HALL, "level": 2}),
        (TROOP, 3, {"data_id": STAR_LAB, "level": 2}),
    ],
)
def test_a_builder_prerequisite_points_at_the_builder_building(
    data_id: int, level: int, required: dict[str, int]
) -> None:
    pack, _ = built()
    assert row_of(pack, data_id, level)["requires"] == [required]


@pytest.mark.parametrize(
    ("moved_file", "data_id", "level", "required_level"),
    [
        (HALL_FILE, TURRET, 2, 2),
        (HALL_FILE, HERO, 2, 2),
        (STAR_LAB_FILE, TROOP, 3, 2),
    ],
)
def test_the_id_of_a_builder_prerequisite_is_read_from_its_file(
    tree: Path, moved_file: str, data_id: int, level: int, required_level: int
) -> None:
    moved = 7700999
    edit(tree, moved_file, lambda content: content.update(dataId=moved))

    pack, _ = built(tree)

    assert row_of(pack, data_id, level)["requires"] == [{"data_id": moved, "level": required_level}]


@pytest.mark.parametrize(
    ("removed", "data_id", "level"),
    [
        (HALL_FILE, TURRET, 2),
        (HALL_FILE, HERO, 2),
        (STAR_LAB_FILE, TROOP, 2),
    ],
)
def test_a_missing_builder_prerequisite_file_skips_the_level(
    tree: Path, removed: str, data_id: int, level: int
) -> None:
    (tree / removed).unlink()

    pack, skipped = built(tree)

    assert item_of(pack, data_id)["village"] == "builder"
    assert row_or_none(pack, data_id, level) is None
    assert is_cited(skipped, data_id, level)


@pytest.mark.parametrize(
    ("relative", "data_id", "field"),
    [
        (TURRET_FILE, TURRET, "builderHallRequired"),
        (HERO_FILE, HERO, "builderHallLevelRequired"),
        (TROOP_FILE, TROOP, "starLabRequired"),
    ],
)
def test_an_unreadable_builder_prerequisite_skips_the_level(
    tree: Path, relative: str, data_id: int, field: str
) -> None:
    edit_level(tree, relative, 2, **{field: "two"})

    pack, skipped = built(tree)

    assert item_of(pack, data_id)["village"] == "builder"
    assert row_or_none(pack, data_id, 2) is None
    assert is_cited(skipped, data_id, 2)


# --------------------------------------------------------------------------- counts and storage


@pytest.mark.parametrize(
    ("data_id", "counts"),
    [
        (TURRET, {"2": 1, "3": 3}),
        (CLOCK, {"2": 1}),
        (STAR_LAB, {"1": 1, "2": 1, "3": 1}),
        (WALL, {"1": 3, "2": 5, "3": 9}),
        (HALL, {}),
    ],
)
def test_the_builder_counts_come_from_available_per_builder_hall(
    data_id: int, counts: dict[str, int]
) -> None:
    pack, _ = built()
    assert item_of(pack, data_id)["max_count_by_th"] == counts


@pytest.mark.parametrize(
    ("data_id", "rows"),
    [
        (GOLD_STORAGE, {(1, "builder_gold", 81000), (2, "builder_gold", 152000)}),
        (ELIXIR_STORAGE, {(1, "builder_elixir", 83000), (2, "builder_elixir", 154000)}),
        (GOLD_MINE, set()),
    ],
)
def test_the_builder_storages_hold_the_builder_resources(
    data_id: int, rows: set[tuple[int, str, int]]
) -> None:
    pack, _ = built()

    assert item_of(pack, data_id)["village"] == "builder"
    assert storage_of(pack, data_id) == rows


def test_a_builder_storage_level_without_capacity_is_cited_not_guessed(tree: Path) -> None:
    drop_field(tree, GOLD_STORAGE_FILE, 2, "capacity")

    pack, skipped = built(tree)

    assert storage_of(pack, GOLD_STORAGE) == {(1, "builder_gold", 81000)}
    assert is_cited(skipped, GOLD_STORAGE, 2)


# --------------------------------------------------------------------------- walls


def test_the_builder_walls_are_instant_upgrades() -> None:
    pack, skipped = built()

    assert "instant_upgrade" in item_of(pack, WALL)["tags"]
    for level, amount in ((2, 4100), (3, 12100)):
        row = row_of(pack, WALL, level)
        assert row["duration_s"] == 0
        assert row["cost"] == {"builder_gold": amount}
    first = row_or_none(pack, WALL, 1)
    if first is None:
        assert is_cited(skipped, WALL, 1)
    else:
        assert first["duration_s"] == 0
    instant = [
        item["data_id"]
        for item in pack["items"]
        if item["village"] == "builder" and "instant_upgrade" in item["tags"]
    ]
    assert instant == [WALL]


def test_a_builder_wall_level_without_cost_is_skipped_and_cited(tree: Path) -> None:
    drop_field(tree, WALL_FILE, 2, "buildCost")

    pack, skipped = built(tree)

    assert row_or_none(pack, WALL, 2) is None
    assert row_or_none(pack, WALL, 3) is not None
    assert is_cited(skipped, WALL, 2)


def test_compiling_the_builder_base_leaves_the_walls_to_the_wall_sink() -> None:
    from clashpilot.ingest.export import parse_export
    from clashpilot.ingest.state import ManualState
    from clashpilot.planning.compile import compile_problem
    from clashpilot.schedule.profile import ScheduleProfile

    now = 1_795_852_800  # 2026-11-28T08:00:00Z
    pack, _ = build()
    export = {
        "tag": "#FICTIFOUVR",
        "timestamp": now - 3600,
        "buildings": [{"data": HOME_TH, "lvl": 1}],
        "buildings2": [
            {"data": HALL, "lvl": 1},
            {"data": STAR_LAB, "lvl": 1},
            {"data": WALL, "lvl": 1, "cnt": 3},
        ],
        "units2": [{"data": TROOP, "lvl": 1}],
    }
    manual = ManualState.model_validate(
        {
            "resources": {
                name: {"value": 500_000, "status": "confirmed", "as_of": now}
                for name in sorted(BUILDER_RESOURCES)
            },
            "builder_base_builders": 2,
        }
    )
    profile = ScheduleProfile.model_validate(
        {
            "tz": "UTC",
            "weekly": [],
            "exceptions": [],
            "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
        }
    )

    problem = compile_problem(
        parse_export(export, now), manual, load_pack(pack), profile, now, 90, base="builder"
    )

    walls = [one for one in problem.excluded if one.data_id == WALL and one.to_level in (2, 3)]
    assert {one.reason for one in walls} == {"wall_sink"}
    assert sum(one.to_level == 2 for one in walls) >= 3
    assert sum(one.to_level == 3 for one in walls) >= 3
    lanes = {task.data_id: task.lane for task in problem.tasks}
    assert WALL not in lanes
    assert lanes[HALL] == "builder"
    assert lanes[TROOP] == "lab"
    assert problem.lanes["lab"] == 1


# --------------------------------------------------------------------------- command line


def test_the_command_line_writes_a_pack_with_the_builder_base(tmp_path: Path) -> None:
    output = tmp_path / "pack.json"
    command = [sys.executable, "-m", "clashpilot.gamedata.builder"]
    command += ["--input", str(TREE), "--output", str(output), "--pack-id", PACK_ID]
    command += ["--game-version", GAME_VERSION, "--as-of", AS_OF, "--retrieved-at", RETRIEVED_AT]

    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=18, check=False
    )

    assert result.returncode == 0, result.stderr
    written = json.loads(output.read_text(encoding="utf-8"))
    load_pack(written)
    assert {item["data_id"] for item in written["items"]} >= BUILDER_IDS
    assert WALL_FILE in result.stdout
