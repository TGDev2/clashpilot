"""The what-if scenarios of each village (migration `0008_scenarios.sql`)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from clashpilot.planning.service import canonical_json


@dataclass(frozen=True)
class StoredScenario:
    scenario_id: int
    name: str
    overlays: list[dict[str, Any]]
    body: dict[str, Any]


def add_scenario(
    connection: sqlite3.Connection,
    village_id: str,
    plan_id: int,
    name: str,
    overlays: Sequence[Mapping[str, Any]],
    body: Mapping[str, Any],
    created_at: int,
) -> int:
    """Store one computed scenario; return its `scenario_id`."""
    cursor = connection.execute(
        "INSERT INTO scenarios (village_id, plan_id, name, overlays, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            village_id,
            plan_id,
            name,
            canonical_json([dict(overlay) for overlay in overlays]),
            canonical_json(dict(body)),
            created_at,
        ),
    )
    scenario_id = cursor.lastrowid
    assert scenario_id is not None
    return scenario_id


def _stored(row: sqlite3.Row) -> StoredScenario:
    return StoredScenario(
        scenario_id=row["scenario_id"],
        name=row["name"],
        overlays=json.loads(row["overlays"]),
        body=json.loads(row["body"]),
    )


def scenarios(connection: sqlite3.Connection, village_id: str) -> list[StoredScenario]:
    """Every scenario of the village, oldest first."""
    rows = connection.execute(
        "SELECT scenario_id, name, overlays, body FROM scenarios "
        "WHERE village_id = ? ORDER BY scenario_id",
        (village_id,),
    ).fetchall()
    return [_stored(row) for row in rows]


def scenario(
    connection: sqlite3.Connection, village_id: str, scenario_id: int
) -> StoredScenario | None:
    row = connection.execute(
        "SELECT scenario_id, name, overlays, body FROM scenarios "
        "WHERE village_id = ? AND scenario_id = ?",
        (village_id, scenario_id),
    ).fetchone()
    return _stored(row) if row is not None else None


def delete_scenario(connection: sqlite3.Connection, village_id: str, scenario_id: int) -> bool:
    """Delete one scenario of the village (the scenarios table only); False if it is unknown."""
    cursor = connection.execute(
        "DELETE FROM scenarios WHERE village_id = ? AND scenario_id = ?",
        (village_id, scenario_id),
    )
    return cursor.rowcount > 0
