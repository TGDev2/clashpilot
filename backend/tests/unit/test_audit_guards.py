"""Guards for the mutations that survived the audit of 2026-11-12 (M03, M06, M12, M14).

Each test fails when its mutation, described in docs/audit/2026-11-12/sondes/mutations.json, is
applied: the bound of an event, the lane capacity count, the timeout of the official API and the
escaping of a lone line feed in the calendar export.
"""

from __future__ import annotations

from typing import Any

import httpx

from clashpilot.api.ics import calendar_of
from clashpilot.ingest.official_api import TIMEOUT_S, fetch_player
from clashpilot.planning.model import Assignment, FrozenJob, Modifier, Plan, Proof
from clashpilot.planning.simulate import effective_cost, effective_duration, simulate
from support.problems import HOUR, NOW, make_problem, make_task


def test_event_does_not_apply_to_a_task_started_at_its_end() -> None:
    task = make_task("4001:0:2", duration_s=10 * HOUR, cost={"gold": 1000})
    problem = make_problem(tasks=(task,), modifiers=(Modifier(NOW, NOW + HOUR, "builder", 50, 50),))
    assert effective_duration(problem, task, NOW + HOUR - 1) == 5 * HOUR
    assert effective_duration(problem, task, NOW + HOUR) == 10 * HOUR
    assert effective_cost(problem, task, NOW + HOUR) == {"gold": 1000}


def test_more_jobs_than_builders_is_a_capacity_violation() -> None:
    # The export shows a running job on a slot the typed builder count does not have.
    task = make_task("4002:0:2", duration_s=HOUR, cost={"gold": 100})
    problem = make_problem(
        lanes={"builder": 1},
        tasks=(task,),
        frozen=(FrozenJob("4003:0:2", "builder", 1, NOW - HOUR, NOW + 2 * HOUR),),
    )
    plan = Plan(
        assignments=(Assignment(task.id, "builder", 0, NOW, NOW + HOUR),),
        engine="test",
        proof=Proof(kind="heuristic", objective=0.0),
    )
    codes = {(v.code, v.task_id) for v in simulate(problem, plan).violations}
    assert ("lane_capacity", task.id) in codes


def test_official_api_calls_have_a_finite_timeout() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions["timeout"])
        return httpx.Response(200, json={"tag": "#ABC"})

    fetch_player("https://api.example", "tok", "ABC", transport=httpx.MockTransport(handler))
    assert seen
    assert all(value == TIMEOUT_S for value in seen.values()), seen


def test_lone_line_feed_in_a_name_stays_inside_one_calendar_line() -> None:
    now = 1_794_133_252
    text = calendar_of(
        [
            {
                "task_id": "1:0:2",
                "name": "Canon\nX",
                "lane": "builder",
                "start": now,
                "end": now + 60,
            }
        ],
        now,
    )
    assert "\n" not in text.replace("\r\n", "")
