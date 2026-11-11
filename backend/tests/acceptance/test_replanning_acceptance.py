"""Acceptance tests for E11: dynamic replanning after unexpected events.

SPEC ADR-4 and ADR-7; brief constraint 8 (no retroactive change).

Frozen with the task. They describe `POST /api/villages/{id}/plans/{plan_id}/events` and
`GET /api/villages/{id}/journal` end to end over HTTP, on the fictional pack
`backend/tests/fixtures/packs/fictional_pack_items.json` (the fictional pack with invented rules
for books, potions and runes), with engine `rollout`, profile `repro` and the fields of
`TEST_CONFIG` as `Settings.planner_overrides`.

Shapes that the task text leaves open are fixed here:

- Event body: `{"type": ..., <fields>, "dry_run": bool}`, `dry_run` false when absent.
  `task_started` / `task_finished`: `{task_id, at}`. `priority_changed`: `{objective}`, the weights
  of `Objective` by name, used as given (an explicit `perturbation` included). The frozen E05
  OpenAPI guard refuses any schema property named like a login, `logins` included: `objective`
  stays a free mapping in the schema. `item_used`: `{item, task_id, at}`, `task_id` null for a
  potion or a rune. `resources_corrected`: `{resources}`, whole units per resource, observed at
  the event. `schedule_changed` and `snapshot_imported` carry no field: they read the stored
  schedule profile and the latest imported snapshot. `game_data_updated`: `{pack_id}`; the tests
  install and activate that pack before posting. An event without `at` happens at the clock's now.
- The event is posted on the latest plan. A 200 answers `plan_id` (the new plan, which
  `GET plans/latest` then returns, with the `engine`, `profile` and `horizon_days` of the plan the
  event was posted on), `previous_plan_id` (the plan the event was posted on),
  `changes` (`[{task_id, before, after}]`, `before` and `after` being `{start, end, lane,
  lane_slot}` or null when the task is absent from that plan), `unchanged_count`, `consequences`
  (`goal_finish_delta_s` a number or null, `score_delta`, `idle_delta_s`, `messages` a list of
  `{code, message}` with a French message), `proof` and `input_hash`.
- The replanning looks the engine of the stored params up in `PLANNERS` at request time, as
  `POST plans` does (E09): the tests wrap `rollout` to see the `Problem`, the objective and the
  reference it receives, and judge the stored plan with `simulate` on that `Problem`. When the
  previous plan cannot be kept (a task planned before the new now was never started, or the input
  hash changed), the engine is called.
- A job already started keeps its task id as `FrozenJob.task_id`. A job running at the new now is
  listed in the plan as a `frozen` assignment; a job that has ended may be left out, and when it is
  listed it keeps its start, its lane slot and its real end.
- A rune used at the new now gives a `Problem` whose initial stock of its resource is the cap, and
  `resources_corrected` gives a `Problem` whose initial stock is the corrected value.
- `GET plans/latest` carries `stale`. `GET journal` answers the rows as a JSON list or under
  `entries`, each `{type, at, task_id, payload, plan_id}`; `task_id` is null for an event without
  task. A refused event changes nothing: same latest plan, same journal, same stored plans.
- Errors keep the SPEC contract `{"detail": {"code", "message"}}`.

The feature imports are local to the helpers below, so that each guarantee fails on its own for as
long as the feature does not exist.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import re
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR

TESTS = Path(__file__).resolve().parents[1]
FIXTURES = TESTS / "fixtures"
ITEMS_PACK = FIXTURES / "packs" / "fictional_pack_items.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

PARIS = "Europe/Paris"
# 2026-11-09T09:00 in Paris (CET, UTC+1): a whole day of play before the 23:00 sleep.
NOW = int(datetime.datetime(2026, 11, 9, 8, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = NOW - HOUR

TAG = "#FICTIF11"
VILLAGE = "FICTIF11"
PACK_ID = "fictional-test-pack-items"
SLOW_PACK_ID = "fictional-test-pack-items-x2"

# Ids of the fictional pack (see fictional_pack_items.json).
TOWN_HALL = 9000001
HUT = 9000002
LAB = 9000003
CANNON = 9000004
STORAGE = 9000005
HERO = 9000006
TROOP = 9000007
MORTAR = 9000008
SPELL = 9000009

# Base export (`base_export`).
HERO_RUNNING = f"{HERO}:0:2"  # lab lane, from NOW - 13000 to NOW + 1400
TOWN_HALL_TO_3 = f"{TOWN_HALL}:0:3"  # 3600 s, 5000 gold
LAB_TO_2 = f"{LAB}:0:2"  # 900 s, builder lane
CANNON_TO_3 = f"{CANNON}:0:3"  # 1800 s, 2000 gold
TROOP_TO_2 = f"{TROOP}:0:2"  # lab lane, needs LAB_TO_2
# Items export (`items_export`).
MORTAR_SHORT = f"{MORTAR}:0:2"  # builder lane, 36000 s left at NOW
MORTAR_LONG = f"{MORTAR}:1:2"  # builder lane, 72000 s left at NOW
SPELL_RUNNING = f"{SPELL}:0:2"  # lab lane, 36000 s left at NOW

REPRO = {"horizon_days": 7, "engine": "rollout", "profile": "repro"}
REPORT_KEYS = (
    "plan_id",
    "previous_plan_id",
    "changes",
    "unchanged_count",
    "consequences",
    "proof",
    "input_hash",
)
CONSEQUENCE_KEYS = ("goal_finish_delta_s", "score_delta", "idle_delta_s", "messages")
JOURNAL_KEYS = ("type", "at", "task_id", "payload", "plan_id")
NEW_OBJECTIVE = {
    "value": 1.0,
    "goal": 0.0,
    "idle": 1.0,
    "night": 0.5,
    "logins": 0.2,
    "spill": 0.0,
    "perturbation": 0.0,
    "preferred": 0.0,
}


# --------------------------------------------------------------------------- feature modules


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e11_acceptance_support_problems"
    if name in sys.modules:
        return sys.modules[name]
    assert SUPPORT_PROBLEMS.is_file(), f"{SUPPORT_PROBLEMS} is missing"
    spec = importlib.util.spec_from_file_location(name, SUPPORT_PROBLEMS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses defined there look their module up
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def config_overrides() -> dict[str, Any]:
    return dataclasses.asdict(support().TEST_CONFIG)


def planners() -> dict[str, Any]:
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def judge(problem: Any, plan: Any) -> Any:
    from clashpilot.planning.simulate import simulate

    return simulate(problem, plan)


def score_of(problem: Any, plan: Any, weights: dict[str, float]) -> float:
    from clashpilot.planning.objective import Objective, score

    return float(score(judge(problem, plan), Objective(**weights), problem))


# --------------------------------------------------------------------------- engine spy


@dataclasses.dataclass(frozen=True)
class Call:
    problem: Any
    objective: Any
    config: Any
    reference: Any
    plan: Any


class Spy:
    """Delegates to the real engine and records each call."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Call] = []

    def solve(self, problem: Any, objective: Any, config: Any, reference: Any = None) -> Any:
        plan = self.inner.solve(problem, objective, config, reference)
        self.calls.append(Call(problem, objective, config, reference, plan))
        return plan

    @property
    def last(self) -> Call:
        assert self.calls, "the engine was never called"
        return self.calls[-1]


