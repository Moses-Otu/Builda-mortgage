"""Application errors, rendered as RFC 9457 ``application/problem+json`` responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    error_type: str = "internal-error"
    title: str = "Internal server error"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra or {}


class UnauthorizedError(AppError):
    status_code = 401
    error_type = "unauthorized"
    title = "Authentication required"


class ForbiddenError(AppError):
    status_code = 403
    error_type = "forbidden"
    title = "Not permitted"


class NotFoundError(AppError):
    status_code = 404
    error_type = "not-found"
    title = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    error_type = "conflict"
    title = "Conflict"


class VersionConflictError(ConflictError):
    error_type = "version-conflict"
    title = "Resource was modified by another request"


class InvalidStateTransitionError(ConflictError):
    error_type = "invalid-state-transition"
    title = "State transition not allowed"


class BusinessRuleError(AppError):
    status_code = 422
    error_type = "business-rule-violation"
    title = "Business rule violated"
