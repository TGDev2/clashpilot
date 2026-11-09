"""Acceptance tests for E02: versioned game data packs (SPEC section 5).

Frozen with the task: they describe the contract of `clashpilot.gamedata.pack` and of the
`/api/gamedata/packs` routes, and use only the fictional pack fixture.
"""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_v1.json"

TOWN_HALL = 9000001
HUT = 9000002
LAB = 9000003
CANNON = 9000004
GOLD_STORAGE = 9000005
HERO = 9000006
TROOP = 9000007


# --------------------------------------------------------------------------- helpers


def raw_pack() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def pack_module() -> Any:
    return importlib.import_module("clashpilot.gamedata.pack")


def load(data: dict[str, Any]) -> Any:
    return pack_module().load_pack(data)


def field(obj: Any, name: str) -> Any:
    """Read `name` from a model or a mapping, whichever the implementation returns."""
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def count(value: Any) -> int:
    """A summary may carry a size as an integer or as the collection itself."""
    return value if isinstance(value, int) else len(value)


def level_row(data: dict[str, Any], data_id: int, level: int) -> dict[str, Any]:
    for row in data["levels"]:
        if row["data_id"] == data_id and row["level"] == level:
            return row  # type: ignore[no-any-return]
    raise AssertionError(f"fixture has no row ({data_id}, {level})")


def validation_errors(data: dict[str, Any]) -> list[str]:
    module = pack_module()
    with pytest.raises(module.PackValidationError) as caught:
        module.load_pack(data)
    errors = caught.value.errors
    assert isinstance(errors, list)
    assert all(isinstance(message, str) and message for message in errors)
    return errors  # type: ignore[no-any-return]


def make_client(data_dir: Path) -> TestClient:
    settings = Settings(data_dir=data_dir, frontend_dist=data_dir / "no-dist")
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_client(tmp_path)


def post(client: TestClient, data: dict[str, Any]) -> Any:
    return client.post("/api/gamedata/packs", json=data)


