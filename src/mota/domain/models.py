"""Canonical domain DTOs shared by CLI, engine and desktop.

This module is the single definition of project geometry, circuit semantics,
checks and design versions. UI code consumes these shapes through the protocol
and never re-defines them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

RunState = Literal[
    "CREATED", "EXTRACTING", "PLANNING", "WAITING_FOR_USER", "RESOLVING",
    "SYNTHESIZING", "VALIDATING_SCHEMATIC", "PLACING", "ROUTING",
    "VALIDATING_BOARD", "RENDERING", "COMMITTING", "SUCCEEDED", "PARTIAL",
    "FAILED", "CANCELLED", "RECOVERABLE",
]
ReviewStatus = Literal["DRAFT", "NEEDS_INPUT", "READY_FOR_REVIEW", "REVIEW_BLOCKED"]
CheckStatus = Literal["PASS", "FAIL", "WARN", "NOT_CHECKED"]
TaskState = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
Side = Literal["top", "bottom"]
Layer = Literal["F.Cu", "B.Cu"]

TERMINAL_RUN_STATES = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"}
ACTIVE_RUN_STATES = {
    "CREATED", "EXTRACTING", "PLANNING", "WAITING_FOR_USER", "RESOLVING",
    "SYNTHESIZING", "VALIDATING_SCHEMATIC", "PLACING", "ROUTING",
    "VALIDATING_BOARD", "RENDERING", "COMMITTING",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)


# --------------------------------------------------------------------------- #
# Design version
# --------------------------------------------------------------------------- #

class DesignVersion(DTO):
    """Exactly one of a saved revision or a draft identity."""

    revision_id: str | None = None
    draft_id: str | None = None
    draft_seq: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "DesignVersion":
        has_rev = self.revision_id is not None
        has_draft = self.draft_id is not None or self.draft_seq is not None
        if has_rev == has_draft:
            raise ValueError("design_version requires either revision_id or draft_id+draft_seq")
        if has_draft and (self.draft_id is None or self.draft_seq is None):
            raise ValueError("draft design_version requires both draft_id and draft_seq")
        if self.draft_seq is not None and self.draft_seq < 0:
            raise ValueError("draft_seq must be >= 0")
        return self

    @classmethod
    def of_revision(cls, revision_id: str) -> "DesignVersion":
        return cls(revision_id=revision_id)

    @classmethod
    def of_draft(cls, draft_id: str, draft_seq: int) -> "DesignVersion":
        return cls(draft_id=draft_id, draft_seq=draft_seq)

    @property
    def key(self) -> str:
        if self.revision_id:
            return f"rev:{self.revision_id}"
        return f"draft:{self.draft_id}:{self.draft_seq}"

    def to_param(self) -> dict[str, Any]:
        if self.revision_id:
            return {"revision_id": self.revision_id}
        return {"draft_id": self.draft_id, "draft_seq": self.draft_seq}


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #

Origin = Literal["user", "template", "memory", "inferred"]


class RequirementValue(DTO):
    value: Any = None
    unit: str | None = None
    origin: Origin = "inferred"
    source_ids: list[str] = Field(default_factory=list)


class RequirementSpec(DTO):
    schema_version: int = SCHEMA_VERSION
    purpose: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    supply: str | None = None
    load: str | None = None
    interfaces: list[str] = Field(default_factory=list)
    board_size: dict[str, float] | None = None
    connector_preferences: list[str] = Field(default_factory=list)
    assembly_preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, RequirementValue] = Field(default_factory=dict)
    template_id: str | None = None
    template_version: int | None = None
    input_kind: Literal["template", "prompt", "requirements"] = "template"
    input_text: str | None = None


# --------------------------------------------------------------------------- #
# Catalog: parts, footprints, symbols
# --------------------------------------------------------------------------- #

PadShape = Literal["rect", "circle", "oval"]


class PadDef(DTO):
    number: str
    x_nm: int
    y_nm: int
    w_nm: int
    h_nm: int
    shape: PadShape = "rect"
    drill_nm: int = 0            # >0 means through-hole drill
    pad_type: Literal["smd", "tht", "npth"] = "tht"
    layers: list[str] = Field(default_factory=lambda: ["F.Cu", "B.Cu"])


class Rect(DTO):
    x_nm: int
    y_nm: int
    w_nm: int
    h_nm: int

    @property
    def x2(self) -> int:
        return self.x_nm + self.w_nm

    @property
    def y2(self) -> int:
        return self.y_nm + self.h_nm

    def contains(self, x: int, y: int) -> bool:
        return self.x_nm <= x <= self.x2 and self.y_nm <= y <= self.y2

    def overlaps(self, other: "Rect") -> bool:
        return not (self.x2 <= other.x_nm or other.x2 <= self.x_nm
                    or self.y2 <= other.y_nm or other.y2 <= self.y_nm)


class Model3D(DTO):
    model_id: str
    kind: Literal["box", "cylinder", "none"] = "box"
    height_mm: float = 1.0
    fidelity: Literal["exact_asset", "simplified", "missing"] = "simplified"
    source_id: str | None = None


class FootprintDef(DTO):
    footprint_id: str
    name: str
    description: str = ""
    pads: list[PadDef] = Field(default_factory=list)
    courtyard: Rect
    courtyard_approximate: bool = False
    body_height_mm: float | None = None
    model: Model3D | None = None
    mounting: Literal["through_hole", "smd"] = "through_hole"
    source_ids: list[str] = Field(default_factory=list)


class PinDef(DTO):
    number: str
    name: str
    electrical_type: Literal[
        "power_in", "power_out", "input", "output", "bidirectional",
        "passive", "no_connect", "open_collector",
    ] = "passive"
    pad_numbers: list[str] = Field(default_factory=list)


class Capabilities(DTO):
    catalog: bool = True
    placeable: bool = True
    mapped: bool = True
    auto_build_ready: bool = False
    model_3d_exact: bool = False
    schematic_render: bool = True
    footprint_render: bool = True


class PartLimit(DTO):
    key: str
    value: str
    source_id: str | None = None


class PartEvidence(DTO):
    evidence_id: str
    kind: str
    status: Literal["verified", "candidate", "not_checked"] = "not_checked"
    source_id: str | None = None
    values: dict[str, float | str] = Field(default_factory=dict)
    note: str = ""


class PartDef(DTO):
    part_id: str
    mpn: str
    manufacturer: str = ""
    description: str
    category: str
    package: str
    footprint_id: str
    symbol_id: str
    pins: list[PinDef]
    pin_pad_map: dict[str, list[str]]
    capabilities: Capabilities = Field(default_factory=Capabilities)
    limits: list[PartLimit] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence: list[PartEvidence] = Field(default_factory=list)
    value_label: str | None = None
    price: None = None
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("pin_pad_map")
    @classmethod
    def _map_keys_str(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {str(k): [str(p) for p in v] for k, v in value.items()}


class SourceDef(DTO):
    source_id: str
    title: str
    kind: Literal["datasheet", "appnote", "engineering_spec", "library", "user"]
    url: str | None = None
    local_path: str | None = None
    note: str = ""


class SymbolPinDef(DTO):
    number: str
    name: str
    x_nm: int
    y_nm: int
    direction: Literal["left", "right", "up", "down"]
    length_nm: int
    electrical_type: str = "passive"


class SymbolDef(DTO):
    symbol_id: str
    body_w_nm: int
    body_h_nm: int
    pins: list[SymbolPinDef]
    reference_prefix: str = "U"


# --------------------------------------------------------------------------- #
# Circuit
# --------------------------------------------------------------------------- #

class Placement(DTO):
    x_nm: int
    y_nm: int
    rotation_deg: int = 0
    side: Side = "top"

    @field_validator("rotation_deg")
    @classmethod
    def _rot(cls, value: int) -> int:
        if value % 90 != 0:
            raise ValueError("rotation must be a multiple of 90 degrees")
        return value % 360


class Component(DTO):
    id: str
    reference: str
    part_id: str
    symbol_id: str
    footprint_id: str
    pin_pad_map: dict[str, list[str]]
    value: str = ""
    placement: Placement | None = None
    placement_origin: Literal["template", "user", "unplaced"] = "template"
    locked: bool = False
    module_id: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    # v2 instance fields; defaults keep every v1 revision loadable.
    instance_path: str = ""
    on_board: bool = True
    in_bom: bool = True
    assembly_state: Literal["assembled", "dnp", "not_fitted"] = "assembled"
    footprint_instance_id: str | None = None
    parameter_bindings: dict[str, str] = Field(default_factory=dict)


class Net(DTO):
    id: str
    name: str
    pins: list[str] = Field(default_factory=list)   # "component_id:pin_number"
    net_class: Literal["power", "ground", "signal"] = "signal"
    voltage_v: float | None = None


class CircuitIR(DTO):
    schema_version: int = SCHEMA_VERSION
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    no_connects: list[str] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)
    rule_profile_id: str = "mota_demo_2layer_v1"
    library_snapshot_id: str = "mota_builtin_v1"

    def component(self, component_id: str) -> Component:
        for comp in self.components:
            if comp.id == component_id:
                return comp
        raise KeyError(component_id)

    def by_reference(self, reference: str) -> Component:
        for comp in self.components:
            if comp.reference == reference:
                return comp
        raise KeyError(reference)

    def net(self, net_id: str) -> Net:
        for net in self.nets:
            if net.id == net_id:
                return net
        raise KeyError(net_id)

    def component_count(self) -> int:
        return len(self.components)


# --------------------------------------------------------------------------- #
# Schematic
# --------------------------------------------------------------------------- #

class SymbolInstance(DTO):
    component_id: str
    symbol_id: str
    reference: str
    value: str
    x_nm: int
    y_nm: int
    rotation_deg: int = 0
    body_w_nm: int
    body_h_nm: int
    pin_anchors: dict[str, list[int]] = Field(default_factory=dict)  # pin -> [x,y]


class SchematicWire(DTO):
    id: str
    net_id: str
    points: list[list[int]]      # [[x,y], ...] nanometres
    width_nm: int = 152_400      # 0.1524 mm (6 mil)


class SchematicJunction(DTO):
    x_nm: int
    y_nm: int
    net_id: str


class NetLabel(DTO):
    net_id: str
    text: str
    x_nm: int
    y_nm: int
    rotation_deg: int = 0
    justify: Literal["left", "right"] = "left"


class SchematicIR(DTO):
    schema_version: int = SCHEMA_VERSION
    page_w_nm: int
    page_h_nm: int
    snap_nm: int = 1_270_000
    symbols: list[SymbolInstance] = Field(default_factory=list)
    wires: list[SchematicWire] = Field(default_factory=list)
    junctions: list[SchematicJunction] = Field(default_factory=list)
    labels: list[NetLabel] = Field(default_factory=list)
    no_connect_pins: list[str] = Field(default_factory=list)
    no_connects: list[list[int]] = Field(default_factory=list)
    title: str = "Mota schematic"

    def symbol(self, component_id: str) -> SymbolInstance:
        for sym in self.symbols:
            if sym.component_id == component_id:
                return sym
        raise KeyError(component_id)


# --------------------------------------------------------------------------- #
# Board
# --------------------------------------------------------------------------- #

class BoardPad(DTO):
    id: str
    component_id: str
    number: str
    net_id: str | None
    x_nm: int
    y_nm: int
    w_nm: int
    h_nm: int
    shape: PadShape
    drill_nm: int = 0
    pad_type: Literal["smd", "tht", "npth"] = "tht"
    layers: list[str] = Field(default_factory=lambda: ["F.Cu", "B.Cu"])

    @property
    def rect(self) -> Rect:
        return Rect(x_nm=self.x_nm - self.w_nm // 2, y_nm=self.y_nm - self.h_nm // 2,
                    w_nm=self.w_nm, h_nm=self.h_nm)


class FootprintInstance(DTO):
    component_id: str
    footprint_id: str
    reference: str
    x_nm: int
    y_nm: int
    rotation_deg: int = 0
    side: Side = "top"
    courtyard: Rect
    courtyard_approximate: bool = False
    body_height_mm: float | None = None
    model_fidelity: Literal["exact_asset", "simplified", "missing"] = "simplified"


class Track(DTO):
    id: str
    net_id: str
    layer: Layer
    width_nm: int
    points: list[list[int]] = Field(default_factory=list)

    def segments(self) -> list[tuple[int, int, int, int]]:
        return [(self.points[i][0], self.points[i][1],
                 self.points[i + 1][0], self.points[i + 1][1])
                for i in range(len(self.points) - 1)]


class Via(DTO):
    id: str
    net_id: str
    x_nm: int
    y_nm: int
    outer_nm: int = 800_000
    drill_nm: int = 400_000


class Hole(DTO):
    id: str
    x_nm: int
    y_nm: int
    drill_nm: int
    plated: bool = False
    purpose: Literal["mounting", "tooling", "other"] = "mounting"


class RatsnestLine(DTO):
    net_id: str
    x1_nm: int
    y1_nm: int
    x2_nm: int
    y2_nm: int


class BoardIR(DTO):
    schema_version: int = SCHEMA_VERSION
    outline: list[list[int]] = Field(default_factory=list)   # closed polygon
    width_nm: int = 0
    height_nm: int = 0
    thickness_nm: int = 1_600_000
    layer_count: int = 2
    footprints: list[FootprintInstance] = Field(default_factory=list)
    pads: list[BoardPad] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    vias: list[Via] = Field(default_factory=list)
    holes: list[Hole] = Field(default_factory=list)
    ratsnest: list[RatsnestLine] = Field(default_factory=list)
    unrouted_nets: list[str] = Field(default_factory=list)
    placement_policy: str = "module_rules_v1"
    route_policy: str = "grid_astar_2layer_v1"

    def pad(self, pad_id: str) -> BoardPad | None:
        for pad in self.pads:
            if pad.id == pad_id:
                return pad
        return None

    def footprint(self, component_id: str) -> FootprintInstance:
        for fp in self.footprints:
            if fp.component_id == component_id:
                return fp
        raise KeyError(component_id)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

class CheckResult(DTO):
    check_id: str
    rule_id: str
    title: str
    status: CheckStatus
    severity: Literal["info", "warning", "error"] = "error"
    required_for_review: bool = True
    object_ids: list[str] = Field(default_factory=list)
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    coverage: str = ""
    source_id: str | None = None
    design_version_key: str = ""
    outdated: bool = False


class ValidationReport(DTO):
    schema_version: int = SCHEMA_VERSION
    project_id: str
    design_version: DesignVersion
    design_version_key: str
    profile_id: str = "mota_demo_2layer_v1"
    created_at: str = Field(default_factory=utcnow)
    checks: list[CheckResult] = Field(default_factory=list)
    review_status: ReviewStatus = "DRAFT"

    @property
    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "FAIL": 0, "WARN": 0, "NOT_CHECKED": 0}
        for check in self.checks:
            out[check.status] = out.get(check.status, 0) + 1
        return out

    def by_status(self, status: CheckStatus) -> list[CheckResult]:
        return [c for c in self.checks if c.status == status]


class BuildManifest(DTO):
    schema_version: int = SCHEMA_VERSION
    mode: Literal["template", "agent"] = "template"
    template_id: str | None = None
    template_version: int | None = None
    model: str | None = None
    recipe_version: int = 1
    library_snapshot_id: str = "mota_builtin_v1"
    rule_profile_id: str = "mota_demo_2layer_v1"
    created_at: str = Field(default_factory=utcnow)
    input_hash: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class CommitMeta(DTO):
    commit_id: str
    run_id: str | None = None
    idempotency_key: str | None = None
    parent_revision_id: str | None = None
    draft_id: str | None = None
    draft_seq: int | None = None
    source: Literal["run", "edit", "import", "restore", "initial"] = "run"
    restored_from_revision_id: str | None = None


class ArtifactRef(DTO):
    artifact_id: str
    kind: str
    relative_path: str
    sha256: str
    bytes: int
    design_version_key: str
    mime_type: str = "application/json"
    created_at: str = Field(default_factory=utcnow)


class ProjectRevisionMeta(DTO):
    schema_version: int = SCHEMA_VERSION
    project_id: str
    revision_id: str
    parent_revision_id: str | None = None
    created_at: str = Field(default_factory=utcnow)
    review_status: ReviewStatus = "DRAFT"
    summary: str = ""
    template_id: str | None = None
    requirement: RequirementSpec = Field(default_factory=RequirementSpec)
    build_manifest: BuildManifest = Field(default_factory=BuildManifest)
    commit_meta: CommitMeta
    component_count: int = 0
    net_count: int = 0
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    checks_summary: dict[str, int] = Field(default_factory=dict)
    design_hash: str = ""
    read_only: bool = False
    restored_from_revision_id: str | None = None


# --------------------------------------------------------------------------- #
# Scene DTO
# --------------------------------------------------------------------------- #

SceneKind = Literal["schematic", "pcb2d", "pcb3d"]


class SceneObject(DTO):
    id: str
    kind: Literal["polyline", "polygon", "rect", "circle", "text", "mesh", "line", "pad", "via"]
    layer: str = ""
    points: list[list[float]] = Field(default_factory=list)
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    r: float | None = None
    text: str | None = None
    font_size: float | None = None
    color: str | None = None
    fill: str | None = None
    width: float | None = None
    dashed: bool = False
    component_id: str | None = None
    net_id: str | None = None
    ref: str | None = None
    height_mm: float | None = None
    fidelity: str | None = None
    vertices: list[list[float]] = Field(default_factory=list)
    faces: list[list[int]] = Field(default_factory=list)
    world_matrix: list[float] = Field(default_factory=list)
    selectable: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class SceneSnapshot(DTO):
    schema_version: int = SCHEMA_VERSION
    project_id: str
    design_version: DesignVersion
    design_version_key: str
    scene_kind: SceneKind
    units: Literal["mm"] = "mm"
    objects: list[SceneObject] = Field(default_factory=list)
    bounds: dict[str, float] = Field(default_factory=dict)
    artifact_id: str | None = None
    generated_at: str = Field(default_factory=utcnow)
    coordinate_system: dict[str, Any] = Field(default_factory=dict)
    missing_models: list[dict[str, str]] = Field(default_factory=list)
    read_only: bool = False


# --------------------------------------------------------------------------- #
# Project cards / history
# --------------------------------------------------------------------------- #

class ProjectCard(DTO):
    project_id: str
    name: str
    updated_at: str
    current_revision_id: str | None
    template_id: str | None = None
    review_status: ReviewStatus = "DRAFT"
    latest_run_state: RunState | None = None
    component_count: int = 0
    thumbnail_artifact_id: str | None = None
    archived: bool = False
    source_kind: str = "template"
    checks_summary: dict[str, int] = Field(default_factory=dict)


class RevisionSummary(DTO):
    revision_id: str
    created_at: str
    review_status: ReviewStatus
    summary: str
    parent_revision_id: str | None = None
    is_current: bool = False
    run_id: str | None = None
    component_count: int = 0
    checks_summary: dict[str, int] = Field(default_factory=dict)
    restored_from_revision_id: str | None = None


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #

class Question(DTO):
    question_id: str
    run_id: str
    prompt: str
    fields: list[dict[str, Any]] = Field(default_factory=list)
    choices: list[dict[str, str]] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)
    answer: dict[str, Any] | None = None
    consumed_at: str | None = None
    created_at: str = Field(default_factory=utcnow)


class RunRecord(DTO):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    project_id: str
    owner_user_id: str
    parent_run_id: str | None = None
    base_revision_id: str | None = None
    draft_id: str | None = None
    draft_seq: int = 0
    state: RunState = "CREATED"
    mode: Literal["template", "agent"] = "template"
    input_kind: str = "template"
    input_text: str | None = None
    template_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    provider_binding: ProviderBinding | None = None
    model_turns: list[dict[str, Any]] = Field(default_factory=list)
    checkpoint_available: bool = False
    committed_revision_id: str | None = None
    last_event_seq: int = 0
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    error: dict[str, Any] | None = None
    review_status: ReviewStatus | None = None
    question_id: str | None = None
    idempotency_key: str | None = None
    request_hash: str = ""
    cancel_requested: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)


class RunEvent(DTO):
    protocol_version: int = 1
    session_epoch: int = 0
    event_id: str
    seq: int
    timestamp: str = Field(default_factory=utcnow)
    project_id: str
    run_id: str
    base_revision_id: str | None = None
    draft_id: str | None = None
    draft_seq: int = 0
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


EVENT_TYPES = [
    "run.state_changed", "assistant.text_delta", "assistant.summary",
    "tool.started", "tool.completed", "tool.failed", "user_input.required",
    "design.patch_applied", "scene.updated", "validation.completed",
    "artifact.created", "project.revision_committed", "run.completed",
    "model.turn",
]


class TaskRecord(DTO):
    schema_version: int = SCHEMA_VERSION
    task_id: str
    owner_user_id: str
    task_kind: Literal["catalog_sync", "model_probe"]
    state: TaskState = "QUEUED"
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    last_seq: int = 0
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class TaskEvent(DTO):
    protocol_version: int = 1
    session_epoch: int = 0
    task_id: str
    task_kind: str
    seq: int
    timestamp: str = Field(default_factory=utcnow)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #

class MemoryEntry(DTO):
    entry_id: str
    layer: Literal["short_term", "long_term", "experience"]
    owner_user_id: str
    scope: Literal["project", "user"]
    project_id: str | None = None
    status: str = "active"
    summary: str = ""
    body: str = ""
    source_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""
    relative_path: str = ""
    hash: str = ""
    semantic_key: str | None = None
    module_id: str | None = None
    module_version: int | None = None
    library_snapshot_id: str | None = None
    evidence_run_id: str | None = None
    evidence_revision_id: str | None = None
    verified_evidence: bool = False
    tombstone: bool = False


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

ProviderKind = Literal["openai", "deepseek", "custom_openai_compatible"]
ApiFormat = Literal["responses", "chat_completions"]
ThinkingMode = Literal["enabled", "auto", "disabled"]
ThinkingEffort = Literal["low", "high", "max"]
ProbeSuite = Literal["text", "tool_roundtrip", "vision"]


class ProviderProfile(DTO):
    profile_id: str
    name: str
    provider: ProviderKind = "openai"
    api_format: ApiFormat = "responses"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5.4"
    stream: bool = True
    thinking_mode: ThinkingMode = "disabled"
    thinking_effort: ThinkingEffort | None = None
    max_output_tokens: int = 8192
    request_timeout_seconds: float = 120.0
    strict_tool_schema: bool = True
    supports_vision: bool = True
    api_key_configured: bool = False
    secret_storage: Literal["keychain", "session_only", "environment", "none"] = "none"
    model_verified: bool = False
    verified_config_version: int | None = None
    test_results: dict[str, Any] = Field(default_factory=dict)


class ProviderBinding(DTO):
    """Frozen request-side configuration for one run. Never contains the key."""

    binding_id: str
    profile_id: str
    profile_name: str
    provider: ProviderKind
    api_format: ApiFormat
    base_url: str
    model: str
    stream: bool = True
    thinking_mode: ThinkingMode = "disabled"
    thinking_effort: ThinkingEffort | None = None
    max_output_tokens: int = 8192
    request_timeout_seconds: float = 120.0
    strict_tool_schema: bool = True
    supports_vision: bool = True
    config_version: int = 2
    frozen_at: str = Field(default_factory=utcnow)


class RedactedConfig(DTO):
    schema_version: int = 2
    config_version: int = 2
    profiles: list[ProviderProfile] = Field(default_factory=list)
    default_profile_id: str | None = None
    # Read-only compatibility projections for older call sites.
    provider: str = "openai"
    api_mode: str = "responses"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5.4"
    model_verified: bool = False
    model_verified_config_version: int | None = None
    api_key_configured: bool = False
    secret_storage: Literal["keychain", "session_only", "environment", "none"] = "none"
    units: Literal["mm", "in"] = "mm"
    default_view: Literal["schematic", "pcb2d", "pcb3d"] = "schematic"
    show_grid: bool = True
    snap_mm: float = 0.25
    reduced_motion: bool = False
    follow_build: bool = True
    send_with_ctrl_enter: bool = False
    restore_last_project: bool = True
    remember_me: bool = False
    left_panel_width: int = 252
    right_panel_width: int = 352
    font_scale: float = 1.0
    request_budget: dict[str, Any] = Field(default_factory=lambda: {
        "max_model_rounds": 60, "max_tool_calls": 240,
        "active_run_timeout_seconds": 1800,
    })


class PatchOperation(DTO):
    op: Literal[
        "move_component", "rotate_component", "set_component_value",
        "add_component", "delete_component", "connect_pins", "set_no_connect",
        "set_board_size", "reroute", "lock_component", "relayout_schematic",
        "move_schematic_symbol", "set_rule_profile",
        "replace_component_asset", "disconnect_terminal",
        "assign_footprint", "connect_terminals", "change_part",
        "place_footprint", "set_assembly_state",
    ]
    component_id: str | None = None
    x_nm: int | None = None
    y_nm: int | None = None
    rotation_deg: int | None = None
    side: Side | None = None
    part_id: str | None = None
    reference: str | None = None
    value: str | None = None
    a: str | None = None
    b: str | None = None
    pin: str | None = None
    net_name: str | None = None
    net_class: Literal["power", "ground", "signal"] | None = None
    width_nm: int | None = None
    height_nm: int | None = None
    scope: Literal["all", "net"] | None = None
    net_ids: list[str] = Field(default_factory=list)
    locked: bool | None = None
    footprint_id: str | None = None
    pin_pad_mapping: dict[str, list[str]] | None = None
    pin_role_mapping: dict[str, str] | None = None
    terminals: list[str] = Field(default_factory=list)
    net_id: str | None = None
    replacement_part_id: str | None = None
    remap: dict[str, list[str]] | None = None
    instance_path: str | None = None
    parameter_bindings: dict[str, str] | None = None
    rotation_udeg: int | None = None
    assembly_state: Literal["assembled", "dnp", "not_fitted"] | None = None


class DesignPatch(DTO):
    operations: list[PatchOperation]
    note: str = ""
