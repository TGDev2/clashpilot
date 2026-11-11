"""The stock history of the manual input (migration `0009_manual_history.sql`)."""

from __future__ import annotations

import sqlite3

from clashpilot.ingest.state import ManualState


def add_manual_history(
    connection: sqlite3.Connection, village_id: str, manual: ManualState, recorded_at: int
) -> None:
    """One row per typed-in resource of `manual`, whatever was stored before."""
    connection.executemany(
        "INSERT INTO manual_history (village_id, resource, value, as_of, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (village_id, name, typed.value, typed.as_of, recorded_at)
            for name, typed in sorted(manual.resources.items())
        ],
    )


def manual_history(
    connection: sqlite3.Connection, village_id: str
) -> dict[str, list[tuple[int, int]]]:
    """Resource -> `(as_of, value)` of every input, by `as_of` then by input order."""
    rows = connection.execute(
        "SELECT resource, value, as_of FROM manual_history WHERE village_id = ? "
        "ORDER BY resource, as_of, history_id",
        (village_id,),
    ).fetchall()
    history: dict[str, list[tuple[int, int]]] = {}
    for row in rows:
        history.setdefault(row["resource"], []).append((row["as_of"], row["value"]))
    return history
