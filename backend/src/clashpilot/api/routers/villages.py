"""Village routes: import an in-game export and read the resulting state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, ValidationError

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.diff import diff_snapshots
from clashpilot.ingest.export import ImportFormatError, parse_export
from clashpilot.ingest.official_api import CocApiError, fetch_player, match_levels
from clashpilot.ingest.state import RESOURCES, ManualState, SnapshotItem, VillageSnapshot
from clashpilot.storage.db import open_db
from clashpilot.storage.packs import load_effective_pack

router = APIRouter(prefix="/api/villages", tags=["villages"])

MAX_EXPORT_BYTES = 5_000_000


class Provenance(BaseModel):
    value: int | None
    status: str
    source: str
    as_of: int | None


class VillageItemOut(BaseModel):
    data_id: int
    name: str | None
    level: int | None
    count: int
    section: str
    village: str
    raw: Any = None
    provenance: Provenance


class RunningJobOut(BaseModel):
    data_id: int
    name: str | None
    finish_at: int
    to_level: int
    section: str
    village: str
    extra: bool
    helper: bool
    provenance: Provenance


class VillageState(BaseModel):
    village_id: str
    snapshot_id: int
    tag: str
    exported_at: int
    format_version: str
    items: list[VillageItemOut]
    running: list[RunningJobOut]
    unknown_ids: list[int]
    unknown_sections: list[str]
    warnings: list[str]
    resources: dict[str, Provenance]
    builders: Provenance


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _active_pack(connection: sqlite3.Connection) -> GamePack | None:
    row = connection.execute("SELECT pack_id FROM gamedata_packs WHERE active = 1").fetchone()
    return load_effective_pack(connection, row["pack_id"]) if row is not None else None


@dataclass(frozen=True)
class _Extras:
    """What the state adds to an export: typed-in values and levels read from the official API."""

    manual: ManualState = field(default_factory=ManualState)
    manual_at: int | None = None
    # data_id -> (level, fetched_at)
    api_levels: dict[int, tuple[int, int]] = field(default_factory=dict)


def _extras(connection: sqlite3.Connection, village_id: str) -> _Extras:
    row = connection.execute(
        "SELECT body_json, updated_at FROM village_manual WHERE village_id = ?", (village_id,)
    ).fetchone()
    levels = {
        r["data_id"]: (r["level"], r["fetched_at"])
        for r in connection.execute(
            "SELECT data_id, level, fetched_at FROM village_api_levels WHERE village_id = ?",
            (village_id,),
        )
    }
    if row is None:
        return _Extras(api_levels=levels)
    manual = ManualState.model_validate_json(row["body_json"])
    return _Extras(manual=manual, manual_at=row["updated_at"], api_levels=levels)


def _builders(snapshot: VillageSnapshot, pack: GamePack | None, extras: _Extras) -> Provenance:
    """Typed-in count, else estimated from the builder huts of the pack, else unknown."""
    if extras.manual.builders_total is not None:
        return Provenance(
            value=extras.manual.builders_total,
            status="confirmed",
            source="manual",
            as_of=extras.manual_at,
        )
    huts = 0
    for item in snapshot.items:
        known = pack.item(item.data_id) if pack is not None else None
        if known is not None and "builder_hut" in known.tags:
            huts += item.count
    if huts:
        return Provenance(
            value=huts, status="estimated", source="model", as_of=snapshot.exported_at
        )
    return Provenance(value=None, status="unknown", source="model", as_of=None)


def _resources(extras: _Extras) -> dict[str, Provenance]:
    out: dict[str, Provenance] = {}
    for name in RESOURCES:
        typed = extras.manual.resources.get(name)
        out[name] = (
            Provenance(value=typed.value, status=typed.status, source="manual", as_of=typed.as_of)
            if typed is not None
            else Provenance(value=None, status="unknown", source="manual", as_of=None)
        )
    return out


def _state(
    snapshot: VillageSnapshot, snapshot_id: int, pack: GamePack | None, extras: _Extras
) -> VillageState:
    def name_of(data_id: int) -> str | None:
        item = pack.item(data_id) if pack is not None else None
        return item.name if item is not None else None

    def provenance(value: int | None) -> Provenance:
        return Provenance(
            value=value, status="confirmed", source="export", as_of=snapshot.exported_at
        )

    def item_out(item: SnapshotItem) -> VillageItemOut:
        level, shown = item.level, provenance(item.level)
        synced = extras.api_levels.get(item.data_id)
        # An export newer than the API read wins over it.
        if synced is not None and item.level is not None and synced[1] >= snapshot.exported_at:
            level = synced[0]
            shown = Provenance(value=level, status="confirmed", source="api", as_of=synced[1])
        return VillageItemOut(
            data_id=item.data_id,
            name=name_of(item.data_id),
            level=level,
            count=item.count,
            section=item.section,
            village=item.village,
            raw=item.raw,
            provenance=shown,
        )

    return VillageState(
        village_id=snapshot.tag,
        snapshot_id=snapshot_id,
        tag=snapshot.tag,
        exported_at=snapshot.exported_at,
        format_version=snapshot.format_version,
        items=[item_out(item) for item in snapshot.items],
        resources=_resources(extras),
        builders=_builders(snapshot, pack, extras),
        running=[
            RunningJobOut(
                data_id=job.data_id,
                name=name_of(job.data_id),
                finish_at=job.finish_at,
                to_level=job.to_level,
                section=job.section,
                village=job.village,
                extra=job.extra,
                helper=job.helper,
                provenance=provenance(job.finish_at),
            )
            for job in snapshot.running
        ],
        unknown_ids=sorted({i.data_id for i in snapshot.items if name_of(i.data_id) is None}),
        unknown_sections=list(snapshot.unknown_sections),
        warnings=list(snapshot.warnings),
    )


async def _read_body(request: Request) -> bytes:
    """The raw body, whatever its content type, read no further than the size limit."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EXPORT_BYTES:
            raise ApiError(413, "export_too_large", "L'export dépasse la taille maximale de 5 Mo.")
    return bytes(body)


