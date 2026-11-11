"""Acceptance tests for E19: magic items and gems advisor.

SPEC section 4 (Problem, FrozenJob, Proof, objective `score`), section 5 (`rules` of the pack: the
key of a rule is the item name; a missing or malformed key means "not supported"; `book_*`,
`builder_potion`, `research_potion`, `rune_*`, `gem_finish`, `helper_upgrade`) and section 7
(budget: the HTTP tests pass the fields of `TEST_CONFIG` as `Settings.planner_overrides`, engine
`rollout`, profile `repro`).

Frozen with the task. An option is a what-if scenario (E18) of the latest real plan judged by
`simulate`: the advisor never writes the real state. Every call goes through HTTP on fictional
packs derived in memory from `backend/tests/fixtures/packs/fictional_pack_items.json` (left
untouched) and invented exports.

Shapes that the task text leaves open are fixed here:

- `POST /api/villages/{village_id}/items/compare` takes `{"item", "candidate_tasks"?, "engine"?,
  "profile"?}`; `item` is a rule key of the active pack, `candidate_tasks` a list of task ids
  (running or planned) the item may be used on. `engine` and `profile` default as for the
  scenarios; these tests always send `rollout` and `repro`. The village needs a plan: the latest
  one is the reference (`no_plan` 404 otherwise).
- `POST /api/villages/{village_id}/gems/compare` takes `{"candidate_tasks"?, "engine"?,
  "profile"?}` and compares keeping the gems with finishing a job with gems.
- Both answer 200 with `{"status", "reasons", "options", "proof", "reference_plan_id", "scope"}`;
  the gems answer also carries `"helpers": {"status", "reasons", "options"}`.
  - `status` is `ok`, `unsupported` (the rule is absent or malformed in the active pack) or, for an
    item, `unavailable` (`magic_items` of the manual input gives it quantity 0). `reasons` is a
    list of `{"code", "message"}` (French message), non-empty unless `ok`; an item without its rule
    gives the code `unsupported_item`, as the `use_item` overlay of E18. An `unsupported` or
    `unavailable` answer has no option other than `keep`, and no engine runs for an unsupported
    item.
  - `options` is the ranking, by `score_delta` decreasing. Each option is `{"kind", "task_id",
    "at", "score_delta", "finish_delta_s", "consequences", "reliability"}`:
    - `kind`: `keep` (keep the item or the gems), `use` (use the item) or `gem_finish`;
    - `task_id`: the task the option acts on, null for `keep` and for an item used on a lane or a
      resource (potion, rune) rather than on one task;
    - `at`: UTC epoch of the use (null allowed for `keep`);
    - `score_delta`: `score` of the plan with the option minus `score` of the plan without item
      (the plan the scenario engine makes from the latest plan's inputs without overlay, whose
      `proof.objective` the latest plan carries), both judged by `simulate`; exactly 0 for `keep`;
    - `finish_delta_s`: end of `task_id` with the option minus its end without it (0 for `keep`);
    - `consequences`: list of `{"code", "message", ...}`. A job whose end the option moves is listed
      with the code `job_shortened` and `task_id`, `end_before`, `end_after`; a resource a rune
      fills with the code `resource_filled` and `resource`, `before` (stock at `at`, before any
      payment of that instant), `after`;
    - `reliability`: `{"level", "factors"}` as in the plan explanations (`high`, `medium`, `low`).
    A gem option adds `gems`: the gems of the first rate of `gem_finish` (by increasing `up_to_s`)
    whose `up_to_s` covers the seconds left, `end - at`. A `use` option of the same item can be
    the same `use_item` overlay of E18 (`{"item", "task_id", "at"}`).
  - Without `candidate_tasks` the options cover at least: for a book, each job running at the
    plan's `now` whose item kind is in `kinds`; for a potion and a rune, a use at the plan's `now`;
    for gems, each job running at the plan's `now`. With `candidate_tasks`, the item options act on
    those tasks only.
  - `helpers.options` holds one `{"data_id", "from_level", "to_level", "cost", "score_delta",
    "consequences", "reliability"}` per helper of the export (section `helpers`) whose next level
    has a row in `helper_upgrade`, from its imported level; `score_delta` may be null when the pack
    has no rule for the helper's effect. Without `helper_upgrade`, `helpers.status` is
    `unsupported` with its reasons and no option.
  - `proof` is the proof of the reference plan (`kind` among `heuristic`, `feasible`,
    `optimal_window`), `reference_plan_id` its `plan_id`, `scope` a non-empty French text giving
    the scope of the comparison. No text of the answer outside `proof` says "optimal": the ranking
    is never presented as optimal (the proof is shown apart).

The feature calls go through HTTP only, so that each guarantee fails on its own for as long as the
feature does not exist.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import importlib.util
import json
import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MINUTE = 60
HOUR = 3600

BACKEND = Path(__file__).resolve().parents[2]
TESTS = BACKEND / "tests"
ITEMS_PACK = TESTS / "fixtures" / "packs" / "fictional_pack_items.json"
SUPPORT_PROBLEMS = TESTS / "support" / "problems.py"

PARIS = "Europe/Paris"
# 2026-11-09T09:00 in Paris (CET, UTC+1): a whole day before 23:00 sleep.
HTTP_NOW = int(datetime.datetime(2026, 11, 9, 8, 0, tzinfo=datetime.UTC).timestamp())
EXPORTED_AT = HTTP_NOW - HOUR
TAG = "#FICTIF19"
VILLAGE = "FICTIF19"
BUILDERS = 3

# Ids of the fictional items pack.
PACK_TOWN_HALL = 9000001
PACK_HUT = 9000002
PACK_LAB = 9000003
PACK_CANNON = 9000004
PACK_HERO = 9000006
PACK_TROOP = 9000007
# Fictional helpers added in memory to the pack.
HELPER_ONE = 9000010
HELPER_TWO = 9000011

PLAN_REQUEST = {"horizon_days": 30, "engine": "rollout", "profile": "repro"}
ENGINE = {"engine": "rollout", "profile": "repro"}

# Running jobs of the main village, ending after now.
CANNON_END = HTTP_NOW + 25 * MINUTE  # builder lane, cannon 2 -> 3 (1800 s)
TOWN_HALL_END = HTTP_NOW + 50 * MINUTE  # builder lane, town hall 2 -> 3 (3600 s)
HERO_END = HTTP_NOW + 3 * HOUR  # lab lane, hero 1 -> 2 (14400 s)
CANNON_TASK = f"{PACK_CANNON}:0:3"
TOWN_HALL_TASK = f"{PACK_TOWN_HALL}:0:3"
HERO_TASK = f"{PACK_HERO}:0:2"
RUNNING = {CANNON_TASK: CANNON_END, TOWN_HALL_TASK: TOWN_HALL_END, HERO_TASK: HERO_END}
LANE_OF = {CANNON_TASK: "builder", TOWN_HALL_TASK: "builder", HERO_TASK: "lab"}

# Potion rules of the main pack, unlike the file's, so that the values must come from the pack.
BUILDER_POTION = {"duration_multiplier": 4, "duration_s": 600}
RESEARCH_POTION = {"duration_multiplier": 3, "duration_s": 900}
GOLD_CAP = 8000  # storage_override of the main village
GOLD_AT_NOW = 2000  # 1000 typed one hour before now, plus 1000 an hour of income

OPTION_FIELDS = ("kind", "task_id", "at", "score_delta", "finish_delta_s", "consequences")
ANSWER_FIELDS = ("status", "reasons", "options", "proof", "reference_plan_id", "scope")

# --------------------------------------------------------------------------- support


def support() -> ModuleType:
    """`backend/tests/support/problems.py`, loaded from its path."""
    name = "e19_acceptance_support_problems"
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


def planners() -> dict[str, Any]:
    import clashpilot.planning  # noqa: F401  (registers the engines)
    from clashpilot.planning.registry import PLANNERS

    return PLANNERS


class Counting:
    """Delegates to the real engine and counts its calls."""

    def __init__(self, inner: Any, calls: list[str], key: str) -> None:
        self.inner = inner
        self.calls = calls
        self.key = key

    def solve(self, problem: Any, objective: Any, config: Any, reference: Any = None) -> Any:
        self.calls.append(self.key)
        return self.inner.solve(problem, objective, config, reference)


@contextmanager
def counting_engines() -> Iterator[list[str]]:
    """Every registered engine counts its calls while the block runs."""
    registry = planners()
    saved = dict(registry)
    calls: list[str] = []
    for key, inner in saved.items():
        registry[key] = Counting(inner, calls, key)
    try:
        yield calls
    finally:
        registry.clear()
        registry.update(saved)


# --------------------------------------------------------------------------- village fixtures


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@dataclasses.dataclass
class Village:
    client: TestClient
    data_dir: Path


def items_pack() -> dict[str, Any]:
    pack: dict[str, Any] = json.loads(ITEMS_PACK.read_text(encoding="utf-8"))
    return pack


def main_pack() -> dict[str, Any]:
    """The items pack with its own potion values, a hero book and two helpers."""
    pack = copy.deepcopy(items_pack())
    pack["pack_id"] = "fictional-test-pack-advisor"
    rules = pack["rules"]
    rules["builder_potion"] = {**BUILDER_POTION, "source": "s2"}
    rules["research_potion"] = {**RESEARCH_POTION, "source": "s2"}
    rules["book_of_fighting"] = {"kinds": ["hero"], "source": "s2"}
    for data_id, name in ((HELPER_ONE, "Assistant de test"), (HELPER_TWO, "Apprenti de test")):
        pack["items"].append(
            {
                "data_id": data_id,
                "name": name,
                "kind": "helper",
                "village": "home",
                "lane": "none",
                "category": "helper",
                "tags": [],
                "max_count_by_th": {"1": 1},
            }
        )
    return pack


def running_job(data_id: int, level: int, finish_at: int) -> dict[str, Any]:
    """An upgrade from `level` in progress at the export, ending at `finish_at`."""
    return {"data": data_id, "lvl": level, "timer": finish_at - EXPORTED_AT}


def export_doc(
    buildings: Sequence[dict[str, Any]],
    heroes: Sequence[dict[str, Any]],
    helpers: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "tag": TAG,
        "timestamp": EXPORTED_AT,
        "buildings": [dict(b) for b in buildings],
        "units": [{"data": PACK_TROOP, "lvl": 1}],
        "heroes": [dict(h) for h in heroes],
    }
    if helpers:
        doc["helpers"] = [dict(h) for h in helpers]
    return doc


def main_export() -> dict[str, Any]:
    """Town hall 2 -> 3 and cannon 2 -> 3 on two builders, the hero 1 -> 2 in the lab."""
    return export_doc(
        [
            running_job(PACK_TOWN_HALL, 2, TOWN_HALL_END),
            {"data": PACK_HUT, "lvl": 1},
            {"data": PACK_HUT, "lvl": 1},
            {"data": PACK_LAB, "lvl": 1},
            running_job(PACK_CANNON, 2, CANNON_END),
        ],
        [running_job(PACK_HERO, 1, HERO_END)],
        [{"data": HELPER_ONE, "lvl": 1}, {"data": HELPER_TWO, "lvl": 2}],
    )


def resource(value: int) -> dict[str, Any]:
    return {"value": value, "status": "confirmed", "as_of": EXPORTED_AT}


MAIN_ITEMS = {
    "book_of_building": 1,
    "book_of_fighting": 0,
    "builder_potion": 1,
    "research_potion": 1,
    "rune_of_gold": 1,
}


def manual_doc(
    *,
    gold: int = 1000,
    magic_items: Mapping[str, int] = MAIN_ITEMS,
    storage_override: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    rate = {"low": 500, "expected": 1000, "high": 1500}
    return {
        "resources": {
            "gold": resource(gold),
            "elixir": resource(50_000),
            "dark_elixir": resource(5_000),
            "gems": resource(500),
        },
        "builders_total": BUILDERS,
        "farm_rate": {"gold": rate, "elixir": rate, "dark_elixir": rate},
        "magic_items": dict(magic_items),
        "storage_override": dict(storage_override or {}),
    }


def profile_doc(*, sleep: bool = True) -> dict[str, Any]:
    weekly = [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}]
    return {
        "tz": PARIS,
        "weekly": weekly if sleep else [],
        "exceptions": [],
        "policy": {"mode": "progress", "tolerance_minutes": 0, "max_logins_per_day": None},
    }


def village(
    data_dir: Path,
    *,
    pack: dict[str, Any],
    export: dict[str, Any],
    manual: dict[str, Any],
    profile: dict[str, Any] | None = None,
) -> Village:
    """A village with a pack, an export, a manual input, a schedule and a plan."""
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=data_dir,
        frontend_dist=data_dir / "no-dist",
        planner_overrides=dataclasses.asdict(support().TEST_CONFIG),
    )
    client = TestClient(create_app(settings, clock=Clock(HTTP_NOW)))
    response = client.post("/api/gamedata/packs", json=pack)
    assert response.status_code in (200, 201), response.text
    response = client.put(f"/api/gamedata/packs/{pack['pack_id']}/active")
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/villages/import",
        content=json.dumps(export).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["village_id"] == VILLAGE
    response = client.put(f"/api/villages/{VILLAGE}/manual", json=manual)
    assert response.status_code == 200, response.text
    response = client.put(
        f"/api/villages/{VILLAGE}/schedule", json=profile_doc() if profile is None else profile
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/api/villages/{VILLAGE}/plans", json=PLAN_REQUEST)
    assert response.status_code == 201, response.text
    return Village(client, data_dir)


@pytest.fixture(scope="module")
def main(tmp_path_factory: pytest.TempPathFactory) -> Village:
    """The main village; the advisor only reads it, so the tests of this module share it."""
    return village(
        tmp_path_factory.mktemp("advisor-main"),
        pack=main_pack(),
        export=main_export(),
        manual=manual_doc(storage_override={"gold": GOLD_CAP}),
    )


# --------------------------------------------------------------------------- HTTP helpers

ITEMS_URL = f"/api/villages/{VILLAGE}/items/compare"
GEMS_URL = f"/api/villages/{VILLAGE}/gems/compare"


def get_json(v: Village, path: str) -> Any:
    response = v.client.get(path)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def latest(v: Village) -> dict[str, Any]:
    body: dict[str, Any] = get_json(v, f"/api/villages/{VILLAGE}/plans/latest")
    return body


def compare_item(
    v: Village, item: str, candidate_tasks: Sequence[str] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"item": item, **ENGINE}
    if candidate_tasks is not None:
        body["candidate_tasks"] = list(candidate_tasks)
    response = v.client.post(ITEMS_URL, json=body)
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    check_answer(v, answer)
    return answer


def compare_gems(v: Village) -> dict[str, Any]:
    response = v.client.post(GEMS_URL, json=dict(ENGINE))
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    check_answer(v, answer)
    helpers = answer["helpers"]
    assert helpers["status"] in ("ok", "unsupported"), helpers
    check_reasons(helpers)
    assert isinstance(helpers["options"], list), helpers
    return answer


# --------------------------------------------------------------------------- shape checks


def check_reasons(body: Mapping[str, Any]) -> None:
    assert isinstance(body["reasons"], list), body
    for reason in body["reasons"]:
        assert isinstance(reason["code"], str) and reason["code"], reason
        assert isinstance(reason["message"], str) and reason["message"].strip(), reason
    if body["status"] != "ok":
        assert body["reasons"], body


def check_reliability(reliability: Any) -> None:
    assert isinstance(reliability, dict), reliability
    assert reliability["level"] in ("high", "medium", "low"), reliability
    assert isinstance(reliability["factors"], list), reliability


def check_option(option: Mapping[str, Any]) -> None:
    for name in (*OPTION_FIELDS, "reliability"):
        assert name in option, (name, option)
    assert option["kind"] in ("keep", "use", "gem_finish"), option
    assert option["task_id"] is None or isinstance(option["task_id"], str), option
    if option["kind"] == "keep":
        assert option["task_id"] is None, option
        assert option["at"] is None or isinstance(option["at"], int), option
    else:
        assert isinstance(option["at"], int), option
    assert isinstance(option["score_delta"], int | float), option
    assert math.isfinite(option["score_delta"]), option
    assert isinstance(option["finish_delta_s"], int), option
    assert isinstance(option["consequences"], list), option
    for consequence in option["consequences"]:
        assert isinstance(consequence["code"], str) and consequence["code"], consequence
        assert isinstance(consequence["message"], str), consequence
        assert consequence["message"].strip(), consequence
    check_reliability(option["reliability"])


def never_optimal(value: Any, path: str = "") -> None:
    """No key and no text outside `proof` presents the ranking as optimal."""
    if isinstance(value, dict):
        for key, inner in value.items():
            assert "optim" not in str(key).lower(), f"{path}.{key}"
            if key != "proof":
                never_optimal(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            never_optimal(inner, f"{path}[{index}]")
    elif isinstance(value, str):
        assert "optim" not in value.lower(), (path, value)


def check_answer(v: Village, answer: Mapping[str, Any]) -> None:
    for name in ANSWER_FIELDS:
        assert name in answer, (name, answer)
    assert answer["status"] in ("ok", "unsupported", "unavailable"), answer
    check_reasons(answer)
    options = answer["options"]
    assert isinstance(options, list), answer
    for option in options:
        check_option(option)
    never_optimal(answer)
    if answer["status"] != "ok":
        assert all(option["kind"] == "keep" for option in options), options
        return
    keeps = [option for option in options if option["kind"] == "keep"]
    assert len(keeps) == 1, options
    assert keeps[0]["score_delta"] == 0, keeps
    assert keeps[0]["finish_delta_s"] == 0, keeps
    deltas = [option["score_delta"] for option in options]
    assert deltas == sorted(deltas, reverse=True), f"not ranked: {deltas}"
    proof = answer["proof"]
    assert isinstance(proof, dict), answer
    assert proof["kind"] in ("heuristic", "feasible", "optimal_window"), proof
    assert answer["reference_plan_id"] == latest(v)["plan_id"], answer
    assert isinstance(answer["scope"], str) and answer["scope"].strip(), answer


def uses(answer: Mapping[str, Any], kind: str = "use") -> list[dict[str, Any]]:
    return [option for option in answer["options"] if option["kind"] == kind]


def shortened(option: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The `job_shortened` consequences of an option, by task id."""
    found: dict[str, dict[str, Any]] = {}
    for consequence in option["consequences"]:
        if consequence["code"] == "job_shortened":
            assert isinstance(consequence["task_id"], str), consequence
            assert isinstance(consequence["end_before"], int), consequence
            assert isinstance(consequence["end_after"], int), consequence
            assert consequence["task_id"] not in found, option["consequences"]
            found[consequence["task_id"]] = consequence
    return found


