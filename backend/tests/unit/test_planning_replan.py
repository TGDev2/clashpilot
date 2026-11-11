"""Unit tests of the replanning (`planning/replan.py`) and of its routes beyond the acceptance."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.gamedata.pack import load_pack
from clashpilot.ingest.export import parse_export
from clashpilot.ingest.state import ManualState
from clashpilot.planning.journal import JournalEntry
from clashpilot.planning.messages import duration_label
from clashpilot.planning.replan import (
    Event,
    PreviousPlan,
    ReplanError,
    changes,
    pair_rows,
    replan,
)
from clashpilot.planning.service import PlanInputs, PlanParams, create_plan
from clashpilot.schedule.profile import ScheduleProfile
from clashpilot.settings import Settings
from support.problems import TEST_CONFIG

HOUR = 3600
NOW = 1_794_211_200  # 2026-11-09T08:00Z, 09:00 in Paris
ITEMS_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_items.json"
)
TOWN_HALL, HUT, LAB, CANNON, HERO, TROOP = 9000001, 9000002, 9000003, 9000004, 9000006, 9000007
TOWN_HALL_TO_3, CANNON_TO_3, TROOP_TO_2 = f"{TOWN_HALL}:0:3", f"{CANNON}:0:3", f"{TROOP}:0:2"
OVERRIDES = dataclasses.asdict(TEST_CONFIG)
PARAMS = PlanParams(horizon_days=7, engine="rollout", profile="repro")


def pack_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    return document


def export_document() -> dict[str, Any]:
    return {
        "tag": "#UNIT11",
        "timestamp": NOW - HOUR,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 2},
            {"data": HUT, "lvl": 1},
            {"data": HUT, "lvl": 1},
            {"data": LAB, "lvl": 1},
            {"data": CANNON, "lvl": 2},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
        "heroes": [{"data": HERO, "lvl": 1, "timer": 5000}],
    }


def manual_document(builders: int = 3) -> dict[str, Any]:
    rate = {"low": 500, "expected": 1000, "high": 1500}
    stock = {"status": "confirmed", "as_of": NOW - HOUR}
    return {
        "resources": {
            "gold": {"value": 100_000, **stock},
            "elixir": {"value": 50_000, **stock},
            "dark_elixir": {"value": 5_000, **stock},
        },
        "builders_total": builders,
        "farm_rate": {"gold": rate, "elixir": rate, "dark_elixir": rate},
    }


def profile_document() -> dict[str, Any]:
    return {
        "tz": "Europe/Paris",
        "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def inputs(*journal: JournalEntry, builders: int = 3) -> PlanInputs:
    return PlanInputs(
        snapshot=parse_export(export_document(), NOW - HOUR),
        manual=ManualState.model_validate(manual_document(builders)),
        pack=load_pack(pack_document()),
        schedule_profile=ScheduleProfile.model_validate(profile_document()),
        journal=journal,
    )


def previous_of(given: PlanInputs) -> PreviousPlan:
    outcome = create_plan(given, PARAMS, OVERRIDES, NOW)
    return PreviousPlan(
        plan_id=1,
        body={"plan_id": 1, **outcome.body},
        params=PARAMS,
        input_hash=outcome.input_hash,
        explanations=outcome.explanations,
        same_snapshot=True,
    )


def refused(event: Event, status: int, code: str, given: PlanInputs | None = None) -> None:
    given = given if given is not None else inputs()
    with pytest.raises(ReplanError) as raised:
        replan(given, previous_of(given), event, NOW, OVERRIDES)
    assert (raised.value.status, raised.value.code) == (status, code)
    assert raised.value.message


# --------------------------------------------------------------------------- pairing


def row(task_id: str, start: int, *, slot: int = 0, status: str = "planned") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "start": start,
        "end": start + 100,
        "lane": "builder",
        "lane_slot": slot,
        "status": status,
    }


def test_rows_pair_by_id_first_then_by_rank_within_their_class() -> None:
    old = [row("7:0:2", 0), row("7:1:2", 10), row("8:0:1", 5)]
    new = [row("7:1:2", 10), row("7:2:2", 20)]

    pairs = [
        (a["task_id"] if a else None, b["task_id"] if b else None) for a, b in pair_rows(old, new)
    ]

    assert pairs == [("7:1:2", "7:1:2"), ("7:0:2", "7:2:2"), ("8:0:1", None)]


def test_changes_skip_the_jobs_that_ended_and_count_the_unchanged_tasks() -> None:
    old = [
        row("1:0:2", -500, status="frozen"),  # ended at -400: left the plan
        row("2:0:2", 100),
        row("3:0:2", 200),
        row("4:0:2", 300, slot=1),
    ]
    new = [row("2:0:2", 100), row("3:0:2", 250), row("4:0:2", 300, slot=2), row("5:0:1", 400)]

    found, unchanged = changes(old, new, now=0)

    assert unchanged == 1
    assert [(c["task_id"], c["before"] and c["before"]["start"]) for c in found] == [
        ("3:0:2", 200),
        ("4:0:2", 300),
        ("5:0:1", None),
    ]


def test_durations_read_in_french() -> None:
    assert duration_label(45) == "45 s"
    assert duration_label(-900) == "15 min"
    assert duration_label(3 * HOUR + 300) == "3 h 05 min"
    assert duration_label(2 * 86400 + HOUR) == "2 j 1 h 00 min"


# --------------------------------------------------------------------------- events


def test_a_partial_priority_keeps_the_other_weights_and_the_replanning_perturbation() -> None:
    given = inputs()
    event = Event("priority_changed", NOW, objective={"idle": 0.7})

    result = replan(given, previous_of(given), event, NOW, OVERRIDES)

    weights = result.entry.payload["objective"]
    assert weights["idle"] == 0.7
    assert (weights["value"], weights["night"], weights["perturbation"]) == (1.0, 0.2, 0.5)
    assert result.report["previous_plan_id"] == 1


def test_an_unknown_weight_is_refused() -> None:
    refused(Event("priority_changed", NOW, objective={"speed": 1.0}), 422, "invalid_event")


def test_a_rune_needs_a_storage_of_its_resource() -> None:
    refused(Event("item_used", NOW, item="rune_of_gold"), 422, "item_not_applicable")


def test_a_task_cannot_start_before_its_prerequisites() -> None:
    refused(Event("task_started", NOW, task_id=TROOP_TO_2), 409, "task_not_ready")


def test_a_start_needs_a_free_slot() -> None:
    town_hall = JournalEntry(
        1,
        "task_started",
        NOW,
        TOWN_HALL_TO_3,
        {"lane": "builder", "lane_slot": 0, "end": NOW + HOUR, "cost": {"gold": 5000}},
    )
    refused(
        Event("task_started", NOW, task_id=CANNON_TO_3),
        409,
        "lane_full",
        inputs(town_hall, builders=1),
    )


def test_only_a_started_job_can_finish() -> None:
    refused(Event("task_finished", NOW, task_id=CANNON_TO_3), 409, "task_not_started")


def test_game_data_updated_names_the_active_pack() -> None:
    refused(Event("game_data_updated", NOW, pack_id="other"), 409, "pack_not_active")


# --------------------------------------------------------------------------- routes


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path, frontend_dist=tmp_path / "no-dist", planner_overrides=OVERRIDES
    )
    client = TestClient(create_app(settings, clock=lambda: NOW))
    pack = pack_document()
    assert client.post("/api/gamedata/packs", json=pack).status_code in (200, 201)
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200
    response = client.post("/api/villages/import", json=export_document())
    assert response.status_code in (200, 201), response.text
    assert client.put("/api/villages/UNIT11/manual", json=manual_document()).status_code == 200
    assert client.put("/api/villages/UNIT11/schedule", json=profile_document()).status_code == 200
    return client


def new_plan(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/villages/UNIT11/plans", json=dataclasses.asdict(PARAMS))
    assert response.status_code == 201, response.text
    plan: dict[str, Any] = response.json()
    return plan


def code_of(response: Any, status: int) -> str:
    assert response.status_code == status, response.text
    code: str = response.json()["detail"]["code"]
    return code


def test_an_event_goes_on_the_latest_plan_of_the_village(client: TestClient) -> None:
    first = new_plan(client)["plan_id"]
    second = new_plan(client)["plan_id"]
    event = {"type": "schedule_changed"}

    outdated = client.post(f"/api/villages/UNIT11/plans/{first}/events", json=event)
    unknown = client.post("/api/villages/UNIT11/plans/999/events", json=event)
    pack = client.post(
        f"/api/villages/UNIT11/plans/{second}/events",
        json={"type": "game_data_updated", "pack_id": "nowhere"},
    )

    assert code_of(outdated, 409) == "plan_outdated"
    assert code_of(unknown, 404) == "plan_not_found"
    assert code_of(pack, 404) == "pack_not_found"
    assert code_of(client.get("/api/villages/NOBODY/journal"), 404) == "village_not_found"


def test_a_new_plan_keeps_the_jobs_the_journal_started(client: TestClient) -> None:
    plan_id = new_plan(client)["plan_id"]
    event = {"type": "task_started", "task_id": CANNON_TO_3, "at": NOW}
    response = client.post(f"/api/villages/UNIT11/plans/{plan_id}/events", json=event)
    assert response.status_code == 200, response.text

    fresh = new_plan(client)

    frozen = {a["task_id"]: a for a in fresh["assignments"] if a["status"] == "frozen"}
    assert (frozen[CANNON_TO_3]["start"], frozen[CANNON_TO_3]["end"]) == (NOW, NOW + 1800)
    assert fresh["stale"] is False