def _invalid_export(message: str) -> ApiError:
    return ApiError(422, "invalid_export", message)


def _parse_body(body: bytes, imported_at: int) -> tuple[str, VillageSnapshot]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _invalid_export("Le contenu envoyé n'est pas du texte UTF-8.") from error
    try:
        return text, parse_export(text, imported_at)
    except ImportFormatError as error:
        raise _invalid_export(str(error)) from error


@router.post(
    "/import",
    status_code=201,
    response_model=VillageState,
    responses={
        200: {"model": VillageState, "description": "Identical export already stored."},
        413: {"model": ErrorBody, "description": "Body over 5 MB (code export_too_large)."},
        422: {"model": ErrorBody, "description": "Not an export (code invalid_export)."},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The raw JSON of the in-game export.",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    },
)
def import_export(
    request: Request, response: Response, body: Annotated[bytes, Depends(_read_body)]
) -> VillageState:
    imported_at: int = request.app.state.clock()
    text, snapshot = _parse_body(body, imported_at)
    digest = hashlib.sha256(body).hexdigest()
    with open_db(_data_dir(request)) as connection:
        connection.execute(
            "INSERT INTO villages (village_id, created_at) VALUES (?, ?) "
            "ON CONFLICT (village_id) DO NOTHING",
            (snapshot.tag, imported_at),
        )
        inserted = connection.execute(
            "INSERT INTO village_snapshots (village_id, sha256, raw_json, format_version, "
            "exported_at, imported_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (village_id, sha256) DO NOTHING",
            (
                snapshot.tag,
                digest,
                text,
                snapshot.format_version,
                snapshot.exported_at,
                imported_at,
            ),
        )
        if inserted.rowcount == 0:
            response.status_code = 200
        row = connection.execute(
            "SELECT snapshot_id FROM village_snapshots WHERE village_id = ? AND sha256 = ?",
            (snapshot.tag, digest),
        ).fetchone()
        return _state(
            snapshot,
            row["snapshot_id"],
            _active_pack(connection),
            _extras(connection, snapshot.tag),
        )


@router.get(
    "/{village_id}/state",
    response_model=VillageState,
    responses={404: {"model": ErrorBody, "description": "Code village_not_found."}},
)
def get_state(request: Request, village_id: str) -> VillageState:
    with open_db(_data_dir(request)) as connection:
        # The most recent export is the state: importing an older one does not move it back.
        row = connection.execute(
            "SELECT snapshot_id, raw_json, imported_at FROM village_snapshots "
            "WHERE village_id = ? ORDER BY exported_at DESC, snapshot_id DESC LIMIT 1",
            (village_id,),
        ).fetchone()
        if row is None:
            raise ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")
        snapshot = parse_export(row["raw_json"], row["imported_at"])
        return _state(
            snapshot,
            row["snapshot_id"],
            _active_pack(connection),
            _extras(connection, village_id),
        )


def _require_village(connection: sqlite3.Connection, village_id: str) -> None:
    found = connection.execute(
        "SELECT 1 FROM villages WHERE village_id = ?", (village_id,)
    ).fetchone()
    if found is None:
        raise ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")


def _latest_snapshot(connection: sqlite3.Connection, village_id: str) -> VillageSnapshot:
    row = connection.execute(
        "SELECT raw_json, imported_at FROM village_snapshots "
        "WHERE village_id = ? ORDER BY exported_at DESC, snapshot_id DESC LIMIT 1",
        (village_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "village_not_found", f"Le village {village_id} est introuvable.")
    return parse_export(row["raw_json"], row["imported_at"])


# ------------------------------------------------------------------------------------- diff


class UpgradedOut(BaseModel):
    data_id: int
    name: str | None
    from_level: int
    to_level: int
    count: int


class PresenceOut(BaseModel):
    data_id: int
    name: str | None
    level: int | None
    count: int


class JobChangeOut(BaseModel):
    data_id: int
    name: str | None
    to_level: int
    finish_at: int


class DiffOut(BaseModel):
    village_id: str
    from_snapshot_id: int
    to_snapshot_id: int
    upgraded: list[UpgradedOut]
    added: list[PresenceOut]
    removed: list[PresenceOut]
    started: list[JobChangeOut]
    finished: list[JobChangeOut]


def _snapshot_by_id(
    connection: sqlite3.Connection, village_id: str, snapshot_id: int
) -> VillageSnapshot:
    row = connection.execute(
        "SELECT raw_json, imported_at FROM village_snapshots "
        "WHERE village_id = ? AND snapshot_id = ?",
        (village_id, snapshot_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "snapshot_not_found", f"L'instantané {snapshot_id} est introuvable.")
    return parse_export(row["raw_json"], row["imported_at"])


@router.get(
    "/{village_id}/diff",
    response_model=DiffOut,
    responses={
        404: {"model": ErrorBody, "description": "Codes village_not_found, snapshot_not_found."},
        409: {"model": ErrorBody, "description": "Code diff_requires_two_snapshots."},
        422: {"model": ErrorBody, "description": "Code invalid_diff_range."},
    },
)
def get_diff(
    request: Request,
    village_id: str,
    from_id: Annotated[int | None, Query(alias="from")] = None,
    to_id: Annotated[int | None, Query(alias="to")] = None,
) -> DiffOut:
    """Compare two imports: the two latest, or the snapshots named by `from` and `to`."""
    if (from_id is None) != (to_id is None):
        raise ApiError(422, "invalid_diff_range", "Les paramètres from et to vont ensemble.")
    with open_db(_data_dir(request)) as connection:
        _require_village(connection, village_id)
        pack = _active_pack(connection)
        latest = connection.execute(
            "SELECT snapshot_id FROM village_snapshots WHERE village_id = ? "
            "ORDER BY exported_at DESC, snapshot_id DESC LIMIT 2",
            (village_id,),
        ).fetchall()
        if len(latest) < 2:
            raise ApiError(
                409,
                "diff_requires_two_snapshots",
                "Il faut au moins deux imports de ce village pour les comparer.",
            )
        if from_id is None or to_id is None:
            newer_id, older_id = latest[0]["snapshot_id"], latest[1]["snapshot_id"]
        else:
            newer_id, older_id = to_id, from_id
        older = _snapshot_by_id(connection, village_id, older_id)
        newer = _snapshot_by_id(connection, village_id, newer_id)
    result = diff_snapshots(older, newer)

    def name_of(data_id: int) -> str | None:
        item = pack.item(data_id) if pack is not None else None
        return item.name if item is not None else None

    return DiffOut(
        village_id=village_id,
        from_snapshot_id=older_id,
        to_snapshot_id=newer_id,
        upgraded=[
            UpgradedOut(
                data_id=u.data_id,
                name=name_of(u.data_id),
                from_level=u.from_level,
                to_level=u.to_level,
                count=u.count,
            )
            for u in result.upgraded
        ],
        added=[
            PresenceOut(data_id=p.data_id, name=name_of(p.data_id), level=p.level, count=p.count)
            for p in result.added
        ],
        removed=[
            PresenceOut(data_id=p.data_id, name=name_of(p.data_id), level=p.level, count=p.count)
            for p in result.removed
        ],
        started=[
            JobChangeOut(
                data_id=j.data_id,
                name=name_of(j.data_id),
                to_level=j.to_level,
                finish_at=j.finish_at,
            )
            for j in result.started
        ],
        finished=[
            JobChangeOut(
                data_id=j.data_id,
                name=name_of(j.data_id),
                to_level=j.to_level,
                finish_at=j.finish_at,
            )
            for j in result.finished
        ],
    )


# ------------------------------------------------------------------------------------- sync


class SyncedLevel(BaseModel):
    data_id: int
    name: str
    section: str
    from_level: int
    level: int
    provenance: Provenance


class ConflictOut(BaseModel):
    name: str
    section: str
    level: int | None
    reason: str


class SyncOut(BaseModel):
    village_id: str
    updated: list[SyncedLevel]
    conflicts: list[ConflictOut]
    fetched_at: int


@router.post(
    "/{village_id}/sync",
    response_model=SyncOut,
    responses={
        404: {"model": ErrorBody, "description": "Codes village_not_found, coc_player_not_found."},
        409: {"model": ErrorBody, "description": "Code api_not_configured."},
        502: {
            "model": ErrorBody,
            "description": "Codes coc_access_denied, coc_unexpected_response.",
        },
        503: {"model": ErrorBody, "description": "Codes coc_rate_limited, coc_unavailable."},
    },
)
def sync_village(request: Request, village_id: str) -> SyncOut:
    """Read troop, hero, spell and equipment levels from the official API.

    Buildings, timers and resources of the export are never touched.
    """
    settings = request.app.state.settings
    transport: httpx.BaseTransport | None = request.app.state.coc_transport
    with open_db(_data_dir(request)) as connection:
        _require_village(connection, village_id)
        if not settings.coc_token:
            raise ApiError(
                409,
                "api_not_configured",
                "La clé de l'API officielle n'est pas configurée (CLASHPILOT_COC_TOKEN).",
            )
        snapshot = _latest_snapshot(connection, village_id)
        pack = _active_pack(connection)
        try:
            player = fetch_player(
                settings.coc_base_url, settings.coc_token, village_id, transport=transport
            )
        except CocApiError as error:
            raise ApiError(error.status, error.code, error.message) from None
        fetched_at: int = request.app.state.clock()
        updated, conflicts = match_levels(player, snapshot, pack)
        connection.execute("DELETE FROM village_api_levels WHERE village_id = ?", (village_id,))
        connection.executemany(
            "INSERT INTO village_api_levels (village_id, data_id, level, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            [(village_id, entry.data_id, entry.level, fetched_at) for entry in updated],
        )
    return SyncOut(
        village_id=village_id,
        updated=[
            SyncedLevel(
                data_id=entry.data_id,
                name=entry.name,
                section=entry.section,
                from_level=entry.from_level,
                level=entry.level,
                provenance=Provenance(
                    value=entry.level, status="confirmed", source="api", as_of=fetched_at
                ),
            )
            for entry in updated
        ],
        conflicts=[
            ConflictOut(name=c.name, section=c.section, level=c.level, reason=c.reason)
            for c in conflicts
        ],
        fetched_at=fetched_at,
    )


# ------------------------------------------------------------------------------------ manual


class ManualResourceOut(BaseModel):
    value: int
    status: str
    as_of: int
    source: str = "manual"


class ManualOut(BaseModel):
    village_id: str
    resources: dict[str, ManualResourceOut]
    builders_total: int | None
    magic_items: dict[str, int]
    gold_pass: dict[str, Any]
    farm_rate: dict[str, dict[str, int]]
    storage_override: dict[str, int]
    events: list[dict[str, Any]]
    source: str = "manual"
    updated_at: int | None


def _manual_out(village_id: str, manual: ManualState, updated_at: int | None) -> ManualOut:
    return ManualOut(
        village_id=village_id,
        resources={
            name: ManualResourceOut(value=r.value, status=r.status, as_of=r.as_of)
            for name, r in manual.resources.items()
        },
        builders_total=manual.builders_total,
        magic_items=manual.magic_items,
        gold_pass=manual.gold_pass,
        farm_rate={name: rate.model_dump() for name, rate in manual.farm_rate.items()},
        storage_override={str(k): v for k, v in manual.storage_override.items()},
        events=[event.model_dump() for event in manual.events],
        updated_at=updated_at,
    )


@router.put(
    "/{village_id}/manual",
    response_model=ManualOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ManualState.model_json_schema()}},
        }
    },
    responses={
        404: {"model": ErrorBody, "description": "Code village_not_found."},
        422: {"model": ErrorBody, "description": "Code invalid_manual_state."},
    },
)
def put_manual(
    request: Request, village_id: str, body: Annotated[dict[str, Any], Body()]
) -> ManualOut:
    """Replace the typed-in state of a village (resources, builders, farm rates, events...)."""
    try:
        manual = ManualState.model_validate(body)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in entry['loc'])}: {entry['msg']}"
            for entry in error.errors()
        ]
        raise ApiError(
            422, "invalid_manual_state", "La saisie manuelle est invalide.", problems
        ) from None
    now: int = request.app.state.clock()
    with open_db(_data_dir(request)) as connection:
        _require_village(connection, village_id)
        connection.execute(
            "INSERT INTO village_manual (village_id, body_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (village_id) DO UPDATE SET "
            "body_json = excluded.body_json, updated_at = excluded.updated_at",
            (village_id, json.dumps(manual.model_dump(mode="json")), now),
        )
    return _manual_out(village_id, manual, now)


@router.get(
    "/{village_id}/manual",
    response_model=ManualOut,
    responses={404: {"model": ErrorBody, "description": "Code village_not_found."}},
)
def get_manual(request: Request, village_id: str) -> ManualOut:
    with open_db(_data_dir(request)) as connection:
        _require_village(connection, village_id)
        extras = _extras(connection, village_id)
    return _manual_out(village_id, extras.manual, extras.manual_at)
