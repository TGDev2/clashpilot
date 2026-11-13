"""Game data pack routes: import, list, activate, read item levels and correct them."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from clashpilot.api.errors import ApiError
from clashpilot.gamedata.pack import (
    COST_RESOURCES,
    GamePack,
    PackValidationError,
    canonical_json,
    content_sha256,
    load_pack,
)
from clashpilot.storage.db import open_db
from clashpilot.storage.packs import (
    PackNotFoundError,
    load_effective_pack,
    overrides_of,
    save_override,
    stored_content,
)

router = APIRouter(prefix="/api/gamedata", tags=["gamedata"])

MAX_PACK_BYTES = 5_000_000


def _invalid_request(problem: str) -> ApiError:
    """The same error as a body that fails FastAPI's own validation."""
    return ApiError(422, "invalid_request", "Requête invalide.", [problem])


async def _read_pack(request: Request) -> dict[str, Any]:
    """The JSON object of the body, read no further than the size limit."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_PACK_BYTES:
            raise ApiError(413, "pack_too_large", "Le pack dépasse la taille maximale de 5 Mo.")
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise _invalid_request("body: JSON invalide") from error
    if not isinstance(payload, dict):
        raise _invalid_request("body: Input should be a valid dictionary")
    return payload


class PackSummary(BaseModel):
    pack_id: str
    game_version: str
    as_of: str
    fictional: bool
    items: int
    levels: int
    active: bool
    sha256: str
    created_at: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    errors: list[str] | None = None


class ErrorBody(BaseModel):
    detail: ErrorDetail


class SourceOut(BaseModel):
    id: str
    label: str
    url: str | None
    retrieved_at: str
    license: str | None


class RequirementOut(BaseModel):
    data_id: int
    level: int


class OriginalLevel(BaseModel):
    """The values of the imported pack, shown next to a corrected level."""

    duration_s: int
    cost: dict[str, int]


class LevelOut(BaseModel):
    level: int
    duration_s: int
    cost: dict[str, int]
    requires: list[RequirementOut]
    source: SourceOut
    original: OriginalLevel | None = None
    note: str | None = None


class LevelCorrection(BaseModel):
    """Body of a level correction: at least one of `duration_s` and `cost`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    duration_s: int | None = Field(default=None, ge=0)
    cost: dict[str, Annotated[int, Field(ge=0)]] | None = None
    note: str | None = None


class ItemLevels(BaseModel):
    pack_id: str
    data_id: int
    name: str
    kind: str
    lane: str
    category: str
    tags: list[str]
    max_level: int
    levels: list[LevelOut]


_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorBody, "description": "Pack or item not found."}
}

_SUMMARY_COLUMNS = (
    "pack_id, game_version, as_of, fictional, items, levels, active, sha256, created_at"
)


def _data_dir(request: Request) -> Path:
    data_dir: Path = request.app.state.settings.data_dir
    return data_dir


def _summary(row: sqlite3.Row) -> PackSummary:
    return PackSummary(
        pack_id=row["pack_id"],
        game_version=row["game_version"],
        as_of=row["as_of"],
        fictional=bool(row["fictional"]),
        items=row["items"],
        levels=row["levels"],
        active=bool(row["active"]),
        sha256=row["sha256"],
        created_at=row["created_at"],
    )


def _pack_not_found(pack_id: str) -> ApiError:
    return ApiError(404, "pack_not_found", f"Pack de données introuvable : {pack_id}.")


def _summary_of(connection: sqlite3.Connection, pack_id: str) -> PackSummary:
    row = connection.execute(
        f"SELECT {_SUMMARY_COLUMNS} FROM gamedata_packs WHERE pack_id = ?", (pack_id,)
    ).fetchone()
    if row is None:
        raise _pack_not_found(pack_id)
    return _summary(row)


@router.post(
    "/packs",
    status_code=201,
    response_model=PackSummary,
    responses={
        200: {"model": PackSummary, "description": "Identical pack already stored."},
        409: {"model": ErrorBody, "description": "Same pack_id, different content."},
        413: {"model": ErrorBody, "description": "Body over 5 MB (code pack_too_large)."},
        422: {"model": ErrorBody, "description": "Invalid pack (code invalid_pack)."},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"additionalProperties": True, "type": "object", "title": "Payload"}
                }
            },
        }
    },
)
def import_pack(
    request: Request,
    response: Response,
    payload: Annotated[dict[str, Any], Depends(_read_pack)],
) -> PackSummary:
    try:
        pack = load_pack(payload)
    except PackValidationError as error:
        raise ApiError(
            422, "invalid_pack", "Le pack de données est invalide.", error.errors
        ) from error
    digest = content_sha256(payload)
    with open_db(_data_dir(request)) as connection:
        existing = connection.execute(
            "SELECT sha256 FROM gamedata_packs WHERE pack_id = ?", (pack.pack_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO gamedata_packs (pack_id, game_version, as_of, fictional, items, "
                "levels, sha256, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pack.pack_id,
                    pack.data.game_version,
                    pack.data.as_of,
                    int(pack.data.fictional),
                    len(pack.data.items),
                    len(pack.data.levels),
                    digest,
                    canonical_json(payload),
                    request.app.state.clock(),
                ),
            )
        elif existing["sha256"] != digest:
            raise ApiError(
                409,
                "pack_conflict",
                f"Le pack {pack.pack_id} existe déjà avec un contenu différent.",
            )
        else:
            response.status_code = 200
        return _summary_of(connection, pack.pack_id)


@router.get("/packs", response_model=list[PackSummary])
def list_packs(request: Request) -> list[PackSummary]:
    with open_db(_data_dir(request)) as connection:
        rows = connection.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM gamedata_packs ORDER BY created_at, pack_id"
        ).fetchall()
    return [_summary(row) for row in rows]


