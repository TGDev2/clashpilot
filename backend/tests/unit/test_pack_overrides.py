from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings
from clashpilot.storage.db import open_db
from clashpilot.storage.packs import PackNotFoundError, load_effective_pack

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_v1.json"
PACK_ID = "fictional-test-pack-v1"
CANNON = 9000004
ITEM_URL = f"/api/gamedata/packs/{PACK_ID}/items/{CANNON}"


def stored_text(data_dir: Path) -> str:
    with open_db(data_dir) as connection:
        row = connection.execute(
            "SELECT content FROM gamedata_packs WHERE pack_id = ?", (PACK_ID,)
        ).fetchone()
    return str(row["content"])


def level_out(client: TestClient, level: int) -> dict[str, Any]:
    levels = client.get(ITEM_URL).json()["levels"]
    return next(entry for entry in levels if entry["level"] == level)  # type: ignore[no-any-return]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    created = TestClient(create_app(settings))
    pack = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert created.post("/api/gamedata/packs", json=pack).status_code == 201
    return created


def test_get_returns_the_correction_marked_manual_with_the_original(client: TestClient) -> None:
    response = client.patch(
        f"{ITEM_URL}/levels/3",
        json={"duration_s": 4321, "cost": {"gold": 77}, "note": "relevé en jeu"},
    )
    assert response.status_code == 200

    level = level_out(client, 3)
    assert level["duration_s"] == 4321
    assert level["cost"] == {"gold": 77}
    assert level["source"]["id"] == "manual"
    assert level["original"] == {"duration_s": 1800, "cost": {"gold": 2000}}
    assert level["note"] == "relevé en jeu"


def test_an_uncorrected_level_has_no_original(client: TestClient) -> None:
    client.patch(f"{ITEM_URL}/levels/3", json={"duration_s": 4321})

    level = level_out(client, 2)
    assert level["original"] is None
    assert level["source"]["id"] == "s1"


def test_a_correction_leaves_the_stored_pack_unchanged(client: TestClient, tmp_path: Path) -> None:
    before = stored_text(tmp_path)

    client.patch(f"{ITEM_URL}/levels/3", json={"duration_s": 4321})

    assert stored_text(tmp_path) == before


def test_a_second_correction_keeps_the_fields_it_does_not_set(client: TestClient) -> None:
    client.patch(f"{ITEM_URL}/levels/3", json={"duration_s": 4321, "note": "première"})
    client.patch(f"{ITEM_URL}/levels/3", json={"cost": {"gold": 77}})

    level = level_out(client, 3)
    assert level["duration_s"] == 4321
    assert level["cost"] == {"gold": 77}
    assert level["note"] == "première"


@pytest.mark.parametrize(
    "body",
    [
        {"duration_s": -1},
        {"duration_s": 0},
        {"duration_s": "60"},
        {"duration_s": True},
        {"cost": {"gold": -1}},
        {"cost": {"gold": "1"}},
        {"cost": {"mithril": 1}},
        {"note": "sans valeur"},
        {},
        {"duration_s": 60, "unknown": 1},
        [1],
    ],
)
def test_an_invalid_value_is_refused_and_not_recorded(client: TestClient, body: Any) -> None:
    response = client.patch(f"{ITEM_URL}/levels/3", json=body)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_override"
    assert detail["errors"]
    level = level_out(client, 3)
    assert level["duration_s"] == 1800
    assert level["original"] is None


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("/api/gamedata/packs/absent/items/1/levels/1", "pack_not_found"),
        (f"/api/gamedata/packs/{PACK_ID}/items/1/levels/1", "item_not_found"),
        (f"{ITEM_URL}/levels/99", "level_not_found"),
    ],
)
def test_an_unknown_target_is_a_404(client: TestClient, url: str, code: str) -> None:
    response = client.patch(url, json={"duration_s": 60})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == code


def test_the_route_documents_its_body(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/gamedata/packs/{pack_id}/items/{data_id}/levels/{level}"]
    body = operation["patch"]["requestBody"]["content"]["application/json"]["schema"]

    assert {"duration_s", "cost", "note"} <= set(body["properties"])


def test_load_effective_pack_refuses_an_unknown_pack(tmp_path: Path) -> None:
    with open_db(tmp_path) as connection, pytest.raises(PackNotFoundError):
        load_effective_pack(connection, "absent")
