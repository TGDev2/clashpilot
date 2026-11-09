"""Acceptance tests for E05: import comparison, manual input and official API sync (SPEC ADR-3).

Frozen with the task. They describe the contract of `GET /api/villages/{id}/diff`,
`POST /api/villages/{id}/sync`, `PUT|GET /api/villages/{id}/manual` and the `resources` / builders
part of `GET /api/villages/{id}/state`. They use only fictional data.

Shapes that the task text leaves open are fixed here:

- `diff`: `from` and `to` are `snapshot_id` values returned by the import. Without them the two
  snapshots with the latest `exported_at` are compared (older to newer). A job is identified by
  `(data_id, to_level)`. `started` lists jobs present only in the newer snapshot, `finished` lists
  jobs present only in the older one whose item level went up in the newer one. Every entry of
  `upgraded`, `added`, `removed`, `started` and `finished` carries `data_id`; `upgraded` entries
  also carry `name`, `from_level` and `to_level`.
- `sync`: the official API is reached through `Settings.coc_base_url` with a Bearer token taken
  from `Settings.coc_token` only. These tests start a real local HTTP server and point
  `coc_base_url` at it. `updated` entries carry `data_id`, `name`, the API level as `level` (or
  `to_level`) and, directly or under `provenance`, `source` "api" and `status` "confirmed".
  `conflicts` entries mention the API entry name that the active pack does not know. After a sync,
  `GET state` shows the API level of an updated item with provenance source "api", status
  "confirmed", `as_of` equal to `fetched_at`.
- `manual`: `PUT` replaces the whole manual state. Body keys: `resources` (resource name ->
  `{value, status, as_of}`), `builders_total` (int), `magic_items` (name -> count), `gold_pass`
  (`{"active": bool}`), `farm_rate` (resource name -> `{low, expected, high}`), `storage_override`
  (resource name -> capacity), `events` (list of `{label, start, end, lane, duration_pct,
  cost_pct}`). `GET` returns them, every resource entry carrying `source` "manual".
- `state`: `resources` maps each of the seven resource names to a provenance
  `{value, status, source, as_of}`. The builders count is a provenance found under `builders` (or
  `builders_total`, or `resources.builders`) of the state.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PACK_FIXTURE = FIXTURES / "packs" / "fictional_pack_v1.json"

IMPORT_URL = "/api/villages/import"

T0 = 1794133192
NOW = 1794300000
TOKEN = "TOKEN-do-not-leak-7f3a9c1e"

# Ids of the fictional pack (see fictional_pack_v1.json), plus a spell and an equipment added here.
TOWN_HALL = 9000001
HUT = 9000002
LAB = 9000003
CANNON = 9000004
STORAGE = 9000005
HERO = 9000006
TROOP = 9000007
SPELL = 9000008
EQUIPMENT = 9000009

FRENCH_WORDS = re.compile(
    r"\b(le|la|les|un|une|des|du|de|est|pas|ne|sans|manquant\w*|invalide\w*|illisible\w*)\b",
    re.IGNORECASE,
)

RESOURCES = ("gold", "elixir", "dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore")

PLAYER_PATH = "/v1/players/%23EXAMPLE0"


# --------------------------------------------------------------------------- fake official API


class FakeCoc:
    """A local HTTP server standing for the official API; records what it receives."""

    def __init__(self) -> None:
        self.status = 200
        self.body: Any = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                payload = json.dumps(owner.body).encode("utf-8")
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def coc() -> Iterator[FakeCoc]:
    server = FakeCoc()
    try:
        yield server
    finally:
        server.close()


def api_entry(name: str, level: int, max_level: int = 10, village: str = "home") -> dict[str, Any]:
    return {"name": name, "level": level, "maxLevel": max_level, "village": village}


def player_payload(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tag": "#EXAMPLE0",
        "name": "Joueur fictif",
        "townHallLevel": 2,
        "troops": [],
        "heroes": [],
        "spells": [],
        "heroEquipment": [],
        **sections,
    }


# --------------------------------------------------------------------------- helpers


def only(entries: Any) -> Any:
    listed = list(entries)
    assert len(listed) == 1, listed
    return listed[0]


def assert_french(message: Any) -> None:
    assert isinstance(message, str)
    assert message
    assert FRENCH_WORDS.search(message), message


def fictional_pack() -> dict[str, Any]:
    """The fixture pack plus a spell and an equipment, so that all four API sections can match."""
    pack: dict[str, Any] = json.loads(PACK_FIXTURE.read_text(encoding="utf-8"))
    pack["pack_id"] = "fictional-sync-pack-v1"
    template = {"village": "home", "tags": [], "max_count_by_th": {"1": 1}}
    pack["items"].append(
        {
            **template,
            "data_id": SPELL,
            "name": "Sort de test",
            "kind": "spell",
            "lane": "lab",
            "category": "spell",
        }
    )
    pack["items"].append(
        {
            **template,
            "data_id": EQUIPMENT,
            "name": "Equipement de test",
            "kind": "equipment",
            "lane": "blacksmith",
            "category": "equipment",
        }
    )
    return pack


def make_client(
    data_dir: Path, token: str | None = None, base_url: str | None = None
) -> TestClient:
    settings = Settings(
        data_dir=data_dir,
        frontend_dist=data_dir / "no-dist",
        coc_token=token,
        **({"coc_base_url": base_url} if base_url is not None else {}),
    )
    return TestClient(create_app(settings, clock=lambda: NOW))


def install_pack(client: TestClient) -> None:
    pack = fictional_pack()
    assert client.post("/api/gamedata/packs", json=pack).status_code == 201
    assert client.put(f"/api/gamedata/packs/{pack['pack_id']}/active").status_code == 200


def export_doc(tag: str = "#EXAMPLE0", at: int = T0, **sections: Any) -> dict[str, Any]:
    return {"tag": tag, "timestamp": at, **sections}


def imported(client: TestClient, doc: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        IMPORT_URL,
        content=json.dumps(doc).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def get_state(client: TestClient, village_id: str = "EXAMPLE0") -> dict[str, Any]:
    response = client.get(f"/api/villages/{village_id}/state")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def state_item(state: dict[str, Any], data_id: int) -> dict[str, Any]:
    found: dict[str, Any] = only(e for e in state["items"] if e["data_id"] == data_id)
    return found


def error_of(response: Any) -> dict[str, Any]:
    detail: dict[str, Any] = response.json()["detail"]
    return detail


def snapshot_rows(data_dir: Path, village_id: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(data_dir / "clashpilot.db")
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM village_snapshots WHERE village_id = ? ORDER BY snapshot_id",
                (village_id,),
            )
        ]
    finally:
        connection.close()


# --------------------------------------------------------------------------- GET diff


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    created = make_client(tmp_path)
    install_pack(created)
    return created


@pytest.fixture
def three_snapshots(client: TestClient) -> dict[str, int]:
    """Snapshots A, B, C of one village, with `exported_at` increasing."""
    first = imported(
        client,
        export_doc(
            at=T0,
            buildings=[
                {"data": TOWN_HALL, "lvl": 1},
                {"data": LAB, "lvl": 1, "timer": 600},
                {"data": CANNON, "lvl": 2},
                {"data": STORAGE, "lvl": 1},
            ],
            units=[{"data": TROOP, "lvl": 1, "timer": 100}],
        ),
    )
    second = imported(
        client,
        export_doc(
            at=T0 + 3600,
            buildings=[
                {"data": TOWN_HALL, "lvl": 1},
                {"data": HUT, "lvl": 1, "timer": 300},
                {"data": LAB, "lvl": 2},
                {"data": CANNON, "lvl": 3},
            ],
            units=[{"data": TROOP, "lvl": 1}],
            heroes=[{"data": HERO, "lvl": 1, "timer": 5000}],
        ),
    )
    third = imported(
        client,
        export_doc(
            at=T0 + 7200,
            buildings=[
                {"data": TOWN_HALL, "lvl": 1},
                {"data": HUT, "lvl": 1},
                {"data": LAB, "lvl": 2},
                {"data": CANNON, "lvl": 4},
            ],
            units=[{"data": TROOP, "lvl": 1}],
            heroes=[{"data": HERO, "lvl": 2}],
        ),
    )
    return {"a": first["snapshot_id"], "b": second["snapshot_id"], "c": third["snapshot_id"]}


def get_diff(client: TestClient, **params: int) -> dict[str, Any]:
    response = client.get("/api/villages/EXAMPLE0/diff", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(entries: Any) -> set[int]:
    return {entry["data_id"] for entry in entries}


def upgrades(diff: dict[str, Any]) -> dict[int, tuple[Any, ...]]:
    return {e["data_id"]: (e["name"], e["from_level"], e["to_level"]) for e in diff["upgraded"]}


def test_diff_without_parameter_compares_the_two_latest_snapshots(
    client: TestClient, three_snapshots: dict[str, int]
) -> None:
    diff = get_diff(client)

    assert upgrades(diff) == {
        CANNON: ("Canon de test", 3, 4),
        HERO: ("Heros de test", 1, 2),
    }
    assert ids(diff["added"]) == set()
    assert ids(diff["removed"]) == set()
    assert ids(diff["started"]) == set()
    # The hero upgrade ended; the hut job disappeared without a level change.
    assert ids(diff["finished"]) == {HERO}


def test_diff_with_from_and_to_compares_the_named_snapshots(
    client: TestClient, three_snapshots: dict[str, int]
) -> None:
    diff = get_diff(client, **{"from": three_snapshots["a"], "to": three_snapshots["b"]})

    assert upgrades(diff) == {
        LAB: ("Laboratoire de test", 1, 2),
        CANNON: ("Canon de test", 2, 3),
    }
    assert ids(diff["added"]) == {HUT, HERO}
    assert ids(diff["removed"]) == {STORAGE}
    assert ids(diff["started"]) == {HUT, HERO}
    # The lab job ended with a level up; the troop job vanished with its level unchanged.
    assert ids(diff["finished"]) == {LAB}


def test_diff_can_span_non_adjacent_snapshots(
    client: TestClient, three_snapshots: dict[str, int]
) -> None:
    diff = get_diff(client, **{"from": three_snapshots["a"], "to": three_snapshots["c"]})

    assert upgrades(diff) == {
        LAB: ("Laboratoire de test", 1, 2),
        CANNON: ("Canon de test", 2, 4),
    }
    assert ids(diff["removed"]) == {STORAGE}
    assert ids(diff["added"]) == {HUT, HERO}


def test_diff_answers_in_the_five_lists(
    client: TestClient, three_snapshots: dict[str, int]
) -> None:
    diff = get_diff(client)
    for key in ("upgraded", "added", "removed", "started", "finished"):
        assert isinstance(diff[key], list), key
        assert all(isinstance(entry, dict) for entry in diff[key]), key


def test_diff_with_one_snapshot_is_a_409(client: TestClient) -> None:
    imported(client, export_doc(buildings=[{"data": CANNON, "lvl": 2}]))

    response = client.get("/api/villages/EXAMPLE0/diff")

    assert response.status_code == 409
    detail = error_of(response)
    assert detail["code"] == "diff_requires_two_snapshots"
    assert_french(detail["message"])


def test_importing_the_same_export_again_does_not_make_a_second_snapshot(
    client: TestClient,
) -> None:
    doc = export_doc(buildings=[{"data": CANNON, "lvl": 2}])
    imported(client, doc)
    assert client.post(IMPORT_URL, content=json.dumps(doc).encode("utf-8")).status_code == 200

    response = client.get("/api/villages/EXAMPLE0/diff")

    assert response.status_code == 409
    assert error_of(response)["code"] == "diff_requires_two_snapshots"


def test_diff_of_an_unknown_village_is_a_404(client: TestClient) -> None:
    response = client.get("/api/villages/NOPE/diff")
    assert response.status_code == 404
    assert error_of(response)["code"] == "village_not_found"


# --------------------------------------------------------------------------- POST sync


def synced_client(tmp_path: Path, coc: FakeCoc, token: str | None = TOKEN) -> TestClient:
    created = make_client(tmp_path, token=token, base_url=coc.base_url)
    install_pack(created)
    return created


def import_village_for_sync(client: TestClient) -> str:
    doc = export_doc(
        buildings=[
            {"data": TOWN_HALL, "lvl": 2},
            {"data": CANNON, "lvl": 3, "timer": 100},
            {"data": LAB, "lvl": 1, "timer": 37745},
        ],
        units=[{"data": TROOP, "lvl": 1}],
        heroes=[{"data": HERO, "lvl": 1}],
        spells=[{"data": SPELL, "lvl": 2}],
        equipment=[{"data": EQUIPMENT, "lvl": 5}],
    )
    return str(imported(client, doc)["village_id"])


def serve_player(coc: FakeCoc) -> None:
    coc.body = player_payload(
        troops=[api_entry("Troupe de test", 3)],
        heroes=[api_entry("Heros de test", 1)],
        spells=[api_entry("Sort de test", 4)],
        heroEquipment=[api_entry("Equipement de test", 4)],
    )


def level_of(entry: dict[str, Any]) -> Any:
    return entry["level"] if "level" in entry else entry["to_level"]


def provenance_of(entry: dict[str, Any]) -> dict[str, Any]:
    nested = entry.get("provenance")
    return nested if isinstance(nested, dict) else entry


def test_sync_calls_the_player_endpoint_with_a_bearer_token(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)

    response = client.post(f"/api/villages/{village_id}/sync")

    assert response.status_code == 200, response.text
    path, headers = only(coc.requests)
    assert path == PLAYER_PATH
    assert headers["authorization"] == f"Bearer {TOKEN}"


def test_sync_lists_the_levels_that_differ_from_the_export(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)

    response = client.post(f"/api/villages/{village_id}/sync")

    assert response.status_code == 200, response.text
    body = response.json()
    updated = {entry["data_id"]: entry for entry in body["updated"]}
    # The hero is at the same level in the API: not listed.
    assert set(updated) == {TROOP, SPELL, EQUIPMENT}
    assert updated[TROOP]["name"] == "Troupe de test"
    assert level_of(updated[TROOP]) == 3
    assert level_of(updated[SPELL]) == 4
    assert level_of(updated[EQUIPMENT]) == 4
    for entry in updated.values():
        provenance = provenance_of(entry)
        assert provenance["source"] == "api"
        assert provenance["status"] == "confirmed"
    assert body["conflicts"] == []
    assert body["fetched_at"] == NOW


def test_sync_reports_entries_unknown_to_the_pack_as_conflicts(
    tmp_path: Path, coc: FakeCoc
) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    coc.body = player_payload(
        troops=[api_entry("Troupe de test", 3), api_entry("Machine inconnue", 2)],
    )

    body = client.post(f"/api/villages/{village_id}/sync").json()

    assert ids(body["updated"]) == {TROOP}
    conflict = only(body["conflicts"])
    assert "Machine inconnue" in json.dumps(conflict, ensure_ascii=False)


def test_sync_levels_are_shown_in_the_state_as_api_confirmed(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)
    fetched_at = client.post(f"/api/villages/{village_id}/sync").json()["fetched_at"]

    state = get_state(client, village_id)

    for data_id, level in ((TROOP, 3), (SPELL, 4), (EQUIPMENT, 4)):
        item = state_item(state, data_id)
        assert item["level"] == level
        assert item["provenance"]["source"] == "api"
        assert item["provenance"]["status"] == "confirmed"
        assert item["provenance"]["as_of"] == fetched_at
    assert state_item(state, HERO)["level"] == 1


def test_synced_levels_survive_a_new_application(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)
    assert client.post(f"/api/villages/{village_id}/sync").status_code == 200

    after = make_client(tmp_path, token=TOKEN, base_url=coc.base_url)

    item = state_item(get_state(after, village_id), TROOP)
    assert item["level"] == 3
    assert item["provenance"]["source"] == "api"


def test_sync_overwrites_no_building_timer_or_snapshot(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    before = get_state(client, village_id)
    rows_before = snapshot_rows(tmp_path, village_id)
    serve_player(coc)
    assert client.post(f"/api/villages/{village_id}/sync").status_code == 200

    after = get_state(client, village_id)

    synced_sections = {"units", "heroes", "spells", "equipment"}

    def untouched(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in state["items"] if item["section"] not in synced_sections]

    assert untouched(after) == untouched(before)
    assert after["running"] == before["running"]
    assert len(after["running"]) == 2
    assert after["snapshot_id"] == before["snapshot_id"]
    assert after["exported_at"] == before["exported_at"]
    assert after["resources"] == before["resources"]
    assert snapshot_rows(tmp_path, village_id) == rows_before


def test_sync_adds_no_export_snapshot(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)
    assert client.post(f"/api/villages/{village_id}/sync").status_code == 200

    # The sync adds no export snapshot: one import, so there is still nothing to compare.
    assert client.get(f"/api/villages/{village_id}/diff").status_code == 409


def test_sync_without_a_token_is_a_409_and_calls_nothing(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc, token=None)
    village_id = import_village_for_sync(client)
    serve_player(coc)

    response = client.post(f"/api/villages/{village_id}/sync")

    assert response.status_code == 409
    detail = error_of(response)
    assert detail["code"] == "api_not_configured"
    assert_french(detail["message"])
    assert coc.requests == []


def test_the_token_comes_only_from_the_settings(
    tmp_path: Path, coc: FakeCoc, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    (work / ".env").write_text(f"CLASHPILOT_COC_TOKEN={TOKEN}\n", encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.setenv("CLASHPILOT_COC_TOKEN", TOKEN)
    client = synced_client(tmp_path / "data", coc, token=None)
    village_id = import_village_for_sync(client)
    serve_player(coc)

    response = client.post(f"/api/villages/{village_id}/sync")

    assert response.status_code == 409
    assert error_of(response)["code"] == "api_not_configured"
    assert coc.requests == []


def test_sync_of_an_unknown_village_is_a_404(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    serve_player(coc)

    response = client.post("/api/villages/NOPE/sync")

    assert response.status_code == 404
    assert error_of(response)["code"] == "village_not_found"
    assert coc.requests == []


INVALID_IP_BODY = {
    "reason": "accessDenied.invalidIp",
    "message": f"Invalid authorization for token {TOKEN} from IP 203.0.113.9",
}


@pytest.mark.parametrize(
    ("upstream_status", "upstream_body", "status", "code"),
    [
        (403, INVALID_IP_BODY, 502, "coc_access_denied"),
        (404, {"reason": "notFound"}, 404, "coc_player_not_found"),
        (429, {"reason": "requestThrottled"}, 503, "coc_rate_limited"),
        (503, {"reason": "inMaintenance"}, 503, "coc_unavailable"),
    ],
)
def test_upstream_errors_become_our_own_codes(
    tmp_path: Path,
    coc: FakeCoc,
    upstream_status: int,
    upstream_body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    coc.status = upstream_status
    coc.body = upstream_body

    response = client.post(f"/api/villages/{village_id}/sync")

    assert response.status_code == status
    detail = error_of(response)
    assert detail["code"] == code
    assert_french(detail["message"])
    assert len(coc.requests) == 1


def test_access_denied_message_mentions_the_ip(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    coc.status = 403
    coc.body = INVALID_IP_BODY

    detail = error_of(client.post(f"/api/villages/{village_id}/sync"))

    assert re.search(r"\bIP\b", detail["message"]), detail["message"]


@pytest.mark.parametrize(
    ("upstream_status", "upstream_body"),
    [
        (200, player_payload(troops=[api_entry("Troupe de test", 3)])),
        (403, INVALID_IP_BODY),
        (404, {"reason": "notFound", "message": f"nothing for {TOKEN}"}),
        (429, {"reason": "requestThrottled", "message": f"slow down {TOKEN}"}),
        (503, {"reason": "inMaintenance", "message": f"maintenance {TOKEN}"}),
    ],
)
def test_the_token_appears_nowhere_it_should_not(
    tmp_path: Path,
    coc: FakeCoc,
    caplog: pytest.LogCaptureFixture,
    upstream_status: int,
    upstream_body: dict[str, Any],
) -> None:
    caplog.set_level(logging.DEBUG)
    client = synced_client(tmp_path / "data", coc)
    village_id = import_village_for_sync(client)
    coc.status = upstream_status
    coc.body = upstream_body

    response = client.post(f"/api/villages/{village_id}/sync")

    if upstream_status == 200:
        assert response.status_code == 200
    assert len(coc.requests) == 1
    assert TOKEN not in response.text
    assert TOKEN not in json.dumps(dict(response.headers))
    assert TOKEN not in caplog.text
    assert TOKEN not in get_state_text(client, village_id)
    for stored in (tmp_path / "data").rglob("*"):
        if stored.is_file():
            assert TOKEN.encode("utf-8") not in stored.read_bytes(), stored


def get_state_text(client: TestClient, village_id: str) -> str:
    return client.get(f"/api/villages/{village_id}/state").text


# --------------------------------------------------------------------------- manual input


MANUAL: dict[str, Any] = {
    "resources": {
        "gold": {"value": 1500000, "status": "confirmed", "as_of": T0},
        "elixir": {"value": 900000, "status": "estimated", "as_of": T0 + 60},
        "dark_elixir": {"value": 12000, "status": "confirmed", "as_of": T0 + 120},
        "gems": {"value": 340, "status": "confirmed", "as_of": T0 + 180},
        "shiny_ore": {"value": 25000, "status": "estimated", "as_of": T0 + 240},
        "glowy_ore": {"value": 1800, "status": "confirmed", "as_of": T0 + 300},
        "starry_ore": {"value": 90, "status": "confirmed", "as_of": T0 + 360},
    },
    "builders_total": 5,
    "magic_items": {"builder_potion": 3, "research_potion": 1},
    "gold_pass": {"active": True},
    "farm_rate": {
        "gold": {"low": 100000, "expected": 150000, "high": 200000},
        "elixir": {"low": 90000, "expected": 140000, "high": 190000},
        "dark_elixir": {"low": 500, "expected": 800, "high": 1200},
    },
    "storage_override": {"gold": 2000000},
    "events": [
        {
            "label": "Reduction de test",
            "start": T0,
            "end": T0 + 86400,
            "lane": "lab",
            "duration_pct": 50,
            "cost_pct": 100,
        },
        {
            "label": "Bonus d'or de test",
            "start": T0 + 86400,
            "end": T0 + 172800,
            "lane": "builder",
            "duration_pct": 100,
            "cost_pct": 80,
        },
    ],
}


def plain(value: Any) -> Any:
    """A manual value, whether returned bare or wrapped in a provenance."""
    if isinstance(value, dict) and "value" in value:
        assert value.get("source", "manual") == "manual"
        return value["value"]
    return value


def put_manual(client: TestClient, body: dict[str, Any], village_id: str = "EXAMPLE0") -> Any:
    return client.put(f"/api/villages/{village_id}/manual", json=body)


@pytest.fixture
def village(client: TestClient) -> str:
    return str(imported(client, export_doc(buildings=[{"data": CANNON, "lvl": 2}]))["village_id"])


def test_manual_state_is_saved_and_read_back_with_source_manual(
    client: TestClient, village: str
) -> None:
    assert put_manual(client, MANUAL).status_code == 200

    response = client.get(f"/api/villages/{village}/manual")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["resources"]) >= set(RESOURCES)
    for name, expected in MANUAL["resources"].items():
        entry = body["resources"][name]
        assert entry["value"] == expected["value"], name
        assert entry["status"] == expected["status"], name
        assert entry["as_of"] == expected["as_of"], name
        assert entry["source"] == "manual", name
    assert plain(body["builders_total"]) == 5
    assert plain(body["magic_items"]) == MANUAL["magic_items"]
    assert plain(body["gold_pass"]) == MANUAL["gold_pass"]
    assert plain(body["storage_override"]) == MANUAL["storage_override"]
    for name, rate in MANUAL["farm_rate"].items():
        for key in ("low", "expected", "high"):
            assert body["farm_rate"][name][key] == rate[key], (name, key)
    events = plain(body["events"])
    assert len(events) == 2
    for sent, got in zip(MANUAL["events"], events, strict=True):
        assert {key: got[key] for key in sent} == sent


def test_manual_state_survives_a_new_application(
    tmp_path: Path, client: TestClient, village: str
) -> None:
    assert put_manual(client, MANUAL).status_code == 200

    after = make_client(tmp_path)

    entry = after.get(f"/api/villages/{village}/manual").json()["resources"]["gold"]
    assert entry["value"] == 1500000
    assert entry["source"] == "manual"


def test_manual_put_replaces_the_whole_manual_state(client: TestClient, village: str) -> None:
    assert put_manual(client, MANUAL).status_code == 200

    assert put_manual(client, {"builders_total": 4}).status_code == 200

    state = get_state(client, village)
    assert state["resources"]["gold"]["status"] == "unknown"
    assert state["resources"]["gold"]["value"] is None
    assert builders_of(state)["value"] == 4


@pytest.mark.parametrize("resource", ["gold", "gems", "starry_ore"])
def test_manual_status_unknown_is_a_422(client: TestClient, village: str, resource: str) -> None:
    body = {"resources": {resource: {"value": 10, "status": "unknown", "as_of": T0}}}

    response = put_manual(client, body)

    assert response.status_code == 422
    detail = error_of(response)
    assert detail["code"] == "invalid_manual_state"
    assert_french(detail["message"])
    assert get_state(client, village)["resources"][resource]["value"] is None


def test_manual_of_an_unknown_village_is_a_404(client: TestClient) -> None:
    response = put_manual(client, MANUAL, village_id="NOPE")
    assert response.status_code == 404
    assert error_of(response)["code"] == "village_not_found"


# --------------------------------------------------------------------------- state resources


def builders_of(state: dict[str, Any]) -> dict[str, Any]:
    for holder in (state, state.get("resources", {})):
        for key in ("builders", "builders_total"):
            if isinstance(holder, dict) and isinstance(holder.get(key), dict):
                found: dict[str, Any] = holder[key]
                return found
    raise AssertionError(f"no builders count in the state: {sorted(state)}")


def test_state_resources_without_input_are_unknown_never_zero(
    client: TestClient, village: str
) -> None:
    resources = get_state(client, village)["resources"]

    assert set(resources) == set(RESOURCES)
    for name in RESOURCES:
        assert resources[name]["status"] == "unknown", name
        assert resources[name]["value"] is None, name
        assert resources[name]["as_of"] is None, name


def test_state_resources_show_the_manual_input_with_status_and_as_of(
    client: TestClient, village: str
) -> None:
    body = {
        "resources": {
            "gold": {"value": 1500000, "status": "confirmed", "as_of": T0},
            "elixir": {"value": 0, "status": "estimated", "as_of": T0 + 60},
        }
    }
    assert put_manual(client, body).status_code == 200

    resources = get_state(client, village)["resources"]

    assert resources["gold"]["value"] == 1500000
    assert resources["gold"]["status"] == "confirmed"
    assert resources["gold"]["as_of"] == T0
    assert resources["gold"]["source"] == "manual"
    # A typed zero is a value, unlike an absent input.
    assert resources["elixir"]["value"] == 0
    assert resources["elixir"]["status"] == "estimated"
    assert resources["elixir"]["as_of"] == T0 + 60
    for name in ("dark_elixir", "gems", "shiny_ore", "glowy_ore", "starry_ore"):
        assert resources[name]["status"] == "unknown", name
        assert resources[name]["value"] is None, name


def test_builders_are_unknown_without_input_or_huts(client: TestClient, village: str) -> None:
    builders = builders_of(get_state(client, village))

    assert builders["status"] == "unknown"
    assert builders["value"] is None


def test_builders_come_from_the_huts_as_an_estimate(client: TestClient) -> None:
    imported(client, export_doc(tag="#HUTS", buildings=[{"data": HUT, "lvl": 1, "cnt": 3}]))

    builders = builders_of(get_state(client, "HUTS"))

    assert builders["value"] == 3
    assert builders["source"] == "model"
    assert builders["status"] == "estimated"


def test_manual_builders_win_over_the_huts(client: TestClient) -> None:
    imported(client, export_doc(tag="#HUTS", buildings=[{"data": HUT, "lvl": 1, "cnt": 3}]))
    assert put_manual(client, {"builders_total": 6}, village_id="HUTS").status_code == 200

    builders = builders_of(get_state(client, "HUTS"))

    assert builders["value"] == 6
    assert builders["source"] == "manual"


# --------------------------------------------------------------------------- several villages


def test_a_second_village_changes_nothing_of_the_first(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    first = import_village_for_sync(client)
    serve_player(coc)
    assert put_manual(client, MANUAL, village_id=first).status_code == 200
    assert client.post(f"/api/villages/{first}/sync").status_code == 200
    state_before = get_state(client, first)
    manual_before = client.get(f"/api/villages/{first}/manual").json()
    rows_before = snapshot_rows(tmp_path, first)

    second = imported(client, export_doc(tag="#OTHER99", buildings=[{"data": CANNON, "lvl": 1}]))[
        "village_id"
    ]

    assert second == "OTHER99"
    assert get_state(client, first) == state_before
    assert client.get(f"/api/villages/{first}/manual").json() == manual_before
    assert snapshot_rows(tmp_path, first) == rows_before
    other = get_state(client, second)
    assert [item["data_id"] for item in other["items"]] == [CANNON]
    assert other["resources"]["gold"]["value"] is None
    assert builders_of(other)["status"] == "unknown"
    assert client.get(f"/api/villages/{second}/diff").status_code == 409


def test_a_manual_input_belongs_to_its_village(client: TestClient, village: str) -> None:
    imported(client, export_doc(tag="#OTHER99", buildings=[{"data": CANNON, "lvl": 1}]))

    assert put_manual(client, MANUAL, village_id="OTHER99").status_code == 200

    assert get_state(client, village)["resources"]["gold"]["value"] is None
    assert get_state(client, "OTHER99")["resources"]["gold"]["value"] == 1500000


# --------------------------------------------------------------------------- no Supercell login

FORBIDDEN_NAMES = re.compile(
    r"password|passwd|mot_?de_?passe|supercell_?id|username|login|e_?mail|credential",
    re.IGNORECASE,
)


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


def test_no_route_asks_for_a_supercell_identifier_or_password(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "get" in paths["/api/villages/{village_id}/diff"]
    assert "post" in paths["/api/villages/{village_id}/sync"]
    assert {"get", "put"} <= set(paths["/api/villages/{village_id}/manual"])

    names = sorted({name for name in schema_names(schema) if FORBIDDEN_NAMES.search(name)})

    assert names == []


def test_no_table_stores_a_supercell_identifier_or_password(tmp_path: Path, coc: FakeCoc) -> None:
    client = synced_client(tmp_path, coc)
    village_id = import_village_for_sync(client)
    serve_player(coc)
    assert put_manual(client, MANUAL, village_id=village_id).status_code == 200
    assert client.post(f"/api/villages/{village_id}/sync").status_code == 200

    connection = sqlite3.connect(tmp_path / "clashpilot.db")
    try:
        columns = [
            f"{table}.{column[1]}"
            for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for column in connection.execute(f'PRAGMA table_info("{table}")')
        ]
    finally:
        connection.close()

    assert any(column.startswith("villages") for column in columns)
    assert [c for c in columns if FORBIDDEN_NAMES.search(c)] == []
