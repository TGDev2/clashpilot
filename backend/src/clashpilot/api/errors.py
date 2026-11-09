"""HTTP error contract: {"detail": {"code": "<snake_case>", "message": "<texte français>"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_DEFAULT_CODES = {
    404: ("not_found", "Ressource introuvable."),
    405: ("method_not_allowed", "Méthode non autorisée."),
}


def error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


async def _http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail: Any = exc.detail
    if not isinstance(detail, dict):
        code, message = _DEFAULT_CODES.get(exc.status_code, ("http_error", "Erreur."))
        detail = error_detail(code, message)
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
