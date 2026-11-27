"""Unit tests of the town hall launch comparator's pure steps (`clashpilot.planning.town_hall`) and
of the scenario overlay `start_at`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clashpilot.gamedata.pack import GamePack, load_pack
from clashpilot.planning.model import Assignment, FrozenJob, Modifier, Plan, Policy, Proof
from clashpilot.planning.scenarios import InfeasibleError, apply_overlays
from clashpilot.planning.town_hall import (
    Outcome,
    discount_instant,
    edge_start,
    figures,
    t_star,
)
from support.problems import DAY, HOUR, NOW, make_calendar, make_problem, make_task

CRITICAL_PACK = (
    Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_critical.json"
)
TOWN_HALL = "9200001:0:2"
HERO = 9200004
MIDNIGHT = NOW - 8 * HOUR  # NOW is 08:00 UTC


def pack() -> GamePack:
    return load_pack(json.loads(CRITICAL_PACK.read_text(encoding="utf-8")))


def plan(*assignments: Assignment) -> Plan:
    return Plan(assignments, "test", Proof("heuristic", 0.0))


def builder(task_id: str, slot: int, start: int, end: int) -> Assignment:
    return Assignment(task_id, "builder", slot, start, end)


# --------------------------------------------------------------------------- T*


def test_t_star_counts_running_jobs_not_slots() -> None:
    # The chain leaves slot 0 at 1 h for slot 1 at 3 h: one builder is free from 1 h on, although
    # no single slot is.
    problem = make_problem(lanes={"builder": 2})
    found = plan(
        builder("1:0:2", 0, NOW, NOW + HOUR),
        builder("2:0:2", 1, NOW, NOW + 3 * HOUR),
        builder("3:0:2", 0, NOW + 3 * HOUR, NOW + 10 * HOUR),
        builder(TOWN_HALL, 0, NOW + 10 * HOUR, NOW + 12 * HOUR),
    )
    assert t_star(problem, found, TOWN_HALL, NOW + 10 * HOUR) == NOW + HOUR


def test_t_star_counts_the_frozen_jobs_that_take_a_slot() -> None:
    chain = builder("3:0:2", 0, NOW, NOW + 10 * HOUR)
    running = FrozenJob("2:0:2", "builder", 1, NOW - HOUR, NOW + 4 * HOUR)
    alone = make_problem(lanes={"builder": 2})
    assert t_star(alone, plan(chain), TOWN_HALL, NOW + 10 * HOUR) == NOW
    held = make_problem(lanes={"builder": 2}, frozen=(running,))
    assert t_star(held, plan(chain), TOWN_HALL, NOW + 10 * HOUR) == NOW + 4 * HOUR
    extra = make_problem(
        lanes={"builder": 2},
        frozen=(FrozenJob("2:0:2", "builder", 1, NOW - HOUR, NOW + 4 * HOUR, extra=True),),
    )
    assert t_star(extra, plan(chain), TOWN_HALL, NOW + 10 * HOUR) == NOW


def test_t_star_is_the_town_hall_start_when_the_lane_stays_full() -> None:
    problem = make_problem(lanes={"builder": 1})
    found = plan(builder("1:0:2", 0, NOW, NOW + 5 * HOUR))
    assert t_star(problem, found, TOWN_HALL, NOW + 5 * HOUR) == NOW + 5 * HOUR


# --------------------------------------------------------------------------- discount edge

NIGHTS = [(MIDNIGHT + k * DAY - HOUR, MIDNIGHT + k * DAY + 7 * HOUR) for k in range(8)]
TOWN_HALL_TASK = make_task(TOWN_HALL, duration_s=2 * DAY, tags=("town_hall",))


def discounted(end: int, *, lane: str = "builder", pct: int = 40) -> Modifier:
    return Modifier(NOW - HOUR, end, lane, pct, 100)


def test_edge_start_is_the_last_second_before_the_end_of_the_reduction() -> None:
    reduction = discounted(MIDNIGHT + 3 * DAY + 10 * HOUR)
    problem = make_problem(
        calendar=make_calendar(sleep=NIGHTS), policy=Policy("progress"), modifiers=(reduction,)
    )
    assert edge_start(problem, TOWN_HALL_TASK, reduction, NOW) == reduction.end - 1


def test_edge_start_keeps_the_end_awake_under_strict() -> None:
    # 40 % of 48 h is 19.2 h: from 07:00 to 10:00 the end falls in the night, so the town hall
    # starts at 22:59:59 the evening before.
    reduction = discounted(MIDNIGHT + 3 * DAY + 10 * HOUR)
    problem = make_problem(
        calendar=make_calendar(sleep=NIGHTS), policy=Policy("strict"), modifiers=(reduction,)
    )
    expected = MIDNIGHT + 2 * DAY + 23 * HOUR - 1
    assert edge_start(problem, TOWN_HALL_TASK, reduction, NOW) == expected
    # Never before the lowest instant asked.
    assert edge_start(problem, TOWN_HALL_TASK, reduction, expected + 1) is None


def test_discount_instant_needs_a_builder_reduction_ending_between_t_star_and_after() -> None:
    end = NOW + 2 * DAY
    for modifier, found in [
        (discounted(end), end - 1),
        (discounted(end, lane="lab"), None),
        (discounted(end, pct=100), None),
    ]:
        problem = make_problem(modifiers=(modifier,))
        assert discount_instant(problem, TOWN_HALL_TASK, NOW + DAY, NOW + 5 * DAY) == found
        # Ending after the `after` start, or before T*: no edge.
        assert discount_instant(problem, TOWN_HALL_TASK, NOW + DAY, end) is None
        assert discount_instant(problem, TOWN_HALL_TASK, end, NOW + 5 * DAY) is None


# --------------------------------------------------------------------------- figures


@pytest.mark.parametrize(("running_end", "reached"), [(NOW + DAY, 2), (NOW + 5 * DAY, 1)])
def test_a_running_hero_level_counts_once_it_ends_before_the_town_hall(
    running_end: int, reached: int
) -> None:
    level_3 = make_task(f"{HERO}:0:3", duration_s=DAY, requires=(f"{HERO}:0:2",))
    problem = make_problem(
        tasks=(level_3,),
        frozen=(FrozenJob(f"{HERO}:0:2", "builder", 0, NOW - HOUR, running_end),),
        lanes={"builder": 1},
    )
    outcome = Outcome(problem, plan(), NOW + 2 * DAY, NOW + 4 * DAY)
    shown = figures(outcome, [level_3], (), pack())
    assert shown["heroes"] == [
        {"data_id": HERO, "name": "Sentinelle de brume", "level_reached": reached}
    ]
    assert shown["unfinished_by_lane"] == {"builder": 1}
    assert shown["unfinished_work_s_by_lane"] == {"builder": DAY}
    # The running job takes the only builder until it ends.
    assert shown["idle_builder_s"] == 4 * DAY - (min(running_end, NOW + 4 * DAY) - NOW)


# --------------------------------------------------------------------------- start_at


def test_start_at_fixes_the_task_at_the_first_accepted_start_from_its_instant() -> None:
    hall = make_task(TOWN_HALL, cost={"gold": 100})
    problem = make_problem(
        tasks=(hall,),
        lanes={"builder": 1},
        frozen=(FrozenJob("5:0:2", "builder", 0, NOW - HOUR, NOW + 3 * HOUR),),
    )
    for at, start in [(NOW + HOUR, NOW + 3 * HOUR), (NOW + 5 * HOUR, NOW + 5 * HOUR)]:
        fixed, forced = apply_overlays(
            problem, pack(), [{"type": "start_at", "task_id": TOWN_HALL, "at": at}]
        )
        assert forced == (hall,)
        job = next(job for job in fixed.frozen if job.task_id == TOWN_HALL)
        assert (job.start, job.end) == (start, start + hall.duration_s)
        assert fixed.resources.initial["gold"] == problem.resources.initial["gold"] - 100


@pytest.mark.parametrize(
    ("task_id", "at", "code"),
    [
        ("99:0:2", NOW, "task_not_found"),
        ("5:0:2", NOW, "already_started"),
        (TOWN_HALL, NOW + 30 * DAY, "no_slot"),
    ],
)
def test_start_at_fails_with_the_reasons_of_start_now(task_id: str, at: int, code: str) -> None:
    problem = make_problem(
        tasks=(make_task(TOWN_HALL),),
        frozen=(FrozenJob("5:0:2", "builder", 0, NOW - HOUR, NOW + 3 * HOUR),),
    )
    overlay = {"type": "start_at", "task_id": task_id, "at": at}
    with pytest.raises(InfeasibleError) as raised:
        apply_overlays(problem, pack(), [overlay])
    assert raised.value.reason.code == code
