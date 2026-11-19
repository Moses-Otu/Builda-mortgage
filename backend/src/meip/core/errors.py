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


class BadRequestError(AppError):
    status_code = 400
    error_type = "bad-request"
    title = "Malformed request"


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


class VersionConflictError(AppError):
    """The client's If-Match version is no longer current (RFC 9110 412)."""

    status_code = 412
    error_type = "precondition-failed"
    title = "Resource was modified by another request"


class PreconditionRequiredError(AppError):
    """A mutation was attempted without If-Match (RFC 6585 428)."""

    status_code = 428
    error_type = "precondition-required"
    title = "If-Match header required"


class InvalidStateTransitionError(ConflictError):
    error_type = "invalid-state-transition"
    title = "State transition not allowed"


class BusinessRuleError(AppError):
    status_code = 422
    error_type = "business-rule-violation"
    title = "Business rule violated"


class PayloadTooLargeError(AppError):
    status_code = 413
    error_type = "payload-too-large"
    title = "Request body too large"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    error_type = "unsupported-media-type"
    title = "Unsupported media type"


class FeatureDisabledError(AppError):
    """The feature is switched off in this deployment. Reported as 404: nothing is there."""

    status_code = 404
    error_type = "feature-disabled"
    title = "Not available"


class IntegrityFailureError(AppError):
    """Stored data failed an integrity check (e.g. a hash mismatch). Never retried silently."""

    status_code = 500
    error_type = "integrity-error"
    title = "Stored data failed an integrity check"
