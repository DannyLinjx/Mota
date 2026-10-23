"""Schematic synthesis: symbols, pin anchors, orthogonal stubs, net labels.

Connectivity in the schematic is explicit: every pin owns a wire stub carrying
its net id, and a net label with the canonical net name. Where two stub ends in
the same net can be joined by a clear Manhattan route, a real wire is added.
Junctions are placed only where three or more wire ends meet.
"""

from __future__ import annotations

import math
from collections import Counter

from mota.circuit.parts import resolve_part, resolve_symbol
from mota.domain.models import (
    CircuitIR, NetLabel, SchematicIR, SchematicJunction, SchematicWire,
    SymbolInstance,
)
from mota.domain.units import mm
from mota.errors import NotFound
from mota.schematic.geometry import SHEETS, STUB_MM, Extent, symbol_local_extent, usable_region

STUB = mm(STUB_MM)
LABEL_OFFSET = mm(0.9)
WIRE_WIDTH = mm(0.1524)
ITEM_GAP_MM = 8.0
ROW_GAP_MM = 8.0
GROUP_GAP_MM = 12.0


def _pin_outward(anchor: tuple[int, int], direction: str, length: int) -> tuple[int, int]:
    x, y = anchor
    if direction == "left":
        return x - length, y
    if direction == "right":
        return x + length, y
    if direction == "up":
        return x, y + length
    return x, y - length


def _seg_clear(seg, excluded_components, symbols_by_component) -> bool:
    """Reject routes crossing a symbol body other than the two pin owners."""
    x1, y1 = seg[0] / 1e6, seg[1] / 1e6
    x2, y2 = seg[2] / 1e6, seg[3] / 1e6
    for cid, sym in symbols_by_component.items():
        if cid in excluded_components:
            continue
        bx1 = (sym.x_nm - sym.body_w_nm / 2) / 1e6 - 0.6
        bx2 = (sym.x_nm + sym.body_w_nm / 2) / 1e6 + 0.6
        by1 = (sym.y_nm - sym.body_h_nm / 2) / 1e6 - 0.6
        by2 = (sym.y_nm + sym.body_h_nm / 2) / 1e6 + 0.6
        # conservative bounding-box crossing test
        sx1, sx2 = min(x1, x2), max(x1, x2)
        sy1, sy2 = min(y1, y2), max(y1, y2)
        if sx2 < bx1 or sx1 > bx2 or sy2 < by1 or sy1 > by2:
            continue
        return False
    return True


GROUP_ORDER = ("Power", "MCU", "Analog inputs", "RS-485", "Debug interface",
               "Load outputs", "EEPROM / I2C", "Other")


def functional_group(part, net_names: set[str]) -> str:
    """Assign one component to a functional block from its real connectivity."""
    if part is not None and len(part.pins) >= 16:
        return "MCU"
    if net_names & {"BOOT0", "NRST"}:
        return "MCU"
    if any(name.startswith("AIN") for name in net_names):
        return "Analog inputs"
    if any(name.startswith("RS485") for name in net_names):
        return "RS-485"
    if net_names & {"UART_TX", "UART_RX", "SWDIO", "SWCLK"}:
        return "Debug interface"
    if any(name.startswith("MOSFET") for name in net_names):
        return "Load outputs"
    if any(name.startswith("I2C") for name in net_names):
        return "EEPROM / I2C"
    if net_names and net_names <= {"GND", "VCC_3V3", "VCC_5V"}:
        return "Power"
    return "Other"


def _natural_key(reference: str) -> tuple[str, int]:
    prefix = "".join(ch for ch in reference if ch.isalpha())
    digits = "".join(ch for ch in reference if ch.isdigit())
    return prefix, int(digits or 0)


def _pack(groups: list[tuple[str, list[tuple[str, Extent]]]],
          page_w: float, page_h: float) -> dict[str, tuple[float, float]] | None:
    """Pack whole functional blocks (shelf of blocks) inside the usable sheet.

    Each block is laid out internally from its own complete extents, then the
    blocks are placed left-to-right, wrapping only when the sheet is full.
    """
    left, bottom, right, top = usable_region(page_w, page_h)
    available_w = right - left
    available_h = top - bottom
    block_limit = min(available_w, 200.0)

    blocks: list[tuple[dict[str, tuple[float, float, Extent]], float, float]] = []
    for _group, items in groups:
        x = 0.0
        y = 0.0
        row_h = 0.0
        width = 0.0
        local: dict[str, tuple[float, float, Extent]] = {}
        for component, extent in items:
            if extent.w > block_limit or extent.h > available_h:
                return None
            if x > 0 and x + extent.w > block_limit:
                x = 0.0
                y += row_h + ROW_GAP_MM
                row_h = 0.0
            local[component] = (x, y, extent)
            x += extent.w + ITEM_GAP_MM
            row_h = max(row_h, extent.h)
            width = max(width, x - ITEM_GAP_MM)
        blocks.append((local, width, y + row_h))

    placed: dict[str, tuple[float, float]] = {}
    cursor_x = left
    cursor_top = top
    shelf_h = 0.0
    for local, block_w, block_h in blocks:
        if cursor_x > left and cursor_x + block_w > right:
            cursor_top -= shelf_h + GROUP_GAP_MM
            cursor_x = left
            shelf_h = 0.0
        if cursor_top - block_h < bottom:
            return None
        for component, (bx, by, extent) in local.items():
            placed[component] = (cursor_x + bx - extent.x0,
                                 cursor_top - by - extent.y1)
        cursor_x += block_w + GROUP_GAP_MM
        shelf_h = max(shelf_h, block_h)
    return placed