def potion_end(rule: Mapping[str, int], at: int, end: int) -> int:
    """SPEC 5: `r` seconds left, `m` the multiplier, `D` the duration."""
    left = end - at
    m, d = rule["duration_multiplier"], rule["duration_s"]
    if left <= d * m:
        return at + math.ceil(left / m)
    return end - d * (m - 1)


def gems_for(rates: Sequence[Mapping[str, int]], left: int) -> int:
    for rate in sorted(rates, key=lambda r: r["up_to_s"]):
        if left <= rate["up_to_s"]:
            return rate["gems"]
    raise AssertionError(f"no rate covers {left} s")


# --------------------------------------------------------------------------- ranking and keep


def test_both_routes_are_documented(main: Village) -> None:
    paths = main.client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/api/villages/{village_id}/items/compare"]
    assert "post" in paths["/api/villages/{village_id}/gems/compare"]


def test_every_comparison_is_a_ranking_with_a_keep_option_and_its_proof(main: Village) -> None:
    plan = latest(main)
    for item in ("book_of_building", "builder_potion", "research_potion", "rune_of_gold"):
        answer = compare_item(main, item)
        assert answer["status"] == "ok", (item, answer)
        assert uses(answer), (item, answer)
        assert answer["proof"]["kind"] == plan["proof"]["kind"], answer
    gems = compare_gems(main)
    assert gems["status"] == "ok", gems
    assert uses(gems, "gem_finish"), gems

    response = main.client.post(
        "/api/villages/NOSUCH99/items/compare", json={"item": "book_of_building", **ENGINE}
    )
    assert response.status_code == 404, response.text


