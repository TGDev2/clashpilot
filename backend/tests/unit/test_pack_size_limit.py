"""Sonde S10 (H10) : l'import d'un pack de données a une taille maximale, comme l'export.

Comportement sûr attendu : un corps de 6 Mo est refusé en 413 avant d'être chargé en mémoire.
Adopted from the audit of 2026-11-12 (probe S10).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from clashpilot.api.app import create_app
from clashpilot.settings import Settings


def test_oversized_pack_is_refused_with_413(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, frontend_dist=tmp_path / "no-dist")
    client = TestClient(create_app(settings, clock=lambda: 1794133252))
    body = '{"pack_id": "big", "padding": "' + "x" * 6_000_000 + '"}'
    response = client.post(
        "/api/gamedata/packs", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413, f"S10 oversized pack not refused: {response.status_code}"
