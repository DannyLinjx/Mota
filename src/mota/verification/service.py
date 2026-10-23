"""Verification service: frozen board + rules -> fill artifact + report.

The native kernel performs fill, physical connectivity and DRC. This service
binds the result to a DesignVersion, maps native checks onto the existing
ValidationReport DTO, and never upgrades a NOT_CHECKED check to PASS.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import Field

from mota.domain.models import (
    BoardIR, CheckResult, CheckStatus, CircuitIR, DTO, DesignVersion,
    ValidationReport,
)
from mota.engine_v2.contracts import JobBudget
from mota.jobs.contracts import GeometryJobInput, run_native_job

VERIFY_PROFILE = "native-verify-v1"


class VerificationOutcome(DTO):
    report: ValidationReport
    fill: dict[str, Any] = Field(default_factory=dict)
    connectivity: dict[str, Any] = Field(default_factory=dict)
    drc: dict[str, Any] = Field(default_factory=dict)
    fill_hash: str = ""
    u_total: int = 0
    external_cross_check: dict[str, Any] = Field(default_factory=dict)
    native_status: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


def rule_hash(rules: list[dict[str, Any]]) -> str:
    payload = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def external_kicad_cross_check() -> dict[str, Any]:
    """Probe for an optional independent KiCad check without pretending to run it."""

    binary = shutil.which("kicad-cli")
    if not binary:
        return {
            "status": "NOT_CHECKED",
            "reason": "kicad-cli is not installed; no external cross-check was run",
            "binary": None,
        }
    return {
        "status": "NOT_CHECKED",
        "reason": (
            "kicad-cli was detected but KiCad DRC needs an exported "
            ".kicad_pcb artifact; deterministic export belongs to a later "
            "stage, so no comparison was run"
        ),
        "binary": binary,
    }


def verify_board(project_id: str, version: DesignVersion,
                 board: dict[str, Any], rules: list[dict[str, Any]], *,
                 binary: str | Path | None = None,
                 budget: JobBudget | None = None,
                 cancel_file: str | Path | None = None) -> VerificationOutcome:
    document = GeometryJobInput(
        input_id=f"verify-{project_id}-{version.key}",
        board=board,
        queries=[{"id": "verify", "kind": "verify"}],
        rules=rules)
    effective_budget = budget or JobBudget(wall_seconds=30.0,
                                           max_expansions=100_000)
    native = run_native_job(document, budget=effective_budget,
                            cancel_file=cancel_file, binary=binary)
    external = external_kicad_cross_check()

    checks: list[CheckResult] = []
    fill: dict[str, Any] = {}
    connectivity: dict[str, Any] = {}
    drc: dict[str, Any] = {}
    fill_hash = ""
    u_total = 0
    metrics: dict[str, Any] = {}

    if native.status != "SUCCEEDED" or not native.results:
        message = "native verification did not complete"
        if native.diagnostics:
            message = str(native.diagnostics[0].get("message", message))
        checks.append(CheckResult(
            check_id="NATIVE_VERIFY", rule_id="NATIVE_VERIFY",
            title="Native verification", status="NOT_CHECKED",
            severity="error", required_for_review=True,
            message=message,
            evidence={"native_status": native.status,
                      "diagnostics": native.diagnostics},
            coverage="native worker did not return a complete verify result",
            design_version_key=version.key))
    elif native.results[0].get("status") != "OK":
        checks.append(CheckResult(
            check_id="NATIVE_VERIFY", rule_id="NATIVE_VERIFY",
            title="Native verification", status="FAIL",
            severity="error", required_for_review=True,
            message=str(native.results[0].get("error", {})),
            coverage="native verify query failed",
            design_version_key=version.key))
    else:
        result = native.results[0]
        fill = dict(result.get("fill", {}))
        connectivity = dict(result.get("connectivity", {}))
        drc = dict(result.get("drc", {}))
        fill_hash = str(fill.get("fill_hash", ""))
        u_total = int(result.get("report", {}).get("u_total", 0))
        metrics = dict(result.get("metrics", {}))

        for check in drc.get("checks", []):
            status = str(check.get("status", "NOT_CHECKED"))
            if status not in {"PASS", "FAIL", "WARN", "NOT_CHECKED"}:
                status = "NOT_CHECKED"
            violations = check.get("violations", [])
            object_ids = sorted({
                str(ref) for violation in violations
                for ref in violation.get("object_refs", [])
            })
            checks.append(CheckResult(
                check_id=str(check.get("id", "DRC")),
                rule_id=str(check.get("id", "DRC")),
                title=str(check.get("kind", "design rule")),
                status=status,  # type: ignore[arg-type]
                severity="error" if check.get("required", True) else "warning",
                required_for_review=bool(check.get("required", True)),
                object_ids=object_ids[:40],
                message=str(check.get("message", "")),
                evidence={
                    "checked_objects": check.get("checked_objects", 0),
                    "coverage": check.get("coverage", ""),
                    "violations": violations[:20],
                },
                coverage=str(check.get("coverage", "")),
                design_version_key=version.key))

        checks.append(_connectivity_check(connectivity, version))
        checks.append(_fill_check(fill, version))
        if drc.get("status") == "NOT_CHECKED" and not drc.get("checks"):
            checks.append(CheckResult(
                check_id="DRC_RULES", rule_id="DRC_RULES",
                title="DRC rule coverage", status="NOT_CHECKED",
                severity="error", required_for_review=True,
                message="no DRC rule was supplied; an empty rule set is not a pass",
                evidence={"diagnostics": drc.get("diagnostics", [])},
                coverage="zero rules",
                design_version_key=version.key))

    checks.append(CheckResult(
        check_id="EXTERNAL_KICAD", rule_id="EXTERNAL_KICAD",
        title="Optional KiCad cross-check",
        status=str(external["status"]),  # type: ignore[arg-type]
        severity="info", required_for_review=False,
        message=str(external["reason"]),
        evidence={"binary": external.get("binary")},
        coverage="external comparison is optional and was not claimed",
        design_version_key=version.key))

    required = [check for check in checks if check.required_for_review]
    if not required:
        review = "REVIEW_BLOCKED"
    elif all(check.status == "PASS" for check in required):
        review = "READY_FOR_REVIEW"
    else:
        review = "REVIEW_BLOCKED"
    report = ValidationReport(
        project_id=project_id, design_version=version,
        design_version_key=version.key,
        profile_id=f"{VERIFY_PROFILE}:{rule_hash(rules)[:12]}",
        checks=checks, review_status=review)
    return VerificationOutcome(
        report=report, fill=fill, connectivity=connectivity, drc=drc,
        fill_hash=fill_hash, u_total=u_total,
        external_cross_check=external, native_status=native.status,
        metrics=metrics)


def verify_design(project_id: str, version: DesignVersion, circuit: CircuitIR,
                  board: BoardIR, rules: list[dict[str, Any]], *,
                  binary: str | Path | None = None,
                  budget: JobBudget | None = None,
                  cancel_file: str | Path | None = None) -> VerificationOutcome:
    """Frozen CircuitIR/BoardIR entry point for the native verifier."""

    return verify_board(project_id, version, board_spec_from_ir(circuit, board),
                        rules, binary=binary, budget=budget,
                        cancel_file=cancel_file)


def board_spec_from_ir(circuit: CircuitIR, board: BoardIR) -> dict[str, Any]:
    """Project the v1 BoardIR onto the native board contract.

    The projection is deliberately explicit: no geometry is invented. Shapes
    that v1 only describes approximately (rounded pads) are stated as such in
    the report boundary, not silently assumed exact.
    """

    net_names = {net.id: net.name for net in circuit.nets}
    net_classes = {net.name: net.net_class for net in circuit.nets
                   if net.name}
    thickness = max(70_000, board.thickness_nm)
    dielectric = max(1, thickness - 70_000)
    stackup = {"layers": [
        {"id": "F.Cu", "order": 1, "kind": "copper",
         "thickness_nm": 35_000, "material_ref": "copper"},
        {"id": "D1", "order": 2, "kind": "dielectric",
         "thickness_nm": dielectric, "material_ref": "FR4"},
        {"id": "B.Cu", "order": 3, "kind": "copper",
         "thickness_nm": 35_000, "material_ref": "copper"},
    ]}
    outline = [[int(x), int(y)] for x, y in board.outline]
    if len(outline) > 1 and outline[0] == outline[-1]:
        outline = outline[:-1]
    if len(outline) < 3:
        outline = [[0, 0], [board.width_nm, 0],
                   [board.width_nm, board.height_nm], [0, board.height_nm]]

    pads: list[dict[str, Any]] = []
    holes: list[dict[str, Any]] = []
    for pad in board.pads:
        net = net_names.get(pad.net_id or "", "")
        if pad.pad_type == "npth":
            if pad.drill_nm > 0:
                holes.append({"id": pad.id, "x": pad.x_nm, "y": pad.y_nm,
                              "drill_nm": pad.drill_nm, "plated": False})
            continue
        if not net:
            continue
        shape = _pad_shape(pad)
        entry = {
            "id": pad.id, "net": net, "component_id": pad.component_id,
            "pin": pad.number, "plated": pad.pad_type != "npth",
            "copper_by_layer": {layer: shape for layer in pad.layers},
        }
        if pad.drill_nm > 0:
            entry["drill"] = {
                "kind": "circle",
                "center": {"x": pad.x_nm, "y": pad.y_nm},
                "radius": pad.drill_nm // 2,
            }
        pads.append(entry)

    tracks = [{
        "id": track.id, "net": net_names.get(track.net_id, ""),
        "layer": track.layer, "width_nm": track.width_nm,
        "points": [{"x": x, "y": y} for x, y in track.points],
    } for track in board.tracks]

    vias = [{
        "id": via.id, "net": net_names.get(via.net_id, ""),
        "kind": "through", "start_layer": "F.Cu", "end_layer": "B.Cu",
        "drill_nm": via.drill_nm,
        "pad": {"kind": "circle", "center": {"x": via.x_nm, "y": via.y_nm},
                "radius": via.outer_nm // 2},
    } for via in board.vias]

    holes.extend({
        "id": hole.id, "x": hole.x_nm, "y": hole.y_nm,
        "drill_nm": hole.drill_nm, "plated": hole.plated,
    } for hole in board.holes)

    return {
        "width_nm": board.width_nm, "height_nm": board.height_nm,
        "thickness_nm": thickness, "stackup": stackup, "outline": outline,
        "pads": pads, "tracks": tracks, "vias": vias, "holes": holes,
        "net_classes": net_classes,
    }


def _pad_shape(pad) -> dict[str, Any]:
    if pad.shape == "circle":
        return {"kind": "circle",
                "center": {"x": pad.x_nm, "y": pad.y_nm},
                "radius": min(pad.w_nm, pad.h_nm) // 2}
    if pad.shape == "oval":
        radius = min(pad.w_nm, pad.h_nm) // 2
        if pad.w_nm >= pad.h_nm:
            half = (pad.w_nm - pad.h_nm) // 2
            a = {"x": pad.x_nm - half, "y": pad.y_nm}
            b = {"x": pad.x_nm + half, "y": pad.y_nm}
        else:
            half = (pad.h_nm - pad.w_nm) // 2
            a = {"x": pad.x_nm, "y": pad.y_nm - half}
            b = {"x": pad.x_nm, "y": pad.y_nm + half}
        return {"kind": "slot", "a": a, "b": b, "radius": radius}
    return {"kind": "round_rect",
            "center": {"x": pad.x_nm, "y": pad.y_nm},
            "half_w": pad.w_nm // 2, "half_h": pad.h_nm // 2,
            "corner_radius": 0}


def _connectivity_check(connectivity: dict[str, Any],
                        version: DesignVersion) -> CheckResult:
    nets = connectivity.get("nets", [])
    issues = connectivity.get("issues", [])
    u_total = int(connectivity.get("u_total", 0))
    shorts = int(connectivity.get("short_count", 0))
    missing = int(connectivity.get("missing_endpoint_count", 0))
    if not nets and not issues:
        return CheckResult(
            check_id="COPPER_CONNECTIVITY", rule_id="COPPER_CONNECTIVITY",
            title="Physical copper connectivity", status="NOT_CHECKED",
            severity="warning", required_for_review=False,
            message="no net pads were present to verify",
            coverage="zero net pads", design_version_key=version.key)
    failed = u_total > 0 or shorts > 0 or missing > 0
    object_ids: list[str] = []
    for issue in issues:
        if issue.get("severity") == "error":
            object_ids.extend(str(ref) for ref in issue.get("object_refs", []))
    return CheckResult(
        check_id="COPPER_CONNECTIVITY", rule_id="COPPER_CONNECTIVITY",
        title="Physical copper connectivity",
        status="FAIL" if failed else "PASS",
        severity="error", required_for_review=True,
        object_ids=sorted(set(object_ids))[:40],
        message=(
            f"U={u_total}, shorts={shorts}, missing endpoints={missing}"
            if failed else "all net pads are connected by real copper"),
        evidence={"u_total": u_total, "short_count": shorts,
                  "missing_endpoint_count": missing,
                  "nets": connectivity.get("nets", []),
                  "issues": issues[:20],
                  "net_ties_used": connectivity.get("net_ties_used", [])},
        coverage="copper union-find over pads, tracks, vias and fill",
        design_version_key=version.key)


def _fill_check(fill: dict[str, Any], version: DesignVersion) -> CheckResult:
    zones = fill.get("zones", [])
    if not zones:
        return CheckResult(
            check_id="ZONE_FILL", rule_id="ZONE_FILL", title="Copper zone fill",
            status="NOT_CHECKED", severity="info", required_for_review=False,
            message="no zone was requested", coverage="zero zones",
            design_version_key=version.key)
    non_rectilinear = [zone for zone in zones if zone.get("non_rectilinear")]
    islands = sum(int(zone.get("islands_removed", 0)) for zone in zones)
    components = sum(len(zone.get("components", [])) for zone in zones)
    if non_rectilinear:
        return CheckResult(
            check_id="ZONE_FILL", rule_id="ZONE_FILL", title="Copper zone fill",
            status="NOT_CHECKED", severity="error", required_for_review=True,
            message="a zone outline is not rectilinear; fill was not approximated",
            object_ids=[f"zone:{zone.get('zone_id', '')}"
                        for zone in non_rectilinear],
            evidence={"fill_hash": fill.get("fill_hash", "")},
            coverage="non-rectilinear outlines are unsupported in C7",
            design_version_key=version.key)
    status: CheckStatus = "WARN" if islands else "PASS"
    return CheckResult(
        check_id="ZONE_FILL", rule_id="ZONE_FILL", title="Copper zone fill",
        status=status, severity="warning" if islands else "info",
        required_for_review=False,
        message=(f"{islands} unanchored island(s) removed" if islands
                 else f"{components} anchored fill component(s)"),
        evidence={"fill_hash": fill.get("fill_hash", ""),
                  "islands_removed": islands, "components": components},
        coverage="real rectilinear copper polygons with thermal relief",
        design_version_key=version.key)