def test_a_book_of_an_unlocking_task_beats_a_book_of_a_longer_task(tmp_path: Path) -> None:
    # The lab (10 h, running for 5 h more) unlocks a 48 h hero and a 24 h troop on the lab lane;
    # the cannon (20 h, running for 15 h more) is at its last level and unlocks nothing. No sleep
    # window, so that no end at night weighs on either plan.
    pack = copy.deepcopy(items_pack())
    pack["pack_id"] = "fictional-test-pack-advisor-unlock"
    durations = {
        (PACK_LAB, 2): 10 * HOUR,
        (PACK_CANNON, 4): 20 * HOUR,
        (PACK_HERO, 2): 48 * HOUR,
        (PACK_TROOP, 2): 24 * HOUR,
    }
    for row in pack["levels"]:
        row["duration_s"] = durations.get((row["data_id"], row["level"]), row["duration_s"])
    lab_end, cannon_end = HTTP_NOW + 5 * HOUR, HTTP_NOW + 15 * HOUR
    export = export_doc(
        [
            {"data": PACK_TOWN_HALL, "lvl": 2},
            {"data": PACK_HUT, "lvl": 1},
            {"data": PACK_HUT, "lvl": 1},
            running_job(PACK_LAB, 1, lab_end),
            running_job(PACK_CANNON, 3, cannon_end),
        ],
        [{"data": PACK_HERO, "lvl": 1}],
    )
    v = village(
        tmp_path,
        pack=pack,
        export=export,
        manual=manual_doc(gold=20_000),
        profile=profile_doc(sleep=False),
    )
    lab_task, cannon_task = f"{PACK_LAB}:0:2", f"{PACK_CANNON}:0:4"

    answer = compare_item(v, "book_of_building", [cannon_task, lab_task])
    assert answer["status"] == "ok", answer
    by_task = {option["task_id"]: option for option in uses(answer)}
    assert set(by_task) == {lab_task, cannon_task}, answer["options"]
    lab, cannon = by_task[lab_task], by_task[cannon_task]
    keep = uses(answer, "keep")[0]

    # The book ends each running job at once.
    assert HTTP_NOW <= lab["at"] < lab_end and lab["finish_delta_s"] == lab["at"] - lab_end
    assert HTTP_NOW <= cannon["at"] < cannon_end
    assert cannon["finish_delta_s"] == cannon["at"] - cannon_end
    assert -cannon["finish_delta_s"] > -lab["finish_delta_s"], "the cannon saves more time"

    # The shorter job that unlocks work ranks first, strictly.
    assert answer["options"][0] is not keep and answer["options"][0]["task_id"] == lab_task
    assert lab["score_delta"] > cannon["score_delta"], answer["options"]
    assert lab["score_delta"] > keep["score_delta"] == 0, answer["options"]

    # The delta is the scenario's simulated score against the plan without item.
    reference = latest(v)["proof"]["objective"]
    for option in (lab, cannon):
        overlay = {"type": "use_item", "item": "book_of_building", **_target(option)}
        response = v.client.post(
            f"/api/villages/{VILLAGE}/scenarios",
            json={"name": "Livre", "overlays": [overlay], **ENGINE},
        )
        assert response.status_code == 201, response.text
        scenario = response.json()
        assert scenario["status"] == "ok", scenario
        expected = scenario["proof"]["objective"] - reference
        assert math.isclose(option["score_delta"], expected, rel_tol=1e-9, abs_tol=1e-9), (
            option,
            expected,
        )


