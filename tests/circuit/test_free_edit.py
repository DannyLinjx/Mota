from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mota.cli.main import app as cli_app
from mota.errors import MotaError
from mota.library.catalog import SourceSpec, import_library
from mota.projects.export import export_revision
from tests.conftest import build_template, make_user, new_project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "library"


def _seed(root: Path) -> None:
    for source_id, sub in (("fx", "packed"), ("fxfp", "footprints"),
                           ("nc", "nc")):
        import_library(SourceSpec(source_id=source_id, path=str(FIXTURES / sub)),
                       root=root)


def _patch(service, session, project_id, revision_id, ops):
    return service.dispatch("design.patch", {
        "project_id": project_id, "expected_revision_id": revision_id,
        "operations": ops}, session)


def _norm(circuit) -> dict:
    refs = {c.id: c.reference for c in circuit.components}
    components = sorted(
        (c.reference, c.part_id, c.symbol_id, c.footprint_id,
         tuple(sorted((pin, tuple(pads)) for pin, pads in c.pin_pad_map.items())),
         c.on_board, c.in_bom, c.assembly_state)
        for c in circuit.components)
    nets = sorted(
        (n.name,
         tuple(sorted(f"{refs.get(token.split(':')[0], token.split(':')[0])}:"
                      f"{token.split(':', 1)[1]}" for token in n.pins)))
        for n in circuit.nets)
    return {"components": components, "nets": nets}


def test_add_assign_connect_place_and_dnp(service, data_root):
    _seed(data_root)
    session = make_user(service, "freeedit")
    project, _ = new_project(service, session, "Free edit")
    built = build_template(service, session, project, "power_3v3")
    revision = built["run"]["committed_revision_id"]
    ops = [
        {"op": "add_component", "part_id": "sym:fx:Device:R", "reference": "R9",
         "value": "10k", "x_nm": 5_000_000, "y_nm": 5_000_000},
        {"op": "assign_footprint", "component_id": "component_r9",
         "footprint_id": "fp:fxfp:MyParts:R_0603"},
        {"op": "connect_terminals", "terminals": ["R9:1", "U1:2"],
         "net_name": "VIN_EXTRA", "net_class": "power"},
        {"op": "set_no_connect", "pin": "R9:2"},
        {"op": "place_footprint", "component_id": "component_r9",
         "x_nm": 25_000_000, "y_nm": 17_500_000, "rotation_udeg": 90_000_000},
        {"op": "set_assembly_state", "component_id": "component_r9",
         "assembly_state": "dnp"},
    ]
    result = _patch(service, session, project, revision, ops)
    assert result["commit_status"] == "COMMITTED"
    snapshot = service._store(session).load_revision(project, result["revision_id"])
    r9 = next(c for c in snapshot.circuit.components if c.reference == "R9")
    assert r9.part_id == "sym:fx:Device:R"
    assert r9.footprint_id == "fp:fxfp:MyParts:R_0603"
    assert r9.pin_pad_map == {"1": ["1"], "2": ["2"]}
    assert r9.assembly_state == "dnp"
    assert r9.placement is not None and r9.placement.rotation_deg == 90
    vin = next(net for net in snapshot.circuit.nets if net.name == "VIN_5V")
    assert f"{r9.id}:1" in vin.pins
    assert f"{r9.id}:2" in snapshot.circuit.no_connects

    pads = [pad for pad in snapshot.board.pads if pad.component_id == r9.id]
    assert len(pads) == 2
    assert any(pad.number == "1" and pad.net_id for pad in pads)
    assert any(symbol.reference == "R9" for symbol in snapshot.schematic.symbols)
    assert any(footprint.component_id == r9.id
               for footprint in snapshot.board.footprints)

    mapping = next((check for check in snapshot.checks.checks
                    if check.check_id == "PIN_PAD_MAPPING"), None)
    assert mapping is not None and mapping.status == "PASS"

    bom_dir = data_root / "exports"
    bom = export_revision(snapshot, "bom", bom_dir)
    bom_text = (Path(bom["destination"]) /
                bom["files"][0]["path"]).read_text(encoding="utf-8")
    assert "R9" in bom_text and "DNP" in bom_text
    bundle = export_revision(snapshot, "mota", bom_dir)
    assert bundle["files"][0]["sha256"]


