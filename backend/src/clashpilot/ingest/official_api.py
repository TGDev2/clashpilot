"""Client of the official Clash of Clans API (GET /players/{tag}) and level matching.

The API gives troops, heroes, spells and hero equipment only: no building, timer or resource.
The token is never written to a log or a message, and upstream messages are never forwarded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.state import VillageSnapshot

# API section -> the name used in answers.
SECTIONS = {
    "troops": "troops",
    "heroes": "heroes",
    "spells": "spells",
    "heroEquipment": "equipment",
}
TIMEOUT_S = 10.0


class CocApiError(Exception):
    """An upstream failure, already translated to our status, code and French message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ApiLevel:
    data_id: int
    name: str
    section: str
    from_level: int
    level: int


@dataclass(frozen=True)
class ApiConflict:
    name: str
    section: str
    level: int | None
    reason: str


def _unexpected() -> CocApiError:
    return CocApiError(502, "coc_unexpected_response", "L'API officielle a répondu de travers.")


def _failure(response: httpx.Response) -> CocApiError:
    status = response.status_code
    reason = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("reason"), str):
        reason = body["reason"]
    if status == 403 and reason == "accessDenied.invalidIp":
        return CocApiError(
            502,
            "coc_access_denied",
            "L'API officielle refuse la clé : l'adresse IP actuelle n'est pas celle de la clé.",
        )
    if status in (401, 403):
        return CocApiError(502, "coc_access_denied", "L'API officielle a refusé la clé.")
    if status == 404:
        return CocApiError(404, "coc_player_not_found", "Le joueur est introuvable côté API.")
    if status == 429:
        return CocApiError(
            503, "coc_rate_limited", "L'API officielle limite le débit : réessaie plus tard."
        )
    if status >= 500:
        return CocApiError(503, "coc_unavailable", "L'API officielle est indisponible.")
    return _unexpected()


def fetch_player(
    base_url: str,
    token: str,
    tag: str,
    transport: httpx.BaseTransport | None = None,
) -> Mapping[str, Any]:
    """GET /players/%23{tag} with a Bearer token; raise CocApiError on any failure."""
    url = f"{base_url.rstrip('/')}/players/%23{quote(tag, safe='')}"
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_S) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        # The error text is dropped: it may carry request details.
        raise CocApiError(503, "coc_unavailable", "L'API officielle est injoignable.") from None
    if response.status_code != 200:
        raise _failure(response)
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise _unexpected()
    return body


def match_levels(
    player: Mapping[str, Any], snapshot: VillageSnapshot, pack: GamePack | None
) -> tuple[list[ApiLevel], list[ApiConflict]]:
    """Compare the API levels with the export, by name through the active pack.

    Returns the entries whose level differs from the export's, and the entries that cannot be
    compared: a name the pack does not know, or an item absent from the export. Entries of the
    builder base are ignored.
    """
    ids_by_name: dict[str, int] = {}
    if pack is not None:
        for known in pack.data.items:
            ids_by_name.setdefault(known.name, known.data_id)
    exported: dict[int, int] = {}
    for item in snapshot.items:
        if item.level is not None:
            exported[item.data_id] = max(item.level, exported.get(item.data_id, item.level))

    updated: list[ApiLevel] = []
    conflicts: list[ApiConflict] = []
    for api_section, section in SECTIONS.items():
        entries = player.get(api_section)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            if entry.get("village", "home") != "home":
                continue
            name = entry["name"]
            raw_level = entry.get("level")
            level = (
                raw_level
                if isinstance(raw_level, int) and not isinstance(raw_level, bool)
                else None
            )
            data_id = ids_by_name.get(name)
            if data_id is None or level is None:
                reason = "unknown_name" if data_id is None else "invalid_level"
                conflicts.append(ApiConflict(name, section, level, reason))
            elif data_id not in exported:
                conflicts.append(ApiConflict(name, section, level, "not_in_export"))
            elif exported[data_id] != level:
                updated.append(ApiLevel(data_id, name, section, exported[data_id], level))
    return updated, conflicts
