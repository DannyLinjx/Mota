"""Acceptance checks for the versioned component library.

These exercise the product paths, not the Markdown contract: migration coverage,
installing a real third-party package, manual and agent placement through the
design command, uninstall/restore of a saved project, and conflict isolation.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mota.app.service import ApplicationService
from mota.catalog import service as catalog_service
from mota.circuit import parts
from mota.domain.models import RunRecord, utcnow
from mota.errors import NotFound
from mota.library import management, resolver
from mota.library.packages import (
    PackageInvalid, PackageWrite, json_bytes, text_bytes, write_package,
)
from mota.library.registry import Registry

from tests.conftest import make_user, new_project

ASSET = "workspace_acme:component:acme_max3485"
SYMBOL = "workspace_acme:symbol:acme_max3485"
FOOTPRINT = "workspace_acme:footprint:acme_soic8"

REAL_LIBRARY = Path.home() / "Library/Application Support/Mota/component_library"


def _real_package(asset_id: str) -> Path | None:
    """Read a migrated package from the developer's real library, if present."""
    reg = Registry(REAL_LIBRARY)
    entry = reg.find(asset_id)
    return reg.entry_dir(entry) if entry else None


def _build_pack(dest: Path) -> Path:
    """Build a genuine third-party pack from real KiCad geometry.

    Symbol and footprint payloads come from the migrated MAX3485 symbol and
    SOIC-8 footprint, including the native geometry files. Only identity,
    provenance and the binding are authored by the "workspace" here.
    """
    symbol_src = _real_package("kicad:symbol:Interface_UART/MAX3485")
    footprint_src = _real_package("kicad:footprint:Package_SO/SOIC-8_3.9x4.9mm_P1.27mm")
    if symbol_src is None or footprint_src is None:
        pytest.skip("migrated KiCad packages are not available in this library root")
    source_pins = json.loads((symbol_src / "pins.json").read_text(encoding="utf-8"))
    source_pads = json.loads((footprint_src / "pads.json").read_text(encoding="utf-8"))
    geometry_path = symbol_src / "geometry.json"
    geometry = json.loads(geometry_path.read_text(encoding="utf-8")) \
        if geometry_path.exists() else None

    terminals = [dict(row, evidence_refs=[{"source_id": "acme-datasheet",
                                           "locator": "MAX3483-MAX3491.pdf"}])
                 for row in source_pins["physical_terminals"]]
    for row in terminals:
        row["terminal_id"] = str(row["number"])
    occurrences = [dict(row,
                        symbol_asset_id=SYMBOL,
                        terminal_id=str(row["pin_number"]),
                        evidence_refs=[{"source_id": "acme-datasheet",
                                        "locator": "MAX3483-MAX3491.pdf"}])
                   for row in source_pins["symbol_occurrences"]]
    pins_doc = {"schema_version": 1, "asset_id": SYMBOL,
                "status": "source_reported",
                "physical_terminal_count": len(terminals),
                "physical_terminals": terminals,
                "symbol_occurrences": occurrences}
    mapping = {str(row["number"]): [str(row["number"])] for row in terminals}

    symbol_entry = write_package(dest, PackageWrite(
        asset_id=SYMBOL, kind="symbol", namespace="workspace_acme",
        display_name="ACME MAX3485 symbol", package_version="1.0.0",
        summary="RS-485 transceiver symbol bound by the ACME workspace",
        category="interface", tags=["rs-485", "acme"],
        payload={"pins.json": json_bytes(pins_doc),
                 "geometry.json": json_bytes(geometry or {"pins": []}),
                 "rules.json": json_bytes({"schema_version": 1,
                                           "asset_id": SYMBOL, "rules": []}),
                 "usage.md": text_bytes("# ACME MAX3485 symbol\n\nReal KiCad pins.\n")},
        origin={"provider_id": "acme_workspace", "source_id": "acme",
                "source_version": "1.0.0"},
        legacy_ids=[SYMBOL],
        documentation_level="basic",
        sources=[{"id": "acme-datasheet", "type": "datasheet",
                  "title": "MAX3483-MAX3491 datasheet",
                  "locator": "MAX3483-MAX3491.pdf"}]))
    footprint_entry = write_package(dest, PackageWrite(
        asset_id=FOOTPRINT, kind="footprint", namespace="workspace_acme",
        display_name="ACME SOIC-8", package_version="1.0.0",
        summary="SOIC-8 3.9x4.9 mm package from the KiCad library",
        category="soic", tags=["soic-8", "acme"],
        payload={"pads.json": json_bytes(source_pads),
                 "rules.json": json_bytes({"schema_version": 1,
                                           "asset_id": FOOTPRINT, "rules": []}),
                 "usage.md": text_bytes("# ACME SOIC-8\n\nReal KiCad pads.\n")},
        origin={"provider_id": "acme_workspace", "source_id": "acme",
                "source_version": "1.0.0"},
        legacy_ids=[FOOTPRINT],
        documentation_level="basic"))
    evidence = [{"source_id": "acme-datasheet", "locator": "MAX3483-MAX3491.pdf"}]
    write_package(dest, PackageWrite(
        asset_id=ASSET, kind="component", namespace="workspace_acme",
        display_name="ACME MAX3485 SOIC-8", package_version="1.0.0",
        summary="Workspace binding of the MAX3485 symbol and SOIC-8 footprint",
        category="interface", tags=["rs-485", "acme"],
        payload={
            "pins.json": json_bytes(dict(pins_doc, asset_id=ASSET)),
            "bindings.json": json_bytes({
                "schema_version": 1, "asset_id": ASSET, "status": "bound",
                "bindings": [{
                    "binding_id": f"{ASSET}:default", "status": "bound",
                    "symbol_ref": {"asset_id": SYMBOL, "version": "1.0.0",
                                   "digest": symbol_entry["digest"]},
                    "footprint_ref": {"asset_id": FOOTPRINT, "version": "1.0.0",
                                      "digest": footprint_entry["digest"]},
                    "model3d_refs": [],
                    "terminal_pad_map": [{"terminal_id": key, "pad_numbers": pads}
                                         for key, pads in sorted(mapping.items())],
                    "unmapped_terminals": [], "unmapped_pads": [],
                    "evidence_refs": evidence}]}),
            "parameters.json": json_bytes({"schema_version": 1, "asset_id": ASSET,
                                           "parameters": [{
                "key": "supply_voltage_v", "label": "推荐工作电压",
                "classification": "recommended_operating",
                "value": {"min": 3.0, "max": 3.6}, "units": "V",
                "conditions": ["recommended operating"],
                "applies_to": ["MAX3485"], "status": "verified",
                "evidence_refs": evidence}]}),
            "rules.json": json_bytes({"schema_version": 1, "asset_id": ASSET,
                                      "rules": [{
                "rule_id": "supply_voltage_range",
                "type": "parameter_in_range", "severity": "error",
                "message": "Supply voltage must stay inside the recommended range.",
                "applies_to": [ASSET], "evidence_refs": evidence,
                "parameters": {"parameter_key": "supply_voltage_v",
                               "min": 3.0, "max": 3.6, "units": "V"}}]}),
            "usage.md": text_bytes("# ACME MAX3485 SOIC-8\n\n3.3 V RS-485 transceiver.\n"),
        },
        origin={"provider_id": "acme_workspace", "source_id": "acme",
                "source_version": "1.0.0"},
        legacy_ids=[ASSET],
        dependencies=[{"asset_id": SYMBOL, "version": "1.0.0",
                       "digest": symbol_entry["digest"]},
                      {"asset_id": FOOTPRINT, "version": "1.0.0",
                       "digest": footprint_entry["digest"]}],
        documentation_level="partial",
        sources=[{"id": "acme-datasheet", "type": "datasheet",
                  "title": "MAX3483-MAX3491 datasheet",
                  "locator": "MAX3483-MAX3491.pdf"}]))
    return dest


