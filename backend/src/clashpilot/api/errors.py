"""HTTP error contract: {"detail": {"code": "<snake_case>", "message": "<texte français>"}}."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_DEFAULT_CODES = {
    404: ("not_found", "Ressource introuvable."),
    405: ("method_not_allowed", "Méthode non autorisée."),
}


def error_detail(code: str, message: str, errors: Sequence[str] | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {"code": code, "message": message}
    if errors is not None:
        detail["errors"] = list(errors)
    return detail


class ApiError(Exception):
    """An error answered as `{"detail": {"code", "message"[, "errors"]}}` (SPEC section 4)."""

    def __init__(
        self, status: int, code: str, message: str, errors: Sequence[str] | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.errors = list(errors) if errors is not None else None


async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    detail = error_detail(exc.code, exc.message, exc.errors)
    return JSONResponse({"detail": detail}, status_code=exc.status)


async def _request_validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    problems = [
        f"{'.'.join(str(part) for part in entry['loc'])}: {entry['msg']}" for entry in exc.errors()
    ]
    detail = error_detail("invalid_request", "Requête invalide.", problems)
    return JSONResponse({"detail": detail}, status_code=422)


async def _http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail: Any = exc.detail
    if not isinstance(detail, dict):
        code, message = _DEFAULT_CODES.get(exc.status_code, ("http_error", "Erreur."))
        detail = error_detail(code, message)
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ApiError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _request_validation_handler)  # type: ignore[arg-type]
