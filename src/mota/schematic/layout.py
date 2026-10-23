"""Deterministic multi-page schematic layout for hierarchical circuits.

Every page places its own components on a fixed grid, emits one wire stub and
one scoped net label per pin, and records page ports. Locked pages keep their
existing component coordinates; new pages are laid out without touching them.
"""

from __future__ import annotations

from collections import defaultdict

from mota.circuit import parts
from mota.domain.models import CircuitIR
from mota.domain.units import mm
from mota.errors import NotFound
from mota.ir.models_v2 import (
    MultiPageSchematicV2, SchematicPageV2, SymbolUnitViewV2,
)

PAGE_W = mm(297.0)
PAGE_H = mm(210.0)
ORIGIN_X = mm(28.0)
ORIGIN_Y = mm(38.0)
COL_W = mm(52.0)
ROW_H = mm(44.0)
COLUMNS = 4
STUB = mm(2.54)
WIRE_WIDTH = mm(0.1524)


def layout_hierarchy(circuit: CircuitIR, page_records: list[dict],
                     page_by_component: dict[str, str],
                     unit_views: list[dict],
                     locked_pages: dict[str, dict[str, tuple[int, int]]] | None = None,
                     title: str = "Mota hierarchy") -> MultiPageSchematicV2:
    locked_pages = locked_pages or {}
    components = {component.id: component for component in circuit.components}
    pin_net: dict[str, tuple[str, str]] = {}
    for net in circuit.nets:
        for token in net.pins:
            pin_net[token] = (net.id, net.name)

    explicit_views: dict[str, list[dict]] = defaultdict(list)
    for view in unit_views:
        explicit_views[view["component_id"]].append(view)

    placements: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for component in circuit.components:
        views = explicit_views.get(component.id)
        if views:
            for view in views:
                placements[view["page_id"]].append(
                    (component.id, int(view["unit"]), view["page_id"]))
        else:
            page_id = page_by_component.get(component.id, "page_TOP")
            placements[page_id].append((component.id, 0, page_id))

    pages: list[SchematicPageV2] = []
    symbol_views: list[SymbolUnitViewV2] = []
    wire_seq = 0
    for page_record in sorted(page_records, key=lambda row: row["page_index"]):
        page_id = page_record["page_id"]
        page = SchematicPageV2(
            page_id=page_id, title=f"{page_record['title']} · "
                                   f"{page_record['instance_path']}",
            sheet_instance_id=page_id,
            instance_path=page_record["instance_path"],
            page_index=page_record["page_index"],
            locked=page_id in locked_pages,
            ports=list(page_record.get("ports", [])))
        locked = locked_pages.get(page_id, {})
        index = 0
        for component_id, unit, _ in placements.get(page_id, []):
            component = components.get(component_id)
            if component is None:
                continue
            try:
                symbol_def = parts.resolve_symbol(component.symbol_id)
                part = parts.resolve_part(component.part_id)
            except NotFound:
                continue
            row, col = divmod(index, COLUMNS)
            x = ORIGIN_X + col * COL_W
            y = PAGE_H - ORIGIN_Y - row * ROW_H
            if component_id in locked:
                x, y = locked[component_id]
            body_w, body_h = symbol_def.body_w_nm, symbol_def.body_h_nm
            anchors: dict[str, list[int]] = {}
            for pin in symbol_def.pins:
                pin_unit = part.pin_units.get(pin.number, 1)
                if unit and pin_unit not in (0, unit):
                    continue
                anchor = [x + pin.x_nm, y + pin.y_nm]
                anchors[pin.number] = anchor
                token = f"{component.id}:{pin.number}"
                entry = pin_net.get(token)
                if entry is None:
                    if token not in circuit.no_connects:
                        continue
                    net_id, net_name = "__nc__", "NC"
                else:
                    net_id, net_name = entry
                ex, ey = _outward(anchor, pin.direction, STUB)
                wire_seq += 1
                page.wires.append({
                    "id": f"wire_{page_id}_{wire_seq:05d}", "net_id": net_id,
                    "points": [anchor, [ex, ey]], "width_nm": WIRE_WIDTH})
                page.labels.append({
                    "net_id": net_id, "text": net_name,
                    "x_nm": ex, "y_nm": ey, "rotation_deg": 0,
                    "justify": "left"})
            page.symbols.append({
                "component_id": component.id, "reference": component.reference,
                "symbol_id": component.symbol_id, "value": component.value,
                "unit_id": unit or 1, "body_style_id": 1,
                "x_nm": x, "y_nm": y,
                "body_w_nm": body_w, "body_h_nm": body_h,
                "pin_anchors": anchors})
            if unit:
                symbol_views.append(SymbolUnitViewV2(
                    component_id=component.id, sheet_instance_id=page_id,
                    unit_id=unit, x_nm=x, y_nm=y))
            index += 1
        pages.append(page)
    return MultiPageSchematicV2(
        page_w_nm=PAGE_W, page_h_nm=PAGE_H, pages=pages,
        unit_views=symbol_views, source="hierarchy")


def roundtrip_nets(schematic: MultiPageSchematicV2) -> dict[str, set[str]]:
    """Reconstruct nets from placed wires, anchors and label text.

    Wires are unioned only through coincident endpoints; labels then merge the
    same net name across pages (global nets), while scoped names stay distinct.
    """
    out: dict[str, set[str]] = defaultdict(set)
    for page in schematic.pages:
        parent: dict[str, str] = {}

        def find(item: str) -> str:
            parent.setdefault(item, item)
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        endpoint_owner: dict[tuple[int, int], str] = {}
        for wire in page.wires:
            find(wire["id"])
            for point in (wire["points"][0], wire["points"][-1]):
                key = (point[0], point[1])
                if key in endpoint_owner:
                    union(wire["id"], endpoint_owner[key])
                else:
                    endpoint_owner[key] = wire["id"]
        group_labels: dict[str, str] = {}
        for label in page.labels:
            for wire in page.wires:
                if wire["net_id"] != label["net_id"]:
                    continue
                group_labels.setdefault(find(wire["id"]), label["text"])
        anchor_owner: dict[tuple[int, int], str] = {
            (wire["points"][0][0], wire["points"][0][1]): wire["id"]
            for wire in page.wires}
        for symbol in page.symbols:
            for pin, anchor in symbol["pin_anchors"].items():
                wire_id = anchor_owner.get((anchor[0], anchor[1]))
                if wire_id is None:
                    continue
                text = group_labels.get(find(wire_id))
                if text and text != "NC":
                    out[text].add(f"{symbol['component_id']}:{pin}")
    return dict(out)


def locked_component_positions(schematic: MultiPageSchematicV2,
                               page_id: str) -> dict[str, tuple[int, int]]:
    page = next((row for row in schematic.pages if row.page_id == page_id), None)
    if page is None:
        return {}
    return {symbol["component_id"]: (symbol["x_nm"], symbol["y_nm"])
            for symbol in page.symbols}


def _outward(anchor: list[int], direction: str, length: int) -> tuple[int, int]:
    x, y = anchor
    if direction == "left":
        return x - length, y
    if direction == "right":
        return x + length, y
    if direction == "up":
        return x, y + length
    return x, y - length
