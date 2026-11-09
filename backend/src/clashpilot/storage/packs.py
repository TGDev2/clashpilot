"""Stored packs and their manual corrections.

The content of a pack is immutable once imported; corrections live in `pack_overrides` and are
applied on read. `load_effective_pack` is the only way the planner reads a pack, so that a
correction of the community dataset (SPEC section 2, risk 4) always reaches it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from clashpilot.gamedata.pack import GamePack, load_pack

MANUAL_SOURCE_ID = "manual"
_MANUAL_SOURCE_LABEL = "Saisie manuelle"


class PackNotFoundError(LookupError):
    """No stored pack has this `pack_id`."""


@dataclass(frozen=True)
class Override:
    """A correction of one level row; a None field keeps the value of the pack."""

    data_id: int
    level: int
    duration_s: int | None
    cost: Mapping[str, int] | None
    note: str | None
    updated_at: int


def stored_content(connection: sqlite3.Connection, pack_id: str) -> dict[str, Any]:
    """The pack exactly as imported, without any correction."""
    row = connection.execute(
        "SELECT content FROM gamedata_packs WHERE pack_id = ?", (pack_id,)
    ).fetchone()
    if row is None:
        raise PackNotFoundError(pack_id)
    content: dict[str, Any] = json.loads(row["content"])
    return content


def overrides_of(connection: sqlite3.Connection, pack_id: str) -> dict[tuple[int, int], Override]:
    rows = connection.execute(
        "SELECT data_id, level, duration_s, cost, note, updated_at "
        "FROM pack_overrides WHERE pack_id = ?",
        (pack_id,),
    ).fetchall()
    return {
        (row["data_id"], row["level"]): Override(
            data_id=row["data_id"],
            level=row["level"],
            duration_s=row["duration_s"],
            cost=json.loads(row["cost"]) if row["cost"] is not None else None,
            note=row["note"],
            updated_at=row["updated_at"],
        )
        for row in rows
    }


def save_override(
    connection: sqlite3.Connection,
    pack_id: str,
    data_id: int,
    level: int,
    *,
    duration_s: int | None,
    cost: Mapping[str, int] | None,
    note: str | None,
    now: int,
) -> None:
    """Record a correction; a field left to None keeps its previous correction."""
    connection.execute(
        "INSERT INTO pack_overrides (pack_id, data_id, level, duration_s, cost, note, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (pack_id, data_id, level) DO UPDATE SET "
        "duration_s = COALESCE(excluded.duration_s, duration_s), "
        "cost = COALESCE(excluded.cost, cost), "
        "note = COALESCE(excluded.note, note), "
        "updated_at = excluded.updated_at",
        (
            pack_id,
            data_id,
            level,
            duration_s,
            json.dumps(dict(cost), sort_keys=True) if cost is not None else None,
            note,
            now,
        ),
    )


def _corrected(row: Mapping[str, Any], override: Override) -> dict[str, Any]:
    corrected = {**row, "source": MANUAL_SOURCE_ID}
    if override.duration_s is not None:
        corrected["duration_s"] = override.duration_s
    if override.cost is not None:
        corrected["cost"] = dict(override.cost)
    return corrected


def apply_overrides(
    content: Mapping[str, Any], overrides: Mapping[tuple[int, int], Override]
) -> dict[str, Any]:
    """A copy of the pack with its corrections, sourced to an added `manual` source."""
    if not overrides:
        return dict(content)
    last_update = max(override.updated_at for override in overrides.values())
    manual_source = {
        "id": MANUAL_SOURCE_ID,
        "label": _MANUAL_SOURCE_LABEL,
        "retrieved_at": datetime.fromtimestamp(last_update, UTC).date().isoformat(),
    }
    levels = [
        _corrected(row, overrides[key])
        if (key := (row["data_id"], row["level"])) in overrides
        else row
        for row in content["levels"]
    ]
    sources = [s for s in content["sources"] if s["id"] != MANUAL_SOURCE_ID]
    return {**content, "sources": [*sources, manual_source], "levels": levels}


def load_effective_pack(connection: sqlite3.Connection, pack_id: str) -> GamePack:
    """The pack with its manual corrections applied.

    Raises `PackNotFoundError` for an unknown pack and `PackValidationError` if a correction makes
    the pack invalid.
    """
    content = stored_content(connection, pack_id)
    return load_pack(apply_overrides(content, overrides_of(connection, pack_id)))