def test_missing_or_wrong_footprint_blocks_review(service, data_root):
    _seed(data_root)
    session = make_user(service, "blocked")
    project, _ = new_project(service, session, "Blocked")
    built = build_template(service, session, project, "led_timer")
    revision = built["run"]["committed_revision_id"]
    assert built["run"]["state"] in ("SUCCEEDED", "PARTIAL")

    added = _patch(service, session, project, revision, [
        {"op": "add_component", "part_id": "sym:fx:Device:R", "reference": "R8",
         "x_nm": 8_000_000, "y_nm": 8_000_000}])
    snapshot = service._store(session).load_revision(project, added["revision_id"])
    mapping = next(check for check in snapshot.checks.checks
                   if check.check_id == "PIN_PAD_MAPPING")
    assert mapping.status == "FAIL"
    assert any(issue["code"] == "FOOTPRINT_MISSING"
               for issue in mapping.evidence["issues"])
    assert snapshot.checks.review_status == "REVIEW_BLOCKED"

    fixed = _patch(service, session, project, added["revision_id"], [
        {"op": "assign_footprint", "component_id": "component_r8",
         "footprint_id": "fp:fxfp:MyParts:R_0603"},
        {"op": "place_footprint", "component_id": "component_r8",
         "x_nm": 8_000_000, "y_nm": 8_000_000, "rotation_udeg": 0}])
    snapshot = service._store(session).load_revision(project, fixed["revision_id"])
    mapping = next(check for check in snapshot.checks.checks
                   if check.check_id == "PIN_PAD_MAPPING")
    assert mapping.status == "PASS"


def test_change_part_remap_and_unsupported_rotation(service, data_root):
    _seed(data_root)
    session = make_user(service, "changepart")
    project, _ = new_project(service, session, "Change part")
    built = build_template(service, session, project, "power_3v3")
    revision = built["run"]["committed_revision_id"]
    ops = [
        {"op": "add_component", "part_id": "sym:fx:Device:R", "reference": "R7",
         "x_nm": 6_000_000, "y_nm": 6_000_000},
        {"op": "assign_footprint", "component_id": "component_r7",
         "footprint_id": "fp:fxfp:MyParts:R_0603"},
        {"op": "connect_terminals", "terminals": ["R7:1", "U1:2"],
         "net_name": "VIN_CHANGED"},
        {"op": "change_part", "component_id": "component_r7",
         "replacement_part_id": "sym:nc:NC:NCPart",
         "footprint_id": "fp:nc:MyParts:NCPart",
         "remap": {"1": ["1"], "2": ["2"]}},
    ]
    result = _patch(service, session, project, revision, ops)
    snapshot = service._store(session).load_revision(project, result["revision_id"])
    r7 = next(c for c in snapshot.circuit.components if c.reference == "R7")
    assert r7.part_id == "sym:nc:NC:NCPart"
    assert r7.symbol_id == "sym:nc:NC:NCPart"
    assert r7.pin_pad_map == {"1": ["1"], "2": ["2"]}
    net = next(net for net in snapshot.circuit.nets if net.name == "VIN_5V")
    assert f"{r7.id}:1" in net.pins

    with pytest.raises(MotaError) as exc:
        _patch(service, session, project, result["revision_id"], [
            {"op": "place_footprint", "component_id": "component_r7",
             "x_nm": 5_000_000, "y_nm": 5_000_000, "rotation_udeg": 45_000_000}])
    assert exc.value.code == "ROTATION_UNSUPPORTED"


def test_cli_and_service_produce_the_same_normalized_design(service, data_root,
                                                            tmp_path):
    _seed(data_root)
    session = make_user(service, "parity")
    ops = [
        {"op": "add_component", "part_id": "sym:fx:Device:R", "reference": "R6",
         "x_nm": 7_000_000, "y_nm": 7_000_000},
        {"op": "assign_footprint", "component_id": "component_r6",
         "footprint_id": "fp:fxfp:MyParts:R_0603"},
        {"op": "connect_terminals", "terminals": ["R6:1", "U1:2"],
         "net_name": "VIN_PARITY"},
    ]
    first, _ = new_project(service, session, "Parity GUI")
    built = build_template(service, session, first, "power_3v3")
    service_result = _patch(service, session, first,
                            built["run"]["committed_revision_id"], ops)

    second, _ = new_project(service, session, "Parity CLI")
    built_two = build_template(service, session, second, "power_3v3")

    runner = CliRunner()
    login = runner.invoke(cli_app, ["auth", "login", "--username", "parity",
                                    "--password", "Passw0rd!23", "--json"])
    assert login.exit_code == 0, login.stdout + login.stderr
    ops_file = tmp_path / "ops.json"
    ops_file.write_text(json.dumps({"operations": ops}), encoding="utf-8")
    cli_result = runner.invoke(cli_app, [
        "design", "patch", "--project", second,
        "--operations", str(ops_file), "--json"])
    assert cli_result.exit_code == 0, cli_result.stdout + cli_result.stderr
    cli_payload = json.loads(cli_result.stdout)

    store = service._store(session)
    a = store.load_revision(first, service_result["revision_id"]).circuit
    b = store.load_revision(second, cli_payload["revision_id"]).circuit
    assert _norm(a) == _norm(b)
    assert built_two["run"]["state"] in ("SUCCEEDED", "PARTIAL")
