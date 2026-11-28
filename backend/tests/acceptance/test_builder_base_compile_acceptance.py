"""Acceptance tests for E30: compile the builder base as a village of its own.

SPEC (builder base backlog): architecture "un problème par village", ADR-BB1 (one problem per
village, same lane names), ADR-BB2 (the builder hall carries the tag `town_hall` in its village),
ADR-BB3 (`base` explicit, `home` by default), ADR-BB4 (nothing is guessed). Frozen with the task.

Shapes that the task text leaves open are fixed here:

- `compile_problem` takes `base` as a keyword argument, `"home"` or `"builder"`; without it the
  problem is the one of the home village, exactly as before this task. "As before" is read from
  `backend/tests/fixtures/problems/home_problems_before_builder_base.json`, written by the code of
  `main` before E30 (every field of a dataclass listed there must keep its value; a field added
  later to a dataclass is not judged).
- `ManualState` gains `builder_base_builders` (int >= 0 or null, absent = null) and its `Resource`
  gains `builder_gold` and `builder_elixir` (resources, farm rates and storage overrides).
  `ManualEvent` gains `base` (`"home"` by default, or `"builder"`). `GET`/`PUT .../manual` echo
  `builder_base_builders` and the `base` of each event.
- The pack accepts the cost resources `builder_gold` and `builder_elixir`: a level correction
  (`PATCH .../items/{data_id}/levels/{level}`) costing them is accepted.
- `GET .../state` carries `builder_base_builders`, a provenance object like `builders`: typed in =
  the typed value with status `confirmed` and source `manual`; otherwise status `estimated` and
  the largest number of upgrades running at once on the builder lane of the builder village in the
  export (lane read from the pack, so a running research or a home job does not count), at
  least 1. Its `resources` keep the seven home resources only (frozen E09 test).
- The builder base capacity never reads `builders_total` nor the builder huts.
- Resources of a village: those its pack items cost or store. The builder problem holds no `gold`
  nor `elixir` (not in `initial`, `cap` nor `income_per_hour`) and does not ask for them.

Fictional data only (ADR-2): the pack `fixtures/packs/fictional_pack_builder_base.json` and the
export `fixtures/exports/fictional_export_builder_base.json` (exported at `NOW - 1 h`). The home
village: a town hall 1, two huts, a laboratory, a cannon running to level 2 (1 day) and a troop.
The builder village: a builder hall 1 (tag `town_hall`, stores 100 000 builder gold and builder
elixir), one hut, a star laboratory (tag `laboratory`), two defenses running to level 2 (1 and
2 days), a hero running to level 2 (3 days, builder lane) and a troop researching level 2 (3 h,
lab lane). A defense unlocks at builder hall 2.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures"
PACK = FIXTURES / "packs" / "fictional_pack_builder_base.json"
EXPORT = FIXTURES / "exports" / "fictional_export_builder_base.json"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"
V1_PACK = FIXTURES / "packs" / "fictional_pack_v1.json"
V1_EXPORT = FIXTURES / "exports" / "fictional_export_2026-11.json"
GOLDEN = FIXTURES / "problems" / "home_problems_before_builder_base.json"

NOW = 1_795_852_800  # 2026-11-28T08:00:00Z
EXPORTED_AT = NOW - HOUR
CRITICAL_NOW = 1_795_766_400  # 2026-11-27T08:00:00Z, one hour after the critical export
VILLAGE = "FICTIFOUVR"

HOME_TH, HOME_HUT, HOME_LAB, HOME_CANNON, HOME_TROOP = (
    9300001,
    9300002,
    9300003,
    9300004,
    9300005,
)
HALL, HUT, STAR_LAB, BUBBLE, ROTOR, GRUNT, KEEPER, HAIL = (
    9400001,
    9400002,
    9400003,
    9400004,
    9400005,
    9400006,
    9400007,
    9400009,
)
HOME_IDS = {HOME_TH, HOME_HUT, HOME_LAB, HOME_CANNON, HOME_TROOP}
BUILDER_IDS = {HALL, HUT, STAR_LAB, BUBBLE, ROTOR, GRUNT, KEEPER, HAIL}
BUILDER_RESOURCES = {"builder_gold", "builder_elixir"}
HOME_RESOURCES = {"gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"}


def task(data_id: int, level: int) -> str:
    return f"{data_id}:0:{level}"


BUILDER_TASKS = {
    task(HALL, 2),
    task(STAR_LAB, 2),
    task(BUBBLE, 3),
    task(BUBBLE, 4),
    task(ROTOR, 3),
    task(GRUNT, 3),
    task(KEEPER, 3),
    task(HAIL, 1),
}
# The builder base tasks that depend on no builder hall upgrade.
BUILDER_CURRENT = {task(BUBBLE, 3), task(ROTOR, 3), task(GRUNT, 3)}
# (task id, lane, end) of each running upgrade of the builder village.
BUILDER_FROZEN = {
    (task(BUBBLE, 2), "builder", EXPORTED_AT + DAY),
    (task(ROTOR, 2), "builder", EXPORTED_AT + 2 * DAY),
    (task(KEEPER, 2), "builder", EXPORTED_AT + 3 * DAY),
    (task(GRUNT, 2), "lab", EXPORTED_AT + 3 * HOUR),
}


# --------------------------------------------------------------------------- inputs


def pack_doc(path: Path = PACK) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def export_doc(path: Path = EXPORT) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def game(doc: Mapping[str, Any] | None = None) -> Any:
    from clashpilot.gamedata.pack import load_pack

    return load_pack(doc if doc is not None else pack_doc())


def snapshot(doc: Mapping[str, Any] | None = None) -> Any:
    from clashpilot.ingest.export import parse_export

    return parse_export(dict(doc if doc is not None else export_doc()), NOW)


def stock(value: int, as_of: int = NOW) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": as_of}


def manual_doc(*resources: str, **extra: Any) -> dict[str, Any]:
    names = resources or ("gold", "elixir", "builder_gold", "builder_elixir")
    return {"resources": {name: stock(50_000) for name in names}, **extra}


def manual(*resources: str, **extra: Any) -> Any:
    from clashpilot.ingest.state import ManualState

    return ManualState.model_validate(manual_doc(*resources, **extra))


def profile_doc() -> dict[str, Any]:
    return {
        "tz": "UTC",
        "weekly": [],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def profile() -> Any:
    from clashpilot.schedule.profile import ScheduleProfile

    return ScheduleProfile.model_validate(profile_doc())


def compile_village(
    typed: Any = None,
    *,
    pack: Any = None,
    export: Mapping[str, Any] | None = None,
    now: int = NOW,
    **base: Any,
) -> Any:
    """`compile_problem` on the fixtures, 90 days; `base` is passed only when given."""
    from clashpilot.planning.compile import compile_problem

    return compile_problem(
        snapshot(export),
        typed if typed is not None else manual(),
        pack if pack is not None else game(),
        profile(),
        now,
        90,
        **base,
    )


def builder_problem(typed: Any = None, export: Mapping[str, Any] | None = None) -> Any:
    return compile_village(typed, export=export, base="builder")


def event(label: str, lane: str, duration_pct: int, **base: Any) -> dict[str, Any]:
    return {
        "label": label,
        "start": NOW,
        "end": NOW + DAY,
        "lane": lane,
        "duration_pct": duration_pct,
        "cost_pct": 100,
        **base,
    }


def without_builder_section(doc: dict[str, Any], *data_ids: int) -> dict[str, Any]:
    """The export without the builder village entries of `data_ids`."""
    out = copy.deepcopy(doc)
    for section in ("buildings2", "units2", "heroes2"):
        out[section] = [entry for entry in out.get(section, []) if entry["data"] not in data_ids]
    return out


def without_builder_timers(doc: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    for section in ("buildings2", "units2", "heroes2"):
        for entry in out.get(section, []):
            entry.pop("timer", None)
    return out


# --------------------------------------------------------------------------- reading a problem


def ident(item: Any) -> str:
    return item if isinstance(item, str) else str(item.id)


def ids(problem: Any) -> set[str]:
    return {one.id for one in problem.tasks}


def data_ids(problem: Any) -> set[int]:
    found = {one.data_id for one in problem.tasks}
    found |= {int(job.task_id.split(":")[0]) for job in problem.frozen}
    found |= {one.data_id for one in problem.excluded}
    return found


def frozen_facts(problem: Any) -> set[tuple[str, str, int]]:
    return {(job.task_id, job.lane, job.end) for job in problem.frozen}


def plain(value: Any) -> Any:
    """A JSON-like copy of a problem; a dataclass keeps its type name under `__type__`."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {"__type__": type(value).__name__}
        for one in dataclasses.fields(value):
            out[one.name] = plain(getattr(value, one.name))
        return out
    if isinstance(value, Mapping):
        return {str(key): plain(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [plain(one) for one in value]
    return value


def assert_matches(actual: Any, expected: Any, where: str = "problem") -> None:
    """`actual` holds every value of `expected`; dataclass fields added since are not judged."""
    if isinstance(expected, dict) and "__type__" in expected:
        assert isinstance(actual, dict), where
        for key, value in expected.items():
            assert key in actual, f"{where}.{key} is missing"
            assert_matches(actual[key], value, f"{where}.{key}")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict), where
        assert set(actual) == set(expected), f"{where}: keys {sorted(actual)} != {sorted(expected)}"
        for key, value in expected.items():
            assert_matches(actual[key], value, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), where
        assert len(actual) == len(expected), f"{where}: {len(actual)} != {len(expected)} entries"
        for index, (one, other) in enumerate(zip(actual, expected, strict=True)):
            assert_matches(one, other, f"{where}[{index}]")
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


# --------------------------------------------------------------------------- the inputs "of today"


def golden_cases() -> dict[str, dict[str, Any]]:
    """Inputs valid before E30, by case name: (pack, export, manual, now)."""
    critical_export = export_doc(CRITICAL_EXPORT)
    critical_manual = {
        "resources": {name: stock(1_000_000, CRITICAL_NOW) for name in ("gold", "elixir")}
        | {"dark_elixir": stock(1_000_000, CRITICAL_NOW)},
    }
    builder_manual = {
        "resources": {name: stock(50_000) for name in ("gold", "elixir")},
        "farm_rate": {"gold": {"low": 0, "expected": 1000, "high": 2000}},
        "events": [event("Bonus fictif", "builder", 50)],
    }
    return {
        "builder_base_fixture": {
            "pack": pack_doc(),
            "export": export_doc(),
            "manual": builder_manual,
            "now": NOW,
        },
        "critical_fixture": {
            "pack": pack_doc(CRITICAL_PACK),
            "export": critical_export,
            "manual": critical_manual,
            "now": CRITICAL_NOW,
        },
    }


def compile_case(case: Mapping[str, Any], **base: Any) -> Any:
    from clashpilot.ingest.state import ManualState

    return compile_village(
        ManualState.model_validate(case["manual"]),
        pack=game(case["pack"]),
        export=case["export"],
        now=case["now"],
        **base,
    )


# --------------------------------------------------------------------------- HTTP fixture


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def client(tmp_path: Path) -> Iterable[TestClient]:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    with TestClient(create_app(settings, clock=Clock(NOW))) as test_client:
        yield test_client


def activate(client: TestClient, doc: Mapping[str, Any] | None = None) -> None:
    pack = doc if doc is not None else pack_doc()
    response = client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text


def import_export(client: TestClient, doc: Mapping[str, Any] | None = None) -> None:
    response = client.post(
        "/api/villages/import",
        content=json.dumps(doc if doc is not None else export_doc()).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE


def put_manual(client: TestClient, doc: Mapping[str, Any]) -> Any:
    return client.put(f"/api/villages/{VILLAGE}/manual", json=doc)


def get_state(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/villages/{VILLAGE}/state")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ================================================== 1. one problem per base


def test_builder_problem_holds_only_the_builder_village() -> None:
    problem = builder_problem()

    assert ids(problem) == BUILDER_TASKS
    assert data_ids(problem) <= BUILDER_IDS
    assert not data_ids(problem) & HOME_IDS
    assert frozen_facts(problem) == BUILDER_FROZEN


def test_builder_problem_holds_only_builder_storage_and_resources() -> None:
    problem = builder_problem()
    resources = problem.resources

    assert dict(resources.cap) == {"builder_gold": 100_000, "builder_elixir": 100_000}
    assert set(resources.initial) == BUILDER_RESOURCES
    assert resources.initial["builder_gold"] == 50_000
    assert set(resources.income_per_hour) <= BUILDER_RESOURCES
    for one in problem.tasks:
        assert set(one.cost) <= BUILDER_RESOURCES, one.id


def test_home_settings_do_not_leak_into_the_builder_problem() -> None:
    typed = manual(
        storage_override={"gold": 123, "builder_gold": 70_000},
        farm_rate={
            "gold": {"low": 1, "expected": 2, "high": 3},
            "builder_elixir": {"low": 10, "expected": 20, "high": 30},
        },
    )
    problem = builder_problem(typed)

    assert dict(problem.resources.cap) == {"builder_gold": 70_000, "builder_elixir": 100_000}
    assert problem.resources.income_per_hour.get("builder_elixir") == 20
    assert "gold" not in problem.resources.income_per_hour


def test_builder_problem_needs_only_builder_resources() -> None:
    from clashpilot.planning.compile import MissingInputError

    problem = builder_problem(manual("builder_gold", "builder_elixir"))
    assert ids(problem) == BUILDER_TASKS

    with pytest.raises(MissingInputError) as raised:
        builder_problem(manual("gold", "elixir", "builder_elixir"))
    assert "builder_gold" in raised.value.missing
    assert "gold" not in raised.value.missing
    assert "builders_total" not in raised.value.missing


def test_home_problem_keeps_the_home_village_only() -> None:
    for problem in (compile_village(), compile_village(base="home")):
        assert data_ids(problem) <= HOME_IDS
        assert frozen_facts(problem) == {(task(HOME_CANNON, 2), "builder", EXPORTED_AT + DAY)}
        assert dict(problem.resources.cap) == {"gold": 100_000, "elixir": 100_000}
        assert problem.lanes["builder"] == 2  # the two home huts


@pytest.mark.parametrize("name", ["builder_base_fixture", "critical_fixture"])
def test_home_problem_without_base_is_unchanged(name: str) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]

    assert_matches(plain(compile_case(golden_cases()[name])), golden)


@pytest.mark.parametrize("name", ["builder_base_fixture", "critical_fixture"])
def test_home_base_is_the_default(name: str) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))[name]
    case = golden_cases()[name]

    home = compile_case(case, base="home")
    assert_matches(plain(home), golden)
    assert plain(home) == plain(compile_case(case))


