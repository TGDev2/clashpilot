"""Acceptance tests for E26: compare the moments to launch the next town hall (SPEC "Comparateur",
ADR-CP3). Frozen with the task.

`POST /api/villages/{village_id}/town-hall/compare {engine?, profile?, skip_lanes?}` reads the
latest plan (404 `no_plan`), stores nothing and answers `{target, skip_lanes, options}`. Every
option starts from the problem the latest plan was computed on (its `now`, horizon and params,
the export and the journal, as `scenarios.compiled` rebuilds it), runs the engine asked (by
default the one of `POST plans`) and is judged by `simulate`:

- `after`: that problem with the `town_hall_last` constraint limited by `skip_lanes` (the town
  hall requires `current_level_ids(tasks, skip_lanes)`), whatever the plan's strategy;
- `no_wait`: the town hall fixed by the new scenario overlay `start_at` at the first start >= T*
  that `simulate` accepts, on the problem without `town_hall_last`; the engine plans the rest.
  T* is the first instant from which, in the `after` plan, at least one place of the builder
  lane stays free until the town hall starts (frozen jobs counted);
- `discount_edge`: only when a modifier of the builder lane with `duration_pct < 100` ends after
  T* and before the `after` town hall start; the town hall then starts at the last possible
  instant before the modifier ends, never before T*, and lasts its reduced duration.

Shapes that the task text leaves open are fixed here:

- `target` is `{task_id, data_id, name, to_level}`, as for `GET .../critical-path`; `skip_lanes`
  is the list of lanes used (the request's, else the latest plan's `town_hall_skip_lanes`, else
  empty); `422 unknown_lane` is `{"detail": {"code": "unknown_lane", ...}}`;
- `options` is a list of objects with a unique `kind` among `after`, `no_wait` and
  `discount_edge`; `after` and `no_wait` are always listed, `discount_edge` only when it exists;
- each option carries `kind`, `status` (`ok` or `infeasible`), `reasons` (`[{code, message}]`,
  empty when `ok`), `th_start`, `th_finish` (UTC epochs, null when `infeasible`),
  `idle_builder_s`, `unfinished_count`, `unfinished_by_lane`, `unfinished_work_s_by_lane`,
  `unfinished_composition {count, work_s}`, `heroes`, `engine` (the name of the engine asked)
  and `proof` (the proof of the plan that engine returned for the option, as `ProofOut`);
- an option that cannot happen (funds, a prerequisite, no slot) is `infeasible` with the
  reasons of the scenarios (`missing_prerequisite`, ...), never a 500;
- T* is counted in jobs: the first instant t >= now such that at every instant of
  `[t, th_start)` fewer builder jobs run than the lane holds (the `after` plan's assignments
  other than the town hall, and the problem's frozen jobs that are not `extra`). It is the plan's
  `now` or the end of one of those jobs. A reading slot by slot would depend on how the engine
  colours the slots (the rollout plan moves the hero chain from one slot to the other);
- "the last possible instant before the modifier ends" is the greatest whole second t with
  T* <= t < modifier end, `calendar.can_act(t)` and, under the policy `strict`, the end
  `t + effective_duration` not asleep;
- "the option's plan" is the plan the engine returned for it and "its problem" the problem the
  engine was given (spied on `PLANNERS`): for `after` the town hall is a task of it, for
  `no_wait` and `discount_edge` a frozen job of it at `[th_start, th_finish)` whose cost came off
  the initial stock (as `start_now` does); `simulate` accepts every option's plan on its problem;
- the current level is `current_level_ids(tasks)` of the `after` problem with no lane skipped
  (the lab still counts when the town hall does not wait for it). A current-level task is
  finished at `th_finish` when the option's plan or a frozen job of its problem ends it at or
  before `th_finish`. The remaining work of an unfinished one is `end - max(start, th_finish)`
  when the option's plan places it, else its `duration_s`; `unfinished_work_s_by_lane` and
  `unfinished_composition.work_s` add them up. Lanes with 0 may be listed or left out: the tests
  read both mappings without their zero entries. A job running at the export is no task: these
  fixtures never have one still running at a `th_finish`;
- `idle_builder_s` = builder capacity x (th_finish - now) minus the time builder jobs run in
  `[now, th_finish)`: the option's assignments (the `after` town hall included) and the frozen
  jobs that are not `extra` (the fixed town hall included);
- `heroes` lists once per data id the hero items (pack kind `hero`) that have a current-level
  task: `level_reached` is the highest level whose upgrade ends at or before `th_finish`, else
  the level the village has (the export's).

`POST .../scenarios` also takes the overlay `{"type": "start_at", "task_id", "at"}`: the task is
fixed at the first start >= `at` that `simulate` accepts, with the failure reasons of
`start_now` (`task_not_found`, `already_started`, `no_slot`); it is echoed in `overlays`.

Every test goes through HTTP with engine `rollout` and profile `repro` (the default engine test
stands a delegate to `rollout` in for `auto`), on the fictional pack
`backend/tests/fixtures/packs/fictional_pack_critical.json` and the export
`backend/tests/fixtures/exports/fictional_export_critical.json` (exported one hour before `NOW` =
2026-11-27T08:00:00Z), changed through the API only: two builders, plentiful resources, always
available, no sleep; the scout's lab levels 2 and 3 last 20 and 25 days (pack corrections), so
the lab holds 46 days of work. The hero chain (levels 2 to 5, 30 days on one builder) outlasts
the rest of the builder work (two 1-day upgrades), so under `town_hall_last` the second builder
stands idle for 28 days before the town hall starts: the case the comparator is made for.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import clashpilot.planning  # noqa: F401  (registers the engines)
from clashpilot.api.app import create_app
from clashpilot.planning.critical import current_level_ids
from clashpilot.planning.model import Assignment, FrozenJob, Modifier, Plan, Problem, Task
from clashpilot.planning.objective import Objective
from clashpilot.planning.registry import PLANNERS
from clashpilot.planning.simulate import effective_duration, simulate
from clashpilot.settings import Settings

HOUR = 3600
DAY = 24 * HOUR

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures"
CRITICAL_PACK = FIXTURES / "packs" / "fictional_pack_critical.json"
CRITICAL_EXPORT = FIXTURES / "exports" / "fictional_export_critical.json"
SCHEMA_TS = ROOT / "frontend" / "src" / "api" / "schema.d.ts"

NOW = 1_795_766_400  # 2026-11-27T08:00:00Z
EXPORTED_AT = NOW - HOUR
VILLAGE = "FICTIFCRIT"
ROUTE = "/api/villages/{village_id}/town-hall/compare"
URL = f"/api/villages/{VILLAGE}/town-hall/compare"
SCENARIOS = f"/api/villages/{VILLAGE}/scenarios"

TOWN_HALL = 9200001
HUT = 9200002
LAB = 9200003
HERO = 9200004
BALLISTA = 9200005
CATAPULT = 9200006
SCOUT = 9200007
SAPPER = 9200008

TOWN_HALL_NAME = "Beffroi imaginaire"
HERO_NAME = "Sentinelle de brume"
BUILDER = "builder"


def task(data_id: int, level: int) -> str:
    return f"{data_id}:0:{level}"


TH_TASK = task(TOWN_HALL, 2)
TH_DURATION = 2 * DAY
TH_GOLD = 9000
HERO_TASKS = {task(HERO, level) for level in (2, 3, 4, 5)}
LAB_TASKS = {task(SCOUT, 2), task(SCOUT, 3), task(SAPPER, 2)}
CURRENT_BUILDER = {*HERO_TASKS, task(BALLISTA, 2), task(CATAPULT, 2)}
CURRENT = CURRENT_BUILDER | LAB_TASKS
COMPOSITION = [SCOUT]
SCOUT_DAYS = {2: 20, 3: 25}

HERO_CHAIN_END = NOW + 30 * DAY  # hero levels 2 to 5: 6 + 7 + 8 + 9 days
LAB_END = NOW + 46 * DAY  # 20 + 25 + 1 days of research
T_STAR = NOW + 2 * DAY + 1  # the ballista (1 day) then the catapult (1 day + 1 s)

ENGINE = {"engine": "rollout", "profile": "repro"}
FINISH_TH = {**ENGINE, "strategy": "finish_th", "composition": COMPOSITION}
# DT's choice (SPEC, 27/11): the town hall does not wait for the lab, the lab researches the
# composition first.
DT_PLAN = {**FINISH_TH, "composition_first": True, "town_hall_skip_lanes": ["lab"]}

OPTION_FIELDS = {
    "kind",
    "status",
    "reasons",
    "th_start",
    "th_finish",
    "idle_builder_s",
    "unfinished_count",
    "unfinished_by_lane",
    "unfinished_work_s_by_lane",
    "unfinished_composition",
    "heroes",
    "engine",
    "proof",
}
FORBIDDEN_NAMES = re.compile(
    r"password|passwd|mot_?de_?passe|supercell_?id|username|login(?!s_per_day)|e_?mail|credential",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- fixture documents


def pack_doc() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CRITICAL_PACK.read_text(encoding="utf-8"))
    return loaded


def export_doc(builders: int, running_until: int | None) -> dict[str, Any]:
    """`builders` huts; with `running_until`, the ballista and the catapult upgrade (levels 1 to 2)
    until that instant."""
    doc: dict[str, Any] = json.loads(CRITICAL_EXPORT.read_text(encoding="utf-8"))
    doc["timestamp"] = EXPORTED_AT
    for entry in doc["buildings"]:
        if entry["data"] == HUT:
            entry["cnt"] = builders
        if running_until is not None and entry["data"] in (BALLISTA, CATAPULT):
            entry["timer"] = running_until - EXPORTED_AT
    return doc


def stock(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": NOW}


def manual_doc(events: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return {
        "resources": {
            "gold": stock(1_000_000),
            "elixir": stock(1_000_000),
            "dark_elixir": stock(1_000_000),
        },
        "events": [dict(event) for event in events],
    }


def event(
    end: int, *, lane: str = BUILDER, duration_pct: int = 50, cost_pct: int = 100
) -> dict[str, Any]:
    return {
        "label": "Remise imaginaire",
        "start": NOW - HOUR,
        "end": end,
        "lane": lane,
        "duration_pct": duration_pct,
        "cost_pct": cost_pct,
    }


PROGRESS = {
    "tz": "UTC",
    "weekly": [],
    "exceptions": [],
    "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
}
STRICT_NIGHTS = {
    "tz": "UTC",
    "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
    "exceptions": [],
    "policy": {"mode": "strict", "tolerance_minutes": 0, "max_logins_per_day": None},
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
    data_dir: Path


def ok(response: Any, *codes: int) -> Any:
    assert response.status_code in (codes or (200,)), response.text
    return response.json()


def village(
    tmp_path: Path,
    *,
    builders: int = 2,
    running_until: int | None = None,
    events: Sequence[Mapping[str, Any]] = (),
    profile: Mapping[str, Any] = PROGRESS,
    pack: Mapping[str, Any] | None = None,
    plan: Mapping[str, Any] | None = DT_PLAN,
) -> Village:
    """The E26 village; unless `plan` is None, a first plan is computed with that request."""
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    v = Village(TestClient(create_app(settings, clock=Clock(NOW))), tmp_path)
    content = dict(pack_doc() if pack is None else pack)
    ok(v.client.post("/api/gamedata/packs", json=content), 200, 201)
    ok(v.client.put(f"/api/gamedata/packs/{content['pack_id']}/active"))
    for level, days in SCOUT_DAYS.items():
        ok(
            v.client.patch(
                f"/api/gamedata/packs/{content['pack_id']}/items/{SCOUT}/levels/{level}",
                json={"duration_s": days * DAY},
            )
        )
    ok(
        v.client.post(
            "/api/villages/import",
            content=json.dumps(export_doc(builders, running_until)).encode("utf-8"),
            headers={"content-type": "application/json"},
        ),
        200,
        201,
    )
    ok(v.client.put(f"/api/villages/{VILLAGE}/schedule", json=dict(profile)))
    ok(v.client.put(f"/api/villages/{VILLAGE}/manual", json=manual_doc(events)))
    if plan is not None:
        post_plan(v, plan)
    return v


def post_plan(v: Village, body: Mapping[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = ok(v.client.post(f"/api/villages/{VILLAGE}/plans", json=dict(body)), 201)
    return plan


def compare(v: Village, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
    answer: dict[str, Any] = ok(v.client.post(URL, json=dict(ENGINE if body is None else body)))
    return answer


def options(answer: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    kinds = [option["kind"] for option in answer["options"]]
    assert len(kinds) == len(set(kinds)), kinds
    assert set(kinds) <= {"after", "no_wait", "discount_edge"}, kinds
    assert {"after", "no_wait"} <= set(kinds), kinds
    for option in answer["options"]:
        assert set(option) >= OPTION_FIELDS, sorted(OPTION_FIELDS - set(option))
    return {option["kind"]: option for option in answer["options"]}


def rows_of_every_table(v: Village) -> dict[str, list[tuple[Any, ...]]]:
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        names = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return {
            name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for name in names
        }


def error_code(response: Any) -> str:
    code: str = response.json()["detail"]["code"]
    return code


# --------------------------------------------------------------------------- engine spy


@dataclasses.dataclass
class Call:
    problem: Problem
    objective: Objective
    plan: Plan


class Spy:
    """Records every run of the engine it stands for; `inner` does the work."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[Call] = []

    def solve(
        self, problem: Problem, objective: Objective, config: Any, reference: Plan | None = None
    ) -> Plan:
        plan: Plan = self.inner.solve(problem, objective, config, reference)
        self.calls.append(Call(problem, objective, plan))
        return plan


