"""Complex PCB Engine v2 contracts and v1 migration.

C0 keeps the existing engine authoritative while publishing a versioned v2
envelope. Nothing in this package routes, places or validates yet; those stages
(C5-C11) consume these contracts.
"""

from mota.engine_v2.contracts import JobRequest, JobResult, JOB_STATUSES
from mota.engine_v2.migration import (
    MigrationError,
    migrate_revision,
    migrate_snapshot,
    read_migration,
    rollback_revision,
    semantic_hash,
    semantic_signature,
)
from mota.engine_v2.schema import (
    BoardV2,
    CircuitV2,
    ConstraintIR,
    EngineDocumentV2,
    HierarchyIR,
    SchematicV2,
    Stackup,
    StackupLayer,
)

__all__ = [
    "BoardV2", "CircuitV2", "ConstraintIR", "EngineDocumentV2", "HierarchyIR",
    "JOB_STATUSES", "JobRequest", "JobResult", "MigrationError", "SchematicV2",
    "Stackup", "StackupLayer", "migrate_revision", "migrate_snapshot",
    "read_migration", "rollback_revision", "semantic_hash", "semantic_signature",
]
