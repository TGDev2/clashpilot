"""Build a game data pack from a local copy of the community dataset (SPEC section 5).

Offline command-line tool: the one module of `gamedata` that reads files, and only the tree it is
given. It never touches the network and never copies an image or a description.

The dataset is community-maintained and its fields may be missing. A level that cannot be read is
never completed with a guess: it stays out of the pack and is reported in the `skipped` list.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from clashpilot.gamedata.pack import (
    INSTANT_UPGRADE_TAG,
    SCHEMA_VERSION,
    Kind,
    Lane,
    PackValidationError,
    load_pack,
)

HOME = "home"
SOURCE_ID = "s1"
_SOURCE_LABEL = "chiefpansancolt/clash-of-clans-data"
_SOURCE_URL = "https://github.com/chiefpansancolt/clash-of-clans-data"
_SOURCE_LICENSE = "MIT"


@dataclass(frozen=True)
class _Folder:
    kind: Kind
    lane: Lane


_EQUIPMENT_FOLDER = "hero-equipment"
_FOLDERS: dict[str, _Folder] = {
    "army-buildings": _Folder("building", "builder"),
    "crafted-defenses": _Folder("building", "builder"),
    "defenses": _Folder("building", "builder"),
    "guardians": _Folder("guardian", "builder"),
    _EQUIPMENT_FOLDER: _Folder("equipment", "blacksmith"),
    "heroes": _Folder("hero", "builder"),
    "other": _Folder("building", "builder"),
    "pets": _Folder("pet", "pet_house"),
    "resource-buildings": _Folder("building", "builder"),
    "siege-machines": _Folder("siege", "lab"),
    "spells": _Folder("spell", "lab"),
    "town-hall": _Folder("building", "builder"),
    "traps": _Folder("trap", "builder"),
    "troops": _Folder("troop", "lab"),
    "walls": _Folder("wall", "builder"),
}

_TAGS = {("town-hall", "town-hall"): "town_hall", ("defenses", "builders-hut"): "builder_hut"}

# Level field -> stem of the file holding the prerequisite building.
_PREREQUISITES = {
    "townHallRequired": "town-hall",
    "laboratoryRequired": "laboratory",
    "heroHallLevelRequired": "hero-hall",
    "blacksmithLevelRequired": "blacksmith",
    "petHouseLevelRequired": "pet-house",
}

_COST_PREFIXES = ("build", "research", "upgrade")
_RESOURCES = {"Gold": "gold", "Elixir": "elixir", "Dark Elixir": "dark_elixir", "Gems": "gems"}
_ORES = {
    "upgradeShinyOre": "shiny_ore",
    "upgradeGlowingOre": "glowy_ore",
    "upgradeStarryOre": "starry_ore",
}
_STORAGE_FILES = {
    "gold-storage": "gold",
    "elixir-storage": "elixir",
    "dark-elixir-storage": "dark_elixir",
}
_STORAGE_CAPACITIES = {"gold": "gold", "elixir": "elixir", "darkElixir": "dark_elixir"}
_SECONDS_PER_UNIT = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}


class SkippedEntry(TypedDict):
    """Something left out of the pack; `data_id` and `level` are None for a whole file."""

    file: str
    data_id: int | None
    level: Any
    reason: str


class _Unreadable(Exception):
    """A level cannot become a pack row; the message is the reason reported in `skipped`."""


@dataclass(frozen=True)
class _SourceFile:
    relative: str
    folder: str
    stem: str
    data_id: int
    name: str
    content: Mapping[str, Any]


def _positive_int(value: Any) -> int | None:
    is_int = isinstance(value, int) and not isinstance(value, bool)
    return value if is_int and value > 0 else None


def _duration_s(value: Any) -> int | None:
    """Seconds of a `{days, hours, minutes, seconds}` object; None if unreadable or zero."""
    if not isinstance(value, Mapping):
        return None
    total = 0
    for unit, seconds in _SECONDS_PER_UNIT.items():
        amount = value.get(unit) or 0
        if amount and _positive_int(amount) is None:
            return None
        total += amount * seconds
    return total or None


def _ore_cost(level: Mapping[str, Any]) -> dict[str, int]:
    amounts = {ore: _positive_int(level.get(field)) for field, ore in _ORES.items()}
    return {ore: amount for ore, amount in amounts.items() if amount is not None}


def _upgrade(file: _SourceFile, level: Mapping[str, Any]) -> tuple[int, dict[str, int]]:
    """Duration and cost of a level, or `_Unreadable`. Only equipment may last 0 s (SPEC 5)."""
    if file.folder == _EQUIPMENT_FOLDER:
        cost = _ore_cost(level)
        if not cost:
            raise _Unreadable("missing_cost")
        return 0, cost
    prefix = next(
        (p for p in _COST_PREFIXES if f"{p}Cost" in level or f"{p}Time" in level),
        None,
    )
    if prefix is None:
        raise _Unreadable("missing_cost")
    amount = _positive_int(level.get(f"{prefix}Cost"))
    if amount is None:
        raise _Unreadable("missing_cost")
    resource_name = level.get(f"{prefix}CostResource")
    resource = _RESOURCES.get(resource_name) if isinstance(resource_name, str) else None
    if resource is None:
        raise _Unreadable("unknown_resource")
    duration = _duration_s(level.get(f"{prefix}Time"))
    if duration is None:
        raise _Unreadable("missing_time")
    return duration, {resource: amount}


def _requires(level: Mapping[str, Any], building_ids: Mapping[str, int]) -> list[dict[str, int]]:
    requires: list[dict[str, int]] = []
    for field, stem in _PREREQUISITES.items():
        value = level.get(field)
        if value is None or value == 0:
            continue
        required = _positive_int(value)
        if required is None:
            raise _Unreadable(f"invalid_prerequisite:{field}")
        data_id = building_ids.get(stem)
        if data_id is None:
            raise _Unreadable(f"missing_prerequisite:{stem}")
        requires.append({"data_id": data_id, "level": required})
    return requires


def _capacities(file: _SourceFile, level: Mapping[str, Any]) -> dict[str, int]:
    resource = _STORAGE_FILES.get(file.stem)
    if resource is not None:
        capacity = _positive_int(level.get("capacity"))
        if capacity is None:
            raise _Unreadable("missing_capacity")
        return {resource: capacity}
    declared = level.get("storageCapacity")
    if not isinstance(declared, Mapping):
        return {}
    amounts = {res: _positive_int(declared.get(key)) for key, res in _STORAGE_CAPACITIES.items()}
    return {res: amount for res, amount in amounts.items() if amount is not None}


def _max_count_by_th(content: Mapping[str, Any]) -> dict[str, int]:
    available = content.get("availablePerTownHall")
    counts: dict[str, int] = {}
    for entry in available if isinstance(available, list) else []:
        if not isinstance(entry, Mapping):
            continue
        town_hall = _positive_int(entry.get("townHallLevel"))
        count = _positive_int(entry.get("count"))
        if town_hall is not None and count is not None:
            counts[str(town_hall)] = count
    return counts


def _item(file: _SourceFile) -> dict[str, Any]:
    folder = _FOLDERS[file.folder]
    tags = [tag for key, tag in _TAGS.items() if key == (file.folder, file.stem)]
    if file.folder == _EQUIPMENT_FOLDER:
        tags.append(INSTANT_UPGRADE_TAG)
    category = file.content.get("category")
    return {
        "data_id": file.data_id,
        "name": file.name,
        "kind": folder.kind,
        "village": HOME,
        "lane": folder.lane,
        "category": category if isinstance(category, str) and category else file.folder,
        "tags": tags,
        "max_count_by_th": _max_count_by_th(file.content),
    }


def _skip(file: _SourceFile, level: Any, reason: str) -> SkippedEntry:
    return {"file": file.relative, "data_id": file.data_id, "level": level, "reason": reason}


def _note_file(relative: str, reason: str) -> SkippedEntry:
    return {"file": relative, "data_id": None, "level": None, "reason": reason}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_files(home: Path, skipped: list[SkippedEntry]) -> list[_SourceFile]:
    files: list[_SourceFile] = []
    seen: set[int] = set()
    for path in sorted(p for p in home.rglob("*.json") if p.is_file()):
        relative = path.relative_to(home.parent).as_posix()
        folder = path.relative_to(home).parts[0]
        if folder not in _FOLDERS:
            skipped.append(_note_file(relative, "unknown_folder"))
            continue
        content = _read_json(path)
        data_id = _positive_int(content.get("dataId")) if isinstance(content, dict) else None
        name = content.get("name") if isinstance(content, dict) else None
        if data_id is None or not isinstance(name, str) or not name:
            skipped.append(_note_file(relative, "not_an_item"))
        elif data_id in seen:
            skipped.append(_note_file(relative, "duplicate_data_id"))
        else:
            seen.add(data_id)
            files.append(_SourceFile(relative, folder, path.stem, data_id, name, content))
    return files


def _read_levels(
    file: _SourceFile, building_ids: Mapping[str, int], skipped: list[SkippedEntry]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Level and storage rows of one file; a storage row does not need a readable upgrade."""
    rows: list[dict[str, Any]] = []
    storage: list[dict[str, Any]] = []
    levels = file.content.get("levels")
    if not isinstance(levels, list):
        skipped.append(_skip(file, None, "missing_levels"))
        return rows, storage
    seen: set[int] = set()
    for raw in levels:
        number = _positive_int(raw.get("level")) if isinstance(raw, Mapping) else None
        if number is None or number in seen:
            shown = raw.get("level") if isinstance(raw, Mapping) else None
            skipped.append(_skip(file, shown, "invalid_level"))
            continue
        seen.add(number)
        try:
            duration, cost = _upgrade(file, raw)
            requires = _requires(raw, building_ids)
        except _Unreadable as unreadable:
            skipped.append(_skip(file, number, str(unreadable)))
        else:
            rows.append(
                {
                    "data_id": file.data_id,
                    "level": number,
                    "duration_s": duration,
                    "cost": cost,
                    "requires": requires,
                    "source": SOURCE_ID,
                }
            )
        try:
            capacities = _capacities(file, raw)
        except _Unreadable as unreadable:
            skipped.append(_skip(file, number, str(unreadable)))
            continue
        storage += [
            {
                "data_id": file.data_id,
                "level": number,
                "resource": resource,
                "capacity": capacity,
                "source": SOURCE_ID,
            }
            for resource, capacity in capacities.items()
        ]
    return rows, storage


