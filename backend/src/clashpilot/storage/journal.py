"""The journal of each village (migration `0007_journal.sql`)."""

from __future__ import annotations

import json
import sqlite3

from clashpilot.planning.journal import JournalEntry
from clashpilot.planning.service import canonical_json


def add_entry(
    connection: sqlite3.Connection,
    village_id: str,
    entry: JournalEntry,
    plan_id: int | None,
    created_at: int,
) -> int:
    """Store one event; return its `entry_id`."""
    cursor = connection.execute(
        "INSERT INTO journal (village_id, type, at, task_id, payload, plan_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            village_id,
            entry.type,
            entry.at,
            entry.task_id,
            canonical_json(dict(entry.payload)),
            plan_id,
            created_at,
        ),
    )
    entry_id = cursor.lastrowid
    assert entry_id is not None
    return entry_id


def entries(connection: sqlite3.Connection, village_id: str) -> tuple[JournalEntry, ...]:
    """Every event of the village, oldest first."""
    rows = connection.execute(
        "SELECT entry_id, type, at, task_id, payload, plan_id FROM journal "
        "WHERE village_id = ? ORDER BY at, entry_id",
        (village_id,),
    ).fetchall()
    return tuple(
        JournalEntry(
            entry_id=row["entry_id"],
            type=row["type"],
            at=row["at"],
            task_id=row["task_id"],
            payload=json.loads(row["payload"]),
            plan_id=row["plan_id"],
        )
        for row in rows
    )