def layout_symbols(circuit: CircuitIR) -> tuple[dict[str, tuple[float, float]],
                                               float, float, str]:
    """Place every symbol by functional group using its complete extent.

    Returns positions in millimetres plus the chosen sheet. The smallest sheet
    that fits every body, pin, reference/value text and net label is selected.
    """
    net_names_by_component: dict[str, set[str]] = {c.id: set() for c in circuit.components}
    pin_nets: dict[str, dict[str, str]] = {c.id: {} for c in circuit.components}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            if cid in net_names_by_component:
                net_names_by_component[cid].add(net.name)
                pin_nets[cid][pin] = net.name

    buckets: dict[str, list[tuple[str, Extent]]] = {group: [] for group in GROUP_ORDER}
    for comp in circuit.components:
        try:
            symbol_def = resolve_symbol(comp.symbol_id)
            part = resolve_part(comp.part_id)
        except NotFound:
            continue
        extent = symbol_local_extent(part, symbol_def, comp.reference, comp.value,
                                     pin_nets[comp.id])
        group = functional_group(part, net_names_by_component[comp.id])
        buckets.setdefault(group, []).append((comp.id, extent))

    groups = [(group, sorted(buckets.get(group, []),
                             key=lambda row: _natural_key(_reference_of(circuit, row[0]))))
              for group in GROUP_ORDER]
    groups = [row for row in groups if row[1]]

    for page_w, page_h, name in SHEETS:
        placed = _pack(groups, page_w, page_h)
        if placed is not None:
            return placed, page_w, page_h, name
    page_w, page_h, name = SHEETS[-1]
    placed = _pack(groups, page_w, page_h) or {}
    return placed, page_w, page_h, name


def _reference_of(circuit: CircuitIR, component: str) -> str:
    row = next((c for c in circuit.components if c.id == component), None)
    return row.reference if row else component


