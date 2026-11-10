"""Acceptance tests for E09: plan service, plan API and explained recommendations.

SPEC ADR-4 and ADR-7.

Frozen with the task. They describe `POST /api/villages/{id}/plans`,
`GET /api/villages/{id}/plans/latest`, `GET /api/villages/{id}/recommendations/now` and
`GET /api/meta/engines`, end to end over HTTP (SPEC section 8, step 1): fictional pack, fictional
export, manual input, schedule profile on a time zone with a DST change, plan, recommendations.

Shapes that the task text leaves open are fixed here:

- The application is built with `Settings.planner_overrides` equal to the fields of `TEST_CONFIG`
  (`backend/tests/support/problems.py`, loaded from its path as the E07/E08 tests do). The test of
  the defaults leaves `profile` out of the overrides: the profile is a request field there.
- `POST plans` body: `{"horizon_days", "engine", "profile"}`, each optional. Engines are looked up
  in `clashpilot.planning.registry.PLANNERS` at request time: the tests put fake engines in it (and
  wrap real ones to watch the `Problem` they receive and the `Plan` they return), then restore it.
- `assignments` lists the running jobs (status `frozen`) and the planned tasks (status `planned`);
  `start` and `end` are UTC epochs. `pack` is `{pack_id, game_version, fictional}`.
- `GET /api/meta/engines` answers the registry keys, as a JSON list or under `engines`, each entry a
  key or an object carrying it under `key`.
- `recommendations/now` explains the latest plan. `next_login_at` is the start of its earliest
  planned assignment, and every planned assignment starting at that instant has an action
  `start` with its `task_id`. The action of a task is explained from the trace decision whose
  `chosen` names that task. The response says `fictional` either at the top level or under `pack`,
  and carries `proof.kind` like the plan.
- `reliability.factors` entries are a code or an object carrying `code`; the causes are
  `fictional_pack`, `estimated_resource` and `stale_resource`, a resource factor naming the
  resource. A resource is fresh when `now - as_of < 7 days` and stale when `now - as_of > 7 days`:
  exactly 7 days is neither, which gives `medium` on a non-fictional pack.
- Errors keep the SPEC contract `{"detail": {"code", "message"}}`; `missing_input` adds `missing`.
- The plans live in the table `plans` of `data_dir/clashpilot.db` (migration `0006_plans.sql`).

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

TESTS = Path(__file__).resolve().parents[1]
FIXTURES = TESTS / "fixtures"
PACK_FIXTURE = FIXTURES / "packs" / "fictional_pack_v1.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

PARIS = "Europe/Paris"
# 2026-10-24T22:00 in Paris (CEST); the clocks go back on 2026-10-25 at 03:00 local.
NOW = int(datetime.datetime(2026, 10, 24, 20, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = NOW - HOUR

TAG = "#FICTIF09"
VILLAGE = "FICTIF09"
PACK_ID = "fictional-test-pack-v1"

# Ids of the fictional pack (see fictional_pack_v1.json).
TOWN_HALL = 9000001
HUT = 9000002
LAB = 9000003
CANNON = 9000004
HERO = 9000006
TROOP = 9000007

LAB_TO_2 = f"{LAB}:0:2"  # 900 s in the pack, in the builder lane
HERO_RUNNING = f"{HERO}:0:2"  # running job of the export

ASSIGNMENT_KEYS = ("task_id", "name", "lane", "lane_slot", "start", "end", "status", "requires")
PLAN_KEYS = (
    "plan_id",
    "now",
    "timezone",
    "assignments",
    "excluded",
    "proof",
    "input_hash",
    "pack",
    "score",
    "engine",
    "profile",
    "reproducible",
    "warnings",
)
ACTION_KEYS = (
    "task_id",
    "name",
    "action",
    "why",
    "advantages",
    "drawbacks",
    "reliability",
    "counterfactual",
)
OPTIMAL = re.compile(r"optima[lu]", re.IGNORECASE)


# --------------------------------------------------------------------------- feature modules


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e09_acceptance_support_problems"
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


def config_overrides(*, with_profile: bool = True) -> dict[str, Any]:
    values = dataclasses.asdict(support().TEST_CONFIG)
    if not with_profile:
        values.pop("profile")
    return values


def planners() -> dict[str, Any]:
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


def model() -> ModuleType:
    import clashpilot.planning.model as module

    return module


def invalid_score() -> float:
    return float(model().INVALID_SCORE)


def judge(problem: Any, plan: Any) -> Any:
    from clashpilot.planning.simulate import simulate

    return simulate(problem, plan)


# --------------------------------------------------------------------------- engines


class Recorder:
    """An engine that delegates to `inner` (or returns `make(problem)`) and records each call."""

    def __init__(
        self,
        inner: Any = None,
        make: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        self.inner = inner
        self.make = make
        self.calls: list[tuple[Any, Any, Any]] = []  # (problem, config, plan)

    def solve(self, problem: Any, objective: Any, config: Any, reference: Any = None) -> Any:
        inner_plan = None
        if self.inner is not None:
            inner_plan = self.inner.solve(problem, objective, config, reference)
        plan = self.make(problem, inner_plan) if self.make is not None else inner_plan
        self.calls.append((problem, config, plan))
        return plan

    @property
    def last(self) -> tuple[Any, Any, Any]:
        assert self.calls, "the engine was never called"
        return self.calls[-1]


_ABSENT = object()


@contextmanager
def engine(key: str, planner: Any) -> Iterator[Any]:
    """Put `planner` under `key` in PLANNERS for the block, then restore what was there."""
    registry = planners()
    previous = registry.get(key, _ABSENT)
    registry[key] = planner
    try:
        yield planner
    finally:
        if previous is _ABSENT:
            registry.pop(key, None)
        else:
            registry[key] = previous


@contextmanager
def without_engine(key: str) -> Iterator[None]:
    registry = planners()
    previous = registry.pop(key, _ABSENT)
    try:
        yield
    finally:
        if previous is not _ABSENT:
            registry[key] = previous


def empty_plan(key: str) -> Callable[[Any, Any], Any]:
    def make(problem: Any, _inner: Any) -> Any:
        m = model()
        return m.Plan(assignments=(), engine=key, proof=m.Proof(kind="heuristic", objective=0.0))

    return make


# --------------------------------------------------------------------------- documents


def fictional_pack(*, fictional: bool = True) -> dict[str, Any]:
    pack: dict[str, Any] = json.loads(PACK_FIXTURE.read_text(encoding="utf-8"))
    if not fictional:
        pack["pack_id"] = "nonfictional-test-pack-v1"
        pack["fictional"] = False
    return pack


def export_doc(*, huts: int = 2) -> dict[str, Any]:
    return {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": [
            {"data": TOWN_HALL, "lvl": 2},
            *({"data": HUT, "lvl": 1} for _ in range(huts)),
            {"data": LAB, "lvl": 1},
            {"data": CANNON, "lvl": 2},
        ],
        "units": [{"data": TROOP, "lvl": 1}],
        # Running until NOW + 1400: a frozen job of the lab lane.
        "heroes": [{"data": HERO, "lvl": 1, "timer": 5000}],
    }


def resource(value: int, status: str = "confirmed", as_of: int = NOW - HOUR) -> dict[str, Any]:
    return {"value": value, "status": status, "as_of": as_of}


def manual_doc(
    *,
    status: str = "confirmed",
    as_of: int = NOW - HOUR,
    gold: bool = True,
    builders: bool = True,
    farm_rate: bool = True,
    gold_value: int = 100_000,
) -> dict[str, Any]:
    """Gold and elixir are what the compiled tasks consume; `status` and `as_of` apply to both."""
    resources = {
        "elixir": resource(50_000, status, as_of),
        "dark_elixir": resource(5_000, "confirmed", NOW - HOUR),
    }
    if gold:
        resources["gold"] = resource(gold_value, status, as_of)
    doc: dict[str, Any] = {"resources": resources}
    if builders:
        doc["builders_total"] = 3
    if farm_rate:
        doc["farm_rate"] = {
            "gold": {"low": 500, "expected": 1000, "high": 1500},
            "elixir": {"low": 500, "expected": 1000, "high": 1500},
            "dark_elixir": {"low": 10, "expected": 20, "high": 30},
        }
    return doc


PROFILE: dict[str, Any] = {
    "tz": PARIS,
    "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
    "exceptions": [],
    "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
}

REPRO = {"horizon_days": 30, "engine": "rollout", "profile": "repro"}


# --------------------------------------------------------------------------- HTTP helpers


def make_client(data_dir: Path, overrides: dict[str, Any] | None = None) -> TestClient:
    settings = Settings(
        data_dir=data_dir,
        frontend_dist=data_dir / "no-dist",
        planner_overrides=overrides if overrides is not None else config_overrides(),
    )
    return TestClient(create_app(settings, clock=lambda: NOW))


def install_pack(client: TestClient, *, fictional: bool = True) -> str:
    pack = fictional_pack(fictional=fictional)
    assert client.post("/api/gamedata/packs", json=pack).status_code in (200, 201)
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200
    return str(pack["pack_id"])


def import_village(client: TestClient, doc: dict[str, Any]) -> str:
    response = client.post(
        "/api/villages/import",
        content=json.dumps(doc).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    village_id = str(response.json()["village_id"])
    assert village_id == VILLAGE
    return village_id


def put_manual(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.put(f"/api/villages/{VILLAGE}/manual", json=doc)
    assert response.status_code == 200, response.text


def put_schedule(client: TestClient) -> None:
    response = client.put(f"/api/villages/{VILLAGE}/schedule", json=PROFILE)
    assert response.status_code == 200, response.text


def prepare(
    client: TestClient,
    *,
    pack: bool = True,
    fictional: bool = True,
    huts: int = 2,
    manual: dict[str, Any] | None = None,
    schedule: bool = True,
) -> None:
    """Pack, export, manual input and schedule: the journey of SPEC section 8 before the plan."""
    if pack:
        install_pack(client, fictional=fictional)
    import_village(client, export_doc(huts=huts))
    put_manual(client, manual if manual is not None else manual_doc())
    if schedule:
        put_schedule(client)


def post_plan(client: TestClient, body: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/villages/{VILLAGE}/plans", json=REPRO if body is None else body)


def created_plan(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = post_plan(client, body)
    assert response.status_code == 201, response.text
    plan: dict[str, Any] = response.json()
    return plan


def recommendations(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/villages/{VILLAGE}/recommendations/now")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def error_of(response: Any) -> dict[str, Any]:
    detail: dict[str, Any] = response.json()["detail"]
    assert isinstance(detail.get("message"), str) and detail["message"], detail
    return detail


def plan_rows(data_dir: Path) -> int:
    connection = sqlite3.connect(data_dir / "clashpilot.db")
    try:
        (count,) = connection.execute("SELECT COUNT(*) FROM plans").fetchone()
    finally:
        connection.close()
    return int(count)


def planned(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in plan["assignments"] if a["status"] == "planned"]


def assignment_of(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [a for a in plan["assignments"] if a["task_id"] == task_id]
    assert len(found) == 1, (task_id, plan["assignments"])
    return found[0]


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def provenances(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every provenance-shaped object (`status` and `source`) with the keys leading to it."""
    if isinstance(value, dict):
        if "status" in value and "source" in value:
            yield path, value
        for key, item in value.items():
            yield from provenances(item, (*path, str(key)))
    elif isinstance(value, list):
        for item in value:
            yield from provenances(item, path)


