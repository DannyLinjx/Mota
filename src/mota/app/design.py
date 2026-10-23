"""DesignService: template preparation and typed design patches on a draft.

All mutations go through this service, get a new draft_seq, invalidate previous
checks and rebuild schematic/board from CircuitIR. Nothing here writes files
except the draft handed to ProjectStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mota.catalog.templates import TemplateRecipe, get_template
from mota.circuit import parts
from mota.domain.models import (
    BoardIR, CheckResult, CircuitIR, Component, DesignPatch, DesignVersion,
    Net, Placement, RequirementSpec, SchematicIR, ValidationReport,
)
from mota.domain.units import mm
from mota.errors import ConstraintConflict, InvalidRequest, NotFound, PinmapMismatch
from mota.generation.board import build_board_ir, rebuild
from mota.generation.modules import build_circuit, component_id, net_id
from mota.generation.schematic import generate_schematic
from mota.projects.store import DesignDraft
from mota.rules import load_rule_profile
from mota.validation.reports import validate_all, validate_electrical


@dataclass
class PreparedDesign:
    requirement: RequirementSpec
    circuit: CircuitIR
    schematic: SchematicIR
    board: BoardIR


@dataclass
class PatchOutcome:
    requirement: RequirementSpec
    circuit: CircuitIR
    schematic: SchematicIR
    board: BoardIR
    checks: ValidationReport
    summary: str
    added: list[str]
    modified: list[str]
    deleted: list[str]


def _move_schematic_symbol(schematic: SchematicIR, circuit: CircuitIR,
                           op) -> str:
    """Move one placed symbol, keeping wires/labels attached and in bounds."""
    from mota.schematic.geometry import (
        symbol_occupancy, title_block_rect, usable_region,
    )

    sym = next((row for row in schematic.symbols
                if row.component_id == op.component_id
                or row.reference == op.component_id), None)
    if sym is None:
        raise NotFound(f"Schematic symbol {op.component_id} not found.",
                       code="SCHEMATIC_SYMBOL_NOT_FOUND")
    if op.x_nm is None or op.y_nm is None:
        raise InvalidRequest("move_schematic_symbol needs x_nm and y_nm.")
    comp = next((row for row in circuit.components if row.id == sym.component_id), None)
    try:
        part = parts.resolve_part(comp.part_id) if comp else None
    except NotFound:
        part = None
    try:
        symbol_def = parts.resolve_symbol(sym.symbol_id)
    except NotFound as exc:
        raise InvalidRequest(f"Symbol {sym.symbol_id} cannot be resolved.") from exc

    pin_nets: dict[str, str] = {}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            if cid == sym.component_id:
                pin_nets[pin] = net.name

    dx = op.x_nm - sym.x_nm
    dy = op.y_nm - sym.y_nm
    anchors = {key: [value[0] + dx, value[1] + dy]
               for key, value in sym.pin_anchors.items()}
    moved = sym.model_copy(update={"x_nm": op.x_nm, "y_nm": op.y_nm,
                                   "pin_anchors": anchors})
    occupancy = symbol_occupancy(moved, part, symbol_def, pin_nets)
    page_w = schematic.page_w_nm / 1e6
    page_h = schematic.page_h_nm / 1e6
    left, bottom, right, top = usable_region(page_w, page_h)
    title = title_block_rect(page_w)
    problems: list[str] = []
    if occupancy.x0 < left:
        problems.append(f"beyond the left edge by {left - occupancy.x0:.1f} mm")
    if occupancy.x1 > right:
        problems.append(f"beyond the right edge by {occupancy.x1 - right:.1f} mm")
    if occupancy.y0 < bottom:
        problems.append(f"beyond the bottom edge by {bottom - occupancy.y0:.1f} mm")
    if occupancy.y1 > top:
        problems.append(f"beyond the top edge by {occupancy.y1 - top:.1f} mm")
    if (occupancy.x1 > title[0] and occupancy.x0 < title[2]
            and occupancy.y1 > title[1] and occupancy.y0 < title[3]):
        problems.append("overlaps the title block")
    if problems:
        raise ConstraintConflict(
            f"{sym.reference} cannot move there: " + "; ".join(problems),
            data={"component_id": sym.component_id, "page": 1,
                  "edge_problems": problems})

    old_anchors = {tuple(value) for value in sym.pin_anchors.values()}
    for wire in schematic.wires:
        points = []
        shifted = False
        for point in wire.points:
            if tuple(point) in old_anchors:
                points.append([point[0] + dx, point[1] + dy])
                shifted = True
            else:
                points.append(point)
        if shifted and len(points) == 2 and points[0] != points[-1] and dx and dy:
            elbow = [points[-1][0], points[0][1]]
            points = [points[0], elbow, points[-1]]
        wire.points = points
    net_ids = {net.id for net in circuit.nets
               if any(token.startswith(sym.component_id + ":") for token in net.pins)}
    for label in schematic.labels:
        if label.net_id not in net_ids:
            continue
        if any((abs(label.x_nm - ax) < 6_000_000 and abs(label.y_nm - ay) < 6_000_000)
               for ax, ay in old_anchors):
            label.x_nm += dx
            label.y_nm += dy
    for junction in schematic.junctions:
        if (junction.x_nm, junction.y_nm) in old_anchors:
            junction.x_nm += dx
            junction.y_nm += dy
    schematic.no_connects = [
        [x + dx, y + dy] if (x, y) in old_anchors else [x, y]
        for x, y in schematic.no_connects]
    schematic.symbols = [moved if row.component_id == sym.component_id else row
                         for row in schematic.symbols]
    return f"Moved {sym.reference} to ({op.x_nm / 1e6:.1f}, {op.y_nm / 1e6:.1f}) mm"


class DesignService:
    def prepare(self, recipe: TemplateRecipe, parameters: dict[str, Any],
                project_id: str = "", version: DesignVersion | None = None
                ) -> PreparedDesign:
        circuit, requirement = build_circuit(recipe, parameters)
        width_nm = mm(float(requirement.board_size["width_mm"]))
        height_nm = mm(float(requirement.board_size["height_mm"]))
        board, _ = build_board_ir(circuit, recipe, width_nm, height_nm)
        schematic = generate_schematic(circuit, title=recipe.title)
        return PreparedDesign(requirement=requirement, circuit=circuit,
                              schematic=schematic, board=board)

    def apply(self, draft: DesignDraft, patch: DesignPatch, *,
              project_id: str, version: DesignVersion,
              reroute: bool = True) -> PatchOutcome:
        circuit = draft.circuit.model_copy(deep=True)
        requirement = draft.requirement.model_copy(deep=True)
        recipe = self._recipe(requirement)
        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        notes: list[str] = []
        relayout = False
        schematic = draft.schematic.model_copy(deep=True)
        schematic_edit = False
        for op in patch.operations:
            if op.op == "move_component":
                comp = self._component(circuit, op.component_id)
                if op.x_nm is None or op.y_nm is None:
                    raise InvalidRequest("move_component needs x_nm and y_nm.")
                comp.placement = Placement(x_nm=op.x_nm, y_nm=op.y_nm,
                                           rotation_deg=comp.placement.rotation_deg
                                           if comp.placement else 0,
                                           side=comp.placement.side if comp.placement else "top")
                comp.placement_origin = "user"
                comp.locked = True
                modified.append(comp.id)
                notes.append(f"Moved {comp.reference}")
            elif op.op == "rotate_component":
                comp = self._component(circuit, op.component_id)
                if comp.placement is None:
                    raise InvalidRequest(f"{comp.reference} has no placement.")
                comp.placement.rotation_deg = (comp.placement.rotation_deg +
                                               (op.rotation_deg or 90)) % 360
                comp.placement_origin = "user"
                modified.append(comp.id)
                notes.append(f"Rotated {comp.reference} to {comp.placement.rotation_deg}°")
            elif op.op == "set_component_value":
                comp = self._component(circuit, op.component_id)
                if op.value is None:
                    raise InvalidRequest("set_component_value needs value.")
                comp.value = op.value
                modified.append(comp.id)
                notes.append(f"Set {comp.reference} to {op.value}")
            elif op.op == "add_component":
                if not op.part_id:
                    raise InvalidRequest("add_component needs part_id.")
                part = parts.resolve_part(op.part_id)
                reference = op.reference or parts.next_reference(
                    circuit, part.reference_prefix)
                if any(c.reference == reference for c in circuit.components):
                    raise PinmapMismatch(f"Reference {reference} already exists.",
                                         code="DUPLICATE_REFERENCE")
                footprint_id = op.footprint_id or part.footprint_id
                footprint = None
                if footprint_id:
                    footprint = parts.resolve_footprint(footprint_id)
                mapping, mapping_issues = parts.normalize_mapping(
                    part.pins, footprint, op.pin_pad_mapping,
                    component_id=component_id(reference)) if footprint else ({}, [])
                structural = [issue for issue in mapping_issues
                              if issue.code in ("PINMAP_KEY_UNKNOWN", "PAD_NOT_FOUND")]
                if structural:
                    raise PinmapMismatch(structural[0].message,
                                         code=structural[0].code)
                comp = Component(
                    id=component_id(reference), reference=reference,
                    part_id=part.part_id, symbol_id=part.symbol_id,
                    footprint_id=footprint_id, pin_pad_map=mapping,
                    value=op.value or part.value_label or part.mpn,
                    placement=Placement(x_nm=op.x_nm or mm(10), y_nm=op.y_nm or mm(10),
                                        rotation_deg=0,
                                        side=op.side or "top"),
                    placement_origin="user", locked=False,
                    instance_path=op.instance_path or reference,
                    on_board=part.on_board, in_bom=True,
                    assembly_state="assembled",
                    footprint_instance_id=(f"{component_id(reference)}_fp"
                                           if footprint else None),
                    parameter_bindings=dict(op.parameter_bindings or {}))
                circuit.components.append(comp)
                added.append(comp.id)
                notes.append(f"Added {reference} ({part.mpn})")
            elif op.op == "delete_component":
                comp = self._component(circuit, op.component_id)
                circuit.components = [c for c in circuit.components if c.id != comp.id]
                for net in circuit.nets:
                    net.pins = [p for p in net.pins if not p.startswith(comp.id + ":")]
                circuit.nets = [n for n in circuit.nets if n.pins]
                circuit.no_connects = [p for p in circuit.no_connects
                                       if not p.startswith(comp.id + ":")]
                deleted.append(comp.id)
                notes.append(f"Deleted {comp.reference}")
            elif op.op == "connect_pins":
                if not op.a or not op.b:
                    raise InvalidRequest("connect_pins needs a and b.")
                key_a = self._resolve_pin(circuit, op.a)
                key_b = self._resolve_pin(circuit, op.b)
                if key_a == key_b:
                    raise InvalidRequest("Cannot connect a pin to itself.")
                target = None
                if op.net_name:
                    target = next((n for n in circuit.nets if n.name == op.net_name), None)
                if target is None:
                    sources = [n for n in circuit.nets if key_a in n.pins or key_b in n.pins]
                    if sources:
                        target = sources[0]
                        target.pins = sorted(set(target.pins + [key_a, key_b]))
                        for other in sources[1:]:
                            target.pins = sorted(set(target.pins + other.pins))
                        circuit.nets = [n for n in circuit.nets if n not in sources[1:]]
                if target is None:
                    name = op.net_name or f"NET_{key_a.split(':')[1]}_{key_b.split(':')[1]}"
                    if any(n.name == name for n in circuit.nets):
                        target = next(n for n in circuit.nets if n.name == name)
                    else:
                        target = Net(id=net_id(name), name=name, pins=[], net_class="signal")
                        circuit.nets.append(target)
                for key in (key_a, key_b):
                    for net in circuit.nets:
                        if net.id != target.id and key in net.pins:
                            net.pins.remove(key)
                    if key not in target.pins:
                        target.pins = sorted(target.pins + [key])
                    if key in circuit.no_connects:
                        circuit.no_connects.remove(key)
                circuit.nets = [n for n in circuit.nets if n.pins]
                notes.append(f"Connected {key_a} to {key_b}")
                modified.extend([key_a.split(":")[0], key_b.split(":")[0]])
            elif op.op == "connect_terminals":
                tokens = list(op.terminals)
                if not tokens:
                    tokens = [token for token in (op.a, op.b) if token]
                if len(tokens) < 2:
                    raise InvalidRequest("connect_terminals needs at least two terminals.")
                keys = [self._resolve_pin(circuit, token) for token in tokens]
                target = None
                if op.net_id:
                    target = next((n for n in circuit.nets if n.id == op.net_id), None)
                    if target is None:
                        raise NotFound(f"Net {op.net_id} was not found.",
                                       code="NET_NOT_FOUND")
                elif op.net_name:
                    target = next((n for n in circuit.nets if n.name == op.net_name), None)
                if target is None:
                    sources = [n for n in circuit.nets
                               if any(key in n.pins for key in keys)]
                    if sources:
                        target = sources[0]
                if target is None:
                    name = op.net_name or "NET_" + "_".join(
                        key.split(":")[1] for key in keys)
                    target = Net(id=net_id(name), name=name, pins=[],
                                 net_class=op.net_class or "signal")
                    circuit.nets.append(target)
                # Merge every net that already owns a terminal into the target,
                # so an explicit net name cannot silently split a net.
                for other in [n for n in circuit.nets if n.id != target.id]:
                    if any(key in other.pins for key in keys):
                        target.pins = sorted(set(target.pins + other.pins))
                        circuit.nets = [n for n in circuit.nets
                                        if n.id != other.id]
                if op.net_class:
                    target.net_class = op.net_class
                for key in keys:
                    for net in circuit.nets:
                        if net.id != target.id and key in net.pins:
                            net.pins.remove(key)
                    if key not in target.pins:
                        target.pins = sorted(target.pins + [key])
                    if key in circuit.no_connects:
                        circuit.no_connects.remove(key)
                circuit.nets = [n for n in circuit.nets if n.pins]
                notes.append(f"Connected {len(keys)} terminals on {target.name}")
                modified.extend(sorted({key.split(":")[0] for key in keys}))
            elif op.op == "assign_footprint":
                comp = self._component(circuit, op.component_id)
                if not op.footprint_id:
                    raise InvalidRequest("assign_footprint needs footprint_id.")
                footprint = parts.resolve_footprint(op.footprint_id)
                part = parts.resolve_part(comp.part_id)
                mapping, mapping_issues = parts.normalize_mapping(
                    part.pins, footprint, op.pin_pad_mapping,
                    component_id=comp.id)
                structural = [issue for issue in mapping_issues
                              if issue.code in ("PINMAP_KEY_UNKNOWN", "PAD_NOT_FOUND")]
                if structural:
                    raise PinmapMismatch(structural[0].message,
                                         code=structural[0].code)
                comp.footprint_id = op.footprint_id
                comp.pin_pad_map = mapping
                comp.footprint_instance_id = f"{comp.id}_fp"
                modified.append(comp.id)
                notes.append(f"Assigned footprint {op.footprint_id} to {comp.reference}")
            elif op.op == "change_part":
                comp = self._component(circuit, op.component_id)
                if not op.replacement_part_id:
                    raise InvalidRequest("change_part needs replacement_part_id.")
                replacement = parts.resolve_part(op.replacement_part_id)
                footprint_id = op.footprint_id or comp.footprint_id or \
                    replacement.footprint_id
                footprint = (parts.resolve_footprint(footprint_id)
                             if footprint_id else None)
                explicit = op.remap if op.remap is not None else None
                mapping, mapping_issues = parts.normalize_mapping(
                    replacement.pins, footprint, explicit,
                    component_id=comp.id) if footprint else ({}, [])
                structural = [issue for issue in mapping_issues
                              if issue.code in ("PINMAP_KEY_UNKNOWN",)]
                if structural:
                    raise PinmapMismatch(structural[0].message,
                                         code=structural[0].code)
                valid_keys = {f"{comp.id}:{pin.number}" for pin in replacement.pins}
                for net in circuit.nets:
                    net.pins = [token for token in net.pins
                                if not token.startswith(comp.id + ":")
                                or token in valid_keys]
                circuit.nets = [net for net in circuit.nets if net.pins]
                circuit.no_connects = [token for token in circuit.no_connects
                                       if not token.startswith(comp.id + ":")
                                       or token in valid_keys]
                comp.part_id = replacement.part_id
                comp.symbol_id = replacement.symbol_id
                comp.footprint_id = footprint_id
                comp.pin_pad_map = mapping
                comp.on_board = replacement.on_board
                if not comp.value:
                    comp.value = replacement.value_label or replacement.mpn
                if footprint:
                    comp.footprint_instance_id = comp.footprint_instance_id or \
                        f"{comp.id}_fp"
                modified.append(comp.id)
                notes.append(f"Changed {comp.reference} to {replacement.mpn}")
            elif op.op == "place_footprint":
                comp = self._component(circuit, op.component_id)
                if op.x_nm is None or op.y_nm is None:
                    raise InvalidRequest("place_footprint needs x_nm and y_nm.")
                rotation_udeg = op.rotation_udeg or 0
                if rotation_udeg % 90_000_000 != 0:
                    raise InvalidRequest(
                        "place_footprint currently supports rotations that are "
                        "multiples of 90 degrees; arbitrary angles need the "
                        "native geometry stage.", code="ROTATION_UNSUPPORTED")
                comp.placement = Placement(
                    x_nm=op.x_nm, y_nm=op.y_nm,
                    rotation_deg=(rotation_udeg // 1_000_000) % 360,
                    side=op.side or (comp.placement.side if comp.placement else "top"))
                comp.placement_origin = "user"
                comp.locked = True
                modified.append(comp.id)
                notes.append(f"Placed {comp.reference} at "
                             f"{op.x_nm / 1e6:.2f}, {op.y_nm / 1e6:.2f} mm")
            elif op.op == "set_assembly_state":
                comp = self._component(circuit, op.component_id)
                if not op.assembly_state:
                    raise InvalidRequest("set_assembly_state needs assembly_state.")
                comp.assembly_state = op.assembly_state
                modified.append(comp.id)
                notes.append(f"{comp.reference} assembly state {op.assembly_state}")
            elif op.op == "set_no_connect":
                if not op.pin:
                    raise InvalidRequest("set_no_connect needs pin.")
                key = self._resolve_pin(circuit, op.pin)
                for net in circuit.nets:
                    net.pins = [p for p in net.pins if p != key]
                circuit.nets = [n for n in circuit.nets if n.pins]
                if key not in circuit.no_connects:
                    circuit.no_connects.append(key)
                notes.append(f"Marked {key} no-connect")
                modified.append(key.split(":")[0])
            elif op.op == "set_board_size":
                if op.width_nm is None or op.height_nm is None:
                    raise InvalidRequest("set_board_size needs width_nm and height_nm.")
                if not (mm(20) <= op.width_nm <= mm(300)) or not (mm(20) <= op.height_nm <= mm(300)):
                    raise ConstraintConflict("Board size must be between 20 and 300 mm.")
                requirement.board_size = {"width_mm": op.width_nm / 1e6,
                                          "height_mm": op.height_nm / 1e6}
                if "board_width_mm" in requirement.parameters:
                    requirement.parameters["board_width_mm"].value = op.width_nm / 1e6
                    requirement.parameters["board_width_mm"].origin = "user"
                    requirement.parameters["board_height_mm"].value = op.height_nm / 1e6
                    requirement.parameters["board_height_mm"].origin = "user"
                notes.append(f"Board size {op.width_nm / 1e6:.1f} × "
                             f"{op.height_nm / 1e6:.1f} mm")
            elif op.op == "set_rule_profile":
                if not op.value:
                    raise InvalidRequest("set_rule_profile needs a profile id in value.")
                profile = load_rule_profile(op.value)
                if profile.rule_profile_id != op.value:
                    raise NotFound(f"Rule profile {op.value} is not installed.",
                                   code="RULE_PROFILE_NOT_FOUND")
                circuit.rule_profile_id = profile.rule_profile_id
                notes.append(f"Rule profile {profile.rule_profile_id} "
                             f"v{profile.version} (clearance "
                             f"{profile.clearance_nm / 1e6:.2f} mm)")
            elif op.op == "replace_component_asset":
                comp = self._component(circuit, op.component_id)
                if not op.part_id:
                    raise InvalidRequest("replace_component_asset needs part_id.")
                part = parts.resolve_part(op.part_id)
                footprint_id = op.footprint_id or part.footprint_id
                if not footprint_id:
                    raise InvalidRequest(
                        "replace_component_asset needs an explicit footprint_id for "
                        "an imported symbol.")
                footprint = parts.resolve_footprint(footprint_id)
                mapping, issues = parts.normalize_mapping(
                    part.pins, footprint, op.pin_pad_mapping, component_id=comp.id)
                structural = [issue for issue in issues
                              if issue.code in ("PINMAP_KEY_UNKNOWN", "PAD_NOT_FOUND")]
                if structural:
                    raise PinmapMismatch(structural[0].message,
                                         code=structural[0].code)
                role_map = op.pin_role_mapping or {pin.number: pin.number
                                                   for pin in part.pins}
                unknown = [old for old, new in role_map.items()
                           if not any(pin.number == new for pin in part.pins)]
                if unknown:
                    raise PinmapMismatch(
                        f"Pin role mapping targets unknown pins {unknown}",
                        code="PINMAP_MISMATCH")
                for net in circuit.nets:
                    migrated = []
                    for token in net.pins:
                        cid, _, pin = token.partition(":")
                        if cid == comp.id and pin in role_map:
                            migrated.append(f"{comp.id}:{role_map[pin]}")
                        else:
                            migrated.append(token)
                    net.pins = sorted(set(migrated))
                migrated_nc: list[str] = []
                for token in circuit.no_connects:
                    cid, _, pin = token.partition(":")
                    if cid == comp.id and pin in role_map:
                        migrated_nc.append(f"{comp.id}:{role_map[pin]}")
                    else:
                        migrated_nc.append(token)
                circuit.no_connects = sorted(set(migrated_nc))
                comp.part_id = part.part_id
                comp.symbol_id = part.symbol_id
                comp.footprint_id = footprint_id
                comp.pin_pad_map = mapping
                comp.value = op.value or part.value_label or part.mpn
                comp.footprint_instance_id = f"{comp.id}_fp"
                modified.append(comp.id)
                notes.append(f"Replaced {comp.reference} with {part.mpn}")
            elif op.op == "disconnect_terminal":
                comp = self._component(circuit, op.component_id)
                if not op.pin:
                    raise InvalidRequest("disconnect_terminal needs pin.")
                key = f"{comp.id}:{op.pin}"
                for net in circuit.nets:
                    net.pins = [token for token in net.pins if token != key]
                circuit.nets = [net for net in circuit.nets if net.pins]
                circuit.no_connects = [token for token in circuit.no_connects
                                       if token != key]
                modified.append(comp.id)
                notes.append(f"Disconnected {comp.reference} pin {op.pin}")
            elif op.op == "relayout_schematic":
                relayout = True
                notes.append("Re-laid out the schematic by functional group")
            elif op.op == "move_schematic_symbol":
                notes.append(_move_schematic_symbol(schematic, circuit, op))
                schematic_edit = True
            elif op.op == "reroute":
                notes.append("Re-routing copper")
            elif op.op == "lock_component":
                comp = self._component(circuit, op.component_id)
                comp.locked = bool(op.locked)
                modified.append(comp.id)
                notes.append(f"{'Locked' if comp.locked else 'Unlocked'} {comp.reference}")
            else:
                raise InvalidRequest(f"Unsupported patch operation {op.op}.")

        schematic_only = all(op.op in ("relayout_schematic", "move_schematic_symbol")
                             for op in patch.operations)
        if schematic_only and (relayout or schematic_edit):
            # A schematic-only edit must not move footprints or copper.
            if relayout:
                schematic = generate_schematic(circuit, title=draft.schematic.title)
            board = draft.board.model_copy(deep=True)
            checks = validate_all(project_id, version, requirement, circuit,
                                  schematic, board, recipe)
            checks = self._apply_mapping_check(circuit, checks)
            return PatchOutcome(requirement=requirement, circuit=circuit,
                                schematic=schematic, board=board, checks=checks,
                                summary="; ".join(notes), added=added,
                                modified=sorted(set(modified)), deleted=deleted)

        schematic, board, _ = rebuild(circuit, requirement, recipe, reroute=reroute,
                                      base_board=draft.board)
        checks = validate_all(project_id, version, requirement, circuit, schematic,
                              board, recipe)
        checks = self._apply_mapping_check(circuit, checks)
        return PatchOutcome(requirement=requirement, circuit=circuit,
                            schematic=schematic, board=board,
                            checks=checks, summary="; ".join(notes) or patch.note or "Design updated",
                            added=added, modified=sorted(set(modified)), deleted=deleted)

    def _apply_mapping_check(self, circuit: CircuitIR,
                             checks: ValidationReport) -> ValidationReport:
        """Block review on bad pin/pad mappings; keep template baselines intact."""
        imported = any(component.part_id.startswith("sym:")
                       or component.symbol_id.startswith("sym:")
                       for component in circuit.components)
        issues = parts.mapping_issues_for_circuit(circuit)
        if not imported and not issues:
            return checks
        errors = [issue for issue in issues if issue.severity == "error"]
        warnings = [issue for issue in issues if issue.severity == "warning"]
        status = "FAIL" if errors else ("WARN" if warnings else "PASS")
        check = CheckResult(
            check_id="PIN_PAD_MAPPING", rule_id="PIN_PAD_MAPPING",
            title="Pin / pad mapping", status=status,
            severity="error" if errors else "warning",
            required_for_review=True,
            object_ids=sorted({issue.component_id for issue in issues
                               if issue.component_id}),
            message=(f"{len(errors)} mapping error(s), {len(warnings)} warning(s)."
                     if issues else
                     "Every physical terminal maps to an existing pad."),
            evidence={"issues": [
                {"code": issue.code, "component_id": issue.component_id,
                 "pin": issue.pin, "pad": issue.pad, "message": issue.message,
                 "severity": issue.severity} for issue in issues]})
        updated = checks.model_copy(deep=True)
        updated.checks = [existing for existing in updated.checks
                          if existing.check_id != "PIN_PAD_MAPPING"] + [check]
        if status == "FAIL":
            updated.review_status = "REVIEW_BLOCKED"
        return updated

    # ------------------------------------------------------------------ helpers
    def _recipe(self, requirement: RequirementSpec) -> TemplateRecipe | None:
        if not requirement.template_id:
            return None
        try:
            return get_template(requirement.template_id)
        except NotFound:
            return None

    def _component(self, circuit: CircuitIR, component_id: str | None) -> Component:
        if not component_id:
            raise InvalidRequest("component_id is required.")
        comp = next((c for c in circuit.components if c.id == component_id), None)
        if comp is not None:
            return comp
        comp = next((c for c in circuit.components if c.reference == component_id), None)
        if comp is None:
            raise NotFound(f"Component {component_id} not found.", code="COMPONENT_NOT_FOUND")
        return comp

    def _resolve_pin(self, circuit: CircuitIR, token: str) -> str:
        if ":" not in token:
            raise InvalidRequest(f"Pin token {token} must be 'component:pin'.")
        owner, _, pin = token.partition(":")
        comp = self._component(circuit, owner)
        part = parts.resolve_part(comp.part_id)
        if not any(item.number == pin for item in part.pins):
            raise PinmapMismatch(f"{comp.reference} has no pin {pin}.",
                                 code="PINMAP_MISMATCH")
        return f"{comp.id}:{pin}"
