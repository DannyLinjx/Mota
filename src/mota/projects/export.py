"""Project export: Mota bundle, SVG, BOM, build report, optional KiCad."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mota import __version__
from mota.catalog import builtin
from mota.catalog.templates import get_template
from mota.circuit import parts
from mota.domain.models import CheckResult, ProjectRevisionMeta
from mota.errors import MotaError, NotFound, SaveFailed
from mota.generation.kicad_writer import write_pcb, write_project, write_schematic
from mota.projects.store import DesignDraft, Snapshot
from mota.render.scene import make_scene
from mota.render.svg import scene_to_svg
from mota.workspace.atomic import sha256_bytes

KICAD_TOOL = shutil.which("kicad-cli")
ALLOW_UNVERIFIED = os.environ.get("MOTA_ALLOW_UNVERIFIED_KICAD_EXPORT") == "1"


def export_capabilities() -> dict[str, Any]:
    kicad_available = bool(KICAD_TOOL) or ALLOW_UNVERIFIED
    return {
        "formats": [
            {"format": "mota", "label": "Mota project bundle", "available": True,
             "reason": ""},
            {"format": "svg", "label": "Schematic and PCB SVG", "available": True,
             "reason": ""},
            {"format": "bom", "label": "BOM CSV", "available": True, "reason": ""},
            {"format": "report", "label": "Build report (Markdown)", "available": True,
             "reason": ""},
            {"format": "kicad", "label": "KiCad schematic/PCB", "available": kicad_available,
             "reason": "" if kicad_available else
             "KiCad compatibility has not been verified with kicad-cli in this build. "
             "Install KiCad 10 and run the external compatibility test to enable it."},
            {"format": "glb", "label": "glTF preview", "available": False,
             "reason": "glTF export is not part of this build's verified scope."},
        ],
        "kicad_tool": KICAD_TOOL or "",
        "kicad_verified": False,
    }


def _safe_name(name: str) -> str:
    keep = [ch if (ch.isalnum() or ch in "-_") else "_" for ch in name.strip()]
    value = "".join(keep).strip("_")
    return value or "mota_project"


def _scene_payloads(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    out = {}
    for view in ("schematic", "pcb2d", "pcb3d"):
        scene = make_scene(snapshot.project_id, snapshot.version, view,
                           snapshot.circuit, snapshot.schematic, snapshot.board)
        out[view] = scene.model_dump()
    return out


def export_revision(snapshot: Snapshot, fmt: str, destination: Path) -> dict[str, Any]:
    destination = Path(destination).expanduser()
    revision = snapshot.revision
    base = f"{_safe_name(revision.project_id)}_{revision.revision_id}"
    capabilities = export_capabilities()
    entry = next((f for f in capabilities["formats"] if f["format"] == fmt), None)
    if entry is None or not entry["available"]:
        raise MotaError(
            entry["reason"] if entry else f"Unknown export format {fmt}.",
            code="COMPATIBILITY_UNVERIFIED" if fmt == "kicad" else "EXPORT_UNAVAILABLE",
            data={"format": fmt, "capabilities": capabilities})
    scenes = _scene_payloads(snapshot)
    export_id = f"export_{revision.revision_id}_{fmt}_{int(datetime.now().timestamp())}"
    files: list[dict[str, str]] = []

    def record(path: Path) -> None:
        data = path.read_bytes()
        files.append({"path": path.name, "sha256": sha256_bytes(data),
                      "bytes": len(data)})

    if fmt == "svg":
        if destination.suffix.lower() != ".svg":
            destination.mkdir(parents=True, exist_ok=True)
        for view, filename in (("schematic", f"{base}_schematic.svg"),
                               ("pcb2d", f"{base}_pcb.svg")):
            target = destination / filename
            target.write_text(scene_to_svg(
                make_scene(snapshot.project_id, snapshot.version, view,
                           snapshot.circuit, snapshot.schematic, snapshot.board)),
                encoding="utf-8")
            record(target)
    elif fmt == "bom":
        if destination.suffix.lower() != ".csv":
            destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{base}_bom.csv"
        _write_bom(snapshot, target)
        record(target)
    elif fmt == "report":
        if destination.suffix.lower() != ".md":
            destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"{base}_build_report.md"
        target.write_text(build_report(snapshot), encoding="utf-8")
        record(target)
    elif fmt == "kicad":
        destination.mkdir(parents=True, exist_ok=True)
        folder = destination / f"{base}_kicad"
        folder.mkdir(exist_ok=True)
        write_schematic(snapshot.circuit, snapshot.schematic, folder / "design.kicad_sch")
        write_pcb(snapshot.circuit, snapshot.board, folder / "design.kicad_pcb")
        write_project(snapshot.project_id, folder / "design.kicad_pro")
        (folder / "format_capabilities.json").write_text(json.dumps({
            "generator": "mota", "kicad_cli": KICAD_TOOL or None,
            "compatibility": "NOT_VERIFIED",
            "supported": ["symbols", "wires", "junctions", "labels", "footprints",
                          "pads", "tracks", "vias", "edge cuts", "nets"],
            "unsupported": ["zones", "custom pads", "hierarchical sheets",
                            "3D model references in PCB"],
            "note": "Export is a snapshot; Mota does not read KiCad edits back.",
        }, indent=2), encoding="utf-8")
        for path in folder.iterdir():
            record(path)
    elif fmt == "mota":
        target = destination if destination.suffix == ".mota" else \
            destination / f"{base}.mota"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_bundle(snapshot, target)
        record(target)
    else:
        raise MotaError(f"Unsupported export format {fmt}.", code="EXPORT_UNAVAILABLE")

    return {
        "export_id": export_id,
        "revision_id": revision.revision_id,
        "project_id": snapshot.project_id,
        "format": fmt,
        "files": files,
        "sha256": files[0]["sha256"] if files else "",
        "destination": str(destination),
        "compatibility": "NOT_VERIFIED" if fmt == "kicad" else "NATIVE",
        "warnings": (["KiCad compatibility is NOT_VERIFIED; no external ERC/DRC was run."]
                     if fmt == "kicad" else []),
    }


def _write_bom(snapshot: Snapshot, target: Path) -> None:
    groups: dict[tuple[str, str], list] = {}
    for comp in snapshot.circuit.components:
        groups.setdefault((comp.part_id, comp.value), []).append(comp)
    rows = []
    for (part_id, value), comps in sorted(groups.items(),
                                          key=lambda kv: sorted(c.reference for c in kv[1])):
        refs = sorted(comp.reference for comp in comps)
        dnp_refs = sorted(comp.reference for comp in comps
                          if comp.assembly_state == "dnp")
        try:
            part = parts.resolve_part(part_id)
        except NotFound:
            rows.append([", ".join(refs), part_id, value, "", str(len(refs)),
                         "unknown", "unknown", "part not in installed catalog",
                         "Not orderable from this catalog",
                         "DNP: " + ", ".join(dnp_refs) if dnp_refs else ""])
            continue
        footprint_id = next((comp.footprint_id for comp in comps if comp.footprint_id), "")
        package = ""
        if footprint_id:
            try:
                package = parts.resolve_footprint(footprint_id).name
            except NotFound:
                package = footprint_id
        pin_status = ("verified" if part_id in builtin.PARTS and
                      builtin.PARTS[part_id].evidence and
                      any(e.kind == "pin_map" and e.status == "verified"
                          for e in builtin.PARTS[part_id].evidence)
                      else "unverified")
        notes = []
        if part.category == "capacitor":
            notes.append("effective capacitance / ESR evidence NOT_CHECKED")
        if part.category == "led":
            notes.append("verify physical lead polarity")
        if part_id.startswith("sym:"):
            notes.append("imported library part; evidence NOT_CHECKED")
        if dnp_refs:
            notes.append("DNP: " + ", ".join(dnp_refs))
        rows.append([", ".join(refs), part.mpn, value, package, str(len(refs)),
                     ";".join(part.source_ids) or "engineering spec", pin_status,
                     "simplified", " ".join(notes)])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Reference", "MPN", "Value", "Package", "Qty", "Sources",
                     "Pin map", "3D model", "Notes"])
    writer.writerows(rows)
    writer.writerow([])
    writer.writerow(["Note", "No prices are included. This BOM is not a purchase order."])
    target.write_text(buffer.getvalue(), encoding="utf-8")


def build_report(snapshot: Snapshot) -> str:
    revision = snapshot.revision
    requirement = snapshot.requirement
    counts = snapshot.checks.counts
    lines = [
        f"# Build report — {revision.project_id} {revision.revision_id}",
        "",
        f"- Created: {revision.created_at}",
        f"- Review status: **{revision.review_status}**",
        f"- Template: {revision.template_id or 'custom'}",
        f"- Commit id: {revision.commit_meta.commit_id}",
        f"- Run id: {revision.commit_meta.run_id or 'manual edit'}",
        f"- Design hash: `{revision.design_hash}`",
        f"- Components: {len(snapshot.circuit.components)} · Nets: {len(snapshot.circuit.nets)}",
        f"- Checks: {counts.get('PASS', 0)} passed · {counts.get('FAIL', 0)} failed · "
        f"{counts.get('WARN', 0)} warnings · {counts.get('NOT_CHECKED', 0)} not checked",
        "",
        "## Requirements",
        "",
        f"{requirement.purpose}",
        "",
        "| Parameter | Value | Unit | Origin |",
        "| --- | --- | --- | --- |",
    ]
    for key, value in sorted(requirement.parameters.items()):
        lines.append(f"| {key} | {value.value} | {value.unit or ''} | {value.origin} |")
    lines += ["", "## Checks", "",
              "| Status | Check | Required | Message |", "| --- | --- | --- | --- |"]
    for check in snapshot.checks.checks:
        lines.append(f"| {check.status} | {check.check_id} | "
                     f"{'yes' if check.required_for_review else 'no'} | "
                     f"{check.message.replace('|', '/')} |")
    if snapshot.board.unrouted_nets:
        lines += ["", "## Unrouted nets", "",
                  ", ".join(snapshot.board.unrouted_nets)]
    lines += ["", "## Scope and limitations", ""]
    lines.append("- READY_FOR_REVIEW means the design is reviewable, not production ready.")
    lines.append("- Thermal, EMC, external cable capacitance and physical assembly are outside "
                 "this demo's verified scope where marked NOT_CHECKED.")
    lines.append("- 3D component bodies are simplified unless marked exact_asset; the board "
                 "mesh is not boolean-cut for holes.")
    lines.append("- Capacitor derating/ESR evidence is not recorded for the demo parts; the "
                 "power_3v3 output-capacitor checks are therefore NOT_CHECKED.")
    lines.append("- KiCad export compatibility is NOT_VERIFIED in this environment.")
    lines += ["", "## Sources", ""]
    for source in builtin.SOURCES:
        if source.source_id in {s.get("source_id") for s in requirement.sources}:
            lines.append(f"- [{source.source_id}] {source.title}"
                         + (f" — {source.url}" if source.url else ""))
    return "\n".join(lines) + "\n"


def _write_bundle(snapshot: Snapshot, target: Path) -> None:
    revision = snapshot.revision
    scenes = _scene_payloads(snapshot)
    part_ids = sorted({c.part_id for c in snapshot.circuit.components})
    footprint_ids = sorted({c.footprint_id for c in snapshot.circuit.components
                            if c.footprint_id})
    symbol_ids = sorted({c.symbol_id for c in snapshot.circuit.components})
    asset_parts: dict[str, Any] = {}
    for part_id in part_ids:
        if part_id in builtin.PARTS:
            asset_parts[part_id] = builtin.PARTS[part_id].model_dump()
            continue
        try:
            view = parts.resolve_part(part_id)
        except NotFound:
            asset_parts[part_id] = {"part_id": part_id, "status": "unresolved"}
            continue
        asset_parts[part_id] = {
            "part_id": view.part_id, "mpn": view.mpn,
            "description": view.description, "symbol_id": view.symbol_id,
            "footprint_id": view.footprint_id,
            "pins": [pin.model_dump() for pin in view.pins],
            "pin_pad_map": view.pin_pad_map, "source_ids": list(view.source_ids),
            "status": "imported",
        }
    asset_footprints: dict[str, Any] = {}
    for footprint_id in footprint_ids:
        try:
            asset_footprints[footprint_id] = \
                parts.resolve_footprint(footprint_id).model_dump()
        except NotFound:
            asset_footprints[footprint_id] = {"footprint_id": footprint_id,
                                              "status": "unresolved"}
    asset_symbols: dict[str, Any] = {}
    for symbol_id in symbol_ids:
        try:
            asset_symbols[symbol_id] = parts.resolve_symbol(symbol_id).model_dump()
        except NotFound:
            asset_symbols[symbol_id] = {"symbol_id": symbol_id,
                                        "status": "unresolved"}
    assets = {
        "parts": asset_parts,
        "footprints": asset_footprints,
        "symbols": asset_symbols,
        "sources": [s.model_dump() for s in builtin.SOURCES],
    }
    template = None
    if revision.template_id:
        try:
            template = get_template(revision.template_id).raw
        except NotFound:
            template = None
    payloads: dict[str, bytes] = {
        "circuit.json": json.dumps(snapshot.circuit.model_dump(), indent=2).encode(),
        "schematic.json": json.dumps(snapshot.schematic.model_dump(), indent=2).encode(),
        "board.json": json.dumps(snapshot.board.model_dump(), indent=2).encode(),
        "checks.json": json.dumps(snapshot.checks.model_dump(), indent=2).encode(),
        "revision.json": json.dumps(revision.model_dump(), indent=2).encode(),
        "requirement.json": json.dumps(snapshot.requirement.model_dump(), indent=2).encode(),
        "assets.json": json.dumps(assets, indent=2).encode(),
        "template.yaml": json.dumps(template or {}, indent=2).encode(),
        "build_report.md": build_report(snapshot).encode(),
        "LICENSES/THIRD_PARTY.md": (
            "# Third-party resources\n\n"
            "Mota application code is proprietary. The bundled demo uses a curated "
            "catalog with engineering specifications authored for this project. "
            "KiCad symbols/footprints are not redistributed in this bundle. "
            "KiCad library licensing: CC-BY-SA 4.0 with design-output exception.\n"
        ).encode(),
    }
    for view, scene in scenes.items():
        payloads[f"scene/{view}.json"] = json.dumps(scene, indent=2).encode()
    manifest = {
        "bundle_format": "mota.bundle.v1",
        "schema_version": 1,
        "generator": f"mota {__version__}",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_id": snapshot.project_id,
        "project_name": snapshot.revision.project_id,
        "revision_id": revision.revision_id,
        "review_status": revision.review_status,
        "design_hash": revision.design_hash,
        "template_id": revision.template_id,
        "library_snapshot_id": snapshot.circuit.library_snapshot_id,
        "object_counts": {"components": len(snapshot.circuit.components),
                          "nets": len(snapshot.circuit.nets),
                          "tracks": len(snapshot.board.tracks)},
        "files": {name: {"sha256": sha256_bytes(data), "bytes": len(data)}
                  for name, data in payloads.items()},
        "capabilities": {"offline_reopen": True, "includes_3d_scene": True,
                         "includes_kicad": False},
        "limitations": ["Charts are regenerated from IR; no external assets required.",
                        "KiCad files are not part of this bundle."],
    }
    payloads["manifest.json"] = json.dumps(manifest, indent=2).encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in payloads.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.date_time = (2026, 1, 1, 0, 0, 0)
            zf.writestr(info, data)
