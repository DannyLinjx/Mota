from __future__ import annotations

from pathlib import Path

import pytest

from mota.circuit.hierarchy import (
    ComponentDef, HierarchyProject, InstanceDef, ModuleDef, NetDef, PortDef,
    UnitViewSpec, compile_hierarchy,
)
from mota.circuit.compiler import compile_circuit, compile_report
from mota.library.catalog import SourceSpec, import_library

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "library"


def _definitions(channels: int = 16) -> dict[str, ModuleDef]:
    leaf = ModuleDef(
        id="leaf", ports={"IN": PortDef("IN", "input"),
                          "OUT": PortDef("OUT", "output")},
        components=(ComponentDef("R_L", "r_10k"),),
        nets=(NetDef("sig", ("PORT:IN", "R_L:1")),
              NetDef("out", ("PORT:OUT", "R_L:2"))))
    block = ModuleDef(
        id="block", ports={"IN": PortDef("IN", "input"),
                           "OUT": PortDef("OUT", "output")},
        components=(ComponentDef("R_B", "r_10k"),),
        nets=(NetDef("sig", ("PORT:IN", "R_B:1")),
              NetDef("out", ("PORT:OUT", "R_B:2"))),
        instances=(InstanceDef("LEAF", "leaf",
                               {"IN": "NET:sig", "OUT": "NET:out"},
                               same_page=True),))
    channel = ModuleDef(
        id="channel", ports={"IN": PortDef("IN", "input"),
                             "OUT": PortDef("OUT", "output")},
        components=(ComponentDef("R_C", "r_10k"),
                    ComponentDef("R_G", "r_10k"),
                    ComponentDef("U_DUAL", "sym:fx:Device:DualAmp")),
        nets=(NetDef("sig", ("PORT:IN", "R_C:1")),
              NetDef("out", ("PORT:OUT", "R_C:2")),
              NetDef("gnd", ("R_G:2",), net_class="ground",
                     global_name="GND")),
        instances=(InstanceDef("BLOCK", "block",
                               {"IN": "NET:sig", "OUT": "NET:out"},
                               same_page=True),))
    group = ModuleDef(
        id="group", ports={"IN": PortDef("IN", "input"),
                           "OUT": PortDef("OUT", "output")},
        instances=(InstanceDef("CH", "channel",
                               {"IN": "PORT:IN", "OUT": "PORT:OUT"}),))
    monitor = ModuleDef(
        id="monitor", components=(ComponentDef("R_M", "r_10k"),),
        nets=(NetDef("mon", ("R_M:1",), net_class="power",
                     global_name="MON"),))
    bus_members = tuple(str(i) for i in range(channels))
    top_components = tuple(ComponentDef(f"RB{i}", "r_10k")
                           for i in range(channels))
    top_nets = []
    for index in range(channels):
        top_nets.append(NetDef(f"in_{index}", (f"RB{index}:1",),
                               bus_member=f"DIN[{index}]"))
        top_nets.append(NetDef(f"out_{index}", (f"RB{index}:2",),
                               bus_member=f"DOUT[{index}]"))
    top_instances = [
        InstanceDef(f"GROUP{index + 1}", "group",
                    {"IN": f"BUS:DIN[{index}]", "OUT": f"BUS:DOUT[{index}]"},
                    same_page=True)
        for index in range(channels)
    ] + [InstanceDef(f"MON{index + 1}", "monitor") for index in range(3)]
    top = ModuleDef(
        id="top", components=top_components, nets=tuple(top_nets),
        instances=tuple(top_instances),
        buses={"din": bus_members, "dout": bus_members},
        bus_aliases={"DIN": "din", "DOUT": "dout"})
    return {"top": top, "group": group, "channel": channel, "block": block,
            "leaf": leaf, "monitor": monitor}


def _project(channels: int = 16, **kwargs) -> HierarchyProject:
    return HierarchyProject(definitions=_definitions(channels),
                            root_definition_id="top", **kwargs)


