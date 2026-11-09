"""Application factory: the JSON API under /api and the built SPA at /."""

from __future__ import annotations

import time
from collections.abc import Callable
from importlib.metadata import version as package_version
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from clashpilot.api.errors import error_detail, register_error_handlers
from clashpilot.api.routers import gamedata, villages
from clashpilot.settings import Settings

Clock = Callable[[], int]


def _default_clock() -> int:
    return int(time.time())


def _not_found() -> HTTPException:
    return HTTPException(404, detail=error_detail("not_found", "Ressource introuvable."))


def _resolve_spa_file(dist: Path, path: str) -> Path | None:
    """Return the file to serve for `path`, or None to refuse (outside `dist`)."""
    root = dist.resolve()
    try:
        candidate = (root / path).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root):
        return None
    if candidate.is_file():
        return candidate
    return root / "index.html"


def _register_spa(app: FastAPI, settings: Settings) -> None:
    """Catch-all route: must be registered after every router."""

    @app.get("/{path:path}", include_in_schema=False)
    def serve_spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise _not_found()
        dist = settings.frontend_dist
        if not dist.is_dir():
            raise _not_found()
        target = _resolve_spa_file(dist, path)
        if target is None or not target.is_file():
            raise _not_found()
        return FileResponse(target)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(title="ClashPilot", version=package_version("clashpilot"))
    app.state.settings = settings
    app.state.clock = clock if clock is not None else _default_clock
    register_error_handlers(app)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": "ClashPilot", "version": app.version}

    app.include_router(gamedata.router)
    app.include_router(villages.router)

    # Future routers are included above this line; the SPA fallback stays last.
    _register_spa(app, settings)
    return app
