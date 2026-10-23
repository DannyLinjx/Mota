from __future__ import annotations

from pathlib import Path

from mota.circuit.compiler import compile_circuit
from mota.circuit.hierarchy import UnitViewSpec
from mota.ir.models_v2 import MultiPageSchematicV2
from mota.library.catalog import SourceSpec, import_library
from mota.schematic.layout import locked_component_positions, roundtrip_nets
from tests.circuit.test_hierarchy import _project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "library"


def _seed(data_root) -> None:
    import_library(SourceSpec(source_id="fx", path=str(FIXTURES / "packed")),
                   root=data_root)


def test_exported_pages_reparse_to_the_same_netlist(data_root):
    _seed(data_root)
    result = compile_circuit(_project(16, unit_views=(
        UnitViewSpec("TOP/GROUP1/CH", "U_DUAL", 1),
        UnitViewSpec("TOP/GROUP1/CH", "U_DUAL", 2, page_id="page_top"),
    )))
    expected = {net.name: set(net.pins) for net in result.circuit.nets if net.pins}
    parsed = roundtrip_nets(result.schematic)
    assert parsed == expected

    exported = result.schematic.model_dump_json()
    restored = MultiPageSchematicV2.model_validate_json(exported)
    assert roundtrip_nets(restored) == expected
    assert len(restored.pages) == 20
    assert sum(1 for page in restored.pages if page.ports) >= 16


def test_locked_pages_do_not_move_when_new_pages_are_added(data_root):
    _seed(data_root)
    small = compile_circuit(_project(2))
    page_id = next(row["page_id"] for row in small.page_records
                   if row["instance_path"].endswith("/CH"))
    locked = locked_component_positions(small.schematic, page_id)
    assert locked

    bigger = compile_circuit(_project(3, locked_pages={page_id: locked}))
    after = locked_component_positions(bigger.schematic, page_id)
    assert after == locked
    assert len(bigger.schematic.pages) == 7
    assert any(page.page_id.endswith("group3_ch")
               for page in bigger.schematic.pages)
    assert roundtrip_nets(bigger.schematic) == {
        net.name: set(net.pins) for net in bigger.circuit.nets if net.pins}