def _target(option: Mapping[str, Any]) -> dict[str, Any]:
    return {"task_id": option["task_id"], "at": option["at"]}


# --------------------------------------------------------------------------- effects of the items


def test_a_book_ends_a_started_building_job_at_once_and_never_a_hero(main: Village) -> None:
    answer = compare_item(main, "book_of_building")
    assert answer["status"] == "ok", answer
    targets = {option["task_id"] for option in uses(answer)}
    assert {CANNON_TASK, TOWN_HALL_TASK} <= targets, answer["options"]
    assert HERO_TASK not in targets, "the book of the pack only covers buildings"
    for option in uses(answer):
        if option["task_id"] not in RUNNING:
            continue
        end = RUNNING[option["task_id"]]
        at = option["at"]
        assert HTTP_NOW <= at < end, option
        assert option["finish_delta_s"] == at - end, option
        jobs = shortened(option)
        assert set(jobs) == {option["task_id"]}, option["consequences"]
        assert jobs[option["task_id"]]["end_before"] == end
        assert jobs[option["task_id"]]["end_after"] == at


@pytest.mark.parametrize(
    ("item", "lane", "rule"),
    [("builder_potion", "builder", BUILDER_POTION), ("research_potion", "lab", RESEARCH_POTION)],
)
def test_a_potion_speeds_up_the_running_jobs_of_its_lane_only(
    main: Village, item: str, lane: str, rule: Mapping[str, int]
) -> None:
    answer = compare_item(main, item)
    assert answer["status"] == "ok", answer
    now_options = [option for option in uses(answer) if option["at"] == HTTP_NOW]
    assert now_options, f"no use of {item} at now: {answer['options']}"
    for option in now_options:
        jobs = shortened(option)
        for task, end in RUNNING.items():
            if LANE_OF[task] == lane:
                assert task in jobs, (task, option["consequences"])
                assert jobs[task]["end_before"] == end, jobs[task]
                assert jobs[task]["end_after"] == potion_end(rule, HTTP_NOW, end), jobs[task]
            else:
                assert task not in jobs, (task, option["consequences"])
    # Both branches of the rule are met: 1500 s left on the cannon, 3000 s on the town hall.
    if lane == "builder":
        assert potion_end(rule, HTTP_NOW, CANNON_END) == HTTP_NOW + 375
        assert potion_end(rule, HTTP_NOW, TOWN_HALL_END) == TOWN_HALL_END - 1800


