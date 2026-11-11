"""Unit tests of the journal replay (`planning/journal.py`) and of `compile_problem` with it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.ingest.export import parse_export
from clashpilot.ingest.state import ManualState, VillageSnapshot
from clashpilot.planning.compile import (
    JobFact,
    JournalState,
    Payment,
    RunningRef,
    StockFact,
    compile_problem,
    running_jobs,
)
from clashpilot.planning.journal import JournalEntry, KnownJob, objective_of, replay
from clashpilot.planning.objective import Objective
from clashpilot.planning.service import PlanInputs, compile_inputs
from clashpilot.schedule.profile import ScheduleProfile

HOUR = 3600
NOW = 1_794_211_200  # 2026-11-09T08:00Z
EXPORTED_AT = NOW - HOUR
ITEMS_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_items.json"
)
TOWN_HALL, HUT, LAB, CANNON, STORAGE, MORTAR, SPELL = (
    9000001,
    9000002,
    9000003,
    9000004,
    9000005,
    9000008,
    9000009,
)
SHORT, LONG = f"{MORTAR}:0:2", f"{MORTAR}:1:2"


def pack() -> GamePack:
    return load_pack(json.loads(ITEMS_PACK.read_text(encoding="utf-8")))


def export(*, storage: int | None = None) -> VillageSnapshot:
    buildings: list[dict[str, Any]] = [
        {"data": TOWN_HALL, "lvl": 2},
        {"data": HUT, "lvl": 1},
        {"data": HUT, "lvl": 1},
        {"data": LAB, "lvl": 1},
        {"data": CANNON, "lvl": 2},
        {"data": MORTAR, "lvl": 1, "timer": 36000 + HOUR},
        {"data": MORTAR, "lvl": 1, "timer": 72000 + HOUR},
    ]
    if storage is not None:
        buildings.append({"data": STORAGE, "lvl": storage})
    document = {
        "tag": "#UNIT11",
        "timestamp": EXPORTED_AT,
        "buildings": buildings,
        "spells": [{"data": SPELL, "lvl": 1, "timer": 36000 + HOUR}],
    }
    return parse_export(document, EXPORTED_AT)


def manual(gold: int = 100_000, as_of: int = EXPORTED_AT) -> ManualState:
    rate = {"low": 0, "expected": 1000, "high": 1000}
    names = ("gold", "elixir", "dark_elixir")
    return ManualState.model_validate(
        {
            "resources": {
                name: {
                    "value": gold if name == "gold" else 50_000,
                    "status": "confirmed",
                    "as_of": as_of,
                }
                for name in names
            },
            "builders_total": 3,
            "farm_rate": dict.fromkeys(names, rate),
        }
    )


def profile() -> ScheduleProfile:
    return ScheduleProfile.model_validate(
        {
            "tz": "Europe/Paris",
            "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
            "exceptions": [],
            "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
        }
    )


def started(
    entry_id: int,
    name: str,
    at: int,
    *,
    slot: int = 0,
    end: int | None = None,
    cost: dict[str, int] | None = None,
    lane: str = "builder",
) -> JournalEntry:
    payload = {"lane": lane, "lane_slot": slot, "end": end or at + 600, "cost": cost or {}}
    return JournalEntry(entry_id, "task_started", at, name, payload)


def item(entry_id: int, at: int, payload: dict[str, Any], task: str | None = None) -> JournalEntry:
    return JournalEntry(entry_id, "item_used", at, task, payload)


# --------------------------------------------------------------------------- replay


def test_a_start_after_the_export_is_a_journal_job_and_a_payment() -> None:
    state, known = replay(
        [started(1, f"{CANNON}:0:3", NOW, slot=2, end=NOW + 1800, cost={"gold": 2000})],
        [],
        EXPORTED_AT,
    )

    assert known[f"{CANNON}:0:3"] == KnownJob(
        f"{CANNON}:0:3", "builder", NOW + 1800, NOW, 2, journal=True
    )
    assert state.jobs == (JobFact(f"{CANNON}:0:3", NOW + 1800, NOW, 2),)
    assert state.payments == (Payment(NOW, {"gold": 2000}),)


def test_a_start_before_the_export_moves_no_job_but_its_cost_was_paid() -> None:
    state, known = replay(
        [started(1, f"{CANNON}:0:3", EXPORTED_AT - 60, cost={"gold": 2000})], [], EXPORTED_AT
    )

    assert known == {}
    assert state.jobs == ()
    assert state.payments == (Payment(EXPORTED_AT - 60, {"gold": 2000}),)


def test_a_potion_speeds_up_the_jobs_running_on_its_lane_only() -> None:
    running = [
        RunningRef("a:0:2", "builder", NOW + 36000),
        RunningRef("b:0:2", "builder", NOW + 72000),
        RunningRef("c:0:2", "lab", NOW + 36000),
    ]
    later = started(1, "d:0:2", NOW + 60, end=NOW + 9000)
    potion = {
        "item": "builder_potion",
        "effect": "potion",
        "lane": "builder",
        "duration_multiplier": 10,
        "duration_s": 3600,
    }

    _, known = replay([item(2, NOW, potion), later], running, EXPORTED_AT)

    ends = {name: job.end for name, job in known.items()}
    assert ends == {
        "a:0:2": NOW + 3600,
        "b:0:2": NOW + 39600,
        "c:0:2": NOW + 36000,
        "d:0:2": NOW + 9000,  # started after the potion: untouched
    }


def test_a_book_or_a_finish_ends_a_running_job_at_its_date_never_later() -> None:
    running = [RunningRef("a:0:2", "builder", NOW + 5000), RunningRef("b:0:2", "builder", NOW + 50)]
    book = item(1, NOW + 10, {"item": "book_of_building", "effect": "book"}, "a:0:2")
    late = JournalEntry(2, "task_finished", NOW + 100, "b:0:2", {})

    state, known = replay([book, late], running, EXPORTED_AT)

    assert known["a:0:2"].end == NOW + 10
    assert known["b:0:2"].end == NOW + 50
    assert state.jobs == (JobFact("a:0:2", NOW + 10),)


def test_runes_and_corrections_are_stock_observations() -> None:
    rune = item(1, NOW, {"item": "rune_of_gold", "effect": "rune", "resource": "gold", "value": 9})
    fix = JournalEntry(2, "resources_corrected", NOW + 5, None, {"resources": {"elixir": 7}})

    state, _ = replay([fix, rune], [], EXPORTED_AT)

    assert state.stock == (StockFact("gold", NOW, 9), StockFact("elixir", NOW + 5, 7))


def test_the_objective_is_the_one_of_the_latest_priority() -> None:
    old = JournalEntry(1, "priority_changed", NOW, None, {"objective": {"value": 1.0, "idle": 1}})
    new = JournalEntry(2, "priority_changed", NOW + 9, None, {"objective": {"night": 0.5}})

    assert objective_of([]) is None
    assert objective_of([new, old]) == Objective(night=0.5)


# --------------------------------------------------------------------------- compile


def test_an_empty_journal_compiles_as_without_one() -> None:
    inputs = PlanInputs(export(), manual(), pack(), profile())

    problem, known = compile_inputs(inputs, NOW, 7)

    assert problem == compile_problem(export(), manual(), pack(), profile(), NOW, 7)
    assert set(known) == {SHORT, LONG, f"{SPELL}:0:2"}


def test_a_job_ended_by_the_journal_keeps_the_ranks_of_the_export() -> None:
    # Ending the first mortar would rank the second one first if the export were ranked again.
    snapshot = export()
    assert [ref.task_id for ref in running_jobs(snapshot, pack(), NOW)][:2] == [SHORT, LONG]
    journal = JournalState(jobs=(JobFact(SHORT, NOW),))

    problem = compile_problem(snapshot, manual(), pack(), profile(), NOW, 7, journal=journal)

    assert [job.task_id for job in problem.frozen if job.lane == "builder"] == [LONG]
    ids = {task.id for task in problem.tasks}
    assert f"{MORTAR}:0:3" in ids and SHORT not in ids


def test_a_journal_job_keeps_its_id_dates_and_slot() -> None:
    fact = JobFact(f"{CANNON}:0:3", NOW + 1800, NOW - 60, 2)

    problem = compile_problem(
        export(), manual(), pack(), profile(), NOW, 7, journal=JournalState(jobs=(fact,))
    )

    jobs = {job.task_id: job for job in problem.frozen}
    cannon = jobs[f"{CANNON}:0:3"]
    assert (cannon.start, cannon.end, cannon.lane_slot) == (NOW - 60, NOW + 1800, 2)
    # The mortars of the export take the other slots.
    assert {jobs[SHORT].lane_slot, jobs[LONG].lane_slot} == {0, 1}
    assert f"{CANNON}:0:3" not in {task.id for task in problem.tasks}
    assert f"{CANNON}:0:3" in next(t for t in problem.tasks if t.id == f"{CANNON}:0:4").requires


def test_the_stock_pays_the_journal_starts_after_the_latest_observation() -> None:
    # 1000 gold at EXPORTED_AT, 1000 per hour; 1500 paid one hour later; 300 paid before.
    journal = JournalState(
        payments=(Payment(EXPORTED_AT - 10, {"gold": 300}), Payment(NOW, {"gold": 1500}))
    )

    problem = compile_problem(
        export(), manual(gold=1000), pack(), profile(), NOW + HOUR, 7, journal=journal
    )

    assert problem.resources.initial["gold"] == 1000 + 1000 - 1500 + 1000


def test_the_cap_holds_between_payments_and_a_newer_observation_wins() -> None:
    # A storage of 3000: 2500 grows to 3000 within the hour, 1000 is paid, then it refills.
    capped = JournalState(payments=(Payment(NOW, {"gold": 1000}),))
    problem = compile_problem(
        export(storage=1), manual(gold=2500), pack(), profile(), NOW + HOUR, 7, journal=capped
    )
    assert problem.resources.cap["gold"] == 3000
    assert problem.resources.initial["gold"] == 3000

    observed = JournalState(
        stock=(StockFact("gold", NOW, 100),), payments=(Payment(NOW, {"gold": 1000}),)
    )
    problem = compile_problem(
        export(storage=1), manual(gold=2500), pack(), profile(), NOW + HOUR, 7, journal=observed
    )
    assert problem.resources.initial["gold"] == 1100
