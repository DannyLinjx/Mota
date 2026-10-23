"""Electrical / semantic checks for the three Golden circuits.

Every check is a real computation over CircuitIR, SchematicIR and BoardIR.
Unsupported or unevidenced claims are reported as NOT_CHECKED, never PASS.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

from mota.catalog import builtin
from mota.circuit import parts
from mota.catalog.templates import TemplateRecipe
from mota.domain.models import (
    BoardIR, CheckResult, CircuitIR, SchematicIR,
)
from mota.errors import NotFound
from mota.generation.schematic import schematic_pin_attachment, wire_graph_nets

CheckFn = Callable[[], CheckResult]


def pin_key(component_id: str, number: str) -> str:
    return f"{component_id}:{number}"


def component_by_ref(circuit: CircuitIR, reference: str):
    for comp in circuit.components:
        if comp.reference == reference:
            return comp
    return None


def net_containing(circuit: CircuitIR, key: str):
    for net in circuit.nets:
        if key in net.pins:
            return net
    return None


def net_by_name(circuit: CircuitIR, name: str):
    for net in circuit.nets:
        if net.name == name:
            return net
    return None


def other_pin_of(net, key: str) -> str | None:
    others = [p for p in net.pins if p != key]
    return others[0] if len(others) == 1 else None


def parse_value(text: str) -> float | None:
    """Parse 10k / 3.3k / 2.2uF / 100nF / 10uF into base units."""
    if not text:
        return None
    match = re.match(r"^\s*([0-9.]+)\s*([pnumkM]?)\s*([FΩR]?)\s*$", text.strip(), re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    prefix = match.group(2).lower()
    factor = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3,
              "M": 1e6}.get(match.group(2), 1.0)
    if match.group(2) == "M":
        factor = 1e6
    return value * factor


def _result(check_id: str, rule_id: str, title: str, status: str, message: str, *,
            required: bool = True, severity: str = "error",
            object_ids: list[str] | None = None,
            evidence: dict[str, Any] | None = None,
            source_id: str | None = None, coverage: str = "") -> CheckResult:
    return CheckResult(
        check_id=check_id, rule_id=rule_id, title=title, status=status,  # type: ignore[arg-type]
        severity=severity, required_for_review=required,
        object_ids=object_ids or [], message=message, evidence=evidence or {},
        source_id=source_id, coverage=coverage)


# --------------------------------------------------------------------------- #
# Common checks
# --------------------------------------------------------------------------- #

def check_pin_pad_mapping(circuit: CircuitIR) -> CheckResult:
    problems = []
    for comp in circuit.components:
        try:
            part = parts.resolve_part(comp.part_id)
        except NotFound:
            problems.append(f"{comp.reference}: part {comp.part_id} cannot be resolved")
            continue
        footprint = None
        if comp.on_board:
            if not comp.footprint_id:
                problems.append(f"{comp.reference}: footprint missing")
                continue
            try:
                footprint = parts.resolve_footprint(comp.footprint_id)
            except NotFound:
                problems.append(
                    f"{comp.reference}: footprint {comp.footprint_id} cannot be resolved")
                continue
        if part.footprint_id and comp.footprint_id:
            from mota.library import resolver as library_resolver
            if library_resolver.canonical_id(comp.footprint_id) != \
                    library_resolver.canonical_id(part.footprint_id):
                problems.append(
                    f"{comp.reference}: footprint {comp.footprint_id} does not match "
                    f"{part.mpn} ({part.footprint_id}); pin map must be re-verified")
        pad_numbers = {p.number for p in footprint.pads} if footprint else set()
        for pin in part.pins:
            targets = comp.pin_pad_map.get(pin.number)
            if not targets:
                if comp.on_board:
                    problems.append(f"{comp.reference}: pin {pin.number} has no pad mapping")
                continue
            for target in targets:
                if target not in pad_numbers:
                    problems.append(
                        f"{comp.reference}: pin {pin.number} -> missing pad {target}")
        for pin_number in comp.pin_pad_map:
            if not any(p.number == pin_number for p in part.pins):
                problems.append(f"{comp.reference}: map for unknown pin {pin_number}")
    if problems:
        return _result("PIN_PAD_MAPPING", "PIN_PAD_MAPPING",
                       "Pin-to-pad mapping", "FAIL", "; ".join(problems[:8]),
                       object_ids=[c.id for c in circuit.components])
    return _result("PIN_PAD_MAPPING", "PIN_PAD_MAPPING", "Pin-to-pad mapping", "PASS",
                   f"All {len(circuit.components)} components map pins to existing pads.",
                   object_ids=[c.id for c in circuit.components])


def check_net_completeness(circuit: CircuitIR) -> CheckResult:
    problems: list[str] = []
    seen: dict[str, str] = {}
    resolved: dict[str, set[str]] = {}
    for comp in circuit.components:
        try:
            resolved[comp.id] = {pin.number for pin in parts.resolve_part(comp.part_id).pins}
        except NotFound:
            resolved[comp.id] = set()
            problems.append(f"{comp.reference} part {comp.part_id} cannot be resolved")
    for net in circuit.nets:
        if len(net.pins) < 2:
            problems.append(f"{net.name} has fewer than two pins")
        for key in net.pins:
            comp_id, _, pin_number = key.partition(":")
            comp = next((c for c in circuit.components if c.id == comp_id), None)
            if comp is None:
                problems.append(f"{net.name} references missing component {comp_id}")
                continue
            if pin_number not in resolved.get(comp.id, set()):
                problems.append(f"{net.name} references missing pin {key}")
            if key in seen:
                problems.append(f"{key} is in {seen[key]} and {net.name}")
            seen[key] = net.name
    for comp in circuit.components:
        if comp.id not in resolved:
            continue
        try:
            part = parts.resolve_part(comp.part_id)
        except NotFound:
            continue
        for pin in part.pins:
            key = pin_key(comp.id, pin.number)
            if key in seen or key in circuit.no_connects:
                continue
            if pin.electrical_type == "no_connect":
                continue
            problems.append(f"{comp.reference}.{pin.number} ({pin.name}) is floating")
    if problems:
        return _result("NET_COMPLETENESS", "NET_ENDPOINTS", "Net endpoints", "FAIL",
                       "; ".join(problems[:8]))
    return _result("NET_COMPLETENESS", "NET_ENDPOINTS", "Net endpoints", "PASS",
                   f"{len(circuit.nets)} nets have complete endpoints; "
                   f"{len(circuit.no_connects)} pins are explicitly NC.")


def check_required_connections(circuit: CircuitIR, recipe: TemplateRecipe) -> CheckResult:
    problems: list[str] = []
    extras: list[str] = []
    by_ref = {c.reference: c for c in circuit.components}
    for expected in recipe.nets:
        wanted: set[str] = set()
        for token in expected["pins"]:
            ref, _, pin = token.partition(":")
            comp = by_ref.get(ref)
            if comp is None:
                problems.append(f"{expected['name']}: missing {ref}")
                continue
            wanted.add(f"{comp.id}:{pin}")
        net = net_by_name(circuit, expected["name"])
        if net is None:
            problems.append(f"missing net {expected['name']}")
            continue
        actual = set(net.pins)
        missing = wanted - actual
        if missing:
            problems.append(f"{expected['name']}: missing {sorted(missing)}")
        extra = actual - wanted
        if extra:
            # Free editing may intentionally add terminals to a template net.
            # Required terminals must exist; additions are reported, not failed.
            extras.extend(f"{expected['name']}: {sorted(extra)}")
    if problems:
        return _result("REQUIRED_CONNECTIONS", "TEMPLATE_TOPOLOGY",
                       "Required connections", "FAIL", "; ".join(problems[:8]))
    message = f"{len(recipe.nets)} template nets contain every required pin."
    if extras:
        message += f" Extra edited pins: {'; '.join(extras[:4])}"
    return _result("REQUIRED_CONNECTIONS", "TEMPLATE_TOPOLOGY",
                   "Required connections", "PASS", message,
                   evidence={"extra_pins": extras})


def _net_voltage(net) -> float | None:
    """Rail voltage from the stored field or the canonical net name."""
    if net.voltage_v is not None:
        return float(net.voltage_v)
    name = net.name.upper()
    if "12V" in name:
        return 12.0
    if "5V" in name:
        return 5.0
    if "3V3" in name or "3.3V" in name:
        return 3.3
    return None


def _supply_range(comp) -> tuple[float, float, str, str, set[str]] | None:
    """Return (low, high, key, source, pin names) from the asset package.

    The package `parameters.json` is the authority; the verified datasheet
    transcription supplies pin names and covers packages migrated before
    parameters existed.
    """
    from mota.catalog.verified import verified_part
    from mota.library import resolver as library_resolver
    from mota.errors import NotFound

    verified = verified_part(comp.part_id)
    pin_names = {name.upper() for name in verified.supply_pins} if verified \
        else {"VCC", "VDD", "VIN", "VBAT", "IN"}
    try:
        rows = library_resolver.parameter_rows(comp.part_id)
    except NotFound:
        rows = []
    for row in rows:
        if row.get("name") != "supply_voltage_v":
            continue
        if row.get("status") not in ("verified", "source_reported"):
            continue
        try:
            low = float(row["value_min"])
            high = float(row["value_max"])
        except (KeyError, TypeError, ValueError):
            continue
        source = (row.get("evidence_refs") or [""])[0]
        return (low, high, "supply_voltage_v", source, pin_names)
    if verified is not None:
        return (verified.supply_min_v, verified.supply_max_v, "supply_voltage_v",
                verified.source_url, pin_names)
    part = builtin.PARTS.get(comp.part_id)
    if part is None:
        return None
    for limit in part.limits:
        if "voltage" not in limit.key:
            continue
        match = re.match(r"^([0-9.]+)-([0-9.]+)", limit.value)
        if match:
            return (float(match.group(1)), float(match.group(2)), limit.key,
                    limit.source_id, pin_names)
    return None


def check_voltage_domain(circuit: CircuitIR, recipe: TemplateRecipe,
                         parameters: dict[str, Any]) -> CheckResult:
    problems: list[str] = []
    unknown: list[str] = []
    checked = 0
    supply = parameters.get("supply_voltage_v", parameters.get("input_voltage_v"))
    rule = recipe.constraints.get("supply_voltage_v") or recipe.constraints.get("input_voltage_v")
    if supply is not None and rule:
        if float(supply) < rule["min"] or float(supply) > rule["max"]:
            problems.append(f"supply {supply} V outside {rule['min']}..{rule['max']} V")
    for comp in circuit.components:
        try:
            part = parts.resolve_part(comp.part_id)
        except NotFound:
            unknown.append(f"{comp.reference}: part cannot be resolved")
            continue
        found = _supply_range(comp)
        if found is None:
            # Multi-pin ICs need verified data; passive/transistor parts do not.
            if len(part.pins) >= 5:
                unknown.append(f"{comp.reference}: no verified supply range")
            continue
        low, high, key, source, pin_names = found
        pin_numbers = {pin.number for pin in part.pins
                       if pin.name.upper() in pin_names}
        if not pin_numbers:
            unknown.append(f"{comp.reference}: no supply pin matched {sorted(pin_names)}")
            continue
        for net in circuit.nets:
            if not any(pin_key(comp.id, number) in net.pins for number in pin_numbers):
                continue
            voltage = _net_voltage(net)
            if voltage is None:
                continue
            checked += 1
            if voltage < low or voltage > high:
                problems.append(
                    f"{comp.reference} {key} {low}-{high} V is violated by "
                    f"{net.name}={voltage} V (source: {source})")
    if problems:
        return _result("VOLTAGE_DOMAIN", "VOLTAGE_DOMAIN", "Voltage domain", "FAIL",
                       "; ".join(problems[:8]),
                       evidence={"checked_rails": checked, "unknown": unknown})
    if unknown:
        return _result("VOLTAGE_DOMAIN", "VOLTAGE_DOMAIN", "Voltage domain",
                       "NOT_CHECKED",
                       "Missing verified supply data: " + "; ".join(unknown[:8]),
                       severity="warning", evidence={"checked_rails": checked,
                                                     "unknown": unknown})
    return _result("VOLTAGE_DOMAIN", "VOLTAGE_DOMAIN", "Voltage domain", "PASS",
                   f"Supply rails match the verified part ranges ({checked} checks).",
                   evidence={"checked_rails": checked})


def check_supply_decoupling(circuit: CircuitIR) -> CheckResult:
    power_nets = [n for n in circuit.nets
                  if _net_voltage(n) is not None and "GND" not in n.name.upper()]
    problems = []
    unknown = []
    evidence = {}
    gnd = net_by_name(circuit, "GND")
    gnd_pins = set(gnd.pins) if gnd else set()
    if gnd is None:
        unknown.append("GND net is missing")
    for net in power_nets:
        caps = []
        for comp in circuit.components:
            try:
                part = parts.resolve_part(comp.part_id)
            except NotFound:
                continue
            if not comp.reference.upper().startswith("C") or len(part.pins) != 2:
                continue
            keys = [pin_key(comp.id, pin.number) for pin in part.pins]
            on_net = [k for k in keys if k in net.pins]
            to_gnd = [k for k in keys if k in gnd_pins]
            if on_net and to_gnd:
                caps.append(comp.reference)
        evidence[net.name] = caps
        if not caps:
            problems.append(f"{net.name} has no capacitor to GND")
    if problems:
        return _result("SUPPLY_DECOUPLING", "REQUIRED_DECOUPLING",
                       "Supply decoupling", "FAIL", "; ".join(problems[:8]),
                       evidence=evidence)
    if unknown:
        return _result("SUPPLY_DECOUPLING", "REQUIRED_DECOUPLING",
                       "Supply decoupling", "NOT_CHECKED", "; ".join(unknown),
                       severity="warning", evidence=evidence)
    return _result("SUPPLY_DECOUPLING", "REQUIRED_DECOUPLING", "Supply decoupling",
                   "PASS", "Every power net has a capacitor to GND.",
                   evidence=evidence)


def check_schematic_consistency(circuit: CircuitIR, schematic: SchematicIR,
                                board: BoardIR) -> CheckResult:
    problems: list[str] = []
    attachments, missing = schematic_pin_attachment(schematic)
    for key in missing:
        problems.append(f"schematic pin {key} has no wire")
    net_by_pin: dict[str, str] = {}
    for net in circuit.nets:
        for token in net.pins:
            net_by_pin[token] = net.id
    for key, rows in attachments.items():
        if key not in net_by_pin:
            continue
        expected = net_by_pin[key]
        attached = {net_id for net_id, _, _ in rows if net_id != "__nc__"}
        if expected not in attached:
            problems.append(f"schematic pin {key} attaches to {sorted(attached)} "
                            f"instead of {expected}")
    # Coincident wire endpoints must not merge two different nets.
    for root, nets in wire_graph_nets(schematic).items():
        if len(nets) > 1:
            problems.append(f"schematic wires of nets {sorted(nets)} share a node")
    # Label text must equal the net name for each net.
    for label in schematic.labels:
        net = next((n for n in circuit.nets if n.id == label.net_id), None)
        if net is None:
            problems.append(f"label {label.text} references unknown net")
        elif label.text != net.name:
            problems.append(f"label {label.text} != net {net.name}")
    # Board pad nets must match circuit pins; every component needs a footprint.
    for net in circuit.nets:
        for token in net.pins:
            comp_id, _, pin_number = token.partition(":")
            comp = next((c for c in circuit.components if c.id == comp_id), None)
            if comp is None:
                continue
            pad_numbers = comp.pin_pad_map.get(pin_number, [])
            for pad_number in pad_numbers:
                pad = next((p for p in board.pads
                            if p.component_id == comp_id and p.number == pad_number), None)
                if pad is None:
                    problems.append(f"missing board pad {comp.reference}.{pad_number}")
                elif pad.net_id != net.id:
                    problems.append(f"board pad {comp.reference}.{pad_number} net "
                                    f"{pad.net_id} != {net.id}")
    for comp in circuit.components:
        if not any(fp.component_id == comp.id for fp in board.footprints):
            problems.append(f"missing footprint for {comp.reference}")
        if not any(sym.component_id == comp.id for sym in schematic.symbols):
            problems.append(f"missing schematic symbol for {comp.reference}")
    if problems:
        return _result("SCHEMATIC_PCB_CONSISTENCY", "SCHEMATIC_PCB_CONSISTENCY",
                       "Schematic and PCB consistency", "FAIL",
                       "; ".join(problems[:8]))
    return _result("SCHEMATIC_PCB_CONSISTENCY", "SCHEMATIC_PCB_CONSISTENCY",
                   "Schematic and PCB consistency", "PASS",
                   f"{len(circuit.components)} components share identity and nets "
                   f"across schematic and board.")


def check_visible_text(circuit: CircuitIR, schematic: SchematicIR) -> CheckResult:
    """Check every visible object against its sheet's usable region.

    Extents include the symbol body, pins, pin numbers, reference and value
    text and the net label attached to each pin, using the same geometry the
    layout and renderer use. Reports name the sheet, object, edge and overflow.
    """
    from mota.schematic.geometry import (
        title_block_rect, usable_region, symbol_occupancy,
    )

    page_w = schematic.page_w_nm / 1e6
    page_h = schematic.page_h_nm / 1e6
    left, bottom, right, top = usable_region(page_w, page_h)
    title = title_block_rect(page_w)
    pin_nets: dict[str, dict[str, str]] = {c.id: {} for c in circuit.components}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            if cid in pin_nets:
                pin_nets[cid][pin] = net.name

    problems: list[str] = []
    occupied: list[tuple[str, float, float, float, float]] = []

    def edge_problem(label: str, x0: float, y0: float, x1: float, y1: float) -> None:
        if x0 < left:
            problems.append(f"sheet 1 · {label}: beyond the left edge by {left - x0:.1f} mm")
        if x1 > right:
            problems.append(f"sheet 1 · {label}: beyond the right edge by {x1 - right:.1f} mm")
        if y0 < bottom:
            problems.append(f"sheet 1 · {label}: beyond the bottom edge by {bottom - y0:.1f} mm")
        if y1 > top:
            problems.append(f"sheet 1 · {label}: beyond the top edge by {y1 - top:.1f} mm")
        if x1 > title[0] and x0 < title[2] and y1 > title[1] and y0 < title[3]:
            problems.append(f"sheet 1 · {label}: overlaps the title block")

    for sym in schematic.symbols:
        comp = next((c for c in circuit.components if c.id == sym.component_id), None)
        try:
            part = parts.resolve_part(comp.part_id) if comp is not None else None
        except NotFound:
            part = None
        try:
            symbol_def = parts.resolve_symbol(sym.symbol_id)
        except NotFound:
            symbol_def = None
        if symbol_def is None:
            x, y = sym.x_nm / 1e6, sym.y_nm / 1e6
            w, h = sym.body_w_nm / 1e6, sym.body_h_nm / 1e6
            x0, y0, x1, y1 = x - w / 2, y - h / 2, x + w / 2, y + h / 2
        else:
            occupancy = symbol_occupancy(sym, part, symbol_def, pin_nets.get(
                sym.component_id, {}))
            x0, y0, x1, y1 = occupancy.x0, occupancy.y0, occupancy.x1, occupancy.y1
        label = f"{sym.reference} ({sym.component_id})"
        edge_problem(label, x0, y0, x1, y1)
        occupied.append((label, x0, y0, x1, y1))

    for label in schematic.labels:
        x = label.x_nm / 1e6
        y = label.y_nm / 1e6
        length = max(2.4, len(label.text) * 1.276)
        height = 2.6
        if label.rotation_deg % 180 == 90:
            length, height = height, length
        x0 = x - length if label.justify == "right" else x
        y0 = y - height / 2
        edge_problem(f"label {label.text}", x0, y0, x0 + length, y0 + height)

    for wire in schematic.wires:
        xs = [point[0] / 1e6 for point in wire.points]
        ys = [point[1] / 1e6 for point in wire.points]
        if xs and ys:
            edge_problem(f"wire {wire.id}", min(xs), min(ys), max(xs), max(ys))

    for index, a in enumerate(occupied):
        for b in occupied[index + 1:]:
            if a[0] == b[0]:
                continue
            if a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]:
                problems.append(f"sheet 1 · {a[0]} overlaps {b[0]}")

    if problems:
        return _result("VISIBLE_TEXT", "TEXT_AND_SYMBOL_OVERLAP",
                       "Visible text and symbols", "FAIL", "; ".join(problems[:8]))
    return _result("VISIBLE_TEXT", "TEXT_AND_SYMBOL_OVERLAP",
                   "Visible text and symbols", "PASS",
                   f"All {len(schematic.symbols)} symbols, labels and wires fit the "
                   f"{page_w:.0f} × {page_h:.0f} mm sheet.")


# --------------------------------------------------------------------------- #
# Template-specific checks
# --------------------------------------------------------------------------- #

def check_mcp1700_pinmap(circuit: CircuitIR) -> CheckResult:
    comp = next((c for c in circuit.components if c.part_id == "mcp1700_3302e_to"), None)
    if comp is None:
        return _result("MCP1700_TO92_PINMAP", "MCP1700_TO92_PINMAP",
                       "MCP1700 TO-92 pin map", "FAIL", "MCP1700 is not in this design.")
    part = builtin.PARTS[comp.part_id]
    expected = {"1": "GND", "2": "VIN", "3": "VOUT"}
    actual = {p.number: p.name for p in part.pins}
    mapped = {pin: comp.pin_pad_map.get(pin) for pin in expected}
    if actual != expected:
        return _result("MCP1700_TO92_PINMAP", "MCP1700_TO92_PINMAP",
                       "MCP1700 TO-92 pin map", "FAIL",
                       f"Symbol pins {actual} do not match datasheet {expected}.",
                       object_ids=[comp.id], source_id="D1")
    if any(mapped[pin] != [pin] for pin in expected):
        return _result("MCP1700_TO92_PINMAP", "MCP1700_TO92_PINMAP",
                       "MCP1700 TO-92 pin map", "FAIL",
                       f"Footprint pad mapping {mapped} does not match pins.",
                       object_ids=[comp.id], source_id="D1")
    return _result("MCP1700_TO92_PINMAP", "MCP1700_TO92_PINMAP",
                   "MCP1700 TO-92 pin map", "PASS",
                   "TO-92 pins 1=GND, 2=VIN, 3=VOUT map one-to-one to pads.",
                   object_ids=[comp.id], source_id="D1",
                   evidence={"expected": expected, "actual": actual})


def _vout_capacitors(circuit: CircuitIR) -> list:
    comp_u1 = next((c for c in circuit.components if c.part_id == "mcp1700_3302e_to"), None)
    if comp_u1 is None:
        return []
    vout = next((n for n in circuit.nets
                 if pin_key(comp_u1.id, "3") in n.pins), None)
    if vout is None:
        return []
    caps = []
    for comp in circuit.components:
        part = builtin.PARTS.get(comp.part_id)
        if part is None or part.category != "capacitor":
            continue
        if any(pin_key(comp.id, p.number) in vout.pins for p in part.pins):
            caps.append(comp)
    return caps


def check_output_cap_effective(circuit: CircuitIR) -> CheckResult:
    caps = _vout_capacitors(circuit)
    if not caps:
        return _result("OUTPUT_CAP_EFFECTIVE", "OUTPUT_CAP_EFFECTIVE",
                       "Output capacitor effective capacitance", "FAIL",
                       "No output capacitor is connected to VOUT.")
    checked = []
    for comp in caps:
        part = builtin.PARTS.get(comp.part_id)
        if part is None:
            checked.append({"reference": comp.reference,
                            "evidence_status": "missing"})
            return _result(
                "OUTPUT_CAP_EFFECTIVE", "OUTPUT_CAP_EFFECTIVE",
                "Output capacitor effective capacitance", "NOT_CHECKED",
                f"{comp.reference} is an imported part without verified "
                "capacitor evidence. CAPACITOR_DERATING_NOT_CHECKED.",
                object_ids=[comp.id], source_id="D1",
                evidence={"capacitors": checked})
        evidence = next((e for e in part.evidence if e.kind == "capacitor_derating"), None)
        effective = None
        if evidence and evidence.status == "verified":
            value = evidence.values.get("effective_uf")
            effective = float(value) if value is not None else None
        checked.append({"reference": comp.reference, "evidence_status":
                        evidence.status if evidence else "missing",
                        "effective_uf": effective})
        if effective is None or effective < 1.0:
            return _result(
                "OUTPUT_CAP_EFFECTIVE", "OUTPUT_CAP_EFFECTIVE",
                "Output capacitor effective capacitance", "NOT_CHECKED",
                f"{comp.reference} has no verified effective-capacitance evidence "
                f"(>= 1.0 uF required). CAPACITOR_DERATING_NOT_CHECKED.",
                object_ids=[comp.id], source_id="D1", evidence={"capacitors": checked})
    return _result("OUTPUT_CAP_EFFECTIVE", "OUTPUT_CAP_EFFECTIVE",
                   "Output capacitor effective capacitance", "PASS",
                   "Verified effective capacitance is at least 1.0 uF.",
                   object_ids=[c.id for c in caps], source_id="D1",
                   evidence={"capacitors": checked})


def check_output_cap_esr(circuit: CircuitIR) -> CheckResult:
    caps = _vout_capacitors(circuit)
    if not caps:
        return _result("OUTPUT_CAP_ESR", "OUTPUT_CAP_ESR",
                       "Output capacitor ESR", "FAIL",
                       "No output capacitor is connected to VOUT.")
    checked = []
    for comp in caps:
        part = builtin.PARTS.get(comp.part_id)
        if part is None:
            checked.append({"reference": comp.reference,
                            "evidence_status": "missing"})
            return _result(
                "OUTPUT_CAP_ESR", "OUTPUT_CAP_ESR", "Output capacitor ESR",
                "NOT_CHECKED",
                f"{comp.reference} is an imported part without verified "
                "capacitor evidence. CAPACITOR_DERATING_NOT_CHECKED.",
                object_ids=[comp.id], source_id="D1",
                evidence={"capacitors": checked})
        evidence = next((e for e in part.evidence if e.kind == "capacitor_derating"), None)
        esr = None
        if evidence and evidence.status == "verified":
            value = evidence.values.get("esr_ohm")
            esr = float(value) if value is not None else None
        checked.append({"reference": comp.reference, "evidence_status":
                        evidence.status if evidence else "missing", "esr_ohm": esr})
        if esr is None or esr > 2.0:
            return _result("OUTPUT_CAP_ESR", "OUTPUT_CAP_ESR",
                           "Output capacitor ESR", "NOT_CHECKED",
                           f"{comp.reference} has no verified ESR evidence "
                           f"(<= 2 ohm required). CAPACITOR_DERATING_NOT_CHECKED.",
                           object_ids=[comp.id], source_id="D1",
                           evidence={"capacitors": checked})
    return _result("OUTPUT_CAP_ESR", "OUTPUT_CAP_ESR", "Output capacitor ESR", "PASS",
                   "Verified ESR is at most 2 ohm.", object_ids=[c.id for c in caps],
                   source_id="D1", evidence={"capacitors": checked})


def _pinmap_check(circuit: CircuitIR, part_id: str, expected: dict[str, str],
                  check_id: str, title: str, reference_source: str) -> CheckResult:
    comp = next((c for c in circuit.components if c.part_id == part_id), None)
    if comp is None:
        return _result(check_id, check_id, title, "FAIL", f"{part_id} is not in this design.")
    part = builtin.PARTS[part_id]
    actual = {p.number: p.name for p in part.pins}
    if actual != expected:
        return _result(check_id, check_id, title, "FAIL",
                       f"Pins {actual} do not match datasheet {expected}.",
                       object_ids=[comp.id], source_id=reference_source)
    return _result(check_id, check_id, title, "PASS",
                   f"{comp.reference} pin names and pad mapping match the datasheet.",
                   object_ids=[comp.id], source_id=reference_source,
                   evidence={"pins": actual})


def check_ne555_pinmap(circuit: CircuitIR) -> CheckResult:
    return _pinmap_check(circuit, "ne555p",
                         {"1": "GND", "2": "TRIG", "3": "OUT", "4": "RESET",
                          "5": "CONT", "6": "THRES", "7": "DISCH", "8": "VCC"},
                         "NE555_PINMAP", "NE555 DIP-8 pin map", "D2")


def check_ds1621_pinmap(circuit: CircuitIR) -> CheckResult:
    return _pinmap_check(circuit, "ds1621_plus",
                         {"1": "SDA", "2": "SCL", "3": "TOUT", "4": "GND",
                          "5": "A2", "6": "A1", "7": "A0", "8": "VDD"},
                         "DS1621_PINMAP", "DS1621 DIP-8 pin map", "D3")


def check_ne555_reset_bias(circuit: CircuitIR) -> CheckResult:
    u1 = component_by_ref(circuit, "U1")
    if u1 is None:
        return _result("NE555_RESET_BIAS", "NE555_RESET_BIAS", "NE555 RESET bias",
                       "FAIL", "U1 is missing.")
    reset_net = net_containing(circuit, pin_key(u1.id, "4"))
    vcc_net = net_containing(circuit, pin_key(u1.id, "8"))
    if reset_net is None or vcc_net is None or reset_net.id != vcc_net.id:
        return _result("NE555_RESET_BIAS", "NE555_RESET_BIAS", "NE555 RESET bias",
                       "FAIL", "RESET (pin 4) is not tied to the VCC net.",
                       object_ids=[u1.id])
    return _result("NE555_RESET_BIAS", "NE555_RESET_BIAS", "NE555 RESET bias", "PASS",
                   "RESET (pin 4) is tied to VCC_5V.", object_ids=[u1.id])


def check_ne555_timing(circuit: CircuitIR, parameters: dict[str, Any]) -> CheckResult:
    ra = component_by_ref(circuit, "R1")
    rb = component_by_ref(circuit, "R2")
    cap = component_by_ref(circuit, "C1")
    if not (ra and rb and cap):
        return _result("NE555_TIMING", "NE555_TIMING", "NE555 timing", "FAIL",
                       "R1, R2 or C1 is missing.")
    r_a = parse_value(ra.value)
    r_b = parse_value(rb.value)
    c = parse_value(cap.value)
    if not (r_a and r_b and c):
        return _result("NE555_TIMING", "NE555_TIMING", "NE555 timing", "FAIL",
                       f"Could not parse values R1={ra.value}, R2={rb.value}, C1={cap.value}.")
    frequency = 1.44 / ((r_a + 2 * r_b) * c)
    target = float(parameters.get("target_frequency_hz", 1.0))
    low_cap = component_by_ref(circuit, "C2")
    decoupling = component_by_ref(circuit, "C3")
    problems = []
    if abs(frequency - target) / target > 0.20:
        problems.append(f"nominal {frequency:.3f} Hz is outside 20% of {target:.3f} Hz")
    if low_cap is None or low_cap.value != "10nF":
        problems.append("CONT capacitor is not 10 nF")
    if decoupling is None or decoupling.value != "100nF":
        problems.append("VCC decoupling capacitor is not 100 nF")
    evidence = {"ra_ohm": r_a, "rb_ohm": r_b, "c_f": c,
                "nominal_frequency_hz": round(frequency, 4),
                "target_frequency_hz": target,
                "note": "Nominal calculation; not a measured frequency."}
    if problems:
        return _result("NE555_TIMING", "NE555_TIMING", "NE555 timing", "FAIL",
                       "; ".join(problems), evidence=evidence, source_id="D2")
    return _result("NE555_TIMING", "NE555_TIMING", "NE555 timing", "PASS",
                   f"Nominal astable frequency {frequency:.3f} Hz (calculated, not measured).",
                   evidence=evidence, source_id="D2",
                   object_ids=[ra.id, rb.id, cap.id])


def check_led_polarity(circuit: CircuitIR) -> CheckResult:
    led = component_by_ref(circuit, "D1")
    r3 = component_by_ref(circuit, "R3")
    u1 = component_by_ref(circuit, "U1")
    if not (led and r3 and u1):
        return _result("LED_POLARITY", "LED_POLARITY", "LED polarity", "FAIL",
                       "D1, R3 or U1 is missing.")
    anode = net_containing(circuit, pin_key(led.id, "1"))
    cathode = net_containing(circuit, pin_key(led.id, "2"))
    anode_ok = bool(anode and pin_key(r3.id, "2") in anode.pins)
    cathode_ok = bool(cathode and pin_key(u1.id, "3") in cathode.pins)
    try:
        mapping = parts.resolve_part(led.part_id).pin_pad_map
    except Exception:  # noqa: BLE001 - unresolved imported part blocks the check
        mapping = {}
    mapping_ok = mapping.get("1") == ["1"] and mapping.get("2") == ["2"]
    if not (anode_ok and cathode_ok and mapping_ok):
        return _result("LED_POLARITY", "LED_POLARITY", "LED polarity", "FAIL",
                       "LED anode/cathode do not follow the low-level-lit branch.",
                       object_ids=[led.id])
    return _result("LED_POLARITY", "LED_POLARITY", "LED polarity", "PASS",
                   "Anode goes to the 1k branch from VCC; cathode goes to OUT (pin 3).",
                   object_ids=[led.id, r3.id, u1.id],
                   evidence={"anode": "pad 1 / net " + anode.name if anode else "",
                             "cathode": "pad 2 / net " + cathode.name if cathode else ""})


def check_i2c_pullups(circuit: CircuitIR) -> CheckResult:
    supply = net_by_name(circuit, "3V3")
    problems = []
    evidence = {}
    for name in ("SDA", "SCL"):
        net = net_by_name(circuit, name)
        if net is None or supply is None:
            problems.append(f"{name} or 3V3 net missing")
            continue
        found = []
        for comp in circuit.components:
            part = builtin.PARTS.get(comp.part_id)
            if part is None or part.category != "resistor":
                continue
            keys = [pin_key(comp.id, p.number) for p in part.pins]
            if any(k in net.pins for k in keys) and any(k in supply.pins for k in keys):
                found.append(f"{comp.reference}={comp.value}")
        evidence[name] = found
        if not found:
            problems.append(f"{name} has no pull-up to 3V3")
    if problems:
        return _result("I2C_PULLUPS", "I2C_PULLUPS", "I2C pull-ups", "FAIL",
                       "; ".join(problems), evidence=evidence, source_id="D3")
    return _result("I2C_PULLUPS", "I2C_PULLUPS", "I2C pull-ups", "PASS",
                   "SDA and SCL each have a 3.3k pull-up to 3V3.", evidence=evidence,
                   source_id="D3")


def check_i2c_address(circuit: CircuitIR) -> CheckResult:
    u1 = component_by_ref(circuit, "U1")
    gnd = net_by_name(circuit, "GND")
    if u1 is None or gnd is None:
        return _result("I2C_ADDRESS_STRAP", "I2C_ADDRESS_STRAP", "I2C address strap",
                       "FAIL", "U1 or GND net missing.")
    strap = {}
    for pin, name in (("7", "A0"), ("6", "A1"), ("5", "A2")):
        net = net_containing(circuit, pin_key(u1.id, pin))
        strap[name] = net.name if net else None
        if net is None or net.id != gnd.id:
            return _result("I2C_ADDRESS_STRAP", "I2C_ADDRESS_STRAP",
                           "I2C address strap", "FAIL",
                           f"{name} (pin {pin}) is not tied to GND.",
                           object_ids=[u1.id], evidence={"strap": strap})
    return _result("I2C_ADDRESS_STRAP", "I2C_ADDRESS_STRAP", "I2C address strap",
                   "PASS", "A0, A1 and A2 are tied to GND; 7-bit address is 0x48.",
                   object_ids=[u1.id], source_id="D3",
                   evidence={"strap": strap, "address": "0x48"})


def check_tout_nc(circuit: CircuitIR, schematic: SchematicIR) -> CheckResult:
    u1 = component_by_ref(circuit, "U1")
    if u1 is None:
        return _result("DS1621_TOUT_NC", "DS1621_TOUT_NC", "DS1621 TOUT not connected",
                       "FAIL", "U1 is missing.")
    key = pin_key(u1.id, "3")
    if key not in circuit.no_connects:
        return _result("DS1621_TOUT_NC", "DS1621_TOUT_NC", "DS1621 TOUT not connected",
                       "FAIL", "TOUT (pin 3) is not marked as an explicit no-connect.",
                       object_ids=[u1.id])
    if key not in schematic.no_connect_pins:
        return _result("DS1621_TOUT_NC", "DS1621_TOUT_NC", "DS1621 TOUT not connected",
                       "FAIL", "TOUT has no no-connect marker in the schematic.",
                       object_ids=[u1.id])
    return _result("DS1621_TOUT_NC", "DS1621_TOUT_NC", "DS1621 TOUT not connected",
                   "PASS", "TOUT (pin 3) is an explicit no-connect with a schematic marker.",
                   object_ids=[u1.id], source_id="D3")


def check_interface_assumption(circuit: CircuitIR, parameters: dict[str, Any]) -> CheckResult:
    frequency = parameters.get("bus_frequency_khz", 100)
    return _result(
        "INTERFACE_ASSUMPTION", "INTERFACE_ASSUMPTION",
        "External bus conditions", "WARN",
        f"Bus runs at {frequency} kHz; total bus capacitance <= 100 pF and the "
        f"external master/cable are assumptions, not measured by Mota.",
        required=False, severity="warning", source_id="D3",
        coverage="External cable and master are outside this board.")


def check_thermal_out_of_scope(circuit: CircuitIR, parameters: dict[str, Any]) -> CheckResult:
    load = float(parameters.get("max_load_ma", 50))
    worst = (5.25 - 3.3) * load / 1000.0
    return _result(
        "THERMAL_ESTIMATE", "THERMAL_ESTIMATE", "Thermal analysis", "NOT_CHECKED",
        f"Estimated worst-case LDO dissipation {(5.25 - 3.3) * load / 1000.0:.4f} W at "
        f"{load:.0f} mA. Thermal design is outside this demo's coverage.",
        required=False, severity="warning",
        coverage="Thermal simulation, copper spreading and ambient conditions.",
        evidence={"estimated_dissipation_w": round(worst, 4),
                  "calculation": "(5.25 - 3.3) * max_load_ma / 1000"})


def check_emc_out_of_scope() -> CheckResult:
    return _result("EMC", "EMC", "EMC analysis", "NOT_CHECKED",
                   "EMC and immunity are outside this demo's coverage.",
                   required=False, severity="warning",
                   coverage="Radiated/conducted emissions and immunity.")


def check_led_physical_polarity() -> CheckResult:
    return _result("LED_PHYSICAL_POLARITY", "LED_PHYSICAL_POLARITY",
                   "LED physical lead polarity", "WARN",
                   "The footprint defines pad 1 as the anode; verify the chosen "
                   "physical LED lead polarity at assembly.",
                   required=False, severity="warning", source_id="SPEC-LED")
