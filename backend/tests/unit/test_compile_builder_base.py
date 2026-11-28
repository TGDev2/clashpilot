"""Unit tests of `compile_problem(..., base=...)` beyond the E30 acceptance tests: the journal
and the running jobs stay with their own village."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.ingest.export import parse_export
from clashpilot.ingest.state import ManualState, Village, VillageSnapshot
from clashpilot.planning.compile import (
    JournalState,
    Payment,
    StockFact,
    builder_base_estimate,
    compile_problem,
    running_jobs,
)
from clashpilot.planning.model import Problem
from clashpilot.schedule.profile import ScheduleProfile

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = 1_795_852_800
EXPORTED_AT = NOW - 3600


def _pack() -> GamePack:
    path = FIXTURES / "packs" / "fictional_pack_builder_base.json"
    return load_pack(json.loads(path.read_text(encoding="utf-8")))


def _snapshot(doc: dict[str, Any] | None = None) -> VillageSnapshot:
    path = FIXTURES / "exports" / "fictional_export_builder_base.json"
    return parse_export(doc or json.loads(path.read_text(encoding="utf-8")), NOW)


def _manual() -> ManualState:
    stock = {"value": 50_000, "status": "confirmed", "as_of": EXPORTED_AT}
    names = ("gold", "elixir", "builder_gold", "builder_elixir")
    return ManualState.model_validate({"resources": dict.fromkeys(names, stock)})


def _compile(base: Village, journal: JournalState) -> Problem:
    profile = ScheduleProfile.model_validate(
        {
            "tz": "UTC",
            "weekly": [],
            "exceptions": [],
            "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
        }
    )
    return compile_problem(
        _snapshot(),
        _manual(),
        _pack(),
        profile,
        NOW,
        90,
        journal=journal,
        base=base,
    )


def test_journal_stock_and_payments_stay_in_their_village() -> None:
    journal = JournalState(
        stock=(StockFact("gold", NOW - 60, 7), StockFact("builder_gold", NOW - 60, 9)),
        payments=(Payment(NOW - 30, {"gold": 5, "builder_gold": 4}),),
    )

    home = _compile("home", journal).resources
    builder = _compile("builder", journal).resources

    assert home.initial["gold"] == 2
    assert "builder_gold" not in home.initial
    assert builder.initial["builder_gold"] == 5
    assert "gold" not in builder.initial


def test_running_jobs_of_each_base() -> None:
    builder = running_jobs(_snapshot(), _pack(), NOW, "builder")
    home = running_jobs(_snapshot(), _pack(), NOW)

    assert [(ref.task_id, ref.lane) for ref in builder] == [
        ("9400004:0:2", "builder"),
        ("9400005:0:2", "builder"),
        ("9400006:0:2", "lab"),
        ("9400007:0:2", "builder"),
    ]
    assert [ref.task_id for ref in home] == ["9300004:0:2"]


def test_builder_base_estimate_skips_the_extra_worker() -> None:
    path = FIXTURES / "exports" / "fictional_export_builder_base.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["buildings2"][3]["extra"] = True

    assert builder_base_estimate(_snapshot(doc), _pack()) == 2
