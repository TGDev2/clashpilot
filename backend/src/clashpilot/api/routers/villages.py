"""Village routes: import an in-game export and read the resulting state."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from clashpilot.api.errors import ApiError
from clashpilot.api.routers.gamedata import ErrorBody
from clashpilot.gamedata.pack import GamePack
from clashpilot.ingest.export import ImportFormatError, parse_export
from clashpilot.ingest.state import VillageSnapshot
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


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _active_pack(connection: sqlite3.Connection) -> GamePack | None:
    row = connection.execute("SELECT pack_id FROM gamedata_packs WHERE active = 1").fetchone()
    return load_effective_pack(connection, row["pack_id"]) if row is not None else None


def _state(snapshot: VillageSnapshot, snapshot_id: int, pack: GamePack | None) -> VillageState:
    def name_of(data_id: int) -> str | None:
        item = pack.item(data_id) if pack is not None else None
        return item.name if item is not None else None

    def provenance(value: int | None) -> Provenance:
        return Provenance(
            value=value, status="confirmed", source="export", as_of=snapshot.exported_at
        )

    return VillageState(
        village_id=snapshot.tag,
        snapshot_id=snapshot_id,
        tag=snapshot.tag,
        exported_at=snapshot.exported_at,
        format_version=snapshot.format_version,
        items=[
            VillageItemOut(
                data_id=item.data_id,
                name=name_of(item.data_id),
                level=item.level,
                count=item.count,
                section=item.section,
                village=item.village,
                raw=item.raw,
                provenance=provenance(item.level),
            )
            for item in snapshot.items
        ],
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
        return _state(snapshot, row["snapshot_id"], _active_pack(connection))


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
        return _state(snapshot, row["snapshot_id"], _active_pack(connection))