@contextmanager
def spying(key: str = "rollout", inner: str | None = None) -> Iterator[Spy]:
    saved = PLANNERS[key]
    spy = Spy(PLANNERS[inner or key])
    PLANNERS[key] = spy
    try:
        yield spy
    finally:
        PLANNERS[key] = saved


def town_hall_of(plan: Plan) -> Assignment | None:
    return next((a for a in plan.assignments if a.task_id == TH_TASK), None)


def town_hall_job(problem: Problem) -> FrozenJob | None:
    return next((job for job in problem.frozen if job.task_id == TH_TASK), None)


def town_hall_task(problem: Problem) -> Task | None:
    return next((t for t in problem.tasks if t.id == TH_TASK), None)


def after_call(spy: Spy, option: Mapping[str, Any]) -> Call:
    """The run whose problem holds the town hall as a task and whose plan places it where the
    option says."""
    wanted = (option["th_start"], option["th_finish"])
    found = []
    for call in spy.calls:
        placed = town_hall_of(call.plan)
        if town_hall_task(call.problem) is None or placed is None:
            continue
        if (placed.start, placed.end) == wanted:
            found.append(call)
    assert found, f"no engine run planned the town hall at {option['th_start']}"
    return found[-1]


def fixed_call(spy: Spy, option: Mapping[str, Any]) -> Call:
    """The run whose problem holds the town hall as a frozen job where the option says."""
    found = []
    for call in spy.calls:
        job = town_hall_job(call.problem)
        if job is not None and (job.start, job.end) == (option["th_start"], option["th_finish"]):
            found.append(call)
    assert found, f"no engine run had the town hall fixed at {option['th_start']}"
    return found[-1]


