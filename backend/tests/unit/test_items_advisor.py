"""Unit tests of the items and gems advisor (planning/items.py, api/routers/items.py)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.gamedata.rules import GemFinishRule, GemRate
from clashpilot.planning.items import gems_for
from clashpilot.settings import Settings
from support.problems import TEST_CONFIG

ITEMS_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_items.json"
)
NOW = 1_794_211_200  # 2026-11-09T08:00Z
EXPORTED_AT = NOW - 3600
VILLAGE = "UNIT19"
HERO_TASK = "9000006:0:2"
CANNON_TASK = "9000004:0:3"
ENGINE = {"engine": "rollout", "profile": "repro"}


def client_for(
    tmp_path: Path,
    *,
    gems: int = 500,
    magic_items: dict[str, int] | None = None,
    hero_left: int = 3 * 3600,
    plan: bool = True,
) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        frontend_dist=tmp_path / "no-dist",
        planner_overrides=dataclasses.asdict(TEST_CONFIG),
    )
    client = TestClient(create_app(settings, clock=lambda: NOW))
    pack = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    assert client.post("/api/gamedata/packs", json=pack).status_code in (200, 201)
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200
    export = {
        "tag": f"#{VILLAGE}",
        "timestamp": EXPORTED_AT,
        "buildings": [
            {"data": 9000001, "lvl": 2},
            {"data": 9000002, "lvl": 1},
            {"data": 9000002, "lvl": 1},
            {"data": 9000003, "lvl": 1},
            {"data": 9000004, "lvl": 2, "timer": NOW + 1500 - EXPORTED_AT},
        ],
        "units": [{"data": 9000007, "lvl": 1}],
        "heroes": [{"data": 9000006, "lvl": 1, "timer": NOW + hero_left - EXPORTED_AT}],
    }
    response = client.post(
        "/api/villages/import",
        content=json.dumps(export).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text

    def typed(value: int) -> dict[str, Any]:
        return {"value": value, "status": "confirmed", "as_of": EXPORTED_AT}

    rate = {"low": 500, "expected": 1000, "high": 1500}
    manual = {
        "resources": {
            "gold": typed(10_000),
            "elixir": typed(50_000),
            "dark_elixir": typed(5_000),
            "gems": typed(gems),
        },
        "builders_total": 3,
        "farm_rate": {"gold": rate, "elixir": rate, "dark_elixir": rate},
        "magic_items": magic_items or {},
    }
    assert client.put(f"/api/villages/{VILLAGE}/manual", json=manual).status_code == 200
    profile = {
        "tz": "Europe/Paris",
        "weekly": [],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }
    assert client.put(f"/api/villages/{VILLAGE}/schedule", json=profile).status_code == 200
    if plan:
        body = {"horizon_days": 30, **ENGINE}
        assert client.post(f"/api/villages/{VILLAGE}/plans", json=body).status_code == 201
    return client


def post(client: TestClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"/api/villages/{VILLAGE}/{path}", json={**body, **ENGINE})
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


def test_gems_for_takes_the_first_rate_covering_the_time_left() -> None:
    rule = GemFinishRule((GemRate(3600, 7), GemRate(86_400, 120)))
    assert gems_for(rule, 1) == 7
    assert gems_for(rule, 3600) == 7
    assert gems_for(rule, 3601) == 120
    assert gems_for(rule, 86_400) == 120
    assert gems_for(rule, 86_401) is None


def test_a_candidate_the_book_does_not_cover_is_skipped_with_its_reason(tmp_path: Path) -> None:
    # The book of the pack covers buildings: the running hero is not a target.
    answer = post(
        client_for(tmp_path),
        "items/compare",
        {"item": "book_of_building", "candidate_tasks": [HERO_TASK, CANNON_TASK]},
    )
    assert answer["status"] == "ok", answer
    targets = [o["task_id"] for o in answer["options"] if o["kind"] == "use"]
    assert targets == [CANNON_TASK]
    assert [r["code"] for r in answer["reasons"]] == ["option_skipped"]


def test_an_item_absent_from_the_manual_input_is_still_compared(tmp_path: Path) -> None:
    # No quantity typed means unknown, not zero: only a typed 0 makes it unavailable.
    client = client_for(tmp_path, magic_items={"rune_of_gold": 0})
    assert post(client, "items/compare", {"item": "builder_potion"})["status"] == "ok"
    assert post(client, "items/compare", {"item": "rune_of_gold"})["status"] == "unavailable"


def test_a_finish_the_typed_gems_cannot_pay_is_left_out_with_a_reason(tmp_path: Path) -> None:
    # 100 gems: the cannon (7 gems) can be finished, the hero (3 h left, 120 gems) cannot.
    answer = post(client_for(tmp_path, gems=100), "gems/compare", {})
    finishes = {o["task_id"]: o for o in answer["options"] if o["kind"] == "gem_finish"}
    assert set(finishes) == {CANNON_TASK}
    assert finishes[CANNON_TASK]["gems"] == 7
    assert [r["code"] for r in answer["reasons"]] == ["not_enough_gems"]


def test_a_job_longer_than_every_rate_has_no_gem_option(tmp_path: Path) -> None:
    # 25 h left on the hero: beyond the last rate (24 h), no price is guessed.
    answer = post(client_for(tmp_path, hero_left=25 * 3600), "gems/compare", {})
    targets = {o["task_id"] for o in answer["options"] if o["kind"] == "gem_finish"}
    assert targets == {CANNON_TASK}


def test_the_advisor_needs_a_plan_and_writes_nothing(tmp_path: Path) -> None:
    client = client_for(tmp_path, plan=False)
    response = client.post(
        f"/api/villages/{VILLAGE}/items/compare", json={"item": "book_of_building", **ENGINE}
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "no_plan"

    planned = client_for(tmp_path / "planned")
    before = planned.get(f"/api/villages/{VILLAGE}/plans/latest").json()
    post(planned, "items/compare", {"item": "builder_potion"})
    post(planned, "gems/compare", {})
    assert planned.get(f"/api/villages/{VILLAGE}/plans/latest").json() == before
    assert planned.get(f"/api/villages/{VILLAGE}/scenarios").json() == []
    assert planned.get(f"/api/villages/{VILLAGE}/journal").json() == []
