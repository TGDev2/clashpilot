"""Acceptance tests for E25: the float of each action and the alerts about stopped critical work
in `GET /api/villages/{village_id}/recommendations/now`.

SPEC (critical path backlog): each `start` action carries `float_s`, `critical` and `date_gain_s`,
and the answer carries `critical_alerts` (`chain_stopped`, `lane_stopped`). The critical path itself
is frozen by `test_critical_path_acceptance.py` (E22); these tests judge what the route says.
Frozen with the task.

How the tests read the task text:

- floats come from `critical_path` on the problem of the latest plan, compiled at the `now` of that
  plan with its strategy: the application clock moving afterwards changes no float;
- a start action without a float (no town hall target, or a task beyond the current level) has
  `float_s` null, `critical` false and `date_gain_s` 0;
- `date_gain_s` is judged at the start of the action, not at the application clock;
- the alerts are read at the application clock (the `now` of the answer): running jobs, finished
  requirements, the 15 minutes and `delay_s` all refer to it;
- an alert holds at least the keys the task text lists; other keys are not judged, nor is the order
  of the alerts. Which chains are critical on a critical lane is not judged either: the lane tests
  only read the `lane_stopped` entries.

Every test goes through HTTP with the E23 fictional pack and export
(`fixtures/packs/fictional_pack_critical.json`, `fixtures/exports/fictional_export_critical.json`,
exported one hour before `NOW` = 2026-11-27T08:00:00Z): town hall 1, five builders, a laboratory,
and on the builder lane a hero at level 1 with four levels left (6, 7, 8 and 9 days). As committed,
the hero chain (30 days) fixes the date and no lane is critical. A 40-day research makes the
laboratory the critical lane. Plans use the `rollout` engine; each test first checks what it needs
from the plan.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"
SCHEMA_TS = ROOT / "frontend" / "src" / "api" / "schema.d.ts"
ROUTE = "/api/villages/{village_id}/recommendations/now"

NOW = 1_795_766_400  # 2026-11-27T08:00:00Z
EXPORTED_AT = NOW - HOUR
VILLAGE = "FICTIFCRIT"

TOWN_HALL = 9200001
LAB_BUILDING = 9200003
HERO = 9200004
BALLISTA = 9200005
CATAPULT = 9200006
SCOUT = 9200007
SAPPER = 9200008


def task(data_id: int, level: int) -> str:
    return f"{data_id}:0:{level}"


TARGET = task(TOWN_HALL, 2)
BEYOND = task(LAB_BUILDING, 2)  # needs town hall 2: outside the current level
HERO_TASKS = [task(HERO, level) for level in (2, 3, 4, 5)]
LAB_DURATIONS = {task(SCOUT, 2): DAY, task(SCOUT, 3): 2 * DAY, task(SAPPER, 2): DAY}

# Floats of the village as committed: the hero chain ends after 30 days.
FLOATS: dict[str, int] = {
    **dict.fromkeys(HERO_TASKS, 0),
    TARGET: 28 * DAY,
    task(BALLISTA, 2): 29 * DAY,
    task(CATAPULT, 2): 29 * DAY - 1,
    task(SCOUT, 2): 27 * DAY,
    task(SCOUT, 3): 27 * DAY,
    task(SAPPER, 2): 29 * DAY,
}

OLD_ACTION_KEYS = [
    "task_id",
    "name",
    "action",
    "at",
    "end",
    "lane",
    "lane_slot",
    "why",
    "advantages",
    "drawbacks",
    "reliability",
    "counterfactual",
]
NEW_ACTION_KEYS = ("float_s", "critical", "date_gain_s")


# --------------------------------------------------------------------------- fixture documents


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CRITICAL_PACK.read_text(encoding="utf-8"))
    return loaded


def item_name(data_id: int) -> str:
    return next(str(item["name"]) for item in pack_doc()["items"] if item["data_id"] == data_id)


def export_doc(
    *, running: Mapping[int, int] | None = None, levels: Mapping[int, int] | None = None
) -> dict[str, Any]:
    """The fixture export; `running` maps an item to the epoch its upgrade ends."""
    doc: dict[str, Any] = json.loads(CRITICAL_EXPORT.read_text(encoding="utf-8"))
    doc["timestamp"] = EXPORTED_AT
    for section in ("buildings", "units", "heroes"):
        for entry in doc[section]:
            if levels and entry["data"] in levels:
                entry["lvl"] = levels[entry["data"]]
            if running and entry["data"] in running:
                entry["timer"] = running[entry["data"]] - EXPORTED_AT
    return doc


def manual_doc(
    *,
    elixir: int = 1_000_000,
    dark_elixir: int = 1_000_000,
    events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    amounts = {"gold": 1_000_000, "elixir": elixir, "dark_elixir": dark_elixir}
    return {
        "resources": {
            name: {"value": value, "status": "confirmed", "as_of": NOW}
            for name, value in amounts.items()
        },
        "events": [dict(event) for event in events],
    }


def modifier(
    lane: str, start: int, end: int, *, duration_pct: int, cost_pct: int = 100
) -> dict[str, Any]:
    return {
        "label": "Bonus fictif",
        "start": start,
        "end": end,
        "lane": lane,
        "duration_pct": duration_pct,
        "cost_pct": cost_pct,
    }


def local(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S")


def profile_doc(absences: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """Always available, in UTC, except during the `(start, end)` epochs of `absences`."""
    return {
        "tz": "UTC",
        "weekly": [],
        "exceptions": [
            {"start_local": local(start), "end_local": local(end), "kind": "absence"}
            for start, end in absences
        ],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


# --------------------------------------------------------------------------- HTTP fixture


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@dataclasses.dataclass
class Village:
    client: TestClient
    clock: Clock


def ok(response: Any, *codes: int) -> Any:
    assert response.status_code in (codes or (200,)), response.text
    return response.json()


def app_client(tmp_path: Path) -> Village:
    clock = Clock(NOW)
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    return Village(TestClient(create_app(settings, clock=clock)), clock)


def village(
    tmp_path: Path,
    *,
    pack: Mapping[str, Any] | None = None,
    export: Mapping[str, Any] | None = None,
    manual: Mapping[str, Any] | None = None,
    absences: Sequence[tuple[int, int]] = (),
    durations: Mapping[tuple[int, int], int] | None = None,
) -> Village:
    """The fixture village; `durations` corrects the pack by `(data_id, level)`. No plan."""
    v = app_client(tmp_path)
    document = dict(pack) if pack is not None else pack_doc()
    pack_id = document["pack_id"]
    ok(v.client.post("/api/gamedata/packs", json=document), 200, 201)
    ok(v.client.put(f"/api/gamedata/packs/{pack_id}/active"))
    for (data_id, level), duration_s in (durations or {}).items():
        ok(
            v.client.patch(
                f"/api/gamedata/packs/{pack_id}/items/{data_id}/levels/{level}",
                json={"duration_s": duration_s},
            )
        )
    ok(
        v.client.post(
            "/api/villages/import",
            content=json.dumps(export if export is not None else export_doc()).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
        200,
        201,
    )
    ok(
        v.client.put(
            f"/api/villages/{VILLAGE}/manual",
            json=manual if manual is not None else manual_doc(),
        )
    )
    ok(v.client.put(f"/api/villages/{VILLAGE}/schedule", json=profile_doc(absences)))
    return v


def make_plan(v: Village, **body: Any) -> dict[str, dict[str, Any]]:
    """Compute a plan at the clock; its assignments by task id."""
    plan = ok(
        v.client.post(f"/api/villages/{VILLAGE}/plans", json={"engine": "rollout", **body}), 201
    )
    assert plan["now"] == v.clock.now
    return {a["task_id"]: a for a in plan["assignments"]}


def lane_starts(plan: Mapping[str, Mapping[str, Any]], lane: str) -> list[int]:
    return sorted(
        a["start"] for a in plan.values() if a["lane"] == lane and a["status"] != "frozen"
    )


def recommendations(v: Village, at: int | None = None) -> dict[str, Any]:
    if at is not None:
        v.clock.now = at
    body: dict[str, Any] = ok(v.client.get(f"/api/villages/{VILLAGE}/recommendations/now"))
    assert body["now"] == v.clock.now
    return body


def starts(body: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The start actions by task id; each one carries the three new fields, well typed."""
    found: dict[str, dict[str, Any]] = {}
    for action in body["actions"]:
        if action["action"] != "start":
            continue
        assert set(action) >= set(NEW_ACTION_KEYS), sorted(set(NEW_ACTION_KEYS) - set(action))
        float_s = action["float_s"]
        assert float_s is None or type(float_s) is int, action
        assert action["critical"] is (float_s is not None and float_s <= 300), action
        assert type(action["date_gain_s"]) is int, action
        found[action["task_id"]] = action
    assert found, body["actions"]
    return found