# --------------------------------------------------------------------------- expected figures


def overlap(start: int, end: int, low: int, high: int) -> int:
    return max(0, min(end, high) - max(start, low))


def builder_jobs(call: Call, *, without_town_hall: bool = False) -> list[tuple[int, int]]:
    jobs = [
        (a.start, a.end)
        for a in call.plan.assignments
        if a.lane == BUILDER and not (without_town_hall and a.task_id == TH_TASK)
    ]
    jobs += [
        (job.start, job.end) for job in call.problem.frozen if job.lane == BUILDER and not job.extra
    ]
    return jobs


def t_star(after: Call) -> int:
    """T* read on the `after` plan, counted in jobs (see the module text)."""
    placed = town_hall_of(after.plan)
    assert placed is not None
    jobs = builder_jobs(after, without_town_hall=True)
    capacity = after.problem.lanes[BUILDER]

    def free_from(t: int) -> bool:
        instants = {t, *(start for start, _ in jobs if t < start < placed.start)}
        return all(
            sum(1 for start, end in jobs if start <= u < end) < capacity
            for u in instants
            if u < placed.start
        )

    now = after.problem.now
    return min(t for t in {now, *(end for _, end in jobs)} if t >= now and free_from(t))


def idle_builder_s(call: Call, th_finish: int) -> int:
    now = call.problem.now
    busy = sum(overlap(start, end, now, th_finish) for start, end in builder_jobs(call))
    return call.problem.lanes[BUILDER] * (th_finish - now) - busy