@pytest.fixture()
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "component_library"
    root.mkdir()
    monkeypatch.setenv("MOTA_LIBRARY_ROOT", str(root))
    resolver.invalidate()
    yield root
    resolver.invalidate()


def test_migration_covers_every_legacy_id(library: Path) -> None:
    """Every id in the old snapshot must resolve through the alias table."""
    from mota.library import catalog as legacy

    rows = legacy._all_index_rows(None, None)
    assert len(rows) == 34208, "the source snapshot changed"
    # Build the migration into the temporary root without touching the real one.
    from mota.library.migration import migrate_builtin, migrate_kicad

    migrate_builtin(library)
    stats = migrate_kicad(library)
    assert stats["parsed"] == 34004
    assert stats["unsupported"] == 203
    assert stats["failed"] == 1

    reg = Registry(library)
    aliases = reg.aliases()
    missing = [row["asset_id"] for row in rows if row["asset_id"] not in aliases]
    assert missing == []


def test_install_place_manual_and_agent(library: Path, tmp_path: Path,
                                        service: ApplicationService) -> None:
    from mota.library.migration import migrate_builtin

    migrate_builtin(library)
    pack = _build_pack(tmp_path / "pack")
    result = management.install(pack, provider="native_folder")
    assert len(result["installed"]) == 3
    assert result["rejected"] == []
    found = catalog_service.search("ACME MAX3485")
    assert any(row["asset_id"] == ASSET for row in found["candidates"])
    detail = catalog_service.inspect(ASSET)
    assert detail["placeable"] is True
    assert detail["pin_pad_map"]["1"] == ["1"]

    session = make_user(service, "libmanual")
    project_id, revision = new_project(service, session, "Manual library place")
    committed = service.dispatch("design.patch", {
        "project_id": project_id, "expected_revision_id": revision,
        "operations": [{"op": "add_component", "part_id": ASSET,
                        "reference": "U1", "x_mm": 30.0, "y_mm": 25.0}],
        "idempotency_key": "manual_library_add"}, session)
    assert committed["commit_status"] == "COMMITTED"
    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    assert opened["component_count"] >= 1

    # Agent path: the same design command through the agent tool executor.
    from mota.agent.tools import ToolExecutor
    from mota.projects.store import ProjectStore

    agent_project, agent_revision = new_project(service, session, "Agent library place")
    store = ProjectStore(session.scope(), service.root)
    draft = store.begin_draft(agent_project, agent_revision, "run_lib")
    run = RunRecord(run_id="run_lib", project_id=agent_project,
                    owner_user_id=session.user_id, base_revision_id=agent_revision,
                    draft_id=draft.draft_id, state="SYNTHESIZING",
                    created_at=utcnow(), updated_at=utcnow())
    service.runs._save_run(store, run)
    executor = ToolExecutor(session.scope(), store, run, service.runs, service.root)
    tool_result = executor.execute("apply_design_patch", {
        "draft_id": draft.draft_id, "expected_draft_seq": draft.draft_seq,
        "operations": [{"op": "add_component", "part_id": ASSET,
                        "reference": "U1", "x_mm": 20.0, "y_mm": 20.0}]})
    assert tool_result["ok"] is True, tool_result
    assert "component_u1" in tool_result["affected_object_ids"]
    view = parts.resolve_part(ASSET)
    assert view.footprint_id == FOOTPRINT
    assert len(view.pins) == 8