def test_a_pack_without_builder_items_gives_an_empty_problem_or_a_clear_error() -> None:
    from clashpilot.planning.compile import MissingInputError

    typed = manual("gold", "elixir", "dark_elixir", builders_total=5)
    try:
        problem = compile_village(
            typed, pack=game(pack_doc(V1_PACK)), export=export_doc(V1_EXPORT), base="builder"
        )
    except MissingInputError:
        return
    assert problem.tasks == ()
    assert not data_ids(problem) & {item["data_id"] for item in pack_doc(V1_PACK)["items"]}


# ================================================== 2. lanes of the builder base


def test_builders_and_star_laboratory_are_the_builder_and_lab_lanes() -> None:
    problem = builder_problem()

    assert problem.lanes["builder"] == 3
    assert problem.lanes["lab"] == 1
    lanes = {one.id: one.lane for one in problem.tasks}
    assert lanes[task(GRUNT, 3)] == "lab"
    assert lanes[task(HALL, 2)] == "builder"
    assert lanes[task(KEEPER, 3)] == "builder"


def test_lab_lane_needs_the_star_laboratory_not_the_home_one() -> None:
    doc = without_builder_section(export_doc(), STAR_LAB, GRUNT)
    problem = builder_problem(export=doc)

    assert problem.lanes.get("lab", 0) == 0
    assert compile_village(export=doc).lanes["lab"] == 1  # the home laboratory still stands