def current_tasks(after: Call) -> list[Task]:
    ids = set(current_level_ids(after.problem.tasks))
    return [t for t in after.problem.tasks if t.id in ids]


def remaining_s(call: Call, current: Task, th_finish: int) -> int:
    """0 when finished at `th_finish`, else the seconds of work left (see the module text)."""
    spans = {job.task_id: (job.start, job.end) for job in call.problem.frozen}
    spans.update((a.task_id, (a.start, a.end)) for a in call.plan.assignments)
    span = spans.get(current.id)
    if span is None:
        return current.duration_s
    start, end = span
    return max(0, end - max(start, th_finish))


def hero_names() -> dict[int, str]:
    return {item["data_id"]: item["name"] for item in pack_doc()["items"] if item["kind"] == "hero"}


def expected_figures(
    call: Call, current: Sequence[Task], th_finish: int, composition: Sequence[int]
) -> dict[str, Any]:
    by_lane: dict[str, int] = {}
    work_by_lane: dict[str, int] = {}
    composition_count = composition_work = 0
    for t in current:
        left = remaining_s(call, t, th_finish)
        if left == 0:
            continue
        by_lane[t.lane] = by_lane.get(t.lane, 0) + 1
        work_by_lane[t.lane] = work_by_lane.get(t.lane, 0) + left
        if t.data_id in composition:
            composition_count += 1
            composition_work += left
    heroes = []
    names = hero_names()
    for data_id in sorted({t.data_id for t in current if t.data_id in names}):
        levels = [t for t in current if t.data_id == data_id]
        done = [t.to_level for t in levels if remaining_s(call, t, th_finish) == 0]
        reached = max(done, default=min(t.to_level for t in levels) - 1)
        heroes.append({"data_id": data_id, "name": names[data_id], "level_reached": reached})
    return {
        "idle_builder_s": idle_builder_s(call, th_finish),
        "unfinished_count": sum(by_lane.values()),
        "unfinished_by_lane": by_lane,
        "unfinished_work_s_by_lane": work_by_lane,
        "unfinished_composition": {"count": composition_count, "work_s": composition_work},
        "heroes": heroes,
    }


def nonzero(mapping: Mapping[str, int]) -> dict[str, int]:
    return {lane: value for lane, value in mapping.items() if value}


def shown_figures(option: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idle_builder_s": option["idle_builder_s"],
        "unfinished_count": option["unfinished_count"],
        "unfinished_by_lane": nonzero(option["unfinished_by_lane"]),
        "unfinished_work_s_by_lane": nonzero(option["unfinished_work_s_by_lane"]),
        "unfinished_composition": dict(option["unfinished_composition"]),
        "heroes": sorted(option["heroes"], key=lambda hero: hero["data_id"]),
    }


def check_option(
    option: Mapping[str, Any],
    call: Call,
    after: Call,
    composition: Sequence[int],
    engine: str = "rollout",
) -> None:
    """The option's figures are those of its own plan, which `simulate` accepts."""
    assert option["status"] == "ok", option
    assert option["reasons"] == []
    assert option["engine"] == engine
    assert option["proof"]["kind"] == call.plan.proof.kind
    sim = simulate(call.problem, call.plan)
    assert sim.valid, sim.violations
    expected = expected_figures(call, current_tasks(after), option["th_finish"], composition)
    assert shown_figures(option) == expected, option["kind"]


def edge_start(problem: Problem, town_hall: Task, modifier: Modifier, lowest: int) -> int:
    """The last whole second before the modifier ends where the town hall may start."""
    t = modifier.end - 1
    while t >= lowest:
        end = t + effective_duration(problem, town_hall, t)
        asleep = problem.policy.mode == "strict" and problem.calendar.is_sleep(end)
        if problem.calendar.can_act(t) and not asleep:
            return t
        t -= 1
    raise AssertionError("no instant between T* and the end of the modifier")


# --------------------------------------------------------------------------- criterion 1


def test_compare_answers_the_target_the_skip_lanes_and_the_options(tmp_path: Path) -> None:
    v = village(tmp_path)
    answer = compare(v)

    assert set(answer) >= {"target", "skip_lanes", "options"}
    assert answer["target"] == {
        "task_id": TH_TASK,
        "data_id": TOWN_HALL,
        "name": TOWN_HALL_NAME,
        "to_level": 2,
    }
    # The latest plan's town_hall_skip_lanes is the default.
    assert answer["skip_lanes"] == ["lab"]
    found = options(answer)
    assert set(found) == {"after", "no_wait"}  # no modifier: no discount_edge
    for option in found.values():
        assert option["status"] == "ok", option
        assert option["reasons"] == []
        assert option["engine"] == "rollout"
        assert option["proof"]["kind"] == "heuristic"


