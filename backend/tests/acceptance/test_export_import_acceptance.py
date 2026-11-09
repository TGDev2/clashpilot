"""Acceptance tests for E04: import of the in-game village JSON export (SPEC ADR-3, section 4).

Frozen with the task: they describe the contract of `clashpilot.ingest.export`,
`clashpilot.ingest.state` and the `/api/villages` routes, and use only fictional data.

`ADAPTERS` is a mutable mapping `name -> (detect, parse)`: `detect` receives the decoded object and
says whether the adapter reads it, `parse` turns it into a `VillageSnapshot`. A newly registered
adapter whose `detect` matches wins over the current one, which accepts any object carrying a tag
and a timestamp.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PACK_FIXTURE = FIXTURES / "packs" / "fictional_pack_v1.json"
EXPORT_FIXTURE = FIXTURES / "exports" / "fictional_export_2026-11.json"

IMPORT_URL = "/api/villages/import"

EXPORTED_AT = 1794133192
IMPORTED_AT = 1794200000

# Ids of the fictional pack.
PACK_LAB = 9000003
PACK_CANNON = 9000004
PACK_HERO = 9000006
# Ids that the fictional pack does not know.
ALIEN = 1000008
OTHER_ALIEN = 1000010

KNOWN_SECTIONS = (
    "helpers",
    "buildings",
    "traps",
    "decos",
    "obstacles",
    "units",
    "siege_machines",
    "heroes",
    "spells",
    "pets",
    "equipment",
    "house_parts",
    "skins",
    "sceneries",
    "buildings2",
    "traps2",
    "decos2",
    "obstacles2",
    "units2",
    "heroes2",
    "skins2",
    "sceneries2",
    "guardians",
)

MODULE_ENTRY: dict[str, Any] = {
    "data": 1000097,
    "types": [{"data": 103000011, "modules": [{"data": 102000033, "lvl": 4}]}],
}

FRENCH_WORDS = re.compile(
    r"\b(le|la|les|un|une|des|du|de|est|pas|ne|sans|manquant\w*|invalide\w*|illisible\w*)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- helpers


def export_module() -> Any:
    return importlib.import_module("clashpilot.ingest.export")


def state_module() -> Any:
    return importlib.import_module("clashpilot.ingest.state")


def export_doc(tag: str = "#EXAMPLE0", **sections: Any) -> dict[str, Any]:
    return {"tag": tag, "timestamp": EXPORTED_AT, **sections}


def parse(raw: Any) -> Any:
    return export_module().parse_export(raw, IMPORTED_AT)


def only(entries: Any) -> Any:
    listed = list(entries)
    assert len(listed) == 1, listed
    return listed[0]


def item_of(snapshot: Any, data_id: int) -> Any:
    return only(item for item in snapshot.items if item.data_id == data_id)


def assert_french(message: Any) -> None:
    assert isinstance(message, str)
    assert message
    assert FRENCH_WORDS.search(message), message
    assert "Expecting" not in message


def warning_text(warnings: Any) -> str:
    parts = [w if isinstance(w, str) else json.dumps(w, ensure_ascii=False) for w in warnings]
    return " ".join(parts).lower()


def mentions_skewed_timers(warnings: Any) -> bool:
    text = warning_text(warnings)
    return "timer" in text and "fauss" in text


def count(value: Any) -> int:
    """A response may carry a size as an integer or as the collection itself."""
    return value if isinstance(value, int) else len(value)


def make_client(data_dir: Path) -> TestClient:
    settings = Settings(data_dir=data_dir, frontend_dist=data_dir / "no-dist")
    return TestClient(create_app(settings, clock=lambda: IMPORTED_AT))


def install_active_pack(client: TestClient) -> None:
    pack = json.loads(PACK_FIXTURE.read_text(encoding="utf-8"))
    assert client.post("/api/gamedata/packs", json=pack).status_code == 201
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    created = make_client(tmp_path)
    install_active_pack(created)
    return created


def post_body(client: TestClient, body: bytes, content_type: str = "application/json") -> Any:
    return client.post(IMPORT_URL, content=body, headers={"content-type": content_type})


def post_doc(client: TestClient, doc: dict[str, Any]) -> Any:
    return post_body(client, json.dumps(doc).encode("utf-8"))


def imported(client: TestClient, doc: dict[str, Any]) -> dict[str, Any]:
    response = post_doc(client, doc)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def get_state(client: TestClient, village_id: str) -> dict[str, Any]:
    response = client.get(f"/api/villages/{village_id}/state")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def state_item(state: dict[str, Any], data_id: int) -> dict[str, Any]:
    found: dict[str, Any] = only(e for e in state["items"] if e["data_id"] == data_id)
    return found


def padded_body(total_bytes: int) -> bytes:
    doc = export_doc(mystery_padding="")
    padding = total_bytes - len(json.dumps(doc).encode("utf-8"))
    doc["mystery_padding"] = "x" * padding
    return json.dumps(doc).encode("utf-8")


def register_wrapping_adapter(monkeypatch: pytest.MonkeyPatch, name: str, key: str) -> None:
    """Register an adapter for objects carrying `key`, reading the export nested under it."""
    module = export_module()
    current_name = parse(export_doc()).format_version
    current_parse = module.ADAPTERS[current_name][1]

    def detect(data: Any, *args: Any, **kwargs: Any) -> bool:
        return key in data

    def adapter_parse(data: Any, *args: Any, **kwargs: Any) -> Any:
        return current_parse(data[key], *args, **kwargs)

    monkeypatch.setitem(module.ADAPTERS, name, (detect, adapter_parse))


# --------------------------------------------------------------------------- parse_export


def test_parse_export_reads_a_dictionary_into_a_snapshot() -> None:
    snapshot = parse(export_doc(buildings=[{"data": 1000001, "lvl": 15}]))

    state = state_module()
    assert isinstance(snapshot, state.VillageSnapshot)
    assert snapshot.tag == "EXAMPLE0"
    assert snapshot.exported_at == EXPORTED_AT
    item = only(snapshot.items)
    assert isinstance(item, state.SnapshotItem)
    assert item.data_id == 1000001
    assert item.level == 15
    assert item.count == 1
    assert item.section == "buildings"
    assert item.village == "home"


def test_parse_export_reads_a_json_string_like_a_dictionary() -> None:
    doc = export_doc(
        buildings=[{"data": 1000001, "lvl": 15}, {"data": 1000003, "lvl": 2, "timer": 60}]
    )
    from_dict = parse(doc)
    from_text = parse(json.dumps(doc))

    assert from_text.tag == from_dict.tag
    assert from_text.exported_at == from_dict.exported_at
    assert from_text.items == from_dict.items
    assert from_text.running == from_dict.running


def test_manual_state_exists_for_the_next_task() -> None:
    assert isinstance(state_module().ManualState, type)


@pytest.mark.parametrize(
    ("raw_tag", "expected"), [("#ABC", "ABC"), ("ABC", "ABC"), ("#abc2", "ABC2")]
)
def test_tag_is_normalized(raw_tag: str, expected: str) -> None:
    assert parse(export_doc(tag=raw_tag)).tag == expected


def test_each_entry_gives_one_item() -> None:
    snapshot = parse(
        export_doc(
            buildings=[{"data": 1000008, "lvl": 3}, {"data": 1000008, "lvl": 4}],
            heroes=[{"data": 28000004, "lvl": 39}],
        )
    )
    assert sorted((i.data_id, i.level, i.section) for i in snapshot.items) == [
        (1000008, 3, "buildings"),
        (1000008, 4, "buildings"),
        (28000004, 39, "heroes"),
    ]


@pytest.mark.parametrize(
    "section",
    ["helpers", "buildings", "traps", "units", "siege_machines", "heroes", "spells", "pets"],
)
def test_main_village_sections_are_home(section: str) -> None:
    item = only(parse(export_doc(**{section: [{"data": 1000001, "lvl": 2}]})).items)
    assert item.section == section
    assert item.village == "home"


@pytest.mark.parametrize("section", ["buildings2", "units2", "heroes2"])
def test_builder_sections_are_read_with_the_builder_village(section: str) -> None:
    item = only(parse(export_doc(**{section: [{"data": 1000001, "lvl": 2}]})).items)
    assert item.section == section
    assert item.village == "builder"


def test_absent_sections_are_not_an_error() -> None:
    snapshot = parse(export_doc())
    assert list(snapshot.items) == []
    assert list(snapshot.running) == []
    assert not snapshot.unknown_sections


@pytest.mark.parametrize(
    "broken",
    [
        {"buildings": [{}]},
        {"buildings": [{"data": 1000008}]},
        {"buildings": [{"lvl": 3}]},
        {"buildings": [{"timer": 5}]},
        {"buildings": [{"data": 1000008, "timer": 5}]},
        {"heroes": [{"data": 28000004, "cnt": 2}]},
        {"skins": [52000001, 52000002]},
        {"sceneries": [60000001]},
        {"house_parts": [82000001]},
        {"heroes": None},
    ],
)
def test_any_field_may_be_missing(broken: dict[str, Any]) -> None:
    sections = {"units": [{"data": 4000001, "lvl": 7}], **broken}
    snapshot = parse(export_doc(**sections))
    assert item_of(snapshot, 4000001).level == 7


# --------------------------------------------------------------------------- running jobs


def test_entry_with_timer_gives_a_running_job() -> None:
    snapshot = parse(export_doc(buildings=[{"data": 1000003, "lvl": 15, "timer": 37745}]))

    job = only(snapshot.running)
    assert isinstance(job, state_module().RunningJob)
    assert job.data_id == 1000003
    assert job.finish_at == EXPORTED_AT + 37745
    assert job.to_level == 16
    assert not job.extra
    assert not job.helper
    assert item_of(snapshot, 1000003).level == 15


def test_timer_of_zero_is_a_job_finishing_at_the_export_time() -> None:
    job = only(parse(export_doc(heroes=[{"data": 28000004, "lvl": 39, "timer": 0}])).running)
    assert job.finish_at == EXPORTED_AT
    assert job.to_level == 40


def test_extra_and_helper_flags_are_kept_on_the_job() -> None:
    entry = {"data": 1000020, "lvl": 5, "timer": 82379, "extra": True, "helper_recurrent": True}
    job = only(parse(export_doc(buildings=[entry])).running)
    assert job.extra is True
    assert job.helper is True


def test_extra_alone_does_not_set_helper() -> None:
    job = only(
        parse(
            export_doc(buildings=[{"data": 1000020, "lvl": 5, "timer": 9, "extra": True}])
        ).running
    )
    assert job.extra is True
    assert not job.helper


def test_entries_without_timer_give_no_running_job() -> None:
    snapshot = parse(
        export_doc(
            helpers=[{"data": 93000000, "lvl": 4, "helper_cooldown": 72462}],
            buildings=[
                {"data": 1000001, "lvl": 15, "weapon": 1},
                {"data": 1000008, "lvl": 20, "gear_up": 1},
            ],
        )
    )
    assert len(list(snapshot.items)) == 3
    assert list(snapshot.running) == []


# --------------------------------------------------------------------------- cnt


def test_cnt_groups_identical_instances_in_one_item() -> None:
    item = only(parse(export_doc(buildings=[{"data": 1000010, "lvl": 8, "cnt": 225}])).items)
    assert item.data_id == 1000010
    assert item.level == 8
    assert item.count == 225


def test_entry_without_cnt_has_a_count_of_one() -> None:
    assert only(parse(export_doc(buildings=[{"data": 1000010, "lvl": 8}])).items).count == 1


# --------------------------------------------------------------------------- unknown sections


def test_unknown_top_level_key_is_kept_without_error() -> None:
    snapshot = parse(export_doc(mystery_v9=[{"data": 1}], buildings=[{"data": 1000001, "lvl": 2}]))
    assert "mystery_v9" in snapshot.unknown_sections
    assert item_of(snapshot, 1000001).level == 2


def test_known_keys_are_not_unknown_sections() -> None:
    sections: dict[str, Any] = {name: [] for name in KNOWN_SECTIONS}
    snapshot = parse(export_doc(boosts={"clocktower_cooldown": 73124}, **sections))
    assert not snapshot.unknown_sections


# --------------------------------------------------------------------------- invalid exports


@pytest.mark.parametrize(
    "raw",
    [
        "pas du json",
        "",
        "{tag:",
        "[1, 2]",
        "42",
        "null",
        '"texte"',
        {},
        {"buildings": []},
        {"tag": "#ABC"},
        {"timestamp": EXPORTED_AT},
        '{"buildings": []}',
    ],
)
def test_invalid_input_raises_import_format_error_in_french(raw: Any) -> None:
    module = export_module()
    with pytest.raises(module.ImportFormatError) as caught:
        module.parse_export(raw, IMPORTED_AT)
    assert_french(str(caught.value))


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize(
    "body",
    [
        b"pas du json",
        b"",
        b"[1, 2]",
        b"null",
        b'{"buildings": []}',
        b'{"tag": "#ABC"}',
        b"\xff\xfe",
    ],
)
def test_invalid_body_is_a_422_invalid_export(
    client: TestClient, body: bytes, content_type: str
) -> None:
    response = post_body(client, body, content_type)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_export"
    assert_french(detail["message"])


# --------------------------------------------------------------------------- warnings


def test_boost_other_than_clocktower_warns_about_skewed_timers() -> None:
    snapshot = parse(export_doc(boosts={"clocktower_cooldown": 73124, "builder_boost": 3600}))
    assert mentions_skewed_timers(snapshot.warnings)


@pytest.mark.parametrize(
    "sections", [{}, {"boosts": {}}, {"boosts": {"clocktower_cooldown": 73124}}]
)
def test_no_skewed_timer_warning_without_another_boost(sections: dict[str, Any]) -> None:
    assert not mentions_skewed_timers(parse(export_doc(**sections)).warnings)


def test_module_entry_is_kept_raw_and_flagged() -> None:
    plain = [{"data": 1000001, "lvl": 15}]
    baseline = parse(export_doc(buildings=plain))
    snapshot = parse(export_doc(buildings=[*plain, MODULE_ENTRY]))

    kept = json.dumps(item_of(snapshot, MODULE_ENTRY["data"]).raw, sort_keys=True)
    assert json.dumps(MODULE_ENTRY["types"], sort_keys=True) in kept
    assert len(list(snapshot.warnings)) > len(list(baseline.warnings))
    assert list(snapshot.running) == []


# --------------------------------------------------------------------------- adapters


def test_current_adapter_is_named_and_registered() -> None:
    name = parse(export_doc()).format_version
    assert isinstance(name, str)
    assert name
    assert name in export_module().ADAPTERS


@pytest.mark.parametrize(
    "outer",
    [{}, {"tag": "#OUTER", "timestamp": 1}],
    ids=["new-signature", "new-signature-keeping-tag-and-timestamp"],
)
def test_new_adapter_is_selected_and_named(
    monkeypatch: pytest.MonkeyPatch, outer: dict[str, Any]
) -> None:
    current = parse(export_doc()).format_version
    register_wrapping_adapter(monkeypatch, "test_envelope_v99", "envelope_v99")
    inner = export_doc(tag="#INNER", buildings=[{"data": 1000001, "lvl": 2}])

    snapshot = parse({**outer, "envelope_v99": inner})

    assert snapshot.format_version == "test_envelope_v99"
    assert snapshot.tag == "INNER"
    assert item_of(snapshot, 1000001).level == 2
    assert parse(export_doc()).format_version == current


def test_new_adapter_works_through_the_routes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_wrapping_adapter(monkeypatch, "test_envelope_v99", "envelope_v99")
    inner = export_doc(tag="#INNER", buildings=[{"data": PACK_CANNON, "lvl": 2}])

    body = imported(client, {"envelope_v99": inner})

    assert body["format_version"] == "test_envelope_v99"
    assert body["tag"] == "INNER"
    assert state_item(get_state(client, body["village_id"]), PACK_CANNON)["level"] == 2


# --------------------------------------------------------------------------- POST import


def test_import_answers_201_with_the_full_summary(client: TestClient) -> None:
    doc = export_doc(
        buildings=[
            {"data": PACK_CANNON, "lvl": 2},
            {"data": PACK_LAB, "lvl": 1, "timer": 600},
            {"data": ALIEN, "lvl": 5},
        ],
        mystery_v9={"a": 1},
        boosts={"builder_boost": 3600},
    )

    body = imported(client, doc)

    assert body["village_id"] == "EXAMPLE0"
    assert body["snapshot_id"] is not None
    assert body["tag"] == "EXAMPLE0"
    assert body["exported_at"] == EXPORTED_AT
    assert isinstance(body["format_version"], str)
    assert body["format_version"]
    assert count(body["items"]) == 3
    assert count(body["running"]) == 1
    assert body["unknown_ids"] == [ALIEN]
    assert "mystery_v9" in body["unknown_sections"]
    assert mentions_skewed_timers(body["warnings"])


def test_import_accepts_a_raw_body_whatever_its_content_type(client: TestClient) -> None:
    body = json.dumps(export_doc(buildings=[{"data": PACK_CANNON, "lvl": 2}])).encode("utf-8")
    response = post_body(client, body, "text/plain")
    assert response.status_code == 201
    assert response.json()["tag"] == "EXAMPLE0"


def test_unknown_ids_do_not_fail_the_import(client: TestClient) -> None:
    doc = export_doc(
        buildings=[{"data": ALIEN, "lvl": 5}, {"data": PACK_CANNON, "lvl": 2}],
        heroes=[{"data": OTHER_ALIEN, "lvl": 1}],
    )
    body = imported(client, doc)
    assert set(body["unknown_ids"]) == {ALIEN, OTHER_ALIEN}


def test_import_without_an_active_pack_succeeds_with_unnamed_items(tmp_path: Path) -> None:
    bare = make_client(tmp_path)
    body = imported(bare, export_doc(buildings=[{"data": PACK_CANNON, "lvl": 2}]))
    assert state_item(get_state(bare, body["village_id"]), PACK_CANNON)["name"] is None


def test_identical_import_answers_200_with_the_same_snapshot(client: TestClient) -> None:
    doc = export_doc(buildings=[{"data": PACK_CANNON, "lvl": 2}])
    first = imported(client, doc)

    again = post_doc(client, doc)

    assert again.status_code == 200
    assert again.json()["snapshot_id"] == first["snapshot_id"]
    assert len(get_state(client, first["village_id"])["items"]) == 1


def test_changed_content_is_a_new_snapshot_and_becomes_the_state(client: TestClient) -> None:
    first = imported(client, export_doc(buildings=[{"data": PACK_CANNON, "lvl": 2}]))

    newer = export_doc(buildings=[{"data": PACK_CANNON, "lvl": 3}])
    newer["timestamp"] = EXPORTED_AT + 3600
    second = imported(client, newer)

    assert second["snapshot_id"] != first["snapshot_id"]
    assert second["village_id"] == first["village_id"]
    state = get_state(client, first["village_id"])
    assert state_item(state, PACK_CANNON)["level"] == 3
    assert state_item(state, PACK_CANNON)["provenance"]["as_of"] == EXPORTED_AT + 3600


@pytest.mark.parametrize(
    "make_body",
    [lambda: padded_body(6_000_000), lambda: b"x" * 6_000_000],
    ids=["valid-json", "not-json"],
)
def test_body_over_five_megabytes_is_a_413(client: TestClient, make_body: Any) -> None:
    response = post_body(client, make_body())
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "export_too_large"
    assert_french(detail["message"])
    assert client.get("/api/villages/EXAMPLE0/state").status_code == 404


def test_body_under_five_megabytes_is_accepted(client: TestClient) -> None:
    assert post_body(client, padded_body(4_500_000)).status_code == 201


def test_raw_json_and_its_sha256_are_persisted(tmp_path: Path) -> None:
    importer = make_client(tmp_path)
    raw = json.dumps(export_doc(buildings=[{"data": PACK_CANNON, "lvl": 2}])).encode("utf-8")
    assert post_body(importer, raw).status_code == 201

    connection = sqlite3.connect(tmp_path / "clashpilot.db")
    try:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        cells = {
            cell.decode("utf-8") if isinstance(cell, bytes) else cell
            for table in tables
            for row in connection.execute(f'SELECT * FROM "{table}"')
            for cell in row
        }
    finally:
        connection.close()

    assert raw.decode("utf-8") in cells
    assert hashlib.sha256(raw).hexdigest() in cells


# --------------------------------------------------------------------------- GET state


def test_state_exposes_items_with_name_and_provenance(client: TestClient) -> None:
    doc = export_doc(
        buildings=[{"data": PACK_CANNON, "lvl": 2, "cnt": 2}, {"data": ALIEN, "lvl": 5}]
    )
    village_id = imported(client, doc)["village_id"]

    state = get_state(client, village_id)

    known = state_item(state, PACK_CANNON)
    assert known["name"] == "Canon de test"
    assert known["level"] == 2
    assert known["count"] == 2
    assert known["section"] == "buildings"
    assert known["provenance"]["status"] == "confirmed"
    assert known["provenance"]["source"] == "export"
    assert known["provenance"]["as_of"] == EXPORTED_AT

    unknown = state_item(state, ALIEN)
    assert unknown["name"] is None
    assert unknown["level"] == 5
    assert unknown["provenance"]["status"] == "confirmed"


def test_state_exposes_running_jobs_with_an_absolute_finish(client: TestClient) -> None:
    doc = export_doc(
        buildings=[{"data": PACK_LAB, "lvl": 1, "timer": 600}],
        heroes=[{"data": PACK_HERO, "lvl": 1}],
    )
    village_id = imported(client, doc)["village_id"]

    state = get_state(client, village_id)

    job = only(state["running"])
    assert job["data_id"] == PACK_LAB
    assert job["finish_at"] == EXPORTED_AT + 600
    assert job["to_level"] == 2


def test_state_of_an_unknown_village_is_a_404(client: TestClient) -> None:
    response = client.get("/api/villages/NOPE/state")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "village_not_found"
    assert_french(detail["message"])


# --------------------------------------------------------------------------- storage


def test_two_tags_are_stored_separately(client: TestClient) -> None:
    first = imported(client, export_doc(tag="#AAA", buildings=[{"data": PACK_CANNON, "lvl": 2}]))
    second = imported(client, export_doc(tag="#BBB", heroes=[{"data": PACK_HERO, "lvl": 1}]))

    assert first["village_id"] == "AAA"
    assert second["village_id"] == "BBB"
    assert first["snapshot_id"] != second["snapshot_id"]

    first_state = get_state(client, "AAA")
    second_state = get_state(client, "BBB")
    assert [entry["data_id"] for entry in first_state["items"]] == [PACK_CANNON]
    assert [entry["data_id"] for entry in second_state["items"]] == [PACK_HERO]


def test_state_survives_a_new_application(tmp_path: Path) -> None:
    before = make_client(tmp_path)
    install_active_pack(before)
    doc = export_doc(buildings=[{"data": PACK_LAB, "lvl": 1, "timer": 600}])
    first = imported(before, doc)
    expected = get_state(before, first["village_id"])

    after = make_client(tmp_path)

    assert get_state(after, first["village_id"]) == expected
    again = post_doc(after, doc)
    assert again.status_code == 200
    assert again.json()["snapshot_id"] == first["snapshot_id"]


# --------------------------------------------------------------------------- fixture


def fixture_doc() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(EXPORT_FIXTURE.read_text(encoding="utf-8"))
    return data


def test_fixture_export_is_parsed_with_an_invented_tag() -> None:
    doc = fixture_doc()
    snapshot = parse(doc)

    assert snapshot.tag == "FICTIF2026"
    assert snapshot.exported_at == doc["timestamp"]
    assert not snapshot.unknown_sections
    assert len(list(snapshot.items)) == 14
    assert sorted(job.finish_at - doc["timestamp"] for job in snapshot.running) == [
        0,
        37745,
        82379,
        164724,
    ]
    assert not mentions_skewed_timers(snapshot.warnings)


def test_fixture_export_imports_over_the_fictional_pack(client: TestClient) -> None:
    body = imported(client, fixture_doc())

    assert body["village_id"] == "FICTIF2026"
    assert PACK_CANNON not in body["unknown_ids"]
    assert OTHER_ALIEN in body["unknown_ids"]
    state = get_state(client, "FICTIF2026")
    assert len(state["running"]) == 4
    assert state_item(state, PACK_LAB)["name"] == "Laboratoire de test"