def test_uninstall_keeps_saved_project_readable(library: Path, tmp_path: Path,
                                                service: ApplicationService) -> None:
    from mota.library.migration import migrate_builtin

    migrate_builtin(library)
    management.install(_build_pack(tmp_path / "pack"))
    session = make_user(service, "libuninstall")
    project_id, revision = new_project(service, session, "Uninstall restore")
    service.dispatch("design.patch", {
        "project_id": project_id, "expected_revision_id": revision,
        "operations": [{"op": "add_component", "part_id": ASSET,
                        "reference": "U1", "x_mm": 30.0, "y_mm": 25.0}],
        "idempotency_key": "uninstall_add"}, session)
    project_dir = service._store(session).project_dir(project_id)
    lock = json.loads((project_dir / "revisions" / "rev_000002"
                       / "library.lock.json").read_text(encoding="utf-8"))
    assert lock["closure_status"] == "complete"
    assert any(row["asset_id"] == ASSET for row in lock["assets"])
    assert any((project_dir / row["vendored_path"]).exists()
               for row in lock["assets"])

    removed = management.remove(ASSET)
    assert removed["removed"] is True
    assert not any(row["asset_id"] == ASSET
                   for row in catalog_service.search("ACME MAX3485")["candidates"])

    parts._imported_record.cache_clear()
    resolver.invalidate()
    with pytest.raises(NotFound):
        parts.resolve_part(ASSET)

    # Opening the saved project rehydrates the pinned package from its snapshot.
    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    assert opened["component_count"] >= 1
    parts._imported_record.cache_clear()
    restored = parts.resolve_part(ASSET)
    assert restored.footprint_id == FOOTPRINT


def test_conflicting_and_broken_pack_do_not_publish(library: Path, tmp_path: Path) -> None:
    from mota.library.migration import migrate_builtin

    migrate_builtin(library)
    pack = _build_pack(tmp_path / "pack")
    first = management.install(pack)
    generation = first["generation"]
    assert first["rejected"] == []

    again = management.install(pack)
    assert again["generation"] == generation
    assert again["rejected"] == []
    assert again["installed"][0]["note"] == "already installed"

    same_version = tmp_path / "pack2"
    shutil.copytree(pack, same_version)
    # Same identity and version, different manifest bytes (and therefore a
    # different digest) must be rejected instead of overwriting the package.
    manifests = sorted(same_version.glob("**/manifest.json"))
    component_manifest = next(path for path in manifests
                              if f'"{ASSET}"' in path.read_text(encoding="utf-8"))
    payload = json.loads(component_manifest.read_text(encoding="utf-8"))
    payload["summary"] = payload["summary"] + " (edited)"
    component_manifest.write_text(json.dumps(payload, ensure_ascii=False,
                                             sort_keys=True), encoding="utf-8")
    conflicted = management.install(same_version)
    assert conflicted["generation"] == generation
    assert any(row["error"].startswith("same asset id and version")
               for row in conflicted["rejected"])

    broken = tmp_path / "pack3"
    shutil.copytree(pack, broken)
    target = next(broken.glob("**/parameters.json"))
    target.write_text('{"parameters": []}', encoding="utf-8")
    with pytest.raises(PackageInvalid):
        management.install(broken)
    assert Registry(library).generation == generation
