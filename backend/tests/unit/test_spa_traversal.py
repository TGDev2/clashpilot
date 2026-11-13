"""Sonde S04 (H04) : la route attrape-tout ne sert jamais un fichier hors de frontend/dist.

Comportement sûr attendu : chaque tentative renvoie 404, ou index.html, mais jamais le contenu du
fichier secret placé à côté du dossier dist.
Adopted from the audit of 2026-11-12.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings

MARKER = "SECRET-S04-MARKER"
ATTEMPTS = [
    "../secret.txt",
    "%2e%2e/secret.txt",
    "%2e%2e%2fsecret.txt",
    "..%5csecret.txt",
    "assets/../../secret.txt",
    "assets/%2e%2e/%2e%2e/secret.txt",
    "%252e%252e/secret.txt",
]


@pytest.mark.parametrize("attempt", ATTEMPTS)
def test_spa_never_serves_outside_dist(tmp_path: Path, attempt: str) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>INDEX</html>")
    (tmp_path / "secret.txt").write_text(MARKER)
    settings = Settings(data_dir=tmp_path / "data", frontend_dist=dist)
    client = TestClient(create_app(settings, clock=lambda: 1794133252))
    response = client.get(f"/{attempt}")
    assert MARKER not in response.text, f"S04 traversal served the secret via {attempt}"