def fictional_flag(body: dict[str, Any]) -> Any:
    return body["fictional"] if "fictional" in body else body["pack"]["fictional"]


def factor_codes(reliability: dict[str, Any]) -> list[str]:
    return [f if isinstance(f, str) else f["code"] for f in reliability["factors"]]


def start_actions(recs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["task_id"]: a for a in recs["actions"] if a["action"] == "start"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    created = make_client(tmp_path)
    prepare(created)
    return created


# --------------------------------------------------------------------------- full journey


def test_full_journey_creates_a_plan_with_every_field(client: TestClient) -> None:
    plan = created_plan(client)

    for key in PLAN_KEYS:
        assert key in plan, key
    assert plan["plan_id"] is not None
    assert plan["now"] == NOW
    assert plan["timezone"] == PARIS
    assert isinstance(plan["excluded"], list)
    assert isinstance(plan["proof"], dict) and plan["proof"]["kind"] == "heuristic"
    assert re.fullmatch(r"[0-9a-f]{64}", plan["input_hash"]), plan["input_hash"]
    assert plan["pack"] == {"pack_id": PACK_ID, "game_version": "test-0", "fictional": True}
    assert isinstance(plan["score"], int | float)
    assert plan["engine"] == "rollout"
    assert plan["profile"] == "repro"
    assert plan["reproducible"] is True
    assert isinstance(plan["warnings"], list)
    assert "no_farm_rate" not in plan["warnings"]

    assert plan["assignments"]
    for entry in plan["assignments"]:
        for key in ASSIGNMENT_KEYS:
            assert key in entry, (key, entry)
        assert entry["status"] in ("planned", "frozen"), entry
        assert isinstance(entry["start"], int) and isinstance(entry["end"], int), entry
        assert isinstance(entry["lane_slot"], int), entry
        assert isinstance(entry["requires"], list), entry
        assert isinstance(entry["name"], str) and entry["name"], entry

    running = assignment_of(plan, HERO_RUNNING)
    assert running["status"] == "frozen"
    assert running["end"] == EXPORTED_AT + 5000
    lab = assignment_of(plan, LAB_TO_2)
    assert lab["status"] == "planned"
    assert lab["name"] == "Laboratoire de test"
    assert lab["lane"] == "builder"
    assert lab["end"] - lab["start"] == 900


def test_planned_starts_are_utc_epochs_outside_the_local_night_across_the_dst_change(
    client: TestClient,
) -> None:
    plan = created_plan(client)
    paris = ZoneInfo(PARIS)

    starts = [entry["start"] for entry in planned(plan)]
    assert starts
    for start in starts:
        assert start >= NOW
        local = datetime.datetime.fromtimestamp(start, paris)
        assert 7 <= local.hour < 23, (start, local.isoformat())
    # The night after NOW holds the change to winter time: some tasks start after it.
    assert any(start > NOW + 6 * HOUR for start in starts)


# --------------------------------------------------------------------------- defaults


def test_without_engine_auto_is_used_when_registered_with_fast_and_90_days(tmp_path: Path) -> None:
    client = make_client(tmp_path, config_overrides(with_profile=False))
    prepare(client)

    with engine("auto", Recorder(make=empty_plan("auto"))) as fake:
        plan = created_plan(client, {})
        problem, config, _ = fake.last

    assert plan["engine"] == "auto"
    assert plan["profile"] == "fast"
    assert config.profile == "fast"
    assert problem.horizon_end - problem.now == 90 * DAY
    assert plan["reproducible"] is False


def test_auto_with_profile_repro_is_reproducible(tmp_path: Path) -> None:
    client = make_client(tmp_path, config_overrides(with_profile=False))
    prepare(client)

    with engine("auto", Recorder(make=empty_plan("auto"))):
        plan = created_plan(client, {"profile": "repro"})

    assert plan["engine"] == "auto"
    assert plan["profile"] == "repro"
    assert plan["reproducible"] is True


def test_without_engine_rollout_is_used_when_auto_is_not_registered(tmp_path: Path) -> None:
    client = make_client(tmp_path, config_overrides(with_profile=False))
    prepare(client)

    with without_engine("auto"), engine("rollout", Recorder(make=empty_plan("rollout"))) as fake:
        plan = created_plan(client, {})
        problem, config, _ = fake.last

    assert plan["engine"] == "rollout"
    assert plan["profile"] == "fast"
    assert config.profile == "fast"
    assert problem.horizon_end - problem.now == 90 * DAY
    assert plan["reproducible"] is True


def test_baseline_in_profile_fast_is_reproducible(tmp_path: Path) -> None:
    client = make_client(tmp_path, config_overrides(with_profile=False))
    prepare(client)

    plan = created_plan(client, {"horizon_days": 30, "engine": "baseline", "profile": "fast"})

    assert plan["engine"] == "baseline"
    assert plan["profile"] == "fast"
    assert plan["reproducible"] is True


def test_planner_overrides_reach_the_engine(client: TestClient) -> None:
    rollout = planners()["rollout"]
    with engine("rollout", Recorder(inner=rollout)) as spy:
        created_plan(client)
        problem, config, _ = spy.last

    expected = support().TEST_CONFIG
    assert config.max_rollouts == expected.max_rollouts
    assert config.seed == expected.seed
    assert config.profile == "repro"
    assert problem.horizon_end - problem.now == 30 * DAY


# --------------------------------------------------------------------------- pack corrections


def test_a_duration_patch_on_the_active_pack_changes_the_planned_duration(
    client: TestClient,
) -> None:
    before = created_plan(client)
    assert assignment_of(before, LAB_TO_2)["end"] - assignment_of(before, LAB_TO_2)["start"] == 900

    response = client.patch(
        f"/api/gamedata/packs/{PACK_ID}/items/{LAB}/levels/2", json={"duration_s": 4321}
    )
    assert response.status_code == 200, response.text

    after = created_plan(client)
    lab = assignment_of(after, LAB_TO_2)
    assert lab["status"] == "planned"
    assert lab["end"] - lab["start"] == 4321
    assert after["input_hash"] != before["input_hash"]


# --------------------------------------------------------------------------- warnings


def test_a_consumed_resource_without_farm_rate_gives_the_no_farm_rate_warning(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    prepare(client, manual=manual_doc(farm_rate=False))

    plan = created_plan(client)

    assert planned(plan)
    assert "no_farm_rate" in plan["warnings"]


# --------------------------------------------------------------------------- reproducibility


def test_two_identical_repro_posts_give_the_same_assignments_and_input_hash(
    client: TestClient,
) -> None:
    first = created_plan(client)
    second = created_plan(client)

    assert second["assignments"] == first["assignments"]
    assert second["input_hash"] == first["input_hash"]


def test_a_different_input_changes_the_input_hash(client: TestClient) -> None:
    reference = created_plan(client)["input_hash"]

    other_horizon = created_plan(client, {**REPRO, "horizon_days": 20})["input_hash"]
    other_engine = created_plan(client, {**REPRO, "engine": "baseline"})["input_hash"]
    put_manual(client, manual_doc(gold_value=90_000))
    other_manual = created_plan(client)["input_hash"]

    assert len({reference, other_horizon, other_engine, other_manual}) == 4


# --------------------------------------------------------------------------- validity (ADR-4)


def test_every_returned_assignment_passes_the_simulator(client: TestClient) -> None:
    m = model()
    rollout = planners()["rollout"]
    with engine("rollout", Recorder(inner=rollout)) as spy:
        plan = created_plan(client)
        problem, _, _ = spy.last

    ours = tuple(
        m.Assignment(e["task_id"], e["lane"], e["lane_slot"], e["start"], e["end"])
        for e in planned(plan)
    )
    assert ours
    result = judge(problem, m.Plan(ours, "rollout", m.Proof(kind="heuristic", objective=0.0)))
    assert result.valid, result.violations

    frozen = {
        e["task_id"]: (e["lane"], e["lane_slot"], e["start"], e["end"])
        for e in plan["assignments"]
        if e["status"] == "frozen"
    }
    assert frozen == {
        job.task_id: (job.lane, job.lane_slot, job.start, job.end) for job in problem.frozen
    }


def test_an_invalid_plan_is_a_500_and_is_never_stored(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client)

    def broken(problem: Any, _inner: Any) -> Any:
        m = model()
        task = problem.tasks[0]
        start = problem.now - DAY  # past_start
        assignment = m.Assignment(task.id, task.lane, 0, start, start + task.duration_s)
        return m.Plan((assignment,), "broken", m.Proof(kind="heuristic", objective=0.0))

    with engine("broken", Recorder(make=broken)):
        response = post_plan(client, {"horizon_days": 30, "engine": "broken", "profile": "repro"})

    assert response.status_code == 500
    assert error_of(response)["code"] == "invalid_plan"
    assert plan_rows(tmp_path) == 0
    latest = client.get(f"/api/villages/{VILLAGE}/plans/latest")
    assert latest.status_code == 404
    assert error_of(latest)["code"] == "no_plan"


# --------------------------------------------------------------------------- missing inputs


def test_a_consumed_resource_without_value_is_a_409_missing_input(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, manual=manual_doc(gold=False))

    response = post_plan(client)

    assert response.status_code == 409
    detail = error_of(response)
    assert detail["code"] == "missing_input"
    assert "gold" in detail["missing"]


def test_no_builders_total_and_no_hut_is_a_409_missing_input(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, huts=0, manual=manual_doc(builders=False))

    response = post_plan(client)

    assert response.status_code == 409
    detail = error_of(response)
    assert detail["code"] == "missing_input"
    assert "builders_total" in detail["missing"]


def test_no_schedule_profile_is_a_409_missing_input(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, schedule=False)

    response = post_plan(client)

    assert response.status_code == 409
    detail = error_of(response)
    assert detail["code"] == "missing_input"
    assert "schedule_profile" in detail["missing"]


def test_no_active_pack_is_a_409_no_active_pack(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, pack=False)

    response = post_plan(client)

    assert response.status_code == 409
    assert error_of(response)["code"] == "no_active_pack"


def test_an_unknown_engine_is_a_422_unknown_engine(client: TestClient) -> None:
    response = post_plan(client, {**REPRO, "engine": "no-such-engine"})

    assert response.status_code == 422
    assert error_of(response)["code"] == "unknown_engine"


# --------------------------------------------------------------------------- latest and meta


def test_latest_is_a_404_no_plan_before_any_plan(client: TestClient) -> None:
    response = client.get(f"/api/villages/{VILLAGE}/plans/latest")

    assert response.status_code == 404
    assert error_of(response)["code"] == "no_plan"


def test_latest_returns_the_last_plan(client: TestClient) -> None:
    first = created_plan(client)
    second = created_plan(client, {**REPRO, "horizon_days": 20})
    assert second["plan_id"] != first["plan_id"]

    response = client.get(f"/api/villages/{VILLAGE}/plans/latest")

    assert response.status_code == 200, response.text
    latest = response.json()
    assert latest["plan_id"] == second["plan_id"]
    assert latest["input_hash"] == second["input_hash"]
    assert latest["assignments"] == second["assignments"]
    assert latest["proof"]["kind"] == second["proof"]["kind"]


def engine_keys(body: Any) -> set[str]:
    entries = body["engines"] if isinstance(body, dict) else body
    return {e if isinstance(e, str) else e["key"] for e in entries}


def test_meta_engines_lists_the_registry_keys(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with engine("probe", Recorder(make=empty_plan("probe"))):
        response = client.get("/api/meta/engines")
        expected = set(planners())

    assert response.status_code == 200, response.text
    keys = engine_keys(response.json())
    assert keys == expected
    assert {"baseline", "rollout", "probe"} <= keys


# --------------------------------------------------------------------------- recommendations


def test_recommendations_now_explain_the_next_login(client: TestClient) -> None:
    plan = created_plan(client)
    recs = recommendations(client)

    first_start = min(entry["start"] for entry in planned(plan))
    assert recs["next_login_at"] == first_start
    assert recs["actions"]
    for action in recs["actions"]:
        for key in ACTION_KEYS:
            assert key in action, (key, action)
        assert action["action"] in ("start", "wait"), action
        reasons = action["why"]["reasons"]
        assert isinstance(reasons, list) and reasons, action
        for reason in reasons:
            assert set(reason) >= {"code", "message", "value"}, reason
            assert isinstance(reason["message"], str) and reason["message"], reason
        assert isinstance(action["advantages"], list), action
        assert isinstance(action["drawbacks"], list), action
        assert action["reliability"]["level"] in ("high", "medium", "low"), action
        assert isinstance(action["reliability"]["factors"], list), action
        counterfactual = action["counterfactual"]
        if counterfactual is not None:
            assert set(counterfactual) >= {"alternative_task_id", "score_delta", "finish_delta_s"}
            assert isinstance(counterfactual["score_delta"], int | float)
            assert isinstance(counterfactual["finish_delta_s"], int | float)

    starting = {e["task_id"] for e in planned(plan) if e["start"] == first_start}
    assert starting <= set(start_actions(recs))
    for task_id, action in start_actions(recs).items():
        assert action["name"] == assignment_of(plan, task_id)["name"]


def expected_counterfactual(decision: Any) -> tuple[float, set[Any]] | None:
    chosen = decision.chosen
    others = [
        c
        for c in decision.candidates
        if (c.task_id, c.start) != (chosen.task_id, chosen.start) and c.score != invalid_score()
    ]
    if not others:
        return None
    best = max(c.score for c in others)
    return chosen.score - best, {c.task_id for c in others if c.score == best}


def check_counterfactuals(recs: dict[str, Any], trace: Any) -> int:
    """Check every start action against its trace decision; return how many had an alternative."""
    by_task = {d.chosen.task_id: d for d in trace if d.chosen.task_id is not None}
    with_alternative = 0
    for task_id, action in start_actions(recs).items():
        assert task_id in by_task, task_id
        expected = expected_counterfactual(by_task[task_id])
        if expected is None:
            assert action["counterfactual"] is None, action
            continue
        delta, alternatives = expected
        counterfactual = action["counterfactual"]
        assert counterfactual is not None, action
        assert counterfactual["score_delta"] == pytest.approx(delta, abs=1e-9)
        assert counterfactual["alternative_task_id"] in alternatives
        with_alternative += 1
    return with_alternative


def test_counterfactuals_follow_the_rollout_trace(client: TestClient) -> None:
    rollout = planners()["rollout"]
    with engine("rollout", Recorder(inner=rollout)) as spy:
        created_plan(client)
        _, _, returned = spy.last

    recs = recommendations(client)

    assert returned.trace
    assert check_counterfactuals(recs, returned.trace) >= 1


def test_counterfactual_against_a_hand_written_trace(client: TestClient) -> None:
    m = model()
    invalid = invalid_score()
    picked: dict[str, Any] = {}

    def traced(problem: Any, inner_plan: Any) -> Any:
        first = min(a.start for a in inner_plan.assignments)
        a1, a2 = sorted(
            (a for a in inner_plan.assignments if a.start == first), key=lambda a: a.task_id
        )[:2]
        other = next(t.id for t in problem.tasks if t.id not in (a1.task_id, a2.task_id))
        chosen1 = m.Candidate(a1.task_id, first, 0.75)
        chosen2 = m.Candidate(a2.task_id, first, 0.6)
        trace = (
            m.Decision(
                at=first,
                candidates=(
                    chosen1,
                    m.Candidate(other, first, 0.5),
                    m.Candidate(None, first, 0.25),
                    m.Candidate(a2.task_id, first, invalid),
                ),
                chosen=chosen1,
            ),
            m.Decision(
                at=first,
                candidates=(chosen2, m.Candidate(other, first, invalid)),
                chosen=chosen2,
            ),
        )
        picked.update(a1=a1.task_id, a2=a2.task_id, other=other)
        return dataclasses.replace(inner_plan, engine="traced", trace=trace)

    with engine("traced", Recorder(inner=planners()["baseline"], make=traced)):
        created_plan(client, {"horizon_days": 30, "engine": "traced", "profile": "repro"})

    actions = start_actions(recommendations(client))

    one = actions[picked["a1"]]["counterfactual"]
    assert one is not None
    assert one["score_delta"] == pytest.approx(0.25, abs=1e-9)
    assert one["alternative_task_id"] == picked["other"]
    assert actions[picked["a2"]]["counterfactual"] is None


def test_a_plan_without_trace_is_explained_with_no_trace(client: TestClient) -> None:
    m = model()

    def untraced(_problem: Any, inner_plan: Any) -> Any:
        proof = m.Proof(kind="feasible", objective=inner_plan.proof.objective)
        return dataclasses.replace(inner_plan, engine="untraced", trace=(), proof=proof)

    with engine("untraced", Recorder(inner=planners()["baseline"], make=untraced)):
        plan = created_plan(client, {"horizon_days": 30, "engine": "untraced", "profile": "repro"})

    recs = recommendations(client)

    assert plan["proof"]["kind"] == "feasible"
    assert start_actions(recs)
    for action in start_actions(recs).values():
        assert "no_trace" in {reason["code"] for reason in action["why"]["reasons"]}, action
        assert action["counterfactual"] is None, action
    for text in [*strings(plan), *strings(recs)]:
        assert not OPTIMAL.search(text), text


# --------------------------------------------------------------------------- reliability


def reliabilities(client: TestClient) -> list[dict[str, Any]]:
    created_plan(client)
    found = [action["reliability"] for action in recommendations(client)["actions"]]
    assert found
    return found


def mentions(reliability: dict[str, Any], code: str, *names: str) -> bool:
    for factor in reliability["factors"]:
        text = json.dumps(factor)
        if (factor if isinstance(factor, str) else factor["code"]) == code and (
            not names or any(name in text for name in names)
        ):
            return True
    return False


def test_reliability_is_low_on_a_fictional_pack(client: TestClient) -> None:
    for reliability in reliabilities(client):
        assert reliability["level"] == "low", reliability
        assert "fictional_pack" in factor_codes(reliability), reliability


def test_reliability_is_high_with_confirmed_fresh_resources_on_a_real_pack(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    prepare(client, fictional=False)

    for reliability in reliabilities(client):
        assert reliability["level"] == "high", reliability


def test_reliability_is_low_with_an_estimated_consumed_resource(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, fictional=False, manual=manual_doc(status="estimated"))

    for reliability in reliabilities(client):
        assert reliability["level"] == "low", reliability
        assert mentions(reliability, "estimated_resource", "gold", "elixir"), reliability


def test_reliability_is_low_with_a_consumed_resource_older_than_7_days(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    prepare(client, fictional=False, manual=manual_doc(as_of=NOW - 7 * DAY - 1))

    for reliability in reliabilities(client):
        assert reliability["level"] == "low", reliability
        assert mentions(reliability, "stale_resource", "gold", "elixir"), reliability


def test_reliability_is_medium_otherwise(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    # Exactly 7 days old: not older than 7 days, not less than 7 days old either.
    prepare(client, fictional=False, manual=manual_doc(as_of=NOW - 7 * DAY))

    for reliability in reliabilities(client):
        assert reliability["level"] == "medium", reliability


# ------------------------------------------------------------------- fictional data and proof


def test_fictional_pack_is_flagged_and_no_estimate_is_shown_as_confirmed(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    manual = manual_doc()
    manual["resources"]["gold"]["status"] = "estimated"
    prepare(client, manual=manual)

    plan = created_plan(client)
    recs = recommendations(client)

    assert plan["pack"]["fictional"] is True
    assert fictional_flag(recs) is True
    for body in (plan, recs):
        for path, entry in provenances(body):
            if entry["source"] in ("model", "pack"):
                assert entry["status"] != "confirmed", (path, entry)
            if "gold" in path:
                assert entry["status"] != "confirmed", (path, entry)


def test_every_response_exposes_proof_kind_and_never_says_optimal_without_proof(
    client: TestClient,
) -> None:
    plan = created_plan(client)
    latest = client.get(f"/api/villages/{VILLAGE}/plans/latest").json()
    recs = recommendations(client)

    for body in (plan, latest, recs):
        kind = body["proof"]["kind"]
        assert kind in ("heuristic", "feasible", "optimal_window"), body["proof"]
        if kind in ("heuristic", "feasible"):
            for text in strings(body):
                assert not OPTIMAL.search(text), text
    assert plan["proof"]["kind"] == "heuristic"
