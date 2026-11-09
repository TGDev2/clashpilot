"""Parser of the in-game village export (Réglages > Plus de réglages > Export des données).

The file carries no version and its schema is undocumented: it is inferred from public 2026
samples. Each known shape is an adapter of `ADAPTERS`, so that a new format is one more entry and
the routes never change. Every field may be missing: what cannot be read is reported in
`warnings`, and only an input without a tag or a timestamp is refused.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from clashpilot.ingest.state import RunningJob, SnapshotItem, Village, VillageSnapshot

Detect = Callable[[Mapping[str, Any]], bool]
Parse = Callable[[Mapping[str, Any], int], VillageSnapshot]

_HOME_SECTIONS = (
    "helpers",
    "buildings",
    "traps",
    "decos",
    "obstacles",
    "units",
    "siege_machines",
    "heroes",
    "spells",
    "pets",
    "equipment",
    "house_parts",
    "skins",
    "sceneries",
    "guardians",
)
_BUILDER_SECTIONS = (
    "buildings2",
    "traps2",
    "decos2",
    "obstacles2",
    "units2",
    "heroes2",
    "skins2",
    "sceneries2",
)
_VILLAGE_OF_SECTION: dict[str, Village] = {
    **dict.fromkeys(_HOME_SECTIONS, "home"),
    **dict.fromkeys(_BUILDER_SECTIONS, "builder"),
}
_KNOWN_KEYS = frozenset({"tag", "timestamp", "boosts", *_VILLAGE_OF_SECTION})
_HARMLESS_BOOSTS = frozenset({"clocktower_cooldown"})

# Epochs are stored in SQLite INTEGER columns.
_MAX_TIMESTAMP = 2**63 - 1


class ImportFormatError(ValueError):
    """The input is not a village export; the message, in French, is shown to the player."""


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _read_tag(data: Mapping[str, Any]) -> str:
    tag = data.get("tag")
    normalized = tag.strip().lstrip("#").upper() if isinstance(tag, str) else ""
    if not normalized:
        raise ImportFormatError("Le tag du village est invalide dans cet export.")
    return normalized


def _read_timestamp(data: Mapping[str, Any]) -> int:
    timestamp = _integer(data.get("timestamp"))
    if timestamp is None or not 0 < timestamp <= _MAX_TIMESTAMP:
        raise ImportFormatError("La date (timestamp) de cet export est invalide.")
    return timestamp


def _read_section(
    section: str,
    entries: list[Any],
    exported_at: int,
    items: list[SnapshotItem],
    running: list[RunningJob],
    warnings: list[str],
) -> None:
    village = _VILLAGE_OF_SECTION[section]
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue  # Cosmetic sections may list bare ids: nothing to plan there.
        data_id = _integer(entry.get("data"))
        if data_id is None:
            warnings.append(f"Une entrée sans identifiant est ignorée dans la section {section}.")
            continue
        # `lvl` is the level reached and an upgrade in progress targets lvl + 1. This semantic
        # is deduced by the community, the game does not document it.
        level = _integer(entry.get("lvl"))
        has_modules = "types" in entry
        if has_modules:
            warnings.append(
                f"L'objet {data_id} est composé de modules : il est conservé tel quel, sans "
                "planification."
            )
        elif level is None:
            warnings.append(f"L'objet {data_id} n'a pas de niveau dans la section {section}.")
        count = _integer(entry.get("cnt"))
        items.append(
            SnapshotItem(
                data_id=data_id,
                level=level,
                count=count if count is not None and count > 0 else 1,
                section=section,
                village=village,
                raw=entry if has_modules else None,
            )
        )
        timer = _integer(entry.get("timer"))
        if timer is not None and level is not None:
            # A timer of 0 is still a job in progress: it ends exactly at the export time.
            running.append(
                RunningJob(
                    data_id=data_id,
                    finish_at=exported_at + timer,
                    to_level=level + 1,
                    section=section,
                    village=village,
                    extra=entry.get("extra") is True,
                    helper=entry.get("helper_recurrent") is True,
                )
            )


def _detect_current(data: Mapping[str, Any]) -> bool:
    return "tag" in data and "timestamp" in data


def _parse_current(data: Mapping[str, Any], imported_at: int) -> VillageSnapshot:
    tag = _read_tag(data)
    exported_at = _read_timestamp(data)
    items: list[SnapshotItem] = []
    running: list[RunningJob] = []
    warnings: list[str] = []
    for section in _VILLAGE_OF_SECTION:
        entries = data.get(section)
        if isinstance(entries, list):
            _read_section(section, entries, exported_at, items, running, warnings)
    boosts = data.get("boosts")
    if isinstance(boosts, Mapping) and set(boosts) - _HARMLESS_BOOSTS:
        # A potion speeds up the countdown, so the remaining time read here may not be the real one.
        warnings.append("Un boost est actif : les timers peuvent être faussés.")
    return VillageSnapshot(
        tag=tag,
        exported_at=exported_at,
        imported_at=imported_at,
        items=tuple(items),
        running=tuple(running),
        unknown_sections={key: value for key, value in data.items() if key not in _KNOWN_KEYS},
        warnings=tuple(warnings),
    )


# Name, deduced from the key signature of the format -> (detect, parse).
ADAPTERS: dict[str, tuple[Detect, Parse]] = {
    "keys-tag-timestamp-buildings2": (_detect_current, _parse_current),
}


def _decode(raw: str | Mapping[str, Any]) -> Mapping[str, Any]:
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise ImportFormatError("Le contenu collé n'est pas du JSON valide.") from error
    if not isinstance(data, Mapping):
        raise ImportFormatError("L'export doit être un objet JSON.")
    return data


def parse_export(raw: str | Mapping[str, Any], imported_at: int) -> VillageSnapshot:
    """Read an export given as a JSON text or as the decoded object.

    Raises `ImportFormatError` when the input is not an export.
    """
    data = _decode(raw)
    # Newest adapter first: the current one reads any object with a tag and a timestamp, so an
    # adapter added later has to be asked before it.
    for name, (detect, parse) in reversed(ADAPTERS.items()):
        if detect(data):
            return replace(parse(data, imported_at), format_version=name)
    raise ImportFormatError("Le tag ou le timestamp est manquant dans cet export.")