# ================================================== 3. pack resources


def test_pack_with_builder_resources_loads() -> None:
    pack = game()

    hall = pack.level(HALL, 2)
    assert hall is not None and hall.cost == {"builder_gold": 50_000}
    assert {row.resource for row in pack.data.storage} == {
        "gold",
        "elixir",
        "builder_gold",
        "builder_elixir",
    }


def test_pack_accepts_builder_resources_in_a_level_correction(client: TestClient) -> None:
    activate(client)
    doc = pack_doc()

    for resource in sorted(BUILDER_RESOURCES):
        response = client.patch(
            f"/api/gamedata/packs/{doc['pack_id']}/items/{BUBBLE}/levels/3",
            json={"cost": {resource: 7_000}},
        )
        assert response.status_code == 200, response.text
        level = next(row for row in response.json()["levels"] if row["level"] == 3)
        assert level["cost"] == {resource: 7_000}, level

    response = client.patch(
        f"/api/gamedata/packs/{doc['pack_id']}/items/{BUBBLE}/levels/3",
        json={"cost": {"moon_dust": 1}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_override"


def test_pack_without_builder_resources_loads_as_before() -> None:
    from clashpilot.gamedata.pack import content_sha256

    for path in (V1_PACK, CRITICAL_PACK):
        doc = pack_doc(path)
        pack = game(doc)
        assert pack.pack_id == doc["pack_id"]
        assert len(pack.data.items) == len(doc["items"])
        assert len(pack.data.levels) == len(doc["levels"])
        assert content_sha256(pack.data.model_dump(mode="json", exclude_unset=True)) == (
            content_sha256(doc)
        )


# ================================================== 4. manual input


def test_manual_state_accepts_builder_resources_and_builders() -> None:
    typed = manual(
        builder_base_builders=2,
        farm_rate={"builder_gold": {"low": 1, "expected": 2, "high": 3}},
        storage_override={"builder_elixir": 9},
    )

    assert typed.builder_base_builders == 2
    assert typed.resources["builder_gold"].value == 50_000
    assert typed.resources["builder_elixir"].value == 50_000
    assert typed.farm_rate["builder_gold"].expected == 2
    assert typed.storage_override["builder_elixir"] == 9


def test_builder_base_builders_absent_is_unknown_never_zero() -> None:
    from clashpilot.ingest.state import ManualState

    assert ManualState().builder_base_builders is None
    assert manual().builder_base_builders is None
    assert "builder_gold" not in manual("gold").resources
    assert ManualState.model_validate({"builder_base_builders": 0}).builder_base_builders == 0


@pytest.mark.parametrize("value", [-1, "2", 1.5, True])
def test_builder_base_builders_is_a_non_negative_integer(value: Any) -> None:
    from pydantic import ValidationError

    from clashpilot.ingest.state import ManualState

    with pytest.raises(ValidationError):
        ManualState.model_validate({"builder_base_builders": value})


def test_manual_route_round_trips_the_builder_inputs(client: TestClient) -> None:
    activate(client)
    import_export(client)
    doc = manual_doc(
        builder_base_builders=4,
        events=[event("Sans village", "builder", 50), event("Base", "lab", 80, base="builder")],
    )

    response = put_manual(client, doc)
    assert response.status_code == 200, response.text
    body = client.get(f"/api/villages/{VILLAGE}/manual").json()

    assert body["builder_base_builders"] == 4
    assert body["resources"]["builder_gold"]["value"] == 50_000
    assert body["resources"]["builder_elixir"]["value"] == 50_000
    assert [one["base"] for one in body["events"]] == ["home", "builder"]

    response = put_manual(client, manual_doc())
    assert response.status_code == 200, response.text
    assert client.get(f"/api/villages/{VILLAGE}/manual").json()["builder_base_builders"] is None

    for wrong in (-1, "4"):
        response = put_manual(client, manual_doc(builder_base_builders=wrong))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_manual_state"


def test_untyped_builder_resource_is_never_counted_as_zero() -> None:
    from clashpilot.planning.compile import MissingInputError

    with pytest.raises(MissingInputError) as raised:
        builder_problem(manual("gold", "elixir"))
    assert {"builder_gold", "builder_elixir"} <= set(raised.value.missing)


# ================================================== 5. builder base capacity


def test_builder_capacity_is_estimated_from_the_running_builder_upgrades() -> None:
    # Three builder-lane upgrades run in the builder village (two defenses and the hero); the
    # research on the lab lane, the home job and the single builder village hut do not count.
    assert builder_problem().lanes["builder"] == 3
    # The home builders are another village's.
    assert builder_problem(manual(builders_total=7)).lanes["builder"] == 3


def test_builder_capacity_estimate_is_at_least_one() -> None:
    doc = without_builder_section(without_builder_timers(export_doc()), HUT)
    problem = builder_problem(export=doc)

    assert problem.lanes["builder"] == 1
    assert problem.frozen == ()


def test_typed_builder_base_builders_wins() -> None:
    assert builder_problem(manual(builder_base_builders=4)).lanes["builder"] == 4
    # Typing the builder base count leaves the home village alone.
    assert compile_village(manual(builder_base_builders=4)).lanes["builder"] == 2


def test_state_shows_the_estimated_then_the_typed_builder_base_builders(
    client: TestClient,
) -> None:
    activate(client)
    import_export(client)

    estimated = get_state(client)["builder_base_builders"]
    assert estimated["value"] == 3
    assert estimated["status"] == "estimated"

    response = put_manual(client, manual_doc(builder_base_builders=2, builders_total=6))
    assert response.status_code == 200, response.text
    state = get_state(client)
    typed = state["builder_base_builders"]
    assert typed["value"] == 2
    assert typed["status"] == "confirmed"
    assert typed["source"] == "manual"
    assert state["builders"]["value"] == 6
    assert set(state["resources"]) == HOME_RESOURCES


def test_state_estimate_is_at_least_one(client: TestClient) -> None:
    activate(client)
    import_export(client, without_builder_timers(export_doc()))

    estimated = get_state(client)["builder_base_builders"]
    assert estimated["value"] == 1
    assert estimated["status"] == "estimated"


# ================================================== 6. events per base


def test_event_base_defaults_to_home() -> None:
    from pydantic import ValidationError

    from clashpilot.ingest.state import ManualEvent

    assert ManualEvent.model_validate(event("Sans village", "builder", 50)).base == "home"
    assert ManualEvent.model_validate(event("Base", "builder", 50, base="builder")).base == (
        "builder"
    )
    with pytest.raises(ValidationError):
        ManualEvent.model_validate(event("Lune", "builder", 50, base="moon"))


def test_events_apply_to_their_own_base_only() -> None:
    typed = manual(
        events=[event("Sans village", "builder", 50), event("Base", "lab", 80, base="builder")]
    )

    home = compile_village(typed)
    assert [(m.lane, m.duration_pct) for m in home.modifiers] == [("builder", 50)]
    assert compile_village(typed, base="home").modifiers == home.modifiers

    builder = builder_problem(typed)
    assert [(m.lane, m.duration_pct) for m in builder.modifiers] == [("lab", 80)]


# ================================================== 7. builder hall as target


def test_builder_hall_is_the_critical_path_target() -> None:
    from clashpilot.planning.critical import critical_path, current_level_ids

    problem = builder_problem()

    assert ident(critical_path(problem).target) == task(HALL, 2)
    assert {ident(one) for one in current_level_ids(problem.tasks)} == BUILDER_CURRENT
    # The home problem still aims at the home town hall.
    assert ident(critical_path(compile_village()).target) == task(HOME_TH, 2)


def test_finish_th_makes_the_builder_hall_wait_for_the_builder_current_level() -> None:
    from clashpilot.planning.strategies import apply_strategy, load_strategies

    applied, _ = apply_strategy(builder_problem(), load_strategies()["finish_th"])
    tasks = {one.id: one for one in applied.tasks}
    required = set(tasks[task(HALL, 2)].requires)

    assert required == BUILDER_CURRENT
    assert {int(name.split(":")[0]) for name in required} <= BUILDER_IDS
    assert not {int(name.split(":")[0]) for name in required} & HOME_IDS


# ================================================== 8. builder base frozen jobs


def test_builder_frozen_jobs_come_from_the_builder_sections() -> None:
    problem = builder_problem()
    sections = {job.data_id: job.section for job in snapshot().running if job.village == "builder"}

    assert frozen_facts(problem) == BUILDER_FROZEN
    assert {sections[int(job.task_id.split(":")[0])] for job in problem.frozen} == {
        "buildings2",
        "units2",
        "heroes2",
    }
    for job in problem.frozen:
        assert job.start <= NOW < job.end, job
        assert not job.extra, job


def test_builder_frozen_jobs_take_the_slots_of_their_lane() -> None:
    from clashpilot.planning.model import Assignment, Plan, Proof
    from clashpilot.planning.simulate import simulate

    problem = builder_problem()
    builder_slots = sorted(job.lane_slot for job in problem.frozen if job.lane == "builder")
    assert builder_slots == [0, 1, 2]
    assert [job.lane_slot for job in problem.frozen if job.lane == "lab"] == [0]

    def plan(slot: int, start: int) -> Any:
        assignment = Assignment(task(HALL, 2), "builder", slot, start, start + 2 * DAY)
        return Plan((assignment,), "test", Proof(kind="heuristic", objective=0.0))

    assert simulate(problem, Plan((), "test", Proof(kind="heuristic", objective=0.0))).valid
    ends = {job.lane_slot: job.end for job in problem.frozen if job.lane == "builder"}
    for slot in range(3):
        # The three builders are busy at `now`: the hall waits for the end of its slot's job.
        result = simulate(problem, plan(slot, NOW))
        assert not result.valid, slot
        assert "lane_capacity" in {v.code for v in result.violations}, result.violations
        assert simulate(problem, plan(slot, ends[slot])).valid, (slot, ends[slot])
