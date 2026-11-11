"""Unit tests of `compile_problem` beyond its acceptance tests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.ingest.state import ManualState, RunningJob, SnapshotItem, VillageSnapshot
from clashpilot.planning.compile import MissingInputError, compile_problem
from clashpilot.schedule.profile import ScheduleProfile

NOW = 1_781_078_400
TH, LAB, CANNON, TROOP, PET_HOUSE, PET = 100, 300, 400, 600, 700, 710


def item(data_id: int, **fields: Any) -> dict[str, Any]:
    return {
        "data_id": data_id,
        "name": f"Objet {data_id}",
        "kind": fields.get("kind", "building"),
        "village": fields.get("village", "home"),
        "lane": fields.get("lane", "builder"),
        "category": fields.get("category", "defense"),
        "tags": list(fields.get("tags", ())),
        "max_count_by_th": fields.get("max_count", {"1": 1}),
    }


def row(data_id: int, level: int, requires: Iterable[tuple[int, int]] = ()) -> dict[str, Any]:
    return {
        "data_id": data_id,
        "level": level,
        "duration_s": 100 * level,
        "cost": {"gold": level},
        "requires": [{"data_id": d, "level": lvl} for d, lvl in requires],
        "source": "s1",
    }


def pack(items: list[dict[str, Any]], levels: list[dict[str, Any]]) -> GamePack:
    return load_pack(
        {
            "schema_version": 1,
            "pack_id": "unit",
            "game_version": "0",
            "as_of": "2026-01-01",
            "fictional": True,
            "sources": [{"id": "s1", "label": "fictive", "retrieved_at": "2026-01-01"}],
            "items": [item(TH, tags=["town_hall"], category="town_hall"), *items],
            "levels": [row(TH, lvl) for lvl in (1, 2, 3)] + levels,
        }
    )


def village(
    *entries: tuple[int, int, int],
    running: Iterable[RunningJob] = (),
    builder_base: Iterable[tuple[int, int, int]] = (),
) -> VillageSnapshot:
    items = [SnapshotItem(d, lvl, n, "buildings", "home") for d, lvl, n in entries]
    items += [SnapshotItem(d, lvl, n, "buildings2", "builder") for d, lvl, n in builder_base]
    return VillageSnapshot(
        tag="UNIT",
        exported_at=NOW - 60,
        imported_at=NOW,
        items=tuple(items),
        running=tuple(running),
    )


def job(data_id: int, finish_at: int, to_level: int, *, extra: bool = False) -> RunningJob:
    return RunningJob(data_id, finish_at, to_level, "buildings", "home", extra=extra)


def manual(**changes: Any) -> ManualState:
    document: dict[str, Any] = {
        "resources": {"gold": {"value": 0, "status": "confirmed", "as_of": NOW}},
        "builders_total": 2,
    }
    document.update(changes)
    return ManualState.model_validate(document)


PROFILE = ScheduleProfile.model_validate(
    {
        "tz": "UTC",
        "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
        "exceptions": [],
        "policy": {"mode": "tolerance", "tolerance_minutes": 30, "max_logins_per_day": 4},
    }
)


def compiled(snapshot: VillageSnapshot, game: GamePack, **options: Any) -> Any:
    return compile_problem(
        snapshot, options.pop("typed", manual()), game, PROFILE, NOW, 2, **options
    )


def test_levels_needing_a_building_beyond_the_target_are_neither_created_nor_listed() -> None:
    game = pack(
        [
            item(LAB, tags=["laboratory"]),
            item(TROOP, kind="troop", lane="lab"),
        ],
        [row(LAB, lvl, [(TH, lvl)]) for lvl in (1, 2, 3)]
        + [row(TROOP, lvl, [(LAB, lvl)]) for lvl in (1, 2, 3)],
    )
    problem = compiled(village((TH, 1, 1), (LAB, 1, 1), (TROOP, 1, 1)), game)
    assert {task.id for task in problem.tasks} == {"100:0:2", "300:0:2", "600:0:2"}
    assert problem.excluded == ()


def test_the_schedule_policy_and_horizon_are_compiled() -> None:
    game = pack([item(CANNON)], [row(CANNON, lvl) for lvl in (1, 2)])
    problem = compiled(village((TH, 3, 1), (CANNON, 1, 1)), game)
    assert problem.horizon_end == NOW + 2 * 86_400
    assert (problem.policy.mode, problem.policy.tolerance_s, problem.policy.max_logins_per_day) == (
        "tolerance",
        1800,
        4,
    )
    # 2026-06-10T08:00Z: the night from 23:00 to 07:00 falls inside the horizon.
    assert problem.calendar.is_sleep(NOW + 15 * 3600)
    assert problem.calendar.can_act(NOW)


def test_running_jobs_get_their_own_slots_and_extra_ones_take_no_builder() -> None:
    game = pack([item(CANNON, max_count={"1": 4})], [row(CANNON, lvl) for lvl in (1, 2, 3)])
    snapshot = village(
        (TH, 3, 1),
        (CANNON, 1, 4),
        running=[
            job(CANNON, NOW + 500, 2),
            job(CANNON, NOW + 100, 2),
            job(CANNON, NOW + 300, 2, extra=True),
        ],
    )
    problem = compiled(snapshot, game)
    frozen = {(j.task_id, j.lane_slot, j.end, j.extra) for j in problem.frozen}
    # Running instances come first, by increasing end: ranks 0, 1 and 2.
    assert frozen == {
        ("400:0:2", 0, NOW + 100, False),
        ("400:1:2", 2, NOW + 300, True),
        ("400:2:2", 1, NOW + 500, False),
    }
    assert {task.id for task in problem.tasks} == {
        "400:0:3",
        "400:1:3",
        "400:2:3",
        "400:3:2",
        "400:3:3",
    }
    started = {j.task_id: j.start for j in problem.frozen}
    assert started["400:0:2"] == NOW + 100 - 200
    assert started["400:2:2"] == NOW  # min(now, finish_at - duration)


def test_a_lane_is_owned_by_the_building_its_levels_require() -> None:
    game = pack(
        [item(PET_HOUSE), item(PET, kind="pet", lane="pet_house")],
        [row(PET_HOUSE, 1), row(PET, 1, [(PET_HOUSE, 1)]), row(PET, 2, [(PET_HOUSE, 1)])],
    )
    with_house = compiled(village((TH, 3, 1), (PET_HOUSE, 1, 1), (PET, 1, 1)), game)
    assert with_house.lanes == {"builder": 2, "pet_house": 1}
    without = compiled(village((TH, 3, 1)), game)
    assert "pet_house" not in without.lanes


def test_every_missing_input_is_named_at_once() -> None:
    game = pack([item(CANNON)], [row(CANNON, lvl) for lvl in (1, 2)])
    typed = manual(resources={}, builders_total=None)
    with pytest.raises(MissingInputError) as caught:
        compile_problem(village((TH, 3, 1), (CANNON, 1, 1)), typed, game, None, NOW, 2)
    assert caught.value.missing == ("gold", "schedule_profile", "builders_total")


def test_the_builder_base_is_not_planned() -> None:
    game = pack([item(CANNON)], [row(CANNON, lvl) for lvl in (1, 2)])
    problem = compiled(village((TH, 3, 1), builder_base=[(CANNON, 1, 1)]), game, target_th=3)
    # The home village has no cannon: one is built from level 0, the builder base one is ignored.
    assert {task.id for task in problem.tasks} == {"400:0:1", "400:0:2"}


def test_a_requirement_on_a_level_that_never_exists_is_a_missing_prerequisite() -> None:
    game = pack(
        [item(CANNON), item(TROOP, kind="troop", lane="lab")],
        [row(CANNON, 1), row(CANNON, 2, [(TROOP, 5)]), row(CANNON, 3), row(TROOP, 1)],
    )
    problem = compiled(village((TH, 3, 1), (CANNON, 1, 1), (TROOP, 1, 1)), game)
    reasons = {(e.data_id, e.to_level, e.reason) for e in problem.excluded}
    assert reasons == {(CANNON, 2, "missing_prerequisite"), (CANNON, 3, "missing_prerequisite")}
    assert problem.tasks == ()


def test_a_running_town_hall_sets_the_default_target_after_it() -> None:
    game = load_pack(
        {
            "schema_version": 1,
            "pack_id": "unit",
            "game_version": "0",
            "as_of": "2026-01-01",
            "fictional": True,
            "sources": [{"id": "s1", "label": "fictive", "retrieved_at": "2026-01-01"}],
            "items": [item(TH, tags=["town_hall"], category="town_hall"), item(CANNON)],
            "levels": [row(TH, lvl) for lvl in (1, 2, 3, 4)]
            + [row(CANNON, lvl, [(TH, lvl)]) for lvl in (1, 2, 3, 4)],
        }
    )
    snapshot = village((TH, 1, 1), (CANNON, 1, 1), running=[job(TH, NOW + 50, 2)])
    problem = compiled(snapshot, game)
    assert [j.task_id for j in problem.frozen] == ["100:0:2"]
    tasks = {task.id: task for task in problem.tasks}
    assert set(tasks) == {"100:0:3", "400:0:2", "400:0:3"}
    assert tasks["100:0:3"].requires == ("100:0:2",)
    assert tasks["400:0:2"].requires == ("100:0:2",)
