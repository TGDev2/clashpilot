"""Error paths of the schedule routes that the acceptance contract leaves to the implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

NOW = 1781056800
URL = "/api/villages/2PP/schedule"


def document(**overrides: Any) -> dict[str, Any]:
    return {
        "tz": "Europe/Paris",
        "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
        "exceptions": [],
        "policy": {"mode": "strict", "tolerance_minutes": 0, "max_logins_per_day": None},
    } | overrides


def absence(start: str, end: str) -> list[dict[str, str]]:
    return [{"start_local": start, "end_local": end, "kind": "absence"}]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    return TestClient(create_app(settings, clock=lambda: NOW))


def code_of(response: Any, status: int) -> str:
    assert response.status_code == status, response.text
    code: str = response.json()["detail"]["code"]
    return code


@pytest.mark.parametrize("path", ["", "/preview"])
def test_a_village_without_a_schedule_answers_404(client: TestClient, path: str) -> None:
    assert code_of(client.get(f"{URL}{path}"), 404) == "schedule_not_found"


def test_a_lower_case_timezone_is_not_an_iana_key(client: TestClient) -> None:
    assert code_of(client.put(URL, json=document(tz="europe/paris")), 422) == "invalid_timezone"


@pytest.mark.parametrize("bound", ["24:00", "7:00", "07:00:00", "soon"])
def test_a_malformed_clock_time_is_an_invalid_window(client: TestClient, bound: str) -> None:
    weekly = [{"weekday": None, "start": bound, "end": "12:00", "kind": "work"}]
    assert code_of(client.put(URL, json=document(weekly=weekly)), 422) == "invalid_window"


@pytest.mark.parametrize(
    "exceptions",
    [
        pytest.param(absence("2026-07-14T09:00:00", "2026-07-14T09:00:00"), id="zero-length"),
        pytest.param(absence("2026-07-14T09:00:00", "2026-07-13T09:00:00"), id="inverted"),
        pytest.param(absence("2026-07-14", "2026-07-15"), id="no-time"),
        pytest.param(absence("2026-07-14T09:00:00+02:00", "2026-07-15T09:00:00"), id="offset"),
        pytest.param(absence("0001-01-01T00:00:00", "2026-07-15T09:00:00"), id="year-too-early"),
        pytest.param(absence("2026-07-14T09:00:00", "9999-12-31T23:59:59"), id="year-too-late"),
    ],
)
def test_an_unusable_absence_is_an_invalid_window(
    client: TestClient, exceptions: list[dict[str, str]]
) -> None:
    assert code_of(client.put(URL, json=document(exceptions=exceptions)), 422) == "invalid_window"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"policy": {"mode": "never"}}, id="unknown-mode"),
        pytest.param({"weekly": "every day"}, id="not-a-list"),
        pytest.param(
            {"weekly": [{"weekday": 7, "start": "09:00", "end": "10:00", "kind": "work"}]},
            id="weekday-out-of-range",
        ),
        pytest.param({"unexpected": 1}, id="extra-field"),
    ],
)
def test_any_other_malformed_profile_is_an_invalid_schedule(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    assert code_of(client.put(URL, json=document(**overrides)), 422) == "invalid_schedule"


@pytest.mark.parametrize("days", [0, -1, 367])
def test_preview_refuses_a_number_of_days_out_of_range(client: TestClient, days: int) -> None:
    assert client.put(URL, json=document()).status_code == 200

    response = client.get(f"{URL}/preview", params={"days": days})

    assert code_of(response, 422) == "invalid_request"


def test_preview_defaults_to_seven_days(client: TestClient) -> None:
    assert client.put(URL, json=document()).status_code == 200

    sleep = client.get(f"{URL}/preview").json()["sleep"]

    assert sleep[-1][1] <= NOW + 7 * 86400 < sleep[-1][1] + 86400