def test_a_rune_fills_its_resource_up_to_the_cap(main: Village) -> None:
    answer = compare_item(main, "rune_of_gold")
    assert answer["status"] == "ok", answer
    now_options = [option for option in uses(answer) if option["at"] == HTTP_NOW]
    assert now_options, answer["options"]
    for option in now_options:
        filled = [c for c in option["consequences"] if c["code"] == "resource_filled"]
        assert len(filled) == 1, option["consequences"]
        assert filled[0]["resource"] == "gold", filled
        assert filled[0]["before"] == GOLD_AT_NOW, filled
        assert filled[0]["after"] == GOLD_CAP, filled
        assert shortened(option) == {}, option["consequences"]


# --------------------------------------------------------------------------- unsupported items


def test_an_item_without_its_rule_is_unsupported_and_runs_no_engine(main: Village) -> None:
    with counting_engines() as calls:
        answer = compare_item(main, "book_of_heroes")
    assert calls == [], f"an engine ran for an unsupported item: {calls}"
    assert answer["status"] == "unsupported", answer
    assert "unsupported_item" in [reason["code"] for reason in answer["reasons"]], answer
    assert uses(answer) == []


def test_an_item_of_quantity_zero_is_unavailable(main: Village) -> None:
    answer = compare_item(main, "book_of_fighting")
    assert answer["status"] == "unavailable", answer
    assert answer["reasons"], answer
    assert uses(answer) == []