@contextmanager
def spying(key: str = "rollout") -> Iterator[Spy]:
    registry = planners()
    inner = registry[key]
    spy = Spy(inner)
    registry[key] = spy
    try:
        yield spy
    finally:
        registry[key] = inner


# --------------------------------------------------------------------------- documents


def items_pack() -> dict[str, Any]:
    pack: dict[str, Any] = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    return pack


def slow_pack() -> dict[str, Any]:
    """The items pack with every duration doubled, under another id."""
    pack = items_pack()
    pack["pack_id"] = SLOW_PACK_ID
    for row in pack["levels"]:
        row["duration_s"] *= 2
    return pack


def base_export(*, timestamp: int = EXPORTED_AT) -> dict[str, Any]:
    return {
        "tag": TAG,
        "timestamp": timestamp,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 2},
            {"data": HUT, "lvl": 1},
            {"data": HUT, "lvl": 1},
            {"data": LAB, "lvl": 1},
            {"data": CANNON, "lvl": 2},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
        # Running until NOW + 1400; its pack row (14400 s) dates its start at NOW - 13000.
        "heroes": [{"data": HERO, "lvl": 1, "timer": 5000}],
    }


def items_export(*, storage: bool = False) -> dict[str, Any]:
    """Two mortars and a spell running; with `storage`, a gold storage of 9000."""
    buildings: list[dict[str, Any]] = [
        {"data": TOWN_HALL, "lvl": 2},
        {"data": HUT, "lvl": 1},
        {"data": HUT, "lvl": 1},
        {"data": LAB, "lvl": 1},
        {"data": CANNON, "lvl": 2},
        {"data": MORTAR, "lvl": 1, "timer": 36000 + HOUR},
        {"data": MORTAR, "lvl": 1, "timer": 72000 + HOUR},
    ]
    if storage:
        buildings.append({"data": STORAGE, "lvl": 2})
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": buildings,
        "units": [{"data": TROOP, "lvl": 1}],
        "heroes": [{"data": HERO, "lvl": 1}],
        "spells": [{"data": SPELL, "lvl": 1, "timer": 36000 + HOUR}],
    }


def slack_export() -> dict[str, Any]:
    """Nothing running, long mortar and spell upgrades: with six builders, much slack."""
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 2},
            {"data": HUT, "lvl": 1},
            {"data": HUT, "lvl": 1},
            {"data": LAB, "lvl": 1},
            {"data": CANNON, "lvl": 2},
            {"data": MORTAR, "lvl": 1},
            {"data": MORTAR, "lvl": 1},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
        "heroes": [{"data": HERO, "lvl": 1}],
        "spells": [{"data": SPELL, "lvl": 1}],
    }


