"""Stored plans (migration `0006_plans.sql`) and the inputs a plan is computed from."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.export import parse_export
from clashpilot.ingest.state import ManualState, VillageSnapshot
from clashpilot.planning.service import PlanOutcome, canonical_json, trace_json
from clashpilot.schedule.profile import ScheduleProfile
from clashpilot.storage.packs import load_effective_pack


def village_exists(connection: sqlite3.Connection, village_id: str) -> bool:
    found = connection.execute(
        "SELECT 1 FROM villages WHERE village_id = ?", (village_id,)
    ).fetchone()
    return found is not None


def latest_snapshot(connection: sqlite3.Connection, village_id: str) -> VillageSnapshot | None:
    row = connection.execute(
        "SELECT raw_json, imported_at FROM village_snapshots "
        "WHERE village_id = ? ORDER BY exported_at DESC, snapshot_id DESC LIMIT 1",
        (village_id,),
    ).fetchone()
    return parse_export(row["raw_json"], row["imported_at"]) if row is not None else None


def manual_state(connection: sqlite3.Connection, village_id: str) -> ManualState:
    row = connection.execute(
        "SELECT body_json FROM village_manual WHERE village_id = ?", (village_id,)
    ).fetchone()
    return ManualState.model_validate_json(row["body_json"]) if row is not None else ManualState()


def schedule_profile(connection: sqlite3.Connection, village_id: str) -> ScheduleProfile | None:
    row = connection.execute(
        "SELECT body_json FROM schedule_profile WHERE village_id = ?", (village_id,)
    ).fetchone()
    return ScheduleProfile.model_validate_json(row["body_json"]) if row is not None else None


def active_pack(connection: sqlite3.Connection) -> GamePack | None:
    """The active pack with its manual corrections applied, or None."""
    row = connection.execute("SELECT pack_id FROM gamedata_packs WHERE active = 1").fetchone()
    return load_effective_pack(connection, row["pack_id"]) if row is not None else None


def save_plan(
    connection: sqlite3.Connection, village_id: str, outcome: PlanOutcome, created_at: int
) -> int:
    """Store a plan the simulator found valid; return its `plan_id`."""
    body = outcome.body
    cursor = connection.execute(
        "INSERT INTO plans (village_id, created_at, now, input_hash, engine, profile, pack_id, "
        "proof, assignments, trace, warnings, params, body, explanations, reliability) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            village_id,
            created_at,
            outcome.problem.now,
            outcome.input_hash,
            outcome.params.engine,
            outcome.params.profile,
            body["pack"]["pack_id"],
            canonical_json(body["proof"]),
            canonical_json([asdict(a) for a in outcome.plan.assignments]),
            canonical_json(trace_json(outcome.plan.trace)),
            canonical_json(list(outcome.warnings)),
            canonical_json(asdict(outcome.params)),
            canonical_json(body),
            canonical_json(outcome.explanations),
            canonical_json(outcome.reliability),
        ),
    )
    plan_id = cursor.lastrowid
    assert plan_id is not None
    return plan_id


@dataclass(frozen=True)
class StoredPlan:
    plan_id: int
    body: dict[str, Any]  # with `plan_id`
    explanations: dict[str, dict[str, Any]]
    reliability: dict[str, Any]


def latest_plan(connection: sqlite3.Connection, village_id: str) -> StoredPlan | None:
    row = connection.execute(
        "SELECT plan_id, body, explanations, reliability FROM plans "
        "WHERE village_id = ? ORDER BY plan_id DESC LIMIT 1",
        (village_id,),
    ).fetchone()
    if row is None:
        return None
    body = {"plan_id": row["plan_id"], **json.loads(row["body"])}
    return StoredPlan(
        plan_id=row["plan_id"],
        body=body,
        explanations=json.loads(row["explanations"]),
        reliability=json.loads(row["reliability"]),
    )
