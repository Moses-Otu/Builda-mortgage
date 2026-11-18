"""ASGI middleware: request correlation ID and structured request logging."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from meip.api.errors import problem
from meip.core.logging import get_logger
from meip.core.request_context import request_id_var

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_log = get_logger("meip.request")


class RequestContextMiddleware:
    """Assigns a request ID (honouring a well-formed inbound ``X-Request-ID``), binds it to
    logging context, echoes it in the response, and logs one line per request.

    Query strings are not logged, as they could carry personal data.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = inbound if _VALID_REQUEST_ID.match(inbound) else str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            # Handled here rather than by a FastAPI handler so that the 500 still carries the
            # request ID (FastAPI's catch-all runs outside this middleware).
            _log.exception("unhandled_error", exc_type=type(exc).__name__)
            if response_started:
                raise
            response = problem(
                500, "internal-error", "Internal server error", "An unexpected error occurred"
            )
            await response(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            _log.info(
                "request",
                method=scope["method"],
                # The route *template* only. An unmatched raw path is client-controlled and
                # could carry anything, including personal data.
                route=getattr(route, "path", None) or "<unmatched>",
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            request_id_var.reset(token)
