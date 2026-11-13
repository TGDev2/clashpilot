"""Cross-site writes and foreign host names are refused (audit 2026-11-12, probes S02 and S03).

A page of another site can send a "simple" POST (text/plain, no CORS preflight) to 127.0.0.1, and
DNS rebinding can make a foreign name resolve to it: neither may reach the API.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

EXPORTED_AT = 1794133192


def test_cross_site_text_plain_import_is_refused(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=lambda: EXPORTED_AT + 60))
    forged = json.dumps({"tag": "#EVIL", "timestamp": EXPORTED_AT})
    response = client.post(
        "/api/villages/import",
        content=forged,
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert response.status_code == 403, f"S02 cross-site import accepted: {response.status_code}"
    assert client.get("/api/villages/EVIL/state").status_code == 404


def test_foreign_host_header_is_refused(tmp_path: Path) -> None:
    settings = Settings.from_env({"CLASHPILOT_DATA_DIR": str(tmp_path)})
    client = TestClient(create_app(settings, clock=lambda: 1794133252))
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code in (400, 421), f"S03 foreign Host accepted: {response.status_code}"


def test_local_host_header_still_works(tmp_path: Path) -> None:
    settings = Settings.from_env({"CLASHPILOT_DATA_DIR": str(tmp_path)})
    client = TestClient(create_app(settings, clock=lambda: 1794133252))
    for host in ("127.0.0.1:8000", "localhost:8000"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200


def _import(client: TestClient, origin: str, host: str = "testserver") -> int:
    body = json.dumps({"tag": "#OWN", "timestamp": EXPORTED_AT})
    headers = {"Content-Type": "application/json", "Origin": origin, "Host": host}
    return client.post("/api/villages/import", content=body, headers=headers).status_code


def test_vite_dev_server_and_same_origin_writes_are_accepted(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=lambda: EXPORTED_AT + 60))
    assert _import(client, "http://localhost:5173") == 201
    assert _import(client, "http://192.168.1.20:8000", host="192.168.1.20:8000") == 200


def test_allowed_hosts_come_from_the_environment(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"CLASHPILOT_DATA_DIR": str(tmp_path), "CLASHPILOT_ALLOWED_HOSTS": "pc.tailnet.ts.net"}
    )
    client = TestClient(create_app(settings, clock=lambda: EXPORTED_AT + 60))
    assert client.get("/api/health", headers={"Host": "pc.tailnet.ts.net"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 400


def test_bound_to_every_interface_without_a_list_any_host_is_accepted() -> None:
    settings = Settings.from_env({"CLASHPILOT_HOST": "0.0.0.0"})
    assert settings.allowed_hosts == ("*",)
