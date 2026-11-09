"""Edge cases of the export import that the acceptance tests leave open."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.ingest.export import ImportFormatError, parse_export
from clashpilot.settings import Settings

EXPORTED_AT = 1794133192
IMPORT_URL = "/api/villages/import"


def export_doc(**sections: Any) -> dict[str, Any]:
    return {"tag": "#ABC", "timestamp": EXPORTED_AT, **sections}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    return TestClient(create_app(settings, clock=lambda: EXPORTED_AT + 60))


@pytest.mark.parametrize(
    "broken",
    [
        {"tag": 123},
        {"tag": ""},
        {"tag": "#"},
        {"timestamp": "demain"},
        {"timestamp": True},
        {"timestamp": 2**70},
        {"timestamp": 0},
    ],
)
def test_unusable_tag_or_timestamp_is_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(ImportFormatError):
        parse_export({**export_doc(), **broken}, EXPORTED_AT)


def test_deeply_nested_json_is_refused_as_a_format_error() -> None:
    with pytest.raises(ImportFormatError):
        parse_export("[" * 200_000, EXPORTED_AT)


def test_unknown_sections_keep_their_content() -> None:
    snapshot = parse_export(export_doc(mystery_v9=[1, 2]), EXPORTED_AT)
    assert snapshot.unknown_sections == {"mystery_v9": [1, 2]}


@pytest.mark.parametrize("cnt", [0, -3, "2", True, None])
def test_unusable_cnt_counts_as_one(cnt: Any) -> None:
    entry = {"data": 1000010, "lvl": 8, "cnt": cnt}
    assert parse_export(export_doc(buildings=[entry]), EXPORTED_AT).items[0].count == 1


def test_entry_without_identifier_or_level_is_reported() -> None:
    snapshot = parse_export(export_doc(buildings=[{"lvl": 3}, {"data": 1000008}]), EXPORTED_AT)
    assert [item.data_id for item in snapshot.items] == [1000008]
    assert snapshot.items[0].level is None
    assert len(snapshot.warnings) == 2


def test_timer_without_level_gives_no_job() -> None:
    snapshot = parse_export(export_doc(buildings=[{"data": 1000008, "timer": 5}]), EXPORTED_AT)
    assert snapshot.running == ()


def test_body_with_a_byte_order_mark_is_imported(client: TestClient) -> None:
    response = client.post(IMPORT_URL, content=b"\xef\xbb\xbf" + json.dumps(export_doc()).encode())
    assert response.status_code == 201


def test_importing_an_older_export_does_not_move_the_state_back(client: TestClient) -> None:
    newest = export_doc(buildings=[{"data": 1000008, "lvl": 3}])
    older = {**export_doc(buildings=[{"data": 1000008, "lvl": 2}]), "timestamp": EXPORTED_AT - 600}
    assert client.post(IMPORT_URL, json=newest).status_code == 201
    assert client.post(IMPORT_URL, json=older).status_code == 201

    state = client.get("/api/villages/ABC/state").json()

    assert state["exported_at"] == EXPORTED_AT
    assert state["items"][0]["level"] == 3


def test_body_is_cut_at_the_limit_even_without_a_content_length(client: TestClient) -> None:
    def chunks() -> Any:
        yield b"x" * 3_000_000
        yield b"x" * 3_000_000

    response = client.post(IMPORT_URL, content=chunks())
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "export_too_large"