def generate_schematic(circuit: CircuitIR, title: str = "Mota schematic") -> SchematicIR:
    positions, page_w_mm, page_h_mm, _sheet = layout_symbols(circuit)
    ordered = sorted(circuit.components, key=lambda c: _natural_key(c.reference))
    symbols: dict[str, SymbolInstance] = {}
    for comp in ordered:
        point = positions.get(comp.id)
        if point is None:
            continue
        try:
            symbol_def = resolve_symbol(comp.symbol_id)
        except NotFound:
            continue
        x = mm(point[0])
        y = mm(point[1])
        anchors: dict[str, list[int]] = {}
        for pin in symbol_def.pins:
            ax = x + pin.x_nm
            ay = y + pin.y_nm
            anchors[pin.number] = [ax, ay]
        symbols[comp.id] = SymbolInstance(
            component_id=comp.id, symbol_id=comp.symbol_id, reference=comp.reference,
            value=comp.value, x_nm=x, y_nm=y, rotation_deg=0,
            body_w_nm=symbol_def.body_w_nm, body_h_nm=symbol_def.body_h_nm,
            pin_anchors=anchors)

    pins_by_key: dict[str, tuple[str, str]] = {}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            pins_by_key[token] = (net.id, pin)

    wires: list[SchematicWire] = []
    labels: list[NetLabel] = []
    no_connect_points: list[list[int]] = []
    wire_counter = 0

    def add_wire(net_id: str, points: list[list[int]]) -> SchematicWire:
        nonlocal wire_counter
        wire_counter += 1
        wire = SchematicWire(id=f"wire_{wire_counter:04d}", net_id=net_id,
                             points=points, width_nm=WIRE_WIDTH)
        wires.append(wire)
        return wire

    stub_ends: dict[str, list[tuple[str, tuple[int, int], str]]] = {}
    for net in circuit.nets:
        stub_ends[net.id] = []

    for comp in ordered:
        sym = symbols.get(comp.id)
        if sym is None:
            continue
        try:
            symbol_def = resolve_symbol(comp.symbol_id)
        except NotFound:
            continue
        for pin in symbol_def.pins:
            key = f"{comp.id}:{pin.number}"
            net_entry = pins_by_key.get(key)
            if net_entry is None:
                if key in circuit.no_connects:
                    ax, ay = sym.pin_anchors[pin.number]
                    ex, ey = _pin_outward((ax, ay), pin.direction, STUB)
                    add_wire("__nc__", [[ax, ay], [ex, ey]])
                    no_connect_points.append([ex, ey])
                continue
            net_id, _ = net_entry
            ax, ay = sym.pin_anchors[pin.number]
            ex, ey = _pin_outward((ax, ay), pin.direction, STUB)
            add_wire(net_id, [[ax, ay], [ex, ey]])
            stub_ends[net_id].append((comp.id, (ex, ey), pin.direction))
            net_name = next(n.name for n in circuit.nets if n.id == net_id)
            lx, ly = ex, ey
            if pin.direction == "left":
                lx -= LABEL_OFFSET
            elif pin.direction == "right":
                lx += LABEL_OFFSET
            elif pin.direction == "up":
                ly += LABEL_OFFSET
            else:
                ly -= LABEL_OFFSET
            labels.append(NetLabel(net_id=net_id, text=net_name, x_nm=lx, y_nm=ly,
                                   rotation_deg=90 if pin.direction in ("up", "down") else 0,
                                   justify="right" if pin.direction == "left" else "left"))

    # Join nearby stubs of the same net with an explicit Manhattan route when it
    # does not cross a symbol body.
    for net in circuit.nets:
        ends = stub_ends.get(net.id, [])
        if len(ends) < 2:
            continue
        connected: list[tuple[str, tuple[int, int], str]] = [ends[0]]
        remaining = ends[1:]
        for entry in list(remaining):
            cid_a, (ax, ay), _ = connected[0]
            cid_b, (bx, by), _ = entry
            if abs(ax - bx) < mm(30.0) and abs(ay - by) < mm(30.0):
                mid_y = ay
                seg1 = [ax, ay, bx, mid_y]
                seg2 = [bx, mid_y, bx, by]
                if (_seg_clear(seg1, {cid_a, cid_b}, symbols)
                        and _seg_clear(seg2, {cid_a, cid_b}, symbols)):
                    add_wire(net.id, [[ax, ay], [bx, mid_y]])
                    if abs(ay - by) > 1:
                        add_wire(net.id, [[bx, mid_y], [bx, by]])
                    connected.append(entry)
                    remaining.remove(entry)
        # Remaining pins stay connected through their unique net labels.

    endpoint_counter: Counter[tuple[int, int, str]] = Counter()
    for wire in wires:
        if wire.net_id == "__nc__":
            continue
        for point in (wire.points[0], wire.points[-1]):
            endpoint_counter[(point[0], point[1], wire.net_id)] += 1
    junctions: list[SchematicJunction] = []
    seen = set()
    for (x, y, net_id), count in endpoint_counter.items():
        if count >= 3 and (x, y, net_id) not in seen:
            seen.add((x, y, net_id))
            junctions.append(SchematicJunction(x_nm=x, y_nm=y, net_id=net_id))

    return SchematicIR(
        page_w_nm=mm(page_w_mm), page_h_nm=mm(page_h_mm), snap_nm=mm(1.27),
        symbols=list(symbols.values()), wires=wires, junctions=junctions,
        labels=labels, no_connect_pins=list(circuit.no_connects),
        no_connects=no_connect_points, title=title)


def schematic_pin_attachment(schematic: SchematicIR) -> tuple[dict[str, list[tuple[str, int, int]]], list[str]]:
    """Return pin_key -> wire endpoints attached to the pin anchor, plus errors."""
    endpoint_index: dict[tuple[int, int], list[SchematicWire]] = {}
    for wire in schematic.wires:
        endpoint_index.setdefault((wire.points[0][0], wire.points[0][1]), []).append(wire)
        endpoint_index.setdefault((wire.points[-1][0], wire.points[-1][1]), []).append(wire)
    attachments: dict[str, list[tuple[str, int, int]]] = {}
    missing: list[str] = []
    for sym in schematic.symbols:
        for pin, (x, y) in sym.pin_anchors.items():
            key = f"{sym.component_id}:{pin}"
            rows = endpoint_index.get((x, y), [])
            if not rows:
                missing.append(key)
                continue
            attachments[key] = [(w.net_id, x, y) for w in rows]
    return attachments, missing


def wire_graph_nets(schematic: SchematicIR) -> dict[str, set[str]]:
    """Group wire ids by electrical connectivity of coincident endpoints."""
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

    point_owner: dict[tuple[int, int], str] = {}
    for wire in schematic.wires:
        if wire.net_id == "__nc__":
            continue
        find(wire.id)
        for point in (wire.points[0], wire.points[-1]):
            key = (point[0], point[1])
            if key in point_owner:
                union(wire.id, point_owner[key])
            else:
                point_owner[key] = wire.id
    groups: dict[str, set[str]] = {}
    net_by_wire = {w.id: w.net_id for w in schematic.wires}
    for wire in schematic.wires:
        if wire.net_id == "__nc__":
            continue
        root = find(wire.id)
        groups.setdefault(root, set()).add(net_by_wire[wire.id])
    return groups