@router.put(
    "/packs/{pack_id}/active",
    response_model=PackSummary,
    responses={404: _NOT_FOUND[404]},
)
def activate_pack(request: Request, pack_id: str) -> PackSummary:
    with open_db(_data_dir(request)) as connection:
        _summary_of(connection, pack_id)
        connection.execute("UPDATE gamedata_packs SET active = 0 WHERE active = 1")
        connection.execute("UPDATE gamedata_packs SET active = 1 WHERE pack_id = ?", (pack_id,))
        return _summary_of(connection, pack_id)


def _effective_pack(connection: sqlite3.Connection, pack_id: str) -> GamePack:
    try:
        return load_effective_pack(connection, pack_id)
    except PackNotFoundError as error:
        raise _pack_not_found(pack_id) from error


def _item_not_found(pack_id: str, data_id: int) -> ApiError:
    return ApiError(404, "item_not_found", f"Objet {data_id} absent du pack {pack_id}.")


def _item_levels(connection: sqlite3.Connection, pack_id: str, data_id: int) -> ItemLevels:
    pack = _effective_pack(connection, pack_id)
    item = pack.item(data_id)
    if item is None:
        raise _item_not_found(pack_id, data_id)
    overrides = overrides_of(connection, pack_id)
    imported = load_pack(stored_content(connection, pack_id))
    levels: list[LevelOut] = []
    for row in pack.levels_of(data_id):
        source = pack.source_of(data_id, row.level)
        assert source is not None  # guaranteed by pack validation
        override = overrides.get((data_id, row.level))
        before = imported.level(data_id, row.level) if override is not None else None
        levels.append(
            LevelOut(
                level=row.level,
                duration_s=row.duration_s,
                cost=dict(row.cost),
                requires=[RequirementOut(**r.model_dump()) for r in row.requires],
                source=SourceOut(**source.model_dump()),
                original=(
                    OriginalLevel(duration_s=before.duration_s, cost=dict(before.cost))
                    if before is not None
                    else None
                ),
                note=override.note if override is not None else None,
            )
        )
    return ItemLevels(
        pack_id=pack_id,
        data_id=data_id,
        name=item.name,
        kind=item.kind,
        lane=item.lane,
        category=item.category,
        tags=list(item.tags),
        max_level=pack.max_level(data_id),
        levels=levels,
    )


@router.get(
    "/packs/{pack_id}/items/{data_id}",
    response_model=ItemLevels,
    responses=_NOT_FOUND,
)
def get_item_levels(request: Request, pack_id: str, data_id: int) -> ItemLevels:
    with open_db(_data_dir(request)) as connection:
        return _item_levels(connection, pack_id, data_id)


def _invalid_override(errors: list[str]) -> ApiError:
    return ApiError(422, "invalid_override", "La correction est invalide.", errors)


def _parse_correction(payload: Any) -> LevelCorrection:
    try:
        correction = LevelCorrection.model_validate(payload)
    except ValidationError as error:
        raise _invalid_override(
            [f"{'.'.join(str(p) for p in e['loc']) or 'body'}: {e['msg']}" for e in error.errors()]
        ) from error
    if correction.duration_s is None and correction.cost is None:
        raise _invalid_override(["duration_s or cost is required"])
    unknown = sorted(set(correction.cost or ()) - COST_RESOURCES)
    if unknown:
        raise _invalid_override([f"cost: unknown resource {name!r}" for name in unknown])
    return correction


@router.patch(
    "/packs/{pack_id}/items/{data_id}/levels/{level}",
    response_model=ItemLevels,
    responses={
        404: _NOT_FOUND[404],
        422: {"model": ErrorBody, "description": "Invalid correction (code invalid_override)."},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": LevelCorrection.model_json_schema()}},
        }
    },
)
def correct_level(
    request: Request, pack_id: str, data_id: int, level: int, payload: Annotated[Any, Body()]
) -> ItemLevels:
    correction = _parse_correction(payload)
    with open_db(_data_dir(request)) as connection:
        pack = _effective_pack(connection, pack_id)
        if pack.item(data_id) is None:
            raise _item_not_found(pack_id, data_id)
        if pack.level(data_id, level) is None:
            raise ApiError(404, "level_not_found", f"Niveau {level} absent de l'objet {data_id}.")
        save_override(
            connection,
            pack_id,
            data_id,
            level,
            duration_s=correction.duration_s,
            cost=correction.cost,
            note=correction.note,
            now=request.app.state.clock(),
        )
        try:
            load_effective_pack(connection, pack_id)
        except PackValidationError as error:  # rolled back by open_db
            raise _invalid_override(error.errors) from error
        return _item_levels(connection, pack_id, data_id)