@pytest.fixture()
def seeded(data_root):
    import_library(SourceSpec(source_id="fx", path=str(FIXTURES / "packed")),
                   root=data_root)
    return data_root


def test_sixteen_channels_stay_isolated_and_pages_are_stable(seeded):
    project = _project(16, unit_views=(
        UnitViewSpec("TOP/GROUP1/CH", "U_DUAL", 1),
        UnitViewSpec("TOP/GROUP1/CH", "U_DUAL", 2, page_id="page_top"),
    ))
    result = compile_circuit(project)
    assert [diag.code for diag in result.diagnostics] == []
    assert len(result.page_records) == 20
    depths = {path.count("/") + 1 for path in result.instance_paths.values()}
    assert max(depths) == 5

    circuit = result.circuit
    assert len(circuit.components) == 99
    din_nets = {net.name: net for net in circuit.nets
                if net.name.startswith("TOP/din[")}
    assert len(din_nets) == 16
    for index in range(16):
        net = din_nets[f"TOP/din[{index}]"]
        assert len(net.pins) == 4
        child_paths = {result.instance_paths[pin.split(":")[0]]
                       for pin in net.pins
                       if result.instance_paths.get(pin.split(":")[0], "").count("/") == 2}
        assert child_paths == {f"TOP/GROUP{index + 1}/CH"}
    assert len({tuple(sorted(net.pins)) for net in din_nets.values()}) == 16

    gnd = next(net for net in circuit.nets if net.name == "GND")
    assert len(gnd.pins) == 16
    channel_scope = result.instance_paths[
        next(c.id for c in circuit.components
             if c.reference == "R_C" and c.instance_path.endswith("/CH"))]
    assert channel_scope.startswith("TOP/GROUP1/CH")

    views = result.schematic.unit_views
    dual = [view for view in views if "udual" in view.component_id]
    assert len(dual) == 2
    assert len({view.sheet_instance_id for view in dual}) == 2
    top_page = next(page for page in result.schematic.pages
                    if page.page_id == "page_top")
    assert any(symbol["reference"] == "RB0" for symbol in top_page.symbols)
    report = compile_report(result)
    assert "pages: 20" in report
    assert "`page_top_group1_ch`" in report


def test_missing_port_and_cycle_are_reported(seeded):
    definitions = _definitions(1)
    definitions["top"] = ModuleDef(
        id="top", instances=(InstanceDef("GROUP1", "group",
                                         {"IN": "BUS:DIN[0]"}),),
        buses={"din": ("0",), "dout": ("0",)},
        bus_aliases={"DIN": "din", "DOUT": "dout"})
    broken = HierarchyProject(definitions=definitions, root_definition_id="top")
    result = compile_hierarchy(broken)
    assert "PORT_UNBOUND" in {diag.code for diag in result.diagnostics}

    cyclic = {
        "A": ModuleDef(id="A", ports={"P": PortDef("P")},
                       instances=(InstanceDef("B_INST", "B"),)),
        "B": ModuleDef(id="B", ports={"P": PortDef("P")},
                       instances=(InstanceDef("A_INST", "A"),)),
    }
    result = compile_hierarchy(HierarchyProject(definitions=cyclic,
                                                root_definition_id="A",
                                                root_name="ROOT"))
    assert "HIERARCHY_CYCLE" in {diag.code for diag in result.diagnostics}


def test_bus_alias_members_are_not_short_circuited(seeded):
    result = compile_circuit(_project(4))
    circuit = result.circuit
    by_name = {net.name: net for net in circuit.nets}
    members = [by_name[f"TOP/din[{index}]"] for index in range(4)]
    all_pins = [set(net.pins) for net in members]
    assert len({tuple(sorted(pins)) for pins in all_pins}) == 4
    for index, pins in enumerate(all_pins):
        assert any("__rc" in pin for pin in pins)
    assert all(not (a & b) for i, a in enumerate(all_pins) for b in all_pins[i + 1:])
