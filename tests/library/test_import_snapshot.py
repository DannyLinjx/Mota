from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mota.app.service import ApplicationService
from mota.app.state import SessionContext
from mota.cli.main import app as cli_app
from mota.errors import AuthRequired, InvalidRequest, NotFound
from mota.library.catalog import (
    SourceSpec, import_library, inspect_asset, library_status, preview_asset,
    rollback_library, search_assets,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "library"


def _import(tmp_root: Path, source_id: str, sub: str, **kwargs):
    path = Path(sub)
    if not path.is_absolute():
        path = FIXTURES / sub
    return import_library(SourceSpec(source_id=source_id, path=str(path), **kwargs),
                          root=tmp_root)


def test_packed_symbols_inheritance_and_unknown_semantics(tmp_path):
    report = _import(tmp_path, "fx-device", "packed", version="fixture-1",
                     license="CC-BY-SA-4.0")
    counts = report.counts
    assert counts.discovered == 6
    assert counts.parsed == 4
    assert counts.unsupported == 1
    assert counts.failed == 1
    assert counts.mapped == 3
    assert counts.discovered == counts.parsed + counts.unsupported + counts.failed
    assert report.failure_codes == {"DUPLICATE_ASSET_NAME": 1}
    assert report.unsupported_codes == {"NO_PINS": 1}
    assert report.semantic_warning_codes == {"UNKNOWN_PIN_ELECTRICAL_TYPE": 1}
    assert report.status == "PARTIAL"

    resistor = inspect_asset("sym:fx-device:Device:R", tmp_path)
    assert resistor["status"] == "parsed"
    assert {pin["number"] for pin in resistor["pins"]} == {"1", "2"}
    assert resistor["description"] == "Resistor"
    assert resistor["keywords"] == "R res resistor"
    assert resistor["mapped"] is True

    derived = inspect_asset("sym:fx-device:Device:DerivedOpAmp", tmp_path)
    assert derived["extends"] == "DualAmp"
    assert derived["units"] == 2
    assert derived["body_styles"] == 2
    assert {pin["number"] for pin in derived["pins"]} == {"A1", "A2", "B1", "B2"}
    assert {pin["unit"] for pin in derived["pins"]} == {1, 2}
    assert derived["properties"]["Value"] == "DerivedOpAmp"

    quantum = inspect_asset("sym:fx-device:Device:Quantum", tmp_path)
    assert quantum["status"] == "parsed"
    assert quantum["mapped"] is False
    assert quantum["unknown_electrical"] is True
    codes = {diag["code"] for diag in quantum["diagnostics"]}
    assert "UNKNOWN_PIN_ELECTRICAL_TYPE" in codes
    assert "UNKNOWN_TOKEN" in codes
    located = [diag for diag in quantum["diagnostics"]
               if diag["code"] == "UNKNOWN_PIN_ELECTRICAL_TYPE"][0]
    assert located["file"].endswith("Device.kicad_sym")
    assert located["line"] > 1
    assert "quantum" in located["message"]

    logo = inspect_asset("sym:fx-device:Device:Logo", tmp_path)
    assert logo["status"] == "unsupported"
    assert logo["unsupported_features"] == ["NO_PINS"]

    duplicate = search_assets("", root=tmp_path, kind="symbol",
                              status="failed")["candidates"]
    assert len(duplicate) == 1
    assert duplicate[0]["status"] == "failed"
    assert duplicate[0]["asset_id"].endswith("#2")

    root_token = [diag for diag in report.diagnostics
                  if diag.code == "UNKNOWN_TOKEN"
                  and "future_root_token" in diag.message]
    assert root_token and root_token[0].line == 2


def test_reimport_incremental_change_rollback_and_objects(tmp_path):
    source = tmp_path / "vendor"
    source.mkdir()
    symbol_path = source / "Device.kicad_sym"
    shutil.copy(FIXTURES / "packed" / "Device.kicad_sym", symbol_path)
    first = _import(tmp_path, "inc", str(source))
    first_snapshot = first.snapshot_id
    assert first.counts.added == first.counts.indexed == 6

    source_sha = __import__("hashlib").sha256(symbol_path.read_bytes()).hexdigest()
    stored = (tmp_path / "catalog" / "library" / "inc" / "objects"
              / source_sha[:2] / source_sha)
    assert stored.exists() and stored.read_bytes() == symbol_path.read_bytes()

    second = _import(tmp_path, "inc", str(source))
    assert second.snapshot_id == first_snapshot
    assert second.counts.added == 0
    assert second.counts.changed == 0
    assert second.counts.removed == 0
    assert second.counts.unchanged == second.counts.indexed == 6
    assert second.counts.files_reused == 1
    assert second.counts.bytes_parsed == 0

    text = symbol_path.read_text(encoding="utf-8")
    symbol_path.write_text(text.replace('"Description" "Resistor"',
                                        '"Description" "Precision resistor"'),
                           encoding="utf-8")
    third = _import(tmp_path, "inc", str(source))
    assert third.snapshot_id != first_snapshot
    assert third.counts.changed >= 1
    assert inspect_asset("sym:inc:Device:R", tmp_path)["description"] == \
        "Precision resistor"

    rolled = rollback_library("inc", tmp_path)
    assert rolled["snapshot_id"] == first_snapshot
    assert inspect_asset("sym:inc:Device:R", tmp_path)["description"] == "Resistor"
    assert library_status(tmp_path)["sources"][0]["snapshot_id"] == first_snapshot


def test_unpacked_legacy_broken_and_same_name_sources(tmp_path):
    unpacked = _import(tmp_path, "fx-unpacked", "unpacked")
    assert unpacked.status == "IMPORTED"
    special = inspect_asset("sym:fx-unpacked:Custom:Special", tmp_path)
    assert special["name"] == "Special"
    assert {pin["number"] for pin in special["pins"]} == {"1", "2"}

    legacy = _import(tmp_path, "fx-legacy", "legacy")
    assert legacy.status == "PARTIAL"
    assert legacy.unsupported_codes == {"LEGACY_LIB_FORMAT": 1}
    assert legacy.counts.indexed == 1

    broken = _import(tmp_path, "fx-broken", "broken")
    assert broken.status == "FAILED"
    assert broken.failure_codes == {"SYNTAX_ERROR": 1}
    failure = broken.diagnostics[0]
    assert failure.file.endswith("Broken.kicad_sym")
    assert failure.line >= 1 and failure.column >= 1

    _import(tmp_path, "src-a", "source_a")
    _import(tmp_path, "src-b", "source_b")
    found = search_assets("shared", root=tmp_path)["candidates"]
    ids = {row["asset_id"] for row in found}
    assert ids == {"sym:src-a:SharedLib:Shared", "sym:src-b:SharedLib:Shared"}
    assert inspect_asset("sym:src-a:SharedLib:Shared", tmp_path)["description"] \
        != inspect_asset("sym:src-b:SharedLib:Shared", tmp_path)["description"]


def test_footprints_pads_unknown_shapes_and_on_demand_preview(tmp_path):
    report = _import(tmp_path, "fx-fp", "footprints", license="CC-BY-SA-4.0")
    assert report.counts.discovered == 3
    assert report.counts.parsed == 2
    assert report.counts.unsupported == 1
    assert report.counts.mapped == 1
    assert report.counts.rendered == 0
    assert report.unsupported_codes == {"NO_PADS": 1}

    resistor = inspect_asset("fp:fx-fp:MyParts:R_0603", tmp_path)
    assert [pad["number"] for pad in resistor["pads"]] == ["1", "2"]
    assert resistor["models"] and resistor["models"][0].endswith(".wrl")
    assert resistor["mapped"] is True

    weird = inspect_asset("fp:fx-fp:MyParts:WeirdPad", tmp_path)
    assert len(weird["pads"]) == 4
    assert weird["mapped"] is False
    assert "UNSUPPORTED_PAD_SHAPE" in weird["unsupported_features"]
    assert "UNSUPPORTED_PAD_TYPE" in weird["unsupported_features"]
    codes = {diag["code"] for diag in weird["diagnostics"]}
    assert "DUPLICATE_PAD_NUMBERS" in codes
    assert "UNKNOWN_TOKEN" in codes

    empty = inspect_asset("fp:fx-fp:MyParts:EmptyGraphic", tmp_path)
    assert empty["status"] == "unsupported"

    svg = preview_asset("fp:fx-fp:MyParts:R_0603", tmp_path)
    assert svg.startswith("<svg") and "<rect" in svg
    assert report.counts.rendered == 0  # previews are generated on demand


def test_service_catalog_surfaces_and_cli(tmp_path, data_root):
    service = ApplicationService(data_root)
    session = _register(service)

    report = service.dispatch("library.import", {
        "source_id": "svc-packed", "path": str(FIXTURES / "packed"),
        "license": "CC-BY-SA-4.0"}, session)
    assert report["counts"]["parsed"] == 4

    catalog = service.dispatch("catalog.search", {"query": "resistor"}, None)
    ids = {row["asset_id"] for row in catalog["library_assets"]}
    assert "sym:svc-packed:Device:R" in ids
    assert catalog["library_total"] >= 1

    inspected = service.dispatch(
        "catalog.inspect", {"asset_id": "sym:svc-packed:Device:R"}, None)
    assert inspected["kind"] == "library_asset"
    assert inspected["asset"]["pins"]

    found = service.dispatch("library.search", {"query": "opamp"}, None)
    assert found["total"] >= 1
    assert service.dispatch("library.status", {}, None)["total_assets"] == 6

    preview = service.dispatch(
        "library.preview", {"asset_id": "sym:svc-packed:Device:R"}, None)
    assert preview["svg"].startswith("<svg")

    with pytest.raises(AuthRequired):
        service.dispatch("library.import", {
            "source_id": "nope", "path": str(FIXTURES / "packed")}, None)

    runner = CliRunner()
    cli_status = runner.invoke(cli_app, ["library", "status", "--json"])
    assert cli_status.exit_code == 0, cli_status.stdout + cli_status.stderr
    assert json.loads(cli_status.stdout)["total_assets"] == 6

    cli_search = runner.invoke(cli_app, ["library", "search", "resistor", "--json"])
    assert cli_search.exit_code == 0
    assert json.loads(cli_search.stdout)["total"] >= 1


def test_invalid_sources_do_not_replace_active_snapshot(tmp_path):
    first = _import(tmp_path, "guard", "packed")
    snapshot = library_status(tmp_path)["sources"][0]["snapshot_id"]

    with pytest.raises(InvalidRequest) as missing:
        import_library(SourceSpec(source_id="guard", path=str(tmp_path / "nope")),
                       root=tmp_path)
    assert missing.value.code == "LIBRARY_SOURCE_MISSING"

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InvalidRequest) as empty_error:
        import_library(SourceSpec(source_id="guard", path=str(empty)), root=tmp_path)
    assert empty_error.value.code == "LIBRARY_SOURCE_EMPTY"

    one_file = FIXTURES / "packed" / "Device.kicad_sym"
    with pytest.raises(InvalidRequest) as mismatch:
        import_library(SourceSpec(source_id="guard", path=str(one_file),
                                  format="unpacked"), root=tmp_path)
    assert mismatch.value.code == "LIBRARY_FORMAT_MISMATCH"

    assert library_status(tmp_path)["sources"][0]["snapshot_id"] == snapshot
    assert library_status(tmp_path)["sources"][0]["counts"]["indexed"] == \
        first.counts.indexed

    with pytest.raises(NotFound) as unknown:
        inspect_asset("sym:guard:Device:NoSuchSymbol", tmp_path)
    assert unknown.value.code == "LIBRARY_ASSET_NOT_FOUND"


def _register(service: ApplicationService) -> SessionContext:
    result = service.dispatch("auth.register", {
        "username": "libtester", "password": "Passw0rd!23",
        "display_name": "libtester"}, None)
    info = result["session"]
    return SessionContext(session_id=info["session_id"],
                          user_id=info["user_id"], username=info["username"],
                          epoch=1)