def test_compare_without_a_plan_is_404_no_plan(tmp_path: Path) -> None:
    v = village(tmp_path, plan=None)
    response = v.client.post(URL, json=ENGINE)
    assert response.status_code == 404, response.text
    assert error_code(response) == "no_plan"


def test_compare_stores_nothing(tmp_path: Path) -> None:
    v = village(tmp_path, events=[event(NOW + 10 * DAY + 7 * HOUR)])
    latest = ok(v.client.get(f"/api/villages/{VILLAGE}/plans/latest"))
    before = rows_of_every_table(v)

    answer = compare(v)
    assert "discount_edge" in options(answer)
    compare(v, {**ENGINE, "skip_lanes": []})

    assert rows_of_every_table(v) == before
    again = ok(v.client.get(f"/api/villages/{VILLAGE}/plans/latest"))
    assert again["plan_id"] == latest["plan_id"]
    assert again["assignments"] == latest["assignments"]


@pytest.mark.parametrize(
    ("plan", "request_lanes", "expected"),
    [
        (DT_PLAN, None, ["lab"]),
        (FINISH_TH, None, []),
        ({**ENGINE, "composition": COMPOSITION}, None, []),
        (DT_PLAN, [], []),
        (FINISH_TH, ["lab"], ["lab"]),
    ],
    ids=["plan-skips-lab", "plan-skips-nothing", "no-strategy", "request-empty", "request-lab"],
)
def test_skip_lanes_defaults_to_the_latest_plan_s_and_is_echoed(
    tmp_path: Path,
    plan: Mapping[str, Any],
    request_lanes: list[str] | None,
    expected: list[str],
) -> None:
    v = village(tmp_path, plan=plan)
    body = dict(ENGINE) if request_lanes is None else {**ENGINE, "skip_lanes": request_lanes}
    answer = compare(v, body)
    assert answer["skip_lanes"] == expected
    after = options(answer)["after"]
    # Waiting for the lab means waiting for its 46 days; skipping it, for the hero chain.
    if expected == ["lab"]:
        assert after["th_start"] == HERO_CHAIN_END
    else:
        assert after["th_start"] >= LAB_END


def test_a_plan_stored_without_the_keys_gives_no_skipped_lane_and_no_composition(
    tmp_path: Path,
) -> None:
    v = village(tmp_path)
    # A plan stored before E29: its params have neither town_hall_skip_lanes nor composition.
    with sqlite3.connect(v.data_dir / "clashpilot.db") as connection:
        ((plan_id, text),) = connection.execute("SELECT plan_id, params FROM plans").fetchall()
        params = json.loads(text)
        for key in ("town_hall_skip_lanes", "composition", "composition_first"):
            params.pop(key)
        connection.execute(
            "UPDATE plans SET params = ? WHERE plan_id = ?", (json.dumps(params), plan_id)
        )

    answer = compare(v)
    assert answer["skip_lanes"] == []
    found = options(answer)
    assert found["after"]["th_start"] >= LAB_END
    for option in found.values():
        assert option["unfinished_composition"] == {"count": 0, "work_s": 0}, option["kind"]
    # The lab is still unfinished when the town hall launched at T* ends.
    assert nonzero(found["no_wait"]["unfinished_by_lane"]).get("lab", 0) > 0


@pytest.mark.parametrize("lanes", [["atelier_inexistant"], ["lab", "atelier_inexistant"]])
def test_an_unknown_lane_is_refused_with_unknown_lane(tmp_path: Path, lanes: list[str]) -> None:
    v = village(tmp_path)
    before = rows_of_every_table(v)
    response = v.client.post(URL, json={**ENGINE, "skip_lanes": lanes})
    assert response.status_code == 422, response.text
    assert error_code(response) == "unknown_lane"
    assert rows_of_every_table(v) == before
    # The lanes of the problem are accepted.
    for known in (["lab"], ["builder", "lab"]):
        compare(v, {**ENGINE, "skip_lanes": known})


# --------------------------------------------------------------------------- criterion 2


def test_after_is_the_latest_plan_s_problem_under_town_hall_last(tmp_path: Path) -> None:
    v = village(tmp_path, plan=None)
    with spying() as spy:
        post_plan(v, DT_PLAN)
    created = spy.calls[-1].problem

    with spying() as spy:
        answer = compare(v)
    after = options(answer)["after"]
    call = after_call(spy, after)
    # The latest plan already ran under town_hall_last without the lab: the very same problem.
    assert call.problem == created
    assert after["th_start"] == HERO_CHAIN_END
    assert after["th_finish"] == HERO_CHAIN_END + TH_DURATION
    check_option(after, call, call, COMPOSITION)