def alerts(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    assert "critical_alerts" in body, sorted(body)
    found: list[dict[str, Any]] = body["critical_alerts"]
    assert all(entry["code"] in ("chain_stopped", "lane_stopped") for entry in found), found
    return found


def chain_alerts(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The `chain_stopped` entries, reduced to the listed keys, ordered by task id."""
    keys = ("code", "task_id", "name", "planned_start", "delay_s")
    found = [e for e in alerts(body) if e["code"] == "chain_stopped"]
    return sorted(({key: e[key] for key in keys} for e in found), key=lambda e: str(e["task_id"]))


def lane_alerts(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The `lane_stopped` entries, reduced to the listed keys, ordered by lane."""
    keys = ("code", "lane", "planned_start", "delay_s")
    found = [e for e in alerts(body) if e["code"] == "lane_stopped"]
    return sorted(({key: e[key] for key in keys} for e in found), key=lambda e: str(e["lane"]))


def chain_stopped(data_id: int, level: int, start: int | None, now: int) -> dict[str, Any]:
    return {
        "code": "chain_stopped",
        "task_id": task(data_id, level),
        "name": item_name(data_id),
        "planned_start": start,
        "delay_s": None if start is None else start - now,
    }


def lane_stopped(lane: str, start: int | None, now: int) -> dict[str, Any]:
    return {
        "code": "lane_stopped",
        "lane": lane,
        "planned_start": start,
        "delay_s": None if start is None else start - now,
    }


# A 40-day research: the laboratory work (43 days) ends after the hero chain (30 days).
LONG_RESEARCH = {(SAPPER, 2): 40 * DAY}


# --------------------------------------------------------------------------- float and critical


def test_each_start_action_carries_its_float_and_is_critical_up_to_it(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = make_plan(v)
    assert plan[HERO_TASKS[0]]["start"] == NOW
    assert any(a["start"] == NOW and name != HERO_TASKS[0] for name, a in plan.items())

    found = starts(recommendations(v))

    assert HERO_TASKS[0] in found
    assert {name: a["float_s"] for name, a in found.items()} == {
        name: FLOATS[name] for name in found
    }
    assert {name for name, a in found.items() if a["critical"]} == {HERO_TASKS[0]}
    assert [a["date_gain_s"] for a in found.values()] == [0] * len(found)


def test_an_action_is_critical_exactly_when_its_float_is_at_most_300_seconds(
    tmp_path: Path,
) -> None:
    durations = {(BALLISTA, 2): 30 * DAY - 300, (CATAPULT, 2): 30 * DAY - 301}
    v = village(tmp_path, durations=durations)
    plan = make_plan(v)
    assert plan[task(BALLISTA, 2)]["start"] == plan[task(CATAPULT, 2)]["start"] == NOW

    found = starts(recommendations(v))

    ballista, catapult = found[task(BALLISTA, 2)], found[task(CATAPULT, 2)]
    assert (ballista["float_s"], ballista["critical"]) == (300, True)
    assert (catapult["float_s"], catapult["critical"]) == (301, False)


def test_the_floats_are_those_of_the_latest_plan_at_its_now_not_at_the_clock(
    tmp_path: Path,
) -> None:
    # The hero upgrade already runs for ten days: it does not move with the clock, so a float
    # compiled at the clock would be one hour shorter than the one of the plan.
    v = village(tmp_path, export=export_doc(running={HERO: NOW + 10 * DAY}))
    plan = make_plan(v)
    chain_end = 34 * DAY  # 10 days left, then levels 3 to 5 (7 + 8 + 9 days)
    expected: dict[str, int | None] = {
        task(SCOUT, 2): chain_end - 3 * DAY,
        task(SCOUT, 3): chain_end - 3 * DAY,
        task(SAPPER, 2): chain_end - DAY,
        BEYOND: None,
    }
    later = min(a["start"] for a in plan.values() if a["start"] >= NOW + HOUR)
    first = {name for name, a in plan.items() if a["start"] == later}
    assert first <= set(expected) and first & set(LAB_DURATIONS), sorted(first)

    found = starts(recommendations(v, NOW + HOUR))

    assert {name: a["float_s"] for name, a in found.items()} == {n: expected[n] for n in first}


def test_the_floats_follow_the_strategy_of_the_latest_plan(tmp_path: Path) -> None:
    # `finish_th` makes the town hall wait for the current level: it loses all its float.
    v = village(tmp_path)
    make_plan(v)
    plan = make_plan(v, strategy="finish_th")
    start = plan[TARGET]["start"]
    assert start > NOW

    town_hall = starts(recommendations(v, start))[TARGET]

    assert (town_hall["float_s"], town_hall["critical"]) == (0, True)


def test_a_task_beyond_the_current_level_has_no_float(tmp_path: Path) -> None:
    v = village(
        tmp_path,
        manual=manual_doc(
            events=[modifier("builder", NOW + HOUR, NOW + 60 * DAY, duration_pct=50)]
        ),
    )
    plan = make_plan(v)
    start = plan[BEYOND]["start"]
    assert start > NOW

    beyond = starts(recommendations(v, start))[BEYOND]

    assert (beyond["float_s"], beyond["critical"], beyond["date_gain_s"]) == (None, False, 0)


def test_without_a_town_hall_target_no_action_is_critical_and_nothing_alerts(
    tmp_path: Path,
) -> None:
    events = [modifier(lane, NOW, NOW + 60 * DAY, duration_pct=50) for lane in ("builder", "lab")]
    v = village(
        tmp_path,
        export=export_doc(levels={TOWN_HALL: 2}),  # the pack holds no town hall 3
        manual=manual_doc(events=events),
        absences=[(NOW, NOW + HOUR)],
    )
    plan = make_plan(v)
    assert TARGET not in plan and plan[HERO_TASKS[0]]["start"] == NOW + HOUR

    body = recommendations(v)
    found = starts(body)

    assert HERO_TASKS[0] in found
    for action in found.values():
        assert (action["float_s"], action["critical"], action["date_gain_s"]) == (None, False, 0)
    assert alerts(body) == []


# --------------------------------------------------------------------------- date gain


def test_a_critical_start_during_a_shorter_duration_modifier_gains_the_time_saved(
    tmp_path: Path,
) -> None:
    events = [
        modifier("builder", NOW, NOW + HOUR, duration_pct=80),
        modifier("lab", NOW, NOW + HOUR, duration_pct=50),
    ]
    v = village(tmp_path, manual=manual_doc(events=events))
    plan = make_plan(v)
    hero = plan[HERO_TASKS[0]]
    assert (hero["start"], hero["end"] - hero["start"]) == (NOW, 414_720)  # 80 % of six days
    others = {name for name, a in plan.items() if a["start"] == NOW and name != HERO_TASKS[0]}
    assert others & set(LAB_DURATIONS) and others - set(LAB_DURATIONS), sorted(others)

    found = starts(recommendations(v))

    assert found[HERO_TASKS[0]]["critical"] is True
    assert found[HERO_TASKS[0]]["date_gain_s"] == 6 * DAY - 414_720
    for name in others:  # shortened too, but with float to spare
        assert (found[name]["critical"], found[name]["date_gain_s"]) == (False, 0), name


def test_the_date_gain_counts_a_modifier_active_when_the_action_starts(tmp_path: Path) -> None:
    # The modifier is not active yet at the clock; it is when the action starts.
    events = [modifier("builder", NOW + HOUR, NOW + 2 * HOUR, duration_pct=80)]
    v = village(tmp_path, manual=manual_doc(events=events), absences=[(NOW, NOW + HOUR)])
    plan = make_plan(v)
    assert plan[HERO_TASKS[0]]["start"] == NOW + HOUR

    hero = starts(recommendations(v))[HERO_TASKS[0]]

    assert (hero["critical"], hero["date_gain_s"]) == (True, 6 * DAY - 414_720)


def test_the_date_gain_ignores_a_modifier_over_before_the_action_starts(tmp_path: Path) -> None:
    # Active at the clock, over when the action starts: nothing is saved.
    events = [modifier("builder", NOW, NOW + HOUR, duration_pct=80)]
    v = village(tmp_path, manual=manual_doc(events=events), absences=[(NOW, NOW + HOUR)])
    plan = make_plan(v)
    assert plan[HERO_TASKS[0]]["start"] == NOW + HOUR

    hero = starts(recommendations(v))[HERO_TASKS[0]]

    assert (hero["critical"], hero["date_gain_s"]) == (True, 0)


def test_a_modifier_that_does_not_shorten_gives_no_date_gain(tmp_path: Path) -> None:
    for index, (duration_pct, cost_pct) in enumerate(((100, 50), (150, 100))):
        events = [
            modifier("builder", NOW, NOW + 60 * DAY, duration_pct=duration_pct, cost_pct=cost_pct)
        ]
        v = village(tmp_path / str(index), manual=manual_doc(events=events))
        plan = make_plan(v)
        assert plan[HERO_TASKS[0]]["start"] == NOW

        hero = starts(recommendations(v))[HERO_TASKS[0]]

        assert (hero["critical"], hero["date_gain_s"]) == (True, 0), duration_pct


def test_the_date_gain_only_counts_the_modifiers_of_the_lane_of_the_action(
    tmp_path: Path,
) -> None:
    # The laboratory is the critical lane (a 100-day research, still the longest when halved).
    durations = {(SAPPER, 2): 100 * DAY}
    base = {**LAB_DURATIONS, task(SAPPER, 2): 100 * DAY}
    for lane, divisor in (("builder", None), ("lab", 2)):
        events = [modifier(lane, NOW, NOW + 120 * DAY, duration_pct=50)]
        v = village(tmp_path / lane, manual=manual_doc(events=events), durations=durations)
        plan = make_plan(v)
        start = lane_starts(plan, "lab")[0]
        first = [name for name in base if name in plan and plan[name]["start"] == start]
        assert len(first) == 1, first

        action = starts(recommendations(v, start))[first[0]]

        assert (action["float_s"], action["critical"]) == (0, True)
        expected = 0 if divisor is None else base[first[0]] // divisor
        assert action["date_gain_s"] == expected, lane


# --------------------------------------------------------------------------- stopped chains


def test_a_fresh_plan_that_starts_the_critical_chain_raises_no_alert(tmp_path: Path) -> None:
    v = village(tmp_path)
    plan = make_plan(v)
    assert plan[HERO_TASKS[0]]["start"] == NOW

    assert alerts(recommendations(v)) == []
    # An hour later the first level runs and the next one is not ready yet.
    assert alerts(recommendations(v, NOW + HOUR)) == []


def test_a_critical_chain_planned_more_than_15_minutes_later_is_stopped(tmp_path: Path) -> None:
    start = NOW + HOUR
    v = village(tmp_path, absences=[(NOW, start)])
    plan = make_plan(v)
    assert plan[HERO_TASKS[0]]["start"] == start

    # The other chains wait just as long, with float to spare: they raise nothing.
    for now in (NOW, start - 15 * MINUTE - 1):
        body = recommendations(v, now)
        assert chain_alerts(body) == [chain_stopped(HERO, 2, start, now)]
        assert lane_alerts(body) == []  # no lane is critical here
    for now in (start - 15 * MINUTE, start):
        assert alerts(recommendations(v, now)) == [], start - now


def test_every_stopped_critical_chain_raises_its_own_alert(tmp_path: Path) -> None:
    start = NOW + HOUR
    durations = {(BALLISTA, 2): 30 * DAY - 300, (CATAPULT, 2): 30 * DAY - 301}
    v = village(tmp_path, durations=durations, absences=[(NOW, start)])
    plan = make_plan(v)
    for name in (HERO_TASKS[0], task(BALLISTA, 2), task(CATAPULT, 2)):
        assert plan[name]["start"] == start, name

    body = recommendations(v)

    # 300 s of float is critical, 301 s is not.
    assert chain_alerts(body) == [
        chain_stopped(HERO, 2, start, NOW),
        chain_stopped(BALLISTA, 2, start, NOW),
    ]


def test_a_stopped_chain_whose_next_task_is_not_planned_has_no_start_nor_delay(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, manual=manual_doc(dark_elixir=0))  # the hero cannot be paid
    plan = make_plan(v)
    assert not set(HERO_TASKS) & set(plan)

    body = recommendations(v)

    assert chain_alerts(body) == [chain_stopped(HERO, 2, None, NOW)]
    assert lane_alerts(body) == []


def test_a_chain_is_stopped_only_once_its_running_requirement_is_finished(
    tmp_path: Path,
) -> None:
    done, start = NOW + 2 * HOUR, NOW + 6 * HOUR
    v = village(
        tmp_path,
        export=export_doc(running={HERO: done}),
        absences=[(NOW + HOUR, start)],
    )
    plan = make_plan(v)
    assert plan[task(HERO, 3)]["start"] == start

    for now in (NOW, done - 1):  # a task of the chain runs
        assert chain_alerts(recommendations(v, now)) == [], done - now
    for now in (done, done + HOUR):
        assert chain_alerts(recommendations(v, now)) == [chain_stopped(HERO, 3, start, now)]


# --------------------------------------------------------------------------- stopped lanes


def test_a_critical_lane_idle_for_more_than_15_minutes_is_stopped(tmp_path: Path) -> None:
    start = NOW + HOUR
    v = village(tmp_path, durations=LONG_RESEARCH, absences=[(NOW, start)])
    plan = make_plan(v)
    assert lane_starts(plan, "lab")[0] == start
    assert lane_starts(plan, "builder")[0] == start

    # The builders wait as long, but their lane is not critical.
    for now in (NOW, start - 15 * MINUTE - 1):
        assert lane_alerts(recommendations(v, now)) == [lane_stopped("lab", start, now)]
    for now in (start - 15 * MINUTE, start):
        assert lane_alerts(recommendations(v, now)) == [], start - now


def test_a_critical_lane_with_a_planned_task_starting_now_is_not_stopped(tmp_path: Path) -> None:
    v = village(tmp_path, durations=LONG_RESEARCH)
    plan = make_plan(v)
    assert lane_starts(plan, "lab")[0] == NOW

    assert lane_alerts(recommendations(v)) == []


def test_a_critical_lane_is_stopped_only_once_its_running_job_is_finished(tmp_path: Path) -> None:
    done, start = NOW + 2 * HOUR, NOW + 6 * HOUR
    v = village(
        tmp_path,
        export=export_doc(running={SCOUT: done}),
        durations=LONG_RESEARCH,
        absences=[(NOW + HOUR, start)],
    )
    plan = make_plan(v)
    assert lane_starts(plan, "lab")[0] == start

    for now in (NOW, done - 1):
        assert lane_alerts(recommendations(v, now)) == [], done - now
    for now in (done, done + HOUR):
        assert lane_alerts(recommendations(v, now)) == [lane_stopped("lab", start, now)]


def test_a_critical_lane_without_any_planned_task_is_stopped_while_one_is_ready(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, manual=manual_doc(elixir=0), durations=LONG_RESEARCH)  # no research paid
    plan = make_plan(v)
    assert lane_starts(plan, "lab") == []

    assert lane_alerts(recommendations(v)) == [lane_stopped("lab", None, NOW)]


def test_a_critical_lane_without_any_planned_task_waits_for_a_ready_one(tmp_path: Path) -> None:
    # Every first research needs the ballista upgrade, which is running.
    done = NOW + 2 * HOUR
    pack = pack_doc()
    for row in pack["levels"]:
        if row["data_id"] in (SCOUT, SAPPER) and row["level"] == 2:
            row["requires"].append({"data_id": BALLISTA, "level": 2})
    v = village(
        tmp_path,
        pack=pack,
        export=export_doc(running={BALLISTA: done}),
        manual=manual_doc(elixir=0),
        durations=LONG_RESEARCH,
    )
    plan = make_plan(v)
    assert lane_starts(plan, "lab") == []

    for now in (NOW, done - 1):
        assert lane_alerts(recommendations(v, now)) == [], done - now
    assert lane_alerts(recommendations(v, done)) == [lane_stopped("lab", None, done)]


# --------------------------------------------------------------------------- unchanged answer


def test_a_wait_action_keeps_its_fields_and_has_no_float(tmp_path: Path) -> None:
    start = NOW + HOUR
    v = village(tmp_path, absences=[(NOW, start)])
    make_plan(v)

    body = recommendations(v)
    found = starts(body)  # the new fields are there for the start actions

    wait, *others = body["actions"]
    assert wait["action"] == "wait"
    assert [a["task_id"] for a in others] == list(found)
    assert (wait["task_id"], wait["at"], wait["counterfactual"]) == (None, NOW, None)
    assert (wait["end"], wait["lane"], wait["lane_slot"]) == (None, None, None)
    assert (wait["advantages"], wait["drawbacks"]) == ([], [])
    assert [reason["code"] for reason in wait["why"]["reasons"]] == ["wait_until"]
    assert [wait.get(key) for key in NEW_ACTION_KEYS] == [None, None, None]
    for action in body["actions"]:
        assert [key for key in action if key in OLD_ACTION_KEYS] == OLD_ACTION_KEYS
    assert body["next_login_at"] == start


# --------------------------------------------------------------------------- API description


def referenced_names(schema: Mapping[str, Any], node: Any, seen: set[str]) -> Iterator[str]:
    """Property names reachable from `node`, through the `$ref` of the OpenAPI document."""
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference not in seen:
            seen.add(reference)
            name = reference.rsplit("/", 1)[-1]
            yield from referenced_names(schema, schema["components"]["schemas"][name], seen)
        yield from node.get("properties", {})
        for value in node.values():
            yield from referenced_names(schema, value, seen)
    elif isinstance(node, list):
        for value in node:
            yield from referenced_names(schema, value, seen)


def test_the_openapi_document_declares_the_new_fields_without_login_like_name(
    tmp_path: Path,
) -> None:
    schema = app_client(tmp_path).client.get("/openapi.json").json()
    answer = schema["paths"][ROUTE]["get"]["responses"]["200"]

    names = set(referenced_names(schema, answer, set()))

    expected = {*NEW_ACTION_KEYS, "critical_alerts", "code", "planned_start", "delay_s", "lane"}
    assert expected <= names, sorted(expected - names)
    forbidden = re.compile(
        r"password|passwd|mot_?de_?passe|supercell_?id|username|login|e_?mail|credential",
        re.IGNORECASE,
    )
    assert sorted(name for name in names if forbidden.search(name)) == []


def test_the_generated_client_schema_holds_the_new_fields() -> None:
    generated = SCHEMA_TS.read_text(encoding="utf-8")

    for name in (*NEW_ACTION_KEYS, "critical_alerts", "planned_start", "delay_s"):
        assert re.search(rf"\b{name}\??:", generated), f"{name}: run node scripts/gen-api.mjs"
