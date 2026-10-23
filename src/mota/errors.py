"""Typed application errors carrying machine-readable codes."""

from __future__ import annotations

from typing import Any


class MotaError(Exception):
    code = "MOTA_ERROR"
    http_like_status = 400

    def __init__(self, message: str, *, code: str | None = None,
                 data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.data = data or {}

    def to_error(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        payload.update(self.data)
        return payload


class AuthRequired(MotaError):
    code = "AUTH_REQUIRED"


class AccessDenied(MotaError):
    code = "ACCESS_DENIED"


class NotFound(MotaError):
    code = "NOT_FOUND"


class InvalidRequest(MotaError):
    code = "INVALID_REQUEST"


class Conflict(MotaError):
    code = "CONFLICT"


class RevisionConflict(MotaError):
    code = "REVISION_CONFLICT"


class DraftConflict(MotaError):
    code = "DRAFT_CONFLICT"


class ProjectBusy(MotaError):
    code = "PROJECT_BUSY"


class IdempotencyConflict(MotaError):
    code = "IDEMPOTENCY_CONFLICT"


class ConfigConflict(MotaError):
    code = "CONFIG_CONFLICT"


class ModelUnavailable(MotaError):
    code = "MODEL_UNAVAILABLE"


class ModelAuthFailed(MotaError):
    code = "MODEL_AUTH_FAILED"


class ModelResponseInvalid(MotaError):
    code = "MODEL_RESPONSE_INVALID"


class RouteIncomplete(MotaError):
    code = "ROUTE_INCOMPLETE"


class ConstraintConflict(MotaError):
    code = "CONSTRAINT_CONFLICT"


class PartNotFound(MotaError):
    code = "PART_NOT_FOUND"


class AssetUnsupported(MotaError):
    code = "ASSET_UNSUPPORTED"


class PinmapMismatch(MotaError):
    code = "PINMAP_MISMATCH"


class BundleInvalid(MotaError):
    code = "BUNDLE_INVALID"


class BundleIncomplete(MotaError):
    code = "BUNDLE_INCOMPLETE"


class ArtifactInvalid(MotaError):
    code = "ARTIFACT_INVALID"


class ArtifactAccessDenied(MotaError):
    code = "ARTIFACT_ACCESS_DENIED"


class WorkspaceLocked(MotaError):
    code = "WORKSPACE_LOCKED"


class SaveFailed(MotaError):
    code = "SAVE_FAILED"


class RenderUnavailable(MotaError):
    code = "RENDER_UNAVAILABLE"