@pytest.mark.parametrize(
    "plan",
    [
        {**ENGINE, "composition": COMPOSITION},
        {**ENGINE, "strategy": "balanced", "composition": COMPOSITION},
        FINISH_TH,
    ],
    ids=["no-strategy", "balanced", "finish_th-waiting-for-the-lab"],
)
def test_after_applies_town_hall_last_limited_by_skip_lanes_whatever_the_strategy(
    tmp_path: Path, plan: Mapping[str, Any]
) -> None:
    v = village(tmp_path, plan=None)
    with spying() as spy:
        post_plan(v, plan)
    created = spy.calls[-1].problem
    original = town_hall_task(created)
    assert original is not None

    with spying() as spy:
        answer = compare(v, {**ENGINE, "skip_lanes": ["lab"]})
    after = options(answer)["after"]
    call = after_call(spy, after)

    required = set(current_level_ids(created.tasks, ["lab"]))
    assert required == CURRENT_BUILDER
    town_hall = town_hall_task(call.problem)
    assert town_hall is not None
    # The pack's town hall level 2 requires nothing of its own. Under finish_th the plan's town
    # hall waited for the lab too: the request's skip_lanes replaces that.
    assert set(original.requires) == (CURRENT if plan.get("strategy") == "finish_th" else set())
    assert set(town_hall.requires) == required
    assert not set(town_hall.requires) & LAB_TASKS
    # Only the town hall's requirements differ from the latest plan's problem.
    assert dataclasses.replace(call.problem, tasks=()) == dataclasses.replace(created, tasks=())
    assert [dataclasses.replace(t, requires=()) for t in call.problem.tasks] == [
        dataclasses.replace(t, requires=()) for t in created.tasks
    ]
    for t in call.problem.tasks:
        if t.id != TH_TASK:
            assert t.requires == next(c for c in created.tasks if c.id == t.id).requires, t.id

    assert after["th_start"] == HERO_CHAIN_END
    assert after["th_finish"] == HERO_CHAIN_END + TH_DURATION
    check_option(after, call, call, COMPOSITION)


def test_after_without_a_skipped_lane_waits_for_the_whole_current_level(tmp_path: Path) -> None:
    v = village(tmp_path)
    with spying() as spy:
        answer = compare(v, {**ENGINE, "skip_lanes": []})
    after = options(answer)["after"]
    call = after_call(spy, after)
    town_hall = town_hall_task(call.problem)
    assert town_hall is not None
    assert set(town_hall.requires) >= CURRENT
    assert after["th_start"] >= LAB_END
    assert after["unfinished_count"] == 0
    assert nonzero(after["unfinished_by_lane"]) == {}
    assert after["unfinished_composition"] == {"count": 0, "work_s": 0}
    check_option(after, call, call, COMPOSITION)


def test_the_engine_defaults_to_the_one_of_post_plans(tmp_path: Path) -> None:
    v = village(tmp_path)
    assert "auto" in PLANNERS
    # A delegate to rollout stands in for auto: the default is checked, the run stays short.
    with spying("auto", inner="rollout") as spy:
        answer = compare(v, {})
    assert spy.calls, "the default engine did not run"
    for option in options(answer).values():
        assert option["engine"] == "auto", option

    response = v.client.post(URL, json={"engine": "moteur_inexistant"})
    assert response.status_code == 422, response.text
    assert error_code(response) == "unknown_engine"


# --------------------------------------------------------------------------- criteria 3, 4 and 6


def test_dt_s_case_every_option_carries_the_figures_of_its_own_plan(tmp_path: Path) -> None:
    v = village(tmp_path)
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    after = after_call(spy, found["after"])
    check_option(found["after"], after, after, COMPOSITION)
    no_wait = fixed_call(spy, found["no_wait"])
    check_option(found["no_wait"], no_wait, after, COMPOSITION)

    # After: the hero chain is done, the lab is not (the scout's level 3 runs until day 45, the
    # sapper waits for it: composition first).
    assert found["after"]["heroes"] == [{"data_id": HERO, "name": HERO_NAME, "level_reached": 5}]
    assert nonzero(found["after"]["unfinished_by_lane"]) == {"lab": 2}
    assert found["after"]["unfinished_count"] == 2
    assert found["after"]["unfinished_composition"] == {"count": 1, "work_s": 13 * DAY}
    assert nonzero(found["after"]["unfinished_work_s_by_lane"]) == {"lab": 14 * DAY}

    # No wait: the town hall ends on day 4, before the hero's first level.
    assert found["no_wait"]["heroes"] == [{"data_id": HERO, "name": HERO_NAME, "level_reached": 1}]
    assert nonzero(found["no_wait"]["unfinished_by_lane"])[BUILDER] >= len(HERO_TASKS)
    assert found["no_wait"]["unfinished_composition"]["count"] == 2


def test_without_composition_unfinished_composition_is_zero(tmp_path: Path) -> None:
    v = village(tmp_path, plan={**ENGINE, "strategy": "finish_th", "town_hall_skip_lanes": ["lab"]})
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    after = after_call(spy, found["after"])
    for kind, option in found.items():
        assert option["unfinished_composition"] == {"count": 0, "work_s": 0}, kind
    # The lab is unfinished all the same.
    assert nonzero(found["after"]["unfinished_by_lane"]).get("lab", 0) > 0
    check_option(found["after"], after, after, ())
    check_option(found["no_wait"], fixed_call(spy, found["no_wait"]), after, ())


# --------------------------------------------------------------------------- criterion 4


def test_no_wait_fixes_the_town_hall_at_t_star_without_town_hall_last(tmp_path: Path) -> None:
    v = village(tmp_path)
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    after = after_call(spy, found["after"])
    assert t_star(after) == T_STAR

    no_wait = found["no_wait"]
    assert no_wait["th_start"] == T_STAR
    assert no_wait["th_finish"] == T_STAR + TH_DURATION
    call = fixed_call(spy, no_wait)
    job = town_hall_job(call.problem)
    assert job is not None and job.lane == BUILDER and not job.extra
    # The start_at overlay: the town hall is no task any more, its cost came off the stock, and
    # nothing else of the problem changed (no town_hall_last anywhere).
    assert town_hall_task(call.problem) is None
    assert [t for t in after.problem.tasks if t.id != TH_TASK] == list(call.problem.tasks)
    gold = after.problem.resources.initial["gold"] - TH_GOLD
    assert call.problem.resources.initial["gold"] == gold
    assert town_hall_of(call.plan) is None
    check_option(no_wait, call, after, COMPOSITION)


