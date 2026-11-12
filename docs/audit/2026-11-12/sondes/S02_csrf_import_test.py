"""Sonde S02 (H02) : une page tierce ne doit pas pouvoir écrire dans ClashPilot.

Un navigateur envoie un POST "simple" (text/plain, sans preflight CORS) vers 127.0.0.1 depuis
n'importe quel site. Comportement sûr attendu : la requête porte une Origin étrangère, elle est
refusée (403) et aucun village n'est créé.
A placer dans backend/tests/unit/ pour l'exécuter.
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