def test_without_their_rules_potions_gems_and_helpers_are_unsupported(tmp_path: Path) -> None:
    pack = main_pack()
    pack["pack_id"] = "fictional-test-pack-advisor-bare"
    for key in ("builder_potion", "gem_finish", "helper_upgrade"):
        del pack["rules"][key]
    v = village(
        tmp_path,
        pack=pack,
        export=main_export(),
        manual=manual_doc(storage_override={"gold": GOLD_CAP}),
    )

    with counting_engines() as calls:
        potion = compare_item(v, "builder_potion")
    assert calls == [], f"an engine ran for an unsupported item: {calls}"
    assert potion["status"] == "unsupported", potion
    assert "unsupported_item" in [reason["code"] for reason in potion["reasons"]], potion
    assert uses(potion) == []

    gems = compare_gems(v)
    assert gems["status"] == "unsupported", gems
    assert uses(gems, "gem_finish") == []
    assert gems["helpers"]["status"] == "unsupported", gems["helpers"]
    assert gems["helpers"]["reasons"], gems["helpers"]
    assert gems["helpers"]["options"] == [], gems["helpers"]

    # The research potion keeps its rule in that pack: it is still compared.
    research = compare_item(v, "research_potion")
    assert research["status"] == "ok", research


# --------------------------------------------------------------------------- gems and helpers


