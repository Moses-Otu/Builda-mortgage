"""Structured logging.

Policy: logs carry identifiers (request, tenant, user, case IDs) and never personal data
(names, dates of birth, emails, financial values). Context is bound via contextvars so that
every log line within a request carries the same correlation fields.

Exceptions are logged as type plus stack frames only. Exception *messages* are dropped,
because database and validation errors routinely embed row values (for example
``Key (email)=(...) already exists``).
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any

import structlog


def _exception_from(exc_info: Any) -> BaseException | None:
    if isinstance(exc_info, BaseException):
        return exc_info
    if exc_info is True:
        return sys.exc_info()[1]
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        value = exc_info[1]
        return value if isinstance(value, BaseException) else None
    return None


def safe_traceback(exc: BaseException) -> list[str]:
    """Exception chain as ``Type`` headers and ``file:line in function`` frames, no messages."""
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        lines.append(f"{type(current).__module__}.{type(current).__qualname__}")
        tb: TracebackType | None = current.__traceback__
        lines.extend(
            f"  {frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(tb)
        )
        current = current.__cause__ or current.__context__
    return lines


def redact_exceptions(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        exc = _exception_from(exc_info)
        if exc is not None:
            event_dict["exception"] = safe_traceback(exc)
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_exceptions,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        # No fixed file: resolve sys.stdout per logger so redirection (and tests) work.
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    # Route stdlib loggers (uvicorn, sqlalchemy) at a sane level; uvicorn access log is
    # replaced by our own request log middleware.
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(message)s")
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
