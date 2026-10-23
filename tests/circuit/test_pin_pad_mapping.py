from __future__ import annotations

from pathlib import Path

import pytest

from mota.circuit import parts
from mota.domain.models import Component, Placement
from mota.library.catalog import SourceSpec, import_library

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "library"


def _seed(root: Path) -> None:
    for source_id, sub in (("fx", "packed"), ("fxfp", "footprints"),
                           ("stack", "stacked"), ("nc", "nc")):
        import_library(SourceSpec(source_id=source_id, path=str(FIXTURES / sub)),
                       root=root)


@pytest.fixture()
def library(data_root):
    _seed(data_root)
    return data_root


def _component(part_id: str, *, footprint_id: str = "", reference: str = "U9",
               on_board: bool = True) -> Component:
    part = parts.resolve_part(part_id)
    footprint = parts.resolve_footprint(footprint_id) if footprint_id else None
    mapping = parts.default_pin_pad_map(part.pins, footprint)
    return Component(
        id=f"component_{reference.lower()}", reference=reference,
        part_id=part_id, symbol_id=part.symbol_id, footprint_id=footprint_id,
        pin_pad_map=mapping, on_board=on_board,
        placement=Placement(x_nm=5_000_000, y_nm=5_000_000))


def test_symbol_footprint_and_part_are_separate(library):
    part = parts.resolve_part("sym:fx:Device:DerivedOpAmp")
    assert part.symbol_id == "sym:fx:Device:DerivedOpAmp"
    assert part.footprint_id == ""
    assert part.units == (1, 2)
    assert {pin.number for pin in part.pins} == {"A1", "A2", "B1", "B2"}
    assert all(isinstance(pin.number, str) for pin in part.pins)

    symbol = parts.resolve_symbol("sym:fx:Device:DerivedOpAmp")
    assert symbol.body_w_nm > 0 and symbol.body_h_nm > 0
    assert {pin.number for pin in symbol.pins} >= {"A1", "B1"}

    footprint = parts.resolve_footprint("fp:fxfp:MyParts:R_0603")
    assert sorted(pad.number for pad in footprint.pads) == ["1", "2"]
    assert footprint.courtyard_approximate is True
    assert footprint.mounting == "smd"

    resistor = parts.resolve_part("sym:fx:Device:R")
    mapping = parts.default_pin_pad_map(resistor.pins, footprint)
    assert mapping == {"1": ["1"], "2": ["2"]}


def test_multiple_geometries_with_the_same_pad_number_are_kept(library):
    from mota.generation.placement import pads_for

    footprint = parts.resolve_footprint("fp:stack:MyParts:StackedPad")
    pad_one = [pad for pad in footprint.pads if pad.number == "1"]
    assert len(pad_one) == 2

    part = parts.resolve_part("sym:fx:Device:R")
    mapping = parts.default_pin_pad_map(part.pins, footprint)
    assert mapping["1"] == ["1"]
    component = Component(
        id="component_r9", reference="R9", part_id="sym:fx:Device:R",
        symbol_id=part.symbol_id, footprint_id="fp:stack:MyParts:StackedPad",
        pin_pad_map=mapping, placement=Placement(x_nm=5_000_000, y_nm=5_000_000))
    from mota.domain.models import CircuitIR, Net

    circuit = CircuitIR(components=[component], nets=[
        Net(id="net_vin", name="VIN", pins=["component_r9:1"], net_class="power")])
    pads = pads_for(circuit, "component_r9")
    pad_one_board = [pad for pad in pads if pad.number == "1"]
    assert len(pad_one_board) == 2
    assert all(pad.net_id == "net_vin" for pad in pad_one_board)


def test_missing_and_wrong_footprints_are_reported(library):
    missing = _component("sym:nc:NC:NCPart", reference="U8")
    codes = {issue.code for issue in parts.validate_component_mapping(missing)}
    assert "FOOTPRINT_MISSING" in codes

    wrong = _component("sym:nc:NC:NCPart",
                       footprint_id="fp:fxfp:MyParts:EmptyGraphic",
                       reference="U7")
    codes = {issue.code for issue in parts.validate_component_mapping(wrong)}
    assert "PIN_UNMAPPED" in codes

    footprint = parts.resolve_footprint("fp:stack:MyParts:StackedPad")
    pin = parts.resolve_part("sym:nc:NC:NCPart").pins[0]
    mapping, issues = parts.normalize_mapping(
        [pin], footprint, {pin.number: ["99"]}, component_id="component_u7")
    assert mapping[pin.number] == ["99"]
    assert {issue.code for issue in issues} == {"PAD_NOT_FOUND"}


def test_legal_non_board_symbol_does_not_require_a_footprint(library):
    part = parts.resolve_part("sym:nc:NC:PWR_FLAG")
    assert part.on_board is False
    component = _component("sym:nc:NC:PWR_FLAG", reference="#PWR1", on_board=False)
    assert parts.validate_component_mapping(component) == []
    report = parts.mapping_report(component)
    assert report and all(row.status == "ok" for row in report)


def test_three_no_connect_kinds_are_distinct(library):
    from mota.domain.models import CircuitIR, Net

    component = _component("sym:nc:NC:NCPart",
                           footprint_id="fp:nc:MyParts:NCPart", reference="U6")
    circuit = CircuitIR(components=[component], nets=[])
    kinds = parts.no_connect_kinds(circuit, component)
    assert kinds == {"1": "ordinary", "2": "part_defined"}

    circuit.no_connects = ["component_u6:1"]
    assert parts.no_connect_kinds(circuit, component)["1"] == "explicit"

    circuit.no_connects = []
    circuit.nets = [Net(id="net_sig", name="SIG", pins=["component_u6:1"])]
    assert parts.no_connect_kinds(circuit, component)["1"] == "connected"

    # A part-defined NC that is wired is an error, not silently accepted.
    circuit.nets[0].pins.append("component_u6:2")
    issues = parts.mapping_issues_for_circuit(circuit)
    assert any(issue.code == "NC_PIN_CONNECTED" and issue.pin == "2"
               for issue in issues)


def test_component_instance_v2_keeps_physical_identity(library):
    component = _component("sym:nc:NC:NCPart",
                           footprint_id="fp:nc:MyParts:NCPart", reference="U5")
    component.instance_path = "CH1/U5"
    component.assembly_state = "dnp"
    from mota.domain.models import CircuitIR

    bundle = parts.component_instances(CircuitIR(components=[component]))
    instance = bundle.instances[0]
    assert instance.component_id == component.id
    assert instance.instance_path == "CH1/U5"
    assert instance.assembly_state == "dnp"
    assert instance.symbol_units == [1]
    assert all(row.status == "ok" for row in bundle.mappings)