def test_t_star_counts_the_frozen_jobs(tmp_path: Path) -> None:
    # Three builders: the ballista and the catapult upgrade until day 8, the hero chain takes the
    # third builder from now. Without the frozen jobs one builder would be free from now on.
    v = village(tmp_path, builders=3, running_until=NOW + 8 * DAY)
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    after = after_call(spy, found["after"])
    frozen = {job.task_id for job in after.problem.frozen}
    assert frozen == {task(BALLISTA, 2), task(CATAPULT, 2)}
    assert found["after"]["th_start"] == HERO_CHAIN_END
    assert t_star(after) == NOW + 8 * DAY

    assert found["no_wait"]["th_start"] == NOW + 8 * DAY
    assert found["no_wait"]["th_finish"] == NOW + 8 * DAY + TH_DURATION
    check_option(found["after"], after, after, COMPOSITION)
    check_option(found["no_wait"], fixed_call(spy, found["no_wait"]), after, COMPOSITION)


def test_an_option_that_cannot_happen_gives_its_reasons_not_a_500(tmp_path: Path) -> None:
    # The town hall itself requires the catapult's level 2, still to do at T*.
    pack = pack_doc()
    pack["pack_id"] = "fictional-test-pack-critical-th-requires"
    for row in pack["levels"]:
        if row["data_id"] == TOWN_HALL and row["level"] == 2:
            row["requires"] = [{"data_id": CATAPULT, "level": 2}]
    v = village(tmp_path, pack=pack)
    response = v.client.post(URL, json=ENGINE)
    assert response.status_code == 200, response.text
    found = options(response.json())
    assert found["after"]["status"] == "ok"
    no_wait = found["no_wait"]
    assert no_wait["status"] == "infeasible", no_wait
    assert no_wait["th_start"] is None and no_wait["th_finish"] is None
    assert [r["code"] for r in no_wait["reasons"]] == ["missing_prerequisite"]
    assert all(isinstance(r["message"], str) and r["message"] for r in no_wait["reasons"])


# --------------------------------------------------------------------------- criterion 5


def test_discount_edge_starts_at_the_last_instant_before_the_modifier_ends(tmp_path: Path) -> None:
    # Builder durations halved from before now until day 10 + 7 h.
    discount = event(NOW + 10 * DAY + 7 * HOUR)
    v = village(tmp_path, events=[discount])
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    assert set(found) == {"after", "no_wait", "discount_edge"}
    after = after_call(spy, found["after"])
    star = t_star(after)
    assert star < discount["end"] < found["after"]["th_start"]

    edge = found["discount_edge"]
    assert edge["th_start"] == discount["end"] - 1
    assert edge["th_finish"] == discount["end"] - 1 + TH_DURATION // 2
    call = fixed_call(spy, edge)
    check_option(edge, call, after, COMPOSITION)
    # No wait still starts at T*, under the same modifier.
    assert found["no_wait"]["th_start"] == star
    assert found["no_wait"]["th_finish"] == star + TH_DURATION // 2
    check_option(found["no_wait"], fixed_call(spy, found["no_wait"]), after, COMPOSITION)


def test_discount_edge_starts_awake_and_ends_awake_under_strict(tmp_path: Path) -> None:
    # Nights 23:00-07:00 UTC under strict; builder durations at 40% until day 10 + 2 h (10:00):
    # the town hall's 19.2 hours started before 11:48 would end asleep, so it starts at 22:59:59
    # the evening before.
    discount = event(NOW + 10 * DAY + 2 * HOUR, duration_pct=40)
    v = village(tmp_path, events=[discount], profile=STRICT_NIGHTS)
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    after = after_call(spy, found["after"])
    star = t_star(after)
    assert star < discount["end"] < found["after"]["th_start"]
    town_hall = town_hall_task(after.problem)
    assert town_hall is not None
    (modifier,) = after.problem.modifiers
    expected = edge_start(after.problem, town_hall, modifier, star)
    assert expected == discount["end"] - 11 * HOUR - 1

    edge = found["discount_edge"]
    assert edge["th_start"] == expected
    reduced = TH_DURATION * 40 // 100
    assert edge["th_finish"] == expected + reduced
    check_option(edge, fixed_call(spy, edge), after, COMPOSITION)


@pytest.mark.parametrize(
    "modifier",
    [
        None,
        event(NOW + 10 * DAY + 7 * HOUR, duration_pct=100, cost_pct=50),
        event(NOW + 10 * DAY + 7 * HOUR, lane="lab"),
        event(NOW + 6 * HOUR),
        event(NOW + 80 * DAY),
    ],
    ids=["none", "cost-only", "lab-lane", "ends-before-t-star", "ends-after-the-town-hall"],
)
def test_discount_edge_exists_only_for_a_builder_reduction_ending_in_between(
    tmp_path: Path, modifier: Mapping[str, Any] | None
) -> None:
    v = village(tmp_path, events=() if modifier is None else [modifier])
    with spying() as spy:
        answer = compare(v)
    found = options(answer)
    assert set(found) == {"after", "no_wait"}
    after = after_call(spy, found["after"])
    if modifier is not None and modifier["lane"] == BUILDER and modifier["duration_pct"] < 100:
        star = t_star(after)
        assert not star < modifier["end"] < found["after"]["th_start"]


