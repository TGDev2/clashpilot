"""Unit tests of the pure scenario overlays (`clashpilot.planning.scenarios`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.planning.model import FrozenJob, Plan, Policy, Proof, ResourceModel
from clashpilot.planning.scenarios import (
    RESERVED_PREFIX,
    InfeasibleError,
    apply_overlays,
    earliest_start,
    figures,
    unsupported,
)
from support.problems import DAY, HOUR, NOW, make_calendar, make_problem, make_task

ITEMS_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_items.json"
)
CANNON = "9000004:0:3"  # a building of the items pack
TROOP = "9000007:0:2"  # a troop of the items pack (lab)


def pack() -> GamePack:
    return load_pack(json.loads(ITEMS_PACK.read_text(encoding="utf-8")))


def problem(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "tasks": (
            make_task(CANNON, cost={"gold": 5000}),
            make_task(TROOP, lane="lab", cost={"elixir": 100}),
        ),
        "lanes": {"builder": 2, "lab": 1},
        "resources": ResourceModel(
            initial={"gold": 1000, "elixir": 5000},
            cap={"gold": 20_000, "elixir": 20_000},
            income_per_hour={"gold": 1000, "elixir": 1000},
        ),
    }
    fields.update(overrides)
    return make_problem(**fields)


def task_of(prob: Any, name: str) -> Any:
    return next(t for t in prob.tasks if t.id == name)


def test_earliest_start_waits_for_the_income_to_cover_the_cost() -> None:
    prob = problem()
    assignment = earliest_start(prob, task_of(prob, CANNON), NOW)
    # 4000 gold missing at 1000 per hour.
    assert assignment.start == NOW + 4 * HOUR
    assert assignment.end == NOW + 5 * HOUR


def test_earliest_start_waits_for_a_free_slot_and_the_end_of_the_sleep() -> None:
    running = FrozenJob("9000003:0:2", "builder", 0, NOW - HOUR, NOW + 2 * HOUR)
    prob = problem(
        lanes={"builder": 1, "lab": 1},
        frozen=(running,),
        resources=ResourceModel({"gold": 9000}, {"gold": 20_000}, {"gold": 0}),
        calendar=make_calendar(sleep=[(NOW + 2 * HOUR, NOW + 3 * HOUR)]),
    )
    assignment = earliest_start(prob, task_of(prob, CANNON), NOW)
    assert assignment.start == NOW + 3 * HOUR
    assert assignment.lane_slot == 0


def test_earliest_start_is_infeasible_without_income_or_with_a_pending_prerequisite() -> None:
    poor = problem(resources=ResourceModel({"gold": 1000}, {}, {}))
    with pytest.raises(InfeasibleError) as caught:
        earliest_start(poor, task_of(poor, CANNON), NOW)
    assert caught.value.reason.code == "insufficient_funds"

    chained = problem(
        tasks=(
            make_task("9000004:0:2", cost={"gold": 10}),
            make_task(CANNON, cost={"gold": 10}, requires=("9000004:0:2",)),
        )
    )
    with pytest.raises(InfeasibleError) as caught:
        earliest_start(chained, task_of(chained, CANNON), NOW)
    assert caught.value.reason.code == "missing_prerequisite"


def test_start_now_fixes_the_task_as_a_frozen_job_and_pays_it() -> None:
    prob = problem()
    fixed, forced = apply_overlays(prob, pack(), [{"type": "start_now", "task_id": CANNON}])
    assert [t.id for t in forced] == [CANNON]
    assert CANNON not in {t.id for t in fixed.tasks}
    job = next(j for j in fixed.frozen if j.task_id == CANNON)
    assert (job.start, job.end) == (NOW + 4 * HOUR, NOW + 5 * HOUR)
    assert fixed.resources.initial["gold"] == 1000 - 5000

    plan = Plan((), "test", Proof("heuristic", 0.0))
    figured = figures(fixed, plan, pack(), {"kind": "heuristic"}, forced)
    assert figured["finished_count"] == 1
    assert figured["goal_finish"] is None  # no goal and no planned task


def test_reserve_builder_takes_the_first_free_slots_and_hides_the_reservations() -> None:
    running = FrozenJob("9000003:0:2", "builder", 0, NOW - HOUR, NOW + HOUR)
    prob = problem(frozen=(running,))
    until = NOW + DAY
    reserved, _ = apply_overlays(
        prob, pack(), [{"type": "reserve_builder", "count": 1, "until": until}]
    )
    held = [j for j in reserved.frozen if j.task_id.startswith(RESERVED_PREFIX)]
    assert [(j.lane, j.lane_slot, j.start, j.end) for j in held] == [("builder", 1, NOW, until)]

    plan = Plan((), "test", Proof("heuristic", 0.0))
    idle = figures(reserved, plan, pack(), {"kind": "heuristic"})["idle_builder_s"]
    horizon = reserved.horizon_end - NOW
    # Two builders minus the reservation, minus the running job's hour.
    assert idle == 2 * horizon - (until - NOW) - HOUR


def test_an_absence_is_sleep_only_under_the_strict_policy() -> None:
    absence = {
        "type": "absence",
        "start_local": "2026-06-10T10:00:00",
        "end_local": "2026-06-11T10:00:00",
    }
    loose, _ = apply_overlays(problem(), pack(), [absence])
    window = (NOW + 2 * HOUR, NOW + 2 * HOUR + DAY)  # the calendar zone is UTC
    assert loose.calendar.absence == [window]
    assert loose.calendar.sleep == []

    strict, _ = apply_overlays(problem(), pack(), [{"type": "policy", "mode": "strict"}, absence])
    assert strict.policy == Policy(mode="strict")
    assert strict.calendar.sleep == [window]


def test_a_builder_potion_shortens_the_running_builder_jobs_only() -> None:
    builder = FrozenJob("9000003:0:2", "builder", 0, NOW - HOUR, NOW + 2 * HOUR)
    lab = FrozenJob("9000006:0:2", "lab", 0, NOW - HOUR, NOW + 2 * HOUR)
    prob = problem(frozen=(builder, lab))
    potion = {"type": "use_item", "item": "builder_potion", "task_id": None, "at": NOW}
    sped, forced = apply_overlays(prob, pack(), [potion])
    assert forced == ()
    ends = {j.task_id: j.end for j in sped.frozen}
    # 7200 s left, multiplier 10 over 3600 s: 720 s.
    assert ends == {"9000003:0:2": NOW + 720, "9000006:0:2": NOW + 2 * HOUR}


def test_a_use_item_without_its_rule_is_unsupported() -> None:
    overlays = [
        {"type": "use_item", "item": "book_of_heroes", "task_id": CANNON, "at": NOW},
        {"type": "use_item", "item": "builder_potion", "task_id": None, "at": NOW},
    ]
    reasons = unsupported(pack(), overlays)
    assert [r.code for r in reasons] == ["unsupported_item"]
    assert "book_of_heroes" in reasons[0].message