def build_pack_from_dir(
    path: Path | str, pack_id: str, game_version: str, as_of: str, retrieved_at: str
) -> tuple[dict[str, Any], list[SkippedEntry]]:
    """Return the pack read from the dataset's `data/` folder and the list of what was left out.

    Raises `FileNotFoundError` if `path` has no `home` folder.
    """
    home = Path(path) / HOME
    if not home.is_dir():
        raise FileNotFoundError(f"no {HOME} folder in dataset directory: {path}")

    skipped: list[SkippedEntry] = []
    files = _read_files(home, skipped)
    prerequisites = set(_PREREQUISITES.values())
    building_ids = {file.stem: file.data_id for file in files if file.stem in prerequisites}

    levels: list[dict[str, Any]] = []
    storage: list[dict[str, Any]] = []
    for file in files:
        file_levels, file_storage = _read_levels(file, building_ids, skipped)
        levels += file_levels
        storage += file_storage

    pack = {
        "schema_version": SCHEMA_VERSION,
        "pack_id": pack_id,
        "game_version": game_version,
        "as_of": as_of,
        "fictional": False,
        "sources": [
            {
                "id": SOURCE_ID,
                "label": _SOURCE_LABEL,
                "url": _SOURCE_URL,
                "retrieved_at": retrieved_at,
                "license": _SOURCE_LICENSE,
            }
        ],
        "items": sorted((_item(file) for file in files), key=lambda item: item["data_id"]),
        "levels": sorted(levels, key=lambda row: (row["data_id"], row["level"])),
        "storage": sorted(storage, key=lambda row: (row["data_id"], row["level"], row["resource"])),
        "rules": {},
    }
    return pack, skipped


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m clashpilot.gamedata.builder",
        description="Build a game data pack from a local copy of clash-of-clans-data.",
    )
    parser.add_argument("--input", type=Path, required=True, help="the dataset's data/ folder")
    parser.add_argument("--output", type=Path, required=True, help="pack file to write")
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--game-version", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--retrieved-at", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        pack, skipped = build_pack_from_dir(
            arguments.input,
            arguments.pack_id,
            arguments.game_version,
            arguments.as_of,
            arguments.retrieved_at,
        )
        load_pack(pack)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    except PackValidationError as error:
        print(f"invalid pack: {error}", file=sys.stderr)
        return 1

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
    arguments.output.write_text(text, encoding="utf-8")
    for entry in skipped:
        print(
            f"skipped {entry['file']}: data_id={entry['data_id']} "
            f"level={entry['level']} reason={entry['reason']}"
        )
    print(
        f"wrote {arguments.output}: {len(pack['items'])} items, "
        f"{len(pack['levels'])} levels, {len(skipped)} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