def pack_list(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/gamedata/packs")
    assert response.status_code == 200
    body = response.json()
    packs = body if isinstance(body, list) else body["packs"]
    assert isinstance(packs, list)
    return packs  # type: ignore[no-any-return]


def second_pack() -> dict[str, Any]:
    other = raw_pack()
    other["pack_id"] = "fictional-test-pack-v2"
    other["game_version"] = "test-1"
    return other


# --------------------------------------------------------------------------- load_pack


def test_load_pack_accepts_the_fixture_and_exposes_items_and_levels() -> None:
    pack = load(raw_pack())

    cannon = pack.item(CANNON)
    assert cannon is not None
    assert field(cannon, "name") == "Canon de test"

    row = pack.level(CANNON, 3)
    assert row is not None
    assert field(row, "duration_s") == 1800
    assert dict(field(row, "cost")) == {"gold": 2000}


def test_level_is_none_for_an_absent_row() -> None:
    pack = load(raw_pack())
    assert pack.level(CANNON, 99) is None
    assert pack.level(CANNON, 0) is None
    assert pack.level(123456789, 1) is None


def test_max_level_is_the_highest_level_present() -> None:
    pack = load(raw_pack())
    assert pack.max_level(CANNON) == 4
    assert pack.max_level(TOWN_HALL) == 3
    assert pack.max_level(HUT) == 1


def test_max_level_follows_the_rows_of_the_pack() -> None:
    data = raw_pack()
    data["levels"] = [
        row for row in data["levels"] if not (row["data_id"] == CANNON and row["level"] == 4)
    ]
    assert load(data).max_level(CANNON) == 3


def test_source_of_resolves_the_source_of_a_row() -> None:
    pack = load(raw_pack())
    source = pack.source_of(CANNON, 2)
    identifier = field(source, "id") if not isinstance(source, str) else source
    label = field(source, "label") if not isinstance(source, str) else source
    assert identifier == "s1" or label == "Source fictive de test"


# --------------------------------------------------------------------------- validation


def test_validation_error_for_wrong_schema_version() -> None:
    data = raw_pack()
    data["schema_version"] = 2
    errors = validation_errors(data)
    assert len(errors) == 1
    assert "schema_version" in errors[0]


def test_validation_error_for_unknown_source() -> None:
    data = raw_pack()
    level_row(data, CANNON, 2)["source"] = "s-absent"
    assert len(validation_errors(data)) == 1


def test_validation_error_for_requires_on_missing_data_id() -> None:
    data = raw_pack()
    level_row(data, CANNON, 2)["requires"].append({"data_id": 111, "level": 1})
    assert len(validation_errors(data)) == 1


def test_validation_error_for_rule_without_source() -> None:
    data = raw_pack()
    del data["rules"]["builder_potion"]["source"]
    assert len(validation_errors(data)) == 1


def test_validation_error_for_negative_duration() -> None:
    data = raw_pack()
    level_row(data, CANNON, 2)["duration_s"] = -1
    assert len(validation_errors(data)) == 1


def test_validation_error_for_duplicate_data_id_and_level() -> None:
    data = raw_pack()
    data["levels"].append(copy.deepcopy(level_row(data, CANNON, 2)))
    assert len(validation_errors(data)) == 1


def test_validation_collects_every_error_at_once() -> None:
    data = raw_pack()
    level_row(data, CANNON, 2)["source"] = "s-absent"
    level_row(data, CANNON, 3)["requires"].append({"data_id": 111, "level": 1})
    level_row(data, CANNON, 4)["duration_s"] = -5
    del data["rules"]["rune_of_gold"]["source"]
    assert len(validation_errors(data)) == 4


# --------------------------------------------------------------------------- POST


def test_post_valid_pack_creates_it_inactive(client: TestClient) -> None:
    data = raw_pack()
    response = post(client, data)
    assert response.status_code == 201
    body = response.json()
    assert body["pack_id"] == data["pack_id"]
    assert body["game_version"] == data["game_version"]
    assert body["as_of"] == data["as_of"]
    assert body["fictional"] is True
    assert count(body["items"]) == len(data["items"])
    assert count(body["levels"]) == len(data["levels"])
    assert body["active"] is False


def test_post_invalid_pack_is_a_422_with_errors(client: TestClient) -> None:
    data = raw_pack()
    data["schema_version"] = 7
    level_row(data, CANNON, 2)["duration_s"] = -1
    response = post(client, data)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_pack"
    assert isinstance(detail["message"], str)
    assert isinstance(detail["errors"], list)
    assert len(detail["errors"]) >= 2
    assert pack_list(client) == []


def test_post_identical_pack_is_idempotent(client: TestClient) -> None:
    data = raw_pack()
    assert post(client, data).status_code == 201
    again = post(client, data)
    assert again.status_code == 200
    assert again.json()["pack_id"] == data["pack_id"]
    assert [entry["pack_id"] for entry in pack_list(client)] == [data["pack_id"]]


def test_post_identical_content_in_another_key_order_is_idempotent(client: TestClient) -> None:
    data = raw_pack()
    assert post(client, data).status_code == 201
    reordered = json.loads(json.dumps(data, sort_keys=True))
    assert list(reordered) != list(data)
    assert post(client, reordered).status_code == 200
    assert len(pack_list(client)) == 1


def test_post_same_pack_id_with_other_content_is_a_conflict(client: TestClient) -> None:
    data = raw_pack()
    assert post(client, data).status_code == 201
    changed = raw_pack()
    level_row(changed, CANNON, 2)["duration_s"] = 301
    response = post(client, changed)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pack_conflict"
    assert len(pack_list(client)) == 1


# --------------------------------------------------------------------------- list / active


def test_list_returns_every_pack(client: TestClient) -> None:
    assert pack_list(client) == []
    first, second = raw_pack(), second_pack()
    assert post(client, first).status_code == 201
    assert post(client, second).status_code == 201
    listed = pack_list(client)
    assert {entry["pack_id"] for entry in listed} == {first["pack_id"], second["pack_id"]}
    assert all(entry["active"] is False for entry in listed)


def test_put_active_switches_the_single_active_pack(client: TestClient) -> None:
    first, second = raw_pack(), second_pack()
    post(client, first)
    post(client, second)

    response = client.put(f"/api/gamedata/packs/{first['pack_id']}/active")
    assert response.status_code == 200
    active = {entry["pack_id"] for entry in pack_list(client) if entry["active"]}
    assert active == {first["pack_id"]}

    response = client.put(f"/api/gamedata/packs/{second['pack_id']}/active")
    assert response.status_code == 200
    active = {entry["pack_id"] for entry in pack_list(client) if entry["active"]}
    assert active == {second["pack_id"]}


def test_put_active_on_unknown_pack_is_a_404(client: TestClient) -> None:
    post(client, raw_pack())
    response = client.put("/api/gamedata/packs/does-not-exist/active")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "pack_not_found"
    assert [entry for entry in pack_list(client) if entry["active"]] == []


# --------------------------------------------------------------------------- item levels


def test_get_item_returns_levels_with_cost_requires_and_source_label(client: TestClient) -> None:
    data = raw_pack()
    post(client, data)
    response = client.get(f"/api/gamedata/packs/{data['pack_id']}/items/{CANNON}")
    assert response.status_code == 200
    levels = response.json()["levels"]
    assert [entry["level"] for entry in levels] == [1, 2, 3, 4]

    third = next(entry for entry in levels if entry["level"] == 3)
    assert third["duration_s"] == 1800
    assert third["cost"] == {"gold": 2000}
    assert third["requires"] == [{"data_id": TOWN_HALL, "level": 2}]
    assert "Source fictive de test" in json.dumps(third, ensure_ascii=False)


# --------------------------------------------------------------------------- persistence


def test_packs_and_active_pack_survive_a_new_application(tmp_path: Path) -> None:
    first, second = raw_pack(), second_pack()
    before = make_client(tmp_path)
    post(before, first)
    post(before, second)
    assert before.put(f"/api/gamedata/packs/{second['pack_id']}/active").status_code == 200

    after = make_client(tmp_path)
    listed = pack_list(after)
    assert {entry["pack_id"] for entry in listed} == {first["pack_id"], second["pack_id"]}
    assert {entry["pack_id"] for entry in listed if entry["active"]} == {second["pack_id"]}
    assert post(after, first).status_code == 200


# --------------------------------------------------------------------------- fixture


def test_fixture_is_a_fictional_pack_that_loads() -> None:
    data = raw_pack()
    assert FIXTURE.is_file()
    assert data["schema_version"] == 1
    assert data["fictional"] is True
    load(data)


def test_fixture_describes_the_core_game_objects() -> None:
    data = raw_pack()
    pack = load(data)
    items = data["items"]

    def with_tag(tag: str) -> list[dict[str, Any]]:
        return [item for item in items if tag in item["tags"]]

    town_halls = with_tag("town_hall")
    assert town_halls and pack.max_level(town_halls[0]["data_id"]) >= 2
    assert with_tag("builder_hut")

    labs = [
        item for item in items if item["category"] == "laboratory" or "laboratory" in item["tags"]
    ]
    assert labs

    assert any(
        pack.max_level(item["data_id"]) >= 3 for item in items if item["category"] == "defense"
    )
    assert any(item["kind"] == "hero" for item in items)
    assert any(item["kind"] == "troop" for item in items)
    assert any(row["resource"] == "gold" for row in data["storage"])


def test_fixture_names_are_visibly_invented() -> None:
    names = [item["name"] for item in raw_pack()["items"]]
    assert all(name.endswith("de test") for name in names)