def resource(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": EXPORTED_AT}


def manual_doc(
    *,
    builders: int = 3,
    gold: int = 100_000,
    magic_items: dict[str, int] | None = None,
) -> dict[str, Any]:
    rate = {"low": 500, "expected": 1000, "high": 1500}
    return {
        "resources": {
            "gold": resource(gold),
            "elixir": resource(50_000),
            "dark_elixir": resource(5_000),
        },
        "builders_total": builders,
        "farm_rate": {"gold": rate, "elixir": rate, "dark_elixir": rate},
        "magic_items": magic_items or {},
    }


def profile_doc(
    *,
    mode: str = "progress",
    sleep: tuple[str, str] = ("23:00", "07:00"),
    absences: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "tz": PARIS,
        "weekly": [{"weekday": None, "start": sleep[0], "end": sleep[1], "kind": "sleep"}],
        "exceptions": [
            {"start_local": start, "end_local": end, "kind": "absence"} for start, end in absences
        ],
        "policy": {"mode": mode, "tolerance_minutes": 0, "max_logins_per_day": None},
    }


# --------------------------------------------------------------------------- HTTP helpers


class Clock:
    """The application clock: tests move it forward."""

    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def set(self, now: int) -> None:
        assert now >= self.now, "the clock never goes back"
        self.now = now


@dataclasses.dataclass
class Village:
    client: TestClient
    clock: Clock
    data_dir: Path


def install_pack(client: TestClient, pack: dict[str, Any]) -> None:
    response = client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text


def import_village(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.post(
        "/api/villages/import",
        content=json.dumps(doc).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE


def put_schedule(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.put(f"/api/villages/{VILLAGE}/schedule", json=doc)
    assert response.status_code == 200, response.text


def village(
    tmp_path: Path,
    *,
    export: dict[str, Any] | None = None,
    manual: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> Village:
    """Items pack, export, manual input and schedule profile, at NOW; no plan yet."""
    clock = Clock(NOW)
    settings = Settings(
        data_dir=tmp_path,
        frontend_dist=tmp_path / "no-dist",
        planner_overrides=config_overrides(),
    )
    client = TestClient(create_app(settings, clock=clock))
    install_pack(client, items_pack())
    import_village(client, export if export is not None else base_export())
    response = client.put(
        f"/api/villages/{VILLAGE}/manual", json=manual if manual is not None else manual_doc()
    )
    assert response.status_code == 200, response.text
    put_schedule(client, profile if profile is not None else profile_doc())
    return Village(client, clock, tmp_path)


def create_plan(v: Village) -> dict[str, Any]:
    response = v.client.post(f"/api/villages/{VILLAGE}/plans", json=REPRO)
    assert response.status_code == 201, response.text
    plan: dict[str, Any] = response.json()
    return plan


def latest(v: Village) -> dict[str, Any]:
    response = v.client.get(f"/api/villages/{VILLAGE}/plans/latest")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def post_event(v: Village, body: dict[str, Any], plan_id: int | None = None) -> Any:
    target = latest(v)["plan_id"] if plan_id is None else plan_id
    return v.client.post(f"/api/villages/{VILLAGE}/plans/{target}/events", json=body)


def replan(v: Village, body: dict[str, Any]) -> dict[str, Any]:
    """Post `body` on the latest plan and check the report against the new latest plan."""
    previous = latest(v)
    response = post_event(v, body, previous["plan_id"])
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    check_report(report)
    assert report["previous_plan_id"] == previous["plan_id"]
    now = latest(v)
    assert report["plan_id"] == now["plan_id"]
    assert now["plan_id"] != previous["plan_id"]
    assert report["input_hash"] == now["input_hash"]
    for key in ("engine", "profile", "horizon_days"):
        assert now[key] == previous[key], key
    return report


def task_started(task_id: str, at: int) -> dict[str, Any]:
    return {"type": "task_started", "task_id": task_id, "at": at}


def task_finished(task_id: str, at: int) -> dict[str, Any]:
    return {"type": "task_finished", "task_id": task_id, "at": at}


def item_used(item: str, task_id: str | None, at: int) -> dict[str, Any]:
    return {"type": "item_used", "item": item, "task_id": task_id, "at": at}


def error_of(response: Any) -> dict[str, Any]:
    detail: dict[str, Any] = response.json()["detail"]
    assert isinstance(detail.get("message"), str) and detail["message"], detail
    return detail


def journal(v: Village) -> list[dict[str, Any]]:
    response = v.client.get(f"/api/villages/{VILLAGE}/journal")
    assert response.status_code == 200, response.text
    body = response.json()
    rows: list[dict[str, Any]] = body["entries"] if isinstance(body, dict) else body
    assert isinstance(rows, list), body
    return rows


def plan_rows(v: Village) -> int:
    connection = sqlite3.connect(v.data_dir / "clashpilot.db")
    try:
        (count,) = connection.execute("SELECT COUNT(*) FROM plans").fetchone()
    finally:
        connection.close()
    return int(count)


def state_of(v: Village) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    return latest(v), journal(v), plan_rows(v)


def refused(v: Village, body: dict[str, Any], status: int, code: str) -> None:
    """The event is refused with `status` and `code`, and nothing changes."""
    before = state_of(v)
    response = post_event(v, body)
    assert response.status_code == status, response.text
    assert error_of(response)["code"] == code
    assert state_of(v) == before


# --------------------------------------------------------------------------- plan helpers


def check_report(report: dict[str, Any]) -> None:
    for key in REPORT_KEYS:
        assert key in report, (key, report)
    assert isinstance(report["changes"], list)
    for change in report["changes"]:
        assert {"task_id", "before", "after"} <= set(change), change
        for side in (change["before"], change["after"]):
            assert side is None or {"start", "end", "lane", "lane_slot"} <= set(side), change
    assert isinstance(report["unchanged_count"], int) and report["unchanged_count"] >= 0
    consequences = report["consequences"]
    for key in CONSEQUENCE_KEYS:
        assert key in consequences, (key, consequences)
    goal = consequences["goal_finish_delta_s"]
    assert goal is None or isinstance(goal, int | float), consequences
    assert isinstance(consequences["score_delta"], int | float), consequences
    assert isinstance(consequences["idle_delta_s"], int | float), consequences
    assert isinstance(consequences["messages"], list), consequences
    for message in consequences["messages"]:
        assert isinstance(message["code"], str) and message["code"], message
        assert isinstance(message["message"], str) and message["message"], message
    assert report["proof"]["kind"] in ("heuristic", "feasible", "optimal_window"), report
    assert re.fullmatch(r"[0-9a-f]{64}", report["input_hash"]), report["input_hash"]


def planned(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in plan["assignments"] if a["status"] == "planned"]


def frozen(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["task_id"]: a for a in plan["assignments"] if a["status"] == "frozen"}


def by_task(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for entry in plan["assignments"]:
        assert entry["task_id"] not in found, entry
        found[entry["task_id"]] = entry
    return found


def placed(entry: dict[str, Any]) -> tuple[int, int, int]:
    return entry["start"], entry["end"], entry["lane_slot"]


def klass(task_id: str) -> tuple[int, int]:
    data_id, _, to_level = task_id.split(":")
    return int(data_id), int(to_level)


def as_plan(plan: dict[str, Any]) -> Any:
    m = model()
    assignments = tuple(
        m.Assignment(e["task_id"], e["lane"], e["lane_slot"], e["start"], e["end"])
        for e in planned(plan)
    )
    return m.Plan(assignments, "rollout", m.Proof(kind="heuristic", objective=0.0))


def check_valid(problem: Any, plan: dict[str, Any]) -> Any:
    result = judge(problem, as_plan(plan))
    assert result.valid, result.violations
    return result


def changed_ids(report: dict[str, Any]) -> set[str]:
    return {change["task_id"] for change in report["changes"]}


def change_of(report: dict[str, Any], task_id: str) -> dict[str, Any]:
    found = [change for change in report["changes"] if change["task_id"] == task_id]
    assert len(found) == 1, (task_id, report["changes"])
    return found[0]


def expected_changes(before: dict[str, Any], after: dict[str, Any], now: int) -> set[str]:
    """Tasks whose start or lane slot changed, or planned on one side only.

    A job that ended by `now` may leave the plan: that is no change.
    """
    old, new = by_task(before), by_task(after)
    moved = {
        task_id
        for task_id in old.keys() & new.keys()
        if (old[task_id]["start"], old[task_id]["lane_slot"])
        != (new[task_id]["start"], new[task_id]["lane_slot"])
    }
    dropped = {
        task_id
        for task_id in old.keys() - new.keys()
        if old[task_id]["status"] == "planned" or old[task_id]["end"] > now
    }
    return moved | dropped | (new.keys() - old.keys())


def duration_of(pack: dict[str, Any], task_id: str) -> int:
    data_id, to_level = klass(task_id)
    for row in pack["levels"]:
        if (row["data_id"], row["level"]) == (data_id, to_level):
            return int(row["duration_s"])
    raise AssertionError(f"no pack row for {task_id}")


def cost_of(pack: dict[str, Any], task_id: str) -> dict[str, int]:
    data_id, to_level = klass(task_id)
    for row in pack["levels"]:
        if (row["data_id"], row["level"]) == (data_id, to_level):
            return {name: int(amount) for name, amount in row["cost"].items() if amount}
    raise AssertionError(f"no pack row for {task_id}")


def first_login(plan: dict[str, Any]) -> tuple[int, set[str]]:
    start = min(entry["start"] for entry in planned(plan))
    return start, {entry["task_id"] for entry in planned(plan) if entry["start"] == start}


def check_kept(plan: dict[str, Any], jobs: dict[str, tuple[int, int, int]]) -> None:
    """Every job of `jobs` is listed frozen with exactly its start, end and lane slot."""
    listed = frozen(plan)
    for task_id, expected in jobs.items():
        assert task_id in listed, (task_id, plan["assignments"])
        assert placed(listed[task_id]) == expected, (task_id, listed[task_id])
        assert task_id not in {entry["task_id"] for entry in planned(plan)}


def check_ended(plan: dict[str, Any], task_id: str, start: int, end: int, slot: int) -> None:
    """A job ended at `end`: never planned again; if listed, start and slot kept, real end."""
    assert task_id not in {entry["task_id"] for entry in planned(plan)}
    listed = frozen(plan).get(task_id)
    if listed is not None:
        assert placed(listed) == (start, end, slot), listed


# --------------------------------------------------------------------------- report shape


def test_every_event_type_answers_the_replanning_report(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)

    replan(v, task_started(CANNON_TO_3, NOW))
    v.clock.set(NOW + 600)
    replan(v, task_finished(CANNON_TO_3, NOW + 600))
    v.clock.set(NOW + 660)
    replan(v, {"type": "priority_changed", "objective": NEW_OBJECTIVE})
    v.clock.set(NOW + 720)
    replan(v, item_used("research_potion", None, NOW + 720))
    v.clock.set(NOW + 780)
    replan(v, {"type": "resources_corrected", "resources": {"gold": 50_000}})
    v.clock.set(NOW + 840)
    put_schedule(v.client, profile_doc(sleep=("22:00", "07:00")))
    replan(v, {"type": "schedule_changed"})
    v.clock.set(NOW + 900)
    install_pack(v.client, slow_pack())
    replan(v, {"type": "game_data_updated", "pack_id": SLOW_PACK_ID})
    v.clock.set(NOW + 960)
    export = base_export(timestamp=NOW + 900)
    export["buildings"][4]["lvl"] = 3  # the cannon finished at NOW + 600
    export["heroes"] = [{"data": HERO, "lvl": 2}]  # the potion ended the hero job at NOW + 788
    import_village(v.client, export)
    replan(v, {"type": "snapshot_imported"})


# --------------------------------------------------------------------------- task_started


@pytest.mark.parametrize("delay", [15 * MINUTE, HOUR, 6 * HOUR])
def test_a_late_task_started_becomes_a_frozen_job_and_the_plan_stays_valid(
    tmp_path: Path, delay: int
) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    start, login = first_login(plan)
    assert CANNON_TO_3 in login and len(login) >= 2, login
    others = login - {CANNON_TO_3}
    at = start + delay
    v.clock.set(at)

    with spying() as spy:
        report = replan(v, task_started(CANNON_TO_3, at))
    # The other tasks of that login were never started: the previous plan cannot be kept.
    call = spy.last
    problem = call.problem
    after = latest(v)

    duration = duration_of(items_pack(), CANNON_TO_3)
    jobs = [job for job in problem.frozen if klass(job.task_id) == klass(CANNON_TO_3)]
    assert [(job.task_id, job.start, job.end) for job in jobs] == [(CANNON_TO_3, at, at + duration)]
    assert CANNON_TO_3 not in {task.id for task in problem.tasks}
    assert placed(frozen(after)[CANNON_TO_3])[:2] == (at, at + duration)
    check_valid(problem, after)

    # Planned in the past but never started: replanned from now, never frozen.
    entries = by_task(after)
    for task_id in others:
        assert entries[task_id]["status"] == "planned", entries[task_id]
    for entry in planned(after):
        assert entry["start"] >= at, entry

    change = change_of(report, CANNON_TO_3)
    assert change["before"]["start"] == start
    assert change["after"]["start"] == at
    assert call.reference is not None
    assert call.objective.perturbation == pytest.approx(0.5)


def test_a_task_started_at_its_planned_date_changes_nothing(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    entry = by_task(plan)[CANNON_TO_3]
    # No task is planned before it: none is left in the past unstarted.
    assert entry["start"] == first_login(plan)[0]
    v.clock.set(entry["start"])

    report = replan(v, task_started(CANNON_TO_3, entry["start"]))

    assert report["changes"] == []
    after = latest(v)
    assert placed(frozen(after)[CANNON_TO_3]) == placed(entry)
    old = {e["task_id"]: placed(e) for e in planned(plan) if e["task_id"] != CANNON_TO_3}
    assert {e["task_id"]: placed(e) for e in planned(after)} == old


def pick_delayed(plan: dict[str, Any], delay: int) -> dict[str, Any]:
    """The first planned task whose delay by `delay` disturbs no other assignment."""
    entries = plan["assignments"]
    for task in sorted(planned(plan), key=lambda e: (e["start"], e["task_id"])):
        late_end = task["end"] + delay
        others = [e for e in entries if e["task_id"] != task["task_id"]]
        if any(task["task_id"] in e["requires"] and e["start"] < late_end for e in others):
            continue
        if any(
            (e["lane"], e["lane_slot"]) == (task["lane"], task["lane_slot"])
            and task["start"] <= e["start"] < late_end
            for e in others
        ):
            continue
        if any(task["start"] < e["start"] < task["start"] + delay for e in others):
            continue
        return task
    raise AssertionError("no task of the slack fixture can be delayed alone")


def test_a_15_minute_delay_on_a_slack_fixture_keeps_the_future_starts(tmp_path: Path) -> None:
    delay = 15 * MINUTE
    v = village(tmp_path, export=slack_export(), manual=manual_doc(builders=6))
    plan = create_plan(v)
    delayed = pick_delayed(plan, delay)
    at = delayed["start"] + delay

    # Every other task up to then starts exactly on time: nothing changes.
    on_time = sorted(
        (e for e in planned(plan) if e["start"] <= delayed["start"] and e is not delayed),
        key=lambda e: (e["start"], e["task_id"]),
    )
    for entry in on_time:
        v.clock.set(entry["start"])
        assert replan(v, task_started(entry["task_id"], entry["start"]))["changes"] == []

    before = latest(v)
    v.clock.set(at)
    report = replan(v, task_started(delayed["task_id"], at))
    after = latest(v)

    futures = [
        e for e in planned(before) if e["start"] >= at and e["task_id"] != delayed["task_id"]
    ]
    assert len(futures) >= 5, futures
    starts = {e["task_id"]: e["start"] for e in after["assignments"]}
    kept = [e for e in futures if starts.get(e["task_id"]) == e["start"]]
    assert len(kept) >= 0.8 * len(futures), (kept, futures)
    assert changed_ids(report) == expected_changes(before, after, at)
    assert delayed["task_id"] in changed_ids(report)


# --------------------------------------------------------------------------- started jobs


def test_no_event_moves_a_started_job(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    jobs = {HERO_RUNNING: placed(frozen(plan)[HERO_RUNNING])}

    replan(v, task_started(TOWN_HALL_TO_3, NOW))
    jobs[TOWN_HALL_TO_3] = placed(frozen(latest(v))[TOWN_HALL_TO_3])
    assert jobs[TOWN_HALL_TO_3][:2] == (NOW, NOW + duration_of(items_pack(), TOWN_HALL_TO_3))

    v.clock.set(NOW + 60)
    replan(v, {"type": "resources_corrected", "resources": {"elixir": 40_000}})
    check_kept(latest(v), jobs)

    v.clock.set(NOW + 120)
    replan(v, {"type": "priority_changed", "objective": NEW_OBJECTIVE})
    check_kept(latest(v), jobs)

    v.clock.set(NOW + 180)
    replan(v, task_started(CANNON_TO_3, NOW + 180))
    jobs[CANNON_TO_3] = placed(frozen(latest(v))[CANNON_TO_3])
    check_kept(latest(v), jobs)

    v.clock.set(NOW + 240)
    put_schedule(v.client, profile_doc(sleep=("22:00", "08:00")))
    replan(v, {"type": "schedule_changed"})
    check_kept(latest(v), jobs)

    v.clock.set(NOW + 300)
    install_pack(v.client, slow_pack())
    replan(v, {"type": "game_data_updated", "pack_id": SLOW_PACK_ID})
    check_kept(latest(v), jobs)

    # Only the end of the job an event is about may change.
    v.clock.set(NOW + 360)
    replan(v, task_finished(TOWN_HALL_TO_3, NOW + 360))
    town_hall = jobs.pop(TOWN_HALL_TO_3)
    check_ended(latest(v), TOWN_HALL_TO_3, town_hall[0], NOW + 360, town_hall[2])
    check_kept(latest(v), jobs)

    v.clock.set(NOW + 420)
    replan(v, item_used("builder_potion", None, NOW + 420))
    cannon_start, cannon_end, cannon_slot = jobs.pop(CANNON_TO_3)
    left = cannon_end - (NOW + 420)  # 1560 s, less than D * m: end = at + ceil(r / m)
    assert placed(frozen(latest(v))[CANNON_TO_3]) == (
        cannon_start,
        NOW + 420 + -(-left // 10),
        cannon_slot,
    )
    check_kept(latest(v), jobs)


# --------------------------------------------------------------------------- task_finished


def test_task_finished_early_frees_the_lane_and_advances_the_next_task(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    hero = by_task(plan)[HERO_RUNNING]
    troop = by_task(plan)[TROOP_TO_2]
    assert by_task(plan)[LAB_TO_2]["start"] == NOW
    # The troop waits for the lab lane, busy with the hero until NOW + 1400.
    assert troop["lane"] == hero["lane"] == "lab"
    assert troop["start"] == hero["end"] == NOW + 1400

    replan(v, task_started(LAB_TO_2, NOW))
    v.clock.set(NOW + 600)
    with spying() as spy:
        report = replan(v, task_finished(HERO_RUNNING, NOW + 600))
    problem = spy.last.problem
    after = latest(v)

    lab_jobs = [job for job in problem.frozen if job.lane == "lab" and job.end > NOW + 600]
    assert lab_jobs == []
    check_ended(after, HERO_RUNNING, hero["start"], NOW + 600, hero["lane_slot"])
    moved = by_task(after)[TROOP_TO_2]
    assert NOW + 600 <= moved["start"] < troop["start"]
    change = change_of(report, TROOP_TO_2)
    assert change["before"]["start"] == troop["start"]
    assert change["after"]["start"] == moved["start"]
    check_valid(problem, after)


# --------------------------------------------------------------------------- resources


def test_resources_corrected_down_delays_an_unfunded_task(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = create_plan(v)
    pack = items_pack()
    funded_now = [
        e["task_id"]
        for e in planned(plan)
        if e["start"] == NOW and cost_of(pack, e["task_id"]).get("gold", 0) > 0
    ]
    assert funded_now, planned(plan)

    with spying() as spy:
        replan(v, {"type": "resources_corrected", "resources": {"gold": 0}})
    problem = spy.last.problem
    after = latest(v)

    assert problem.resources.initial["gold"] == 0
    starts = {e["task_id"]: e["start"] for e in planned(after)}
    for task_id in funded_now:
        assert task_id not in starts or starts[task_id] > NOW, (task_id, starts.get(task_id))
    assert any(task_id in starts for task_id in funded_now)
    result = check_valid(problem, after)
    assert "insufficient_funds" not in {violation.code for violation in result.violations}


# --------------------------------------------------------------------------- schedule


def in_new_sleep(t: int) -> bool:
    """10:30 to 18:00 local time, every day."""
    local = datetime.datetime.fromtimestamp(t, ZoneInfo(PARIS))
    minutes = local.hour * 60 + local.minute
    return 10 * 60 + 30 <= minutes < 18 * 60


ABSENCE = ("2026-11-09T18:00:00", "2026-11-09T20:00:00")


def in_absence(t: int) -> bool:
    local = datetime.datetime.fromtimestamp(t, ZoneInfo(PARIS)).replace(tzinfo=None)
    low, high = (datetime.datetime.fromisoformat(bound) for bound in ABSENCE)
    return low <= local < high


def test_schedule_changed_with_an_absence_keeps_strict_ends_out_of_the_new_sleep(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, profile=profile_doc(mode="strict"))
    plan = create_plan(v)
    assert any(in_new_sleep(e["end"]) for e in planned(plan)), planned(plan)

    put_schedule(
        v.client, profile_doc(mode="strict", sleep=("10:30", "18:00"), absences=(ABSENCE,))
    )
    with spying() as spy:
        replan(v, {"type": "schedule_changed"})
    problem = spy.last.problem
    after = latest(v)

    assert planned(after)
    for entry in planned(after):
        assert not in_new_sleep(entry["end"]), entry
        assert not problem.calendar.is_sleep(entry["end"]), entry
        assert not in_absence(entry["start"]), entry
        assert problem.calendar.can_act(entry["start"]), entry
    check_valid(problem, after)


# --------------------------------------------------------------------------- game data


def test_game_data_updated_changes_planned_durations_not_running_jobs(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    replan(v, task_started(CANNON_TO_3, NOW))
    running = {task_id: placed(entry) for task_id, entry in frozen(latest(v)).items()}
    assert set(running) == {HERO_RUNNING, CANNON_TO_3}

    v.clock.set(NOW + 60)
    install_pack(v.client, slow_pack())
    with spying() as spy:
        replan(v, {"type": "game_data_updated", "pack_id": SLOW_PACK_ID})
    after = latest(v)

    check_kept(after, running)
    assert set(frozen(after)) == set(running)
    slow = slow_pack()
    assert planned(after)
    for entry in planned(after):
        expected = duration_of(slow, entry["task_id"])
        assert entry["end"] - entry["start"] == expected, entry
    check_valid(spy.last.problem, after)


# --------------------------------------------------------------------------- priorities


def test_priority_changed_scores_the_plan_with_the_new_objective(tmp_path: Path) -> None:
    v = village(tmp_path)
    before = create_plan(v)

    with spying() as spy:
        report = replan(v, {"type": "priority_changed", "objective": NEW_OBJECTIVE})
    call = spy.last
    after = latest(v)

    for name, weight in NEW_OBJECTIVE.items():
        assert getattr(call.objective, name) == pytest.approx(weight), name
    problem = call.problem
    check_valid(problem, before)  # same now, same inputs: the old plan still fits
    new_score = score_of(problem, as_plan(after), NEW_OBJECTIVE)
    old_score = score_of(problem, as_plan(before), NEW_OBJECTIVE)
    assert after["score"] == pytest.approx(new_score, abs=1e-9)
    assert report["consequences"]["score_delta"] == pytest.approx(new_score - old_score, abs=1e-9)


# --------------------------------------------------------------------------- magic items


def test_an_item_without_pack_rule_is_a_422_unsupported_item_and_changes_nothing(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, export=items_export())
    create_plan(v)
    assert "book_of_heroes" not in items_pack()["rules"]
    assert "rune_of_elixir" not in items_pack()["rules"]

    refused(v, item_used("book_of_heroes", MORTAR_SHORT, NOW), 422, "unsupported_item")
    refused(v, item_used("rune_of_elixir", None, NOW), 422, "unsupported_item")


def test_a_book_ends_a_running_building_job_at_at(tmp_path: Path) -> None:
    v = village(tmp_path, export=items_export())
    plan = create_plan(v)
    job = frozen(plan)[MORTAR_LONG]
    assert job["end"] == NOW + 72000
    v.clock.set(NOW + 600)

    with spying() as spy:
        replan(v, item_used("book_of_building", MORTAR_LONG, NOW + 600))
    problem = spy.last.problem
    after = latest(v)

    check_ended(after, MORTAR_LONG, job["start"], NOW + 600, job["lane_slot"])
    assert MORTAR_LONG not in {task.id for task in problem.tasks}
    for other in problem.frozen:
        if other.task_id == MORTAR_LONG:
            assert (other.start, other.end) == (job["start"], NOW + 600)
    # The lane is free at `at`: only the other mortar still runs on it.
    busy = [j.task_id for j in problem.frozen if j.lane == "builder" and j.end > NOW + 600]
    assert busy == [MORTAR_SHORT]
    check_kept(after, {MORTAR_SHORT: placed(frozen(plan)[MORTAR_SHORT])})
    check_valid(problem, after)


def test_a_book_ends_a_job_started_through_the_journal(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    replan(v, task_started(TOWN_HALL_TO_3, NOW))
    slot = frozen(latest(v))[TOWN_HALL_TO_3]["lane_slot"]
    v.clock.set(NOW + 600)

    with spying() as spy:
        replan(v, item_used("book_of_building", TOWN_HALL_TO_3, NOW + 600))
    after = latest(v)

    check_ended(after, TOWN_HALL_TO_3, NOW, NOW + 600, slot)
    busy = [j for j in spy.last.problem.frozen if j.lane == "builder" and j.end > NOW + 600]
    assert busy == []
    check_valid(spy.last.problem, after)


def test_a_book_on_a_task_not_started_is_a_409_task_not_started(tmp_path: Path) -> None:
    v = village(tmp_path, export=items_export())
    plan = create_plan(v)
    assert LAB_TO_2 in {entry["task_id"] for entry in planned(plan)}

    refused(v, item_used("book_of_building", LAB_TO_2, NOW), 409, "task_not_started")


def test_a_book_on_a_job_of_another_kind_is_a_422_item_not_applicable(tmp_path: Path) -> None:
    v = village(tmp_path, export=items_export())
    plan = create_plan(v)
    assert SPELL_RUNNING in frozen(plan)

    refused(v, item_used("book_of_building", SPELL_RUNNING, NOW), 422, "item_not_applicable")


def test_a_builder_potion_speeds_up_the_running_builder_jobs_only(tmp_path: Path) -> None:
    magic = {"builder_potion": 2, "research_potion": 1}
    v = village(tmp_path, export=items_export(), manual=manual_doc(magic_items=magic))
    plan = create_plan(v)
    jobs = frozen(plan)
    assert jobs[MORTAR_SHORT]["end"] == NOW + 36000
    assert jobs[MORTAR_LONG]["end"] == NOW + 72000
    assert jobs[SPELL_RUNNING]["end"] == NOW + 36000

    with spying() as spy:
        replan(v, item_used("builder_potion", None, NOW))
    after = latest(v)

    # Multiplier 10 for 3600 s: 36000 s left end at at + 3600, 72000 s at at + 39600.
    check_kept(
        after,
        {
            MORTAR_SHORT: (
                jobs[MORTAR_SHORT]["start"],
                NOW + 3600,
                jobs[MORTAR_SHORT]["lane_slot"],
            ),
            MORTAR_LONG: (jobs[MORTAR_LONG]["start"], NOW + 39600, jobs[MORTAR_LONG]["lane_slot"]),
            SPELL_RUNNING: placed(jobs[SPELL_RUNNING]),
        },
    )
    check_valid(spy.last.problem, after)
    manual = v.client.get(f"/api/villages/{VILLAGE}/manual")
    assert manual.status_code == 200, manual.text
    assert manual.json()["magic_items"] == magic


def test_a_research_potion_speeds_up_the_running_lab_job_only(tmp_path: Path) -> None:
    v = village(tmp_path, export=items_export())
    plan = create_plan(v)
    jobs = frozen(plan)

    with spying() as spy:
        replan(v, item_used("research_potion", None, NOW))
    after = latest(v)

    check_kept(
        after,
        {
            SPELL_RUNNING: (
                jobs[SPELL_RUNNING]["start"],
                NOW + 3600,
                jobs[SPELL_RUNNING]["lane_slot"],
            ),
            MORTAR_SHORT: placed(jobs[MORTAR_SHORT]),
            MORTAR_LONG: placed(jobs[MORTAR_LONG]),
        },
    )
    check_valid(spy.last.problem, after)


def test_a_rune_of_gold_fills_the_gold_storage_at_at(tmp_path: Path) -> None:
    v = village(tmp_path, export=items_export(storage=True), manual=manual_doc(gold=1000))
    with spying() as first:
        create_plan(v)
    initial = first.last.problem.resources
    assert initial.cap["gold"] == 9000
    assert initial.initial["gold"] < 9000

    with spying() as spy:
        replan(v, item_used("rune_of_gold", None, NOW))
    problem = spy.last.problem

    assert problem.resources.cap["gold"] == 9000
    assert problem.resources.initial["gold"] == 9000
    for name, amount in initial.initial.items():
        if name != "gold":
            assert problem.resources.initial[name] == amount, name
    check_valid(problem, latest(v))


# --------------------------------------------------------------------------- dry run and errors


def test_a_dry_run_answers_the_report_and_persists_nothing(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    before = state_of(v)
    assert before[1] == []

    response = post_event(v, {**task_started(CANNON_TO_3, NOW), "dry_run": True})

    assert response.status_code == 200, response.text
    report = response.json()
    for key in REPORT_KEYS:
        assert key in report, key
    assert isinstance(report["changes"], list)
    assert state_of(v) == before


def test_an_event_in_the_future_is_a_422_future_event(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)

    refused(v, task_started(CANNON_TO_3, NOW + 60), 422, "future_event")
    refused(v, item_used("research_potion", None, NOW + 60), 422, "future_event")


def test_an_unknown_task_is_a_404_task_not_found(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)

    refused(v, task_started(f"{CANNON}:7:3", NOW), 404, "task_not_found")
    refused(v, task_finished("1234567:0:1", NOW), 404, "task_not_found")


def test_a_task_started_twice_is_a_409_already_started(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    replan(v, task_started(CANNON_TO_3, NOW))
    v.clock.set(NOW + 60)

    refused(v, task_started(CANNON_TO_3, NOW + 60), 409, "already_started")


# --------------------------------------------------------------------------- journal


def test_every_event_adds_a_journal_row_newest_first(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    assert journal(v) == []

    first = replan(v, task_started(CANNON_TO_3, NOW))
    v.clock.set(NOW + 600)
    second = replan(v, {"type": "resources_corrected", "resources": {"gold": 90_000}})
    v.clock.set(NOW + 1200)
    third = replan(v, {"type": "priority_changed", "objective": NEW_OBJECTIVE})

    rows = journal(v)
    for row in rows:
        for key in JOURNAL_KEYS:
            assert key in row, (key, row)
    assert [row["type"] for row in rows] == [
        "priority_changed",
        "resources_corrected",
        "task_started",
    ]
    assert [row["at"] for row in rows] == [NOW + 1200, NOW + 600, NOW]
    assert [row["task_id"] for row in rows] == [None, None, CANNON_TO_3]
    for row, report in zip(rows, (third, second, first), strict=True):
        assert row["plan_id"] in (report["previous_plan_id"], report["plan_id"]), row
    cost = rows[2]["payload"]["cost"]
    assert {name: amount for name, amount in cost.items() if amount} == cost_of(
        items_pack(), CANNON_TO_3
    )


# --------------------------------------------------------------------------- new snapshot


def test_a_new_import_makes_the_plan_stale_until_snapshot_imported(tmp_path: Path) -> None:
    v = village(tmp_path)
    create_plan(v)
    assert latest(v)["stale"] is False
    replan(v, task_started(CANNON_TO_3, NOW))
    assert latest(v)["stale"] is False

    v.clock.set(NOW + 2 * HOUR)
    export = base_export(timestamp=NOW + 2 * HOUR - 5 * MINUTE)
    export["buildings"][4]["lvl"] = 3  # the started cannon upgrade is done
    export["heroes"] = [{"data": HERO, "lvl": 2}]
    import_village(v.client, export)
    assert latest(v)["stale"] is True

    replan(v, {"type": "snapshot_imported"})
    after = latest(v)

    assert after["stale"] is False
    now = NOW + 2 * HOUR
    live = [e for e in after["assignments"] if e["status"] == "planned" or e["end"] > now]
    # One cannon is at level 3 now: only the other one still goes to 3, and both go to 4.
    assert sum(1 for e in live if klass(e["task_id"]) == (CANNON, 3)) == 1, live
    assert sum(1 for e in live if klass(e["task_id"]) == (CANNON, 4)) == 2, live
    assert not any(klass(e["task_id"]) == (HERO, 2) for e in live), live
