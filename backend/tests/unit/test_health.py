from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings


def test_health_reports_app_and_version() -> None:
    client = TestClient(create_app(Settings(frontend_dist=Path("absent"))))
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "ClashPilot"


def test_clock_is_injectable() -> None:
    app = create_app(clock=lambda: 42)
    assert app.state.clock() == 42


def test_unknown_api_route_is_a_json_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    client = TestClient(create_app(Settings(frontend_dist=tmp_path)))
    response = client.get("/api/inconnu")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"
