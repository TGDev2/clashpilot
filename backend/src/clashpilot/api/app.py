"""Application factory: the JSON API under /api and the built SPA at /."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clashpilot.api.errors import error_detail, register_error_handlers
from clashpilot.api.routers import (
    calendar,
    forecast,
    gamedata,
    items,
    meta,
    plans,
    scenarios,
    schedule,
    villages,
)
from clashpilot.settings import LOOPBACK_HOSTS, Settings

Clock = Callable[[], int]


def _default_clock() -> int:
    return int(time.time())


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _foreign_origin(request: Request) -> bool:
    """Does a state-changing request come from a page of another site?

    A loopback origin (the Vite dev server) and the origin of the host itself are ours.
    """
    origin = request.headers.get("origin")
    if request.method in _SAFE_METHODS or origin is None:
        return False
    parts = urlsplit(origin)
    if parts.hostname in LOOPBACK_HOSTS:
        return False
    return parts.netloc != request.headers.get("host", "")


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


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    coc_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(title="ClashPilot", version=package_version("clashpilot"))
    app.state.settings = settings
    app.state.clock = clock if clock is not None else _default_clock
    # Test seam: an httpx transport standing in for the network of the official API client.
    app.state.coc_transport = coc_transport
    register_error_handlers(app)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def refuse_cross_site_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if _foreign_origin(request):
            detail = error_detail("forbidden_origin", "Requête d'un autre site refusée.")
            return JSONResponse({"detail": detail}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": "ClashPilot", "version": app.version}

    app.include_router(gamedata.router)
    app.include_router(villages.router)
    app.include_router(schedule.router)
    app.include_router(plans.router)
    app.include_router(calendar.router)
    app.include_router(forecast.router)
    app.include_router(scenarios.router)
    app.include_router(items.router)
    app.include_router(meta.router)

    # Future routers are included above this line; the SPA fallback stays last.
    _register_spa(app, settings)
    return app
