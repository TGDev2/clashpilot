"""Plans per village (E32): the stored `base` of a plan, its hash and the journal of each base."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from clashpilot.ingest.state import Village
from clashpilot.planning.journal import JournalEntry
from clashpilot.planning.service import (
    DEFAULT_OBJECTIVE,
    PlanParams,
    hash_options,
    input_hash,
)
from clashpilot.storage import journal as journal_store
from clashpilot.storage import plans as store
from clashpilot.storage.db import connect, migrate
from support.problems import make_problem

PARAMS = {"horizon_days": 90, "engine": "rollout", "profile": "repro"}


def test_params_stored_before_e32_belong_to_the_main_village() -> None:
    assert PlanParams.from_json(PARAMS).base == "home"
    assert PlanParams.from_json({**PARAMS, "base": "builder"}).base == "builder"
    with pytest.raises(ValueError):
        PlanParams.from_json({**PARAMS, "base": "moon"})


def test_the_hash_changes_with_the_builder_base_only() -> None:
    problem = make_problem()

    def digest(params: PlanParams) -> str:
        return input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0, **hash_options(params))

    home = PlanParams.from_json(PARAMS)
    assert digest(home) == input_hash(problem, DEFAULT_OBJECTIVE, "rollout", "repro", 0)
    assert digest(PlanParams.from_json({**PARAMS, "base": "builder"})) != digest(home)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path)
    migrate(connection)
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        yield connection
    finally:
        connection.close()


def add_plan(connection: sqlite3.Connection, base: str) -> int:
    cursor = connection.execute(
        "INSERT INTO plans (village_id, created_at, now, input_hash, engine, profile, pack_id, "
        "proof, assignments, trace, warnings, params, body, explanations, reliability, base) "
        "VALUES ('V', 1, 1, 'h', 'rollout', 'repro', 'p', '{}', '[]', '[]', '[]', ?, '{}', "
        "'{}', '{}', ?)",
        (
            f'{{"horizon_days": 90, "engine": "rollout", "profile": "repro", "base": "{base}"}}',
            base,
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def add_event(connection: sqlite3.Connection, at: int, plan_id: int | None) -> None:
    entry = JournalEntry(entry_id=0, type="priority_changed", at=at, task_id=None, payload={})
    journal_store.add_entry(connection, "V", entry, plan_id, at)


def test_the_journal_of_a_base_holds_the_events_of_its_plans(
    connection: sqlite3.Connection,
) -> None:
    home, builder = add_plan(connection, "home"), add_plan(connection, "builder")
    add_event(connection, 1, home)
    add_event(connection, 2, builder)
    add_event(connection, 3, None)  # its plan is gone: the main village's, as before E32

    def ats(base: Village | None) -> list[int]:
        return [entry.at for entry in journal_store.entries(connection, "V", base)]

    assert ats(None) == [1, 2, 3]
    assert ats("home") == [1, 3]
    assert ats("builder") == [2]


def test_the_latest_plan_and_the_base_of_a_plan_are_read_per_base(
    connection: sqlite3.Connection,
) -> None:
    first_home = add_plan(connection, "home")
    builder = add_plan(connection, "builder")

    home_plan = store.latest_plan(connection, "V")
    builder_plan = store.latest_plan(connection, "V", "builder")
    assert home_plan is not None and home_plan.plan_id == first_home
    assert builder_plan is not None and builder_plan.plan_id == builder
    assert builder_plan.params.base == "builder"
    assert store.plan_base(connection, "V", builder) == "builder"
    assert store.plan_base(connection, "V", first_home) == "home"
    assert store.plan_base(connection, "V", builder + 1) is None
    assert store.plan_base(connection, "OTHER", builder) is None
