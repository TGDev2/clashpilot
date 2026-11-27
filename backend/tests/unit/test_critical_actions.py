"""Unit tests of the idle critical work (E25): the cases the HTTP exam does not reach."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.planning.critical import ChainStopped, critical_path, stopped_work
from clashpilot.planning.model import Assignment, FrozenJob
from clashpilot.settings import Settings
from support.problems import HOUR, NOW, make_problem, make_task

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE_NOW = 1_795_766_400  # 2026-11-27T08:00:00Z, one hour after the export
VILLAGE = "FICTIFCRIT"

FIRST = make_task("2001:0:2", duration_s=10 * HOUR, tags=("hero",))
SECOND = make_task("2001:0:3", duration_s=20 * HOUR, requires=(FIRST.id,), tags=("hero",))
TOWN_HALL = make_task("1:0:10", duration_s=HOUR, tags=("town_hall",))


def planned(task_id: str, start: int, hours: int) -> Assignment:
    return Assignment(task_id, "builder", 0, start, start + hours * HOUR)


def test_the_next_task_of_a_chain_follows_the_assignments_the_plan_has_finished() -> None:
    problem = make_problem(tasks=(FIRST, SECOND, TOWN_HALL))
    path = critical_path(problem)
    plan = (planned(FIRST.id, NOW, 10), planned(SECOND.id, NOW + 12 * HOUR, 20))

    assert stopped_work(problem, path, plan, NOW + 9 * HOUR) == ()
    assert stopped_work(problem, path, plan, NOW + 10 * HOUR) == (
        ChainStopped(SECOND, NOW + 12 * HOUR, 2 * HOUR),
    )
    assert stopped_work(problem, path, plan, NOW + 40 * HOUR) == ()


def test_a_job_the_problem_runs_counts_even_when_the_plan_starts_it_later() -> None:
    # A newer export shows the first level launched ahead of the plan.
    running = FrozenJob(FIRST.id, "builder", 0, NOW - HOUR, NOW + 9 * HOUR)
    problem = make_problem(tasks=(SECOND, TOWN_HALL), frozen=(running,))
    plan = (planned(FIRST.id, NOW + 5 * HOUR, 10), planned(SECOND.id, NOW + 15 * HOUR, 20))

    assert stopped_work(problem, critical_path(problem), plan, NOW) == ()


def test_an_assignment_a_newer_export_shows_done_neither_runs_nor_blocks() -> None:
    # The plan still runs the first level; the problem holds it no more.
    problem = make_problem(tasks=(SECOND, TOWN_HALL))
    plan = (planned(FIRST.id, NOW, 10), planned(SECOND.id, NOW + 10 * HOUR, 20))

    assert stopped_work(problem, critical_path(problem), plan, NOW + HOUR) == (
        ChainStopped(SECOND, NOW + 10 * HOUR, 9 * HOUR),
    )


def test_inputs_that_no_longer_compile_leave_the_recommendations_without_criticality(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=lambda: FIXTURE_NOW))
    pack = json.loads((FIXTURES / "packs" / "fictional_pack_critical.json").read_bytes())
    assert client.post("/api/gamedata/packs", json=pack).status_code in (200, 201)
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200
    export = (FIXTURES / "exports" / "fictional_export_critical.json").read_bytes()
    headers = {"content-type": "application/json"}
    imported = client.post("/api/villages/import", content=export, headers=headers)
    assert imported.status_code in (200, 201), imported.text
    base = f"/api/villages/{VILLAGE}"
    resources = {
        name: {"value": 1_000_000, "status": "confirmed", "as_of": FIXTURE_NOW}
        for name in ("gold", "elixir", "dark_elixir")
    }
    schedule = {
        "tz": "UTC",
        "weekly": [],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }
    assert client.put(f"{base}/manual", json={"resources": resources}).status_code == 200
    assert client.put(f"{base}/schedule", json=schedule).status_code == 200
    assert client.post(f"{base}/plans", json={"engine": "rollout"}).status_code == 201
    # The stocks are typed no more: the plan stays, its problem cannot be compiled again.
    assert client.put(f"{base}/manual", json={}).status_code == 200

    response = client.get(f"{base}/recommendations/now")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["critical_alerts"] == []
    starts = [action for action in body["actions"] if action["action"] == "start"]
    assert starts
    for action in starts:
        assert (action["float_s"], action["critical"], action["date_gain_s"]) == (None, False, 0)
