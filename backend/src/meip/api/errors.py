"""Exception handlers producing RFC 9457 problem details.

Problem ``type`` values are stable URNs (``urn:meip:problem:<slug>``), so clients can match
on them without depending on a documentation host. No handler echoes request values, SQL,
constraint details or exception messages to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from meip.core.errors import AppError, VersionConflictError
from meip.core.logging import get_logger
from meip.core.request_context import request_id_var

PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:meip:problem:"
_log = get_logger("meip.errors")


def problem(status: int, error_type: str, title: str, detail: str, **extra: Any) -> JSONResponse:
    body = {
        "type": f"{PROBLEM_TYPE_PREFIX}{error_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "request_id": request_id_var.get(),
        **extra,
    }
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        jsonable_encoder(body), status_code=status, media_type=PROBLEM_JSON, headers=headers
    )


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101
    if exc.status_code >= 500:
        _log.error("app_error", error_type=exc.error_type)
    return problem(exc.status_code, exc.error_type, exc.title, exc.detail, **exc.extra)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Echo locations and messages, never the submitted values (they may be personal data).
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return problem(
        422, "validation-error", "Request validation failed", "Invalid request", errors=errors
    )


async def _stale_data(_: Request, exc: Exception) -> JSONResponse:
    err = VersionConflictError()
    return problem(err.status_code, err.error_type, err.title, err.detail)


# PostgreSQL SQLSTATE codes for integrity violations.
_UNIQUE_VIOLATION = "23505"


def _sqlstate(exc: IntegrityError) -> str | None:
    orig: Any = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(code, str):
            return code
    return None


def _constraint_name(exc: IntegrityError) -> str | None:
    orig: Any = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None


async def _integrity_error(_: Request, exc: Exception) -> JSONResponse:
    """Constraint violations that services did not pre-empt, typically lost races.

    Logged by SQLSTATE and constraint name only: the driver message contains row values.
    """
    assert isinstance(exc, IntegrityError)  # noqa: S101
    state = _sqlstate(exc)
    _log.warning("integrity_violation", sqlstate=state, constraint=_constraint_name(exc))
    if state == _UNIQUE_VIOLATION:
        return problem(
            409, "conflict", "Conflict", "The request conflicts with an existing resource"
        )
    return problem(
        422,
        "constraint-violation",
        "Data constraint violated",
        "The request violates a data integrity rule",
    )


def install_error_handlers(app: FastAPI) -> None:
    """Domain and validation errors. Unexpected exceptions are handled in
    :class:`meip.api.middleware.RequestContextMiddleware` so 500s keep their request ID."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StaleDataError, _stale_data)
    app.add_exception_handler(IntegrityError, _integrity_error)
