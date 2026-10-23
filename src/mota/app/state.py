"""Application state helpers shared by CLI, engine and desktop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mota.domain.models import (
    ACTIVE_RUN_STATES,
    TERMINAL_RUN_STATES,
    DesignVersion,
    ValidationReport,
)

__all__ = [
    "ACTIVE_RUN_STATES", "TERMINAL_RUN_STATES", "DesignVersion",
    "ValidationReport", "UserScope", "SessionContext", "RunProgress",
]

RUN_STATE_LABELS: dict[str, str] = {
    "CREATED": "Starting build",
    "EXTRACTING": "Reading requirements",
    "PLANNING": "Preparing a build plan",
    "WAITING_FOR_USER": "Waiting for your input",
    "RESOLVING": "Resolving parts and rules",
    "SYNTHESIZING": "Building the schematic",
    "VALIDATING_SCHEMATIC": "Checking connections",
    "PLACING": "Placing components",
    "ROUTING": "Routing copper",
    "VALIDATING_BOARD": "Checking the board",
    "RENDERING": "Preparing views",
    "COMMITTING": "Saving revision",
    "SUCCEEDED": "Build saved",
    "PARTIAL": "Build has unresolved items",
    "FAILED": "Build stopped",
    "CANCELLED": "Build cancelled",
    "RECOVERABLE": "Interrupted build found",
}


@dataclass
class UserScope:
    user_id: str
    username: str

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.username}<{self.user_id}>"


@dataclass
class SessionContext:
    session_id: str
    user_id: str
    username: str
    epoch: int = 0
    secret_storage: str = "none"

    def scope(self) -> UserScope:
        return UserScope(user_id=self.user_id, username=self.username)


@dataclass
class RunProgress:
    run_id: str
    state: str = "CREATED"
    seq: int = 0
    draft_id: str | None = None
    draft_seq: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
