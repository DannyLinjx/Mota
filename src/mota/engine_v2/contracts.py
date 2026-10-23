"""Native worker contracts shared by Python orchestration and future C++ jobs.

These are interface contracts, not an implementation of geometry or routing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mota.domain.models import DTO, DesignVersion

JOB_STATUSES = (
    "SUCCEEDED", "PARTIAL", "INFEASIBLE", "UNKNOWN", "CANCELLED", "FAILED",
)

JobStatus = Literal[
    "SUCCEEDED", "PARTIAL", "INFEASIBLE", "UNKNOWN", "CANCELLED", "FAILED",
]


class DependencyRead(DTO):
    artifact_id: str
    sha256: str
    purpose: str = ""


class DependencyWrite(DTO):
    artifact_id: str
    kind: str
    sha256: str = ""
    design_version_key: str = ""


class JobBudget(DTO):
    wall_seconds: float = 30.0
    max_expansions: int | None = None
    max_memory_mb: int | None = None


class JobRequest(DTO):
    """A frozen request; the worker must not read live project state."""

    schema_version: int = 2
    job_id: str
    kind: str
    base_version: DesignVersion
    input_artifact_ids: list[str] = Field(default_factory=list)
    constraints_hash: str = ""
    kernel_version: str = ""
    seed: int = 0
    budget: JobBudget = Field(default_factory=JobBudget)
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Frozen request denominator: targets must not be removed on failure.
    requested_targets: list[str] = Field(default_factory=list)


class JobResult(DTO):
    """Result envelope. UNKNOWN and INFEASIBLE are deliberately distinct."""

    schema_version: int = 2
    job_id: str
    base_version: DesignVersion
    status: JobStatus
    patch_artifact_id: str | None = None
    report_artifact_id: str | None = None
    checkpoint_artifact_id: str | None = None
    read_set: list[DependencyRead] = Field(default_factory=list)
    write_set: list[DependencyWrite] = Field(default_factory=list)
    # Frozen denominator: every requested target must stay in the denominator,
    # including targets that failed or were not reached before a budget stop.
    requested_targets: list[str] = Field(default_factory=list)
    completed_targets: list[str] = Field(default_factory=list)
    unimplemented_targets: list[str] = Field(default_factory=list)
    unimplemented_checks: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
