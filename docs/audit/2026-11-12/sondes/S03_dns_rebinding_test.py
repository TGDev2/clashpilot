"""Sonde S03 (H03) : un nom d'hôte étranger (rebond DNS) ne doit pas lire l'API.

Comportement sûr attendu : le serveur tel qu'il est lancé (réglages lus dans l'environnement,
`CLASHPILOT_HOST` par défaut) refuse (400 ou 421) une requête dont l'en-tête Host n'est ni
127.0.0.1, ni localhost, et n'expose aucune donnée.
A placer dans backend/tests/unit/ pour l'exécuter.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings


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