# --------------------------------------------------------------------------- criterion 7


def test_on_the_hero_chain_fixture_no_wait_idles_less_and_ends_sooner(tmp_path: Path) -> None:
    v = village(tmp_path)
    # The fixture: the hero chain fixes the date, not the builders' work.
    path = ok(v.client.get(f"/api/villages/{VILLAGE}/critical-path?skip_lanes=lab"))
    assert path["bound"]["kind"] == "chain", path["bound"]
    found = options(compare(v))
    no_wait, after = found["no_wait"], found["after"]
    assert no_wait["idle_builder_s"] < after["idle_builder_s"]
    assert no_wait["th_finish"] < after["th_finish"]
    # The second builder of the after plan waits 28 days for the town hall.
    assert after["idle_builder_s"] >= 28 * DAY


# --------------------------------------------------------------------------- criterion 8


# The scenarios start from a plan without strategy: under finish_th the town hall would wait for
# the current level, still to do, and no instant would be accepted.
NO_STRATEGY = {**ENGINE, "composition": COMPOSITION}


def scenario(v: Village, overlay: Mapping[str, Any]) -> dict[str, Any]:
    body = {"name": "Hôtel de Ville plus tard", "overlays": [dict(overlay)], **ENGINE}
    created: dict[str, Any] = ok(v.client.post(SCENARIOS, json=body), 201)
    return created


def assignment_of(body: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    found = [a for a in body["assignments"] if a["task_id"] == task_id]
    assert len(found) == 1, body["assignments"]
    return found[0]


def test_start_at_fixes_the_task_at_the_given_instant_when_the_simulator_accepts_it(
    tmp_path: Path,
) -> None:
    v = village(tmp_path, plan=NO_STRATEGY)
    at = NOW + 5 * DAY + 17
    overlay = {"type": "start_at", "task_id": TH_TASK, "at": at}
    created = scenario(v, overlay)
    assert created["status"] == "ok", created
    assert created["overlays"] == [overlay]
    town_hall = assignment_of(created, TH_TASK)
    assert (town_hall["start"], town_hall["end"]) == (at, at + TH_DURATION)
    assert town_hall["lane"] == BUILDER
    listed = ok(v.client.get(SCENARIOS))
    assert [row["overlays"] for row in listed] == [[overlay]]


def test_start_at_takes_the_first_start_the_simulator_accepts_after_the_instant(
    tmp_path: Path,
) -> None:
    # Both builders upgrade the ballista and the catapult until day 8.
    v = village(tmp_path, running_until=NOW + 8 * DAY, plan=NO_STRATEGY)
    created = scenario(v, {"type": "start_at", "task_id": TH_TASK, "at": NOW + DAY})
    assert created["status"] == "ok", created
    town_hall = assignment_of(created, TH_TASK)
    assert (town_hall["start"], town_hall["end"]) == (NOW + 8 * DAY, NOW + 8 * DAY + TH_DURATION)


@pytest.mark.parametrize(
    ("task_id", "at", "code"),
    [
        ("9200099:0:2", NOW + DAY, "task_not_found"),
        (task(CATAPULT, 2), NOW + DAY, "already_started"),
        (TH_TASK, NOW + 95 * DAY, "no_slot"),
    ],
    ids=["unknown-task", "running-job", "beyond-the-horizon"],
)
def test_start_at_fails_with_the_reasons_of_start_now(
    tmp_path: Path, task_id: str, at: int, code: str
) -> None:
    v = village(tmp_path, running_until=NOW + 8 * DAY, plan=NO_STRATEGY)
    created = scenario(v, {"type": "start_at", "task_id": task_id, "at": at})
    assert created["status"] == "infeasible", created
    assert [r["code"] for r in created["reasons"]] == [code]
    assert created["assignments"] == []


# --------------------------------------------------------------------------- API document


def schema_names(node: Any) -> Iterator[str]:
    """Property and parameter names found anywhere in an OpenAPI document."""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            yield from (str(name) for name in properties)
        if node.get("in") in {"query", "header", "path", "cookie"} and "name" in node:
            yield str(node["name"])
        for value in node.values():
            yield from schema_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from schema_names(value)


def resolved(schema: Mapping[str, Any], node: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = node.get("$ref")
    if ref is None:
        return node
    found: Mapping[str, Any] = schema
    for part in str(ref).removeprefix("#/").split("/"):
        found = found[part]
    return found


def test_the_openapi_document_declares_the_route_without_login_like_property(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    schema = TestClient(create_app(settings, clock=Clock(NOW))).get("/openapi.json").json()

    assert ROUTE in schema["paths"], sorted(schema["paths"])
    operation = schema["paths"][ROUTE]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    request = resolved(schema, body)
    assert set(request["properties"]) == {"engine", "profile", "skip_lanes"}
    assert not request.get("required")
    assert sorted({name for name in schema_names(schema) if FORBIDDEN_NAMES.search(name)}) == []


@pytest.mark.timeout(120)
def test_the_generated_client_schema_holds_the_route_without_drift() -> None:
    assert f'"{ROUTE}"' in SCHEMA_TS.read_text(encoding="utf-8"), "run node scripts/gen-api.mjs"

    done = subprocess.run(
        ["node", str(ROOT / "scripts" / "gen-api.mjs"), "--check"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