def test_gems_compare_keeping_them_with_finishing_each_running_job(main: Village) -> None:
    rates = main_pack()["rules"]["gem_finish"]["rates"]
    answer = compare_gems(main)
    assert answer["status"] == "ok", answer
    finishes = uses(answer, "gem_finish")
    targets = {option["task_id"] for option in finishes}
    assert set(RUNNING) <= targets, answer["options"]
    for option in finishes:
        if option["task_id"] not in RUNNING:
            continue
        end = RUNNING[option["task_id"]]
        at = option["at"]
        assert HTTP_NOW <= at < end, option
        assert option["gems"] == gems_for(rates, end - at), option
        assert option["finish_delta_s"] == at - end, option
        assert shortened(option)[option["task_id"]]["end_after"] == at, option
    # At now the hero (3 h left) is on the dear rate, the cannon on the cheap one.
    at_now = {o["task_id"]: o["gems"] for o in finishes if o["at"] == HTTP_NOW}
    if HERO_TASK in at_now:
        assert at_now[HERO_TASK] == 120
    if CANNON_TASK in at_now:
        assert at_now[CANNON_TASK] == 7


def test_helpers_are_compared_from_their_imported_level_by_the_pack_rule(main: Village) -> None:
    helpers = compare_gems(main)["helpers"]
    assert helpers["status"] == "ok", helpers
    rows = helpers["options"]
    # Helper one is level 1: the rule prices level 2. Helper two is level 2: no row for level 3.
    assert [(row["data_id"], row["from_level"], row["to_level"]) for row in rows] == [
        (HELPER_ONE, 1, 2)
    ], rows
    row = rows[0]
    assert row["cost"] == {"gems": 75}, row
    assert row["score_delta"] is None or isinstance(row["score_delta"], int | float), row
    assert isinstance(row["consequences"], list), row
    check_reliability(row["reliability"])
