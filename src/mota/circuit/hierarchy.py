"""Hierarchical module definitions, instance paths, scoped nets and buses.

The compiler flattens a module tree into a CircuitIR without leaking local net
names between instances. Ports are bound explicitly by the parent; global
power/ground and explicit global labels are the only names merged across
scopes. Bus members expand to scalar nets and are never shorted together.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from mota.circuit import parts
from mota.domain.models import Component, CircuitIR, Net, Placement
from mota.errors import NotFound
from mota.generation.modules import slug
from mota.ir.models_v2 import (
    CircuitCompileResult, HierarchyDiagnostic, MultiPageSchematicV2,
)

Direction = Literal["input", "output", "bidirectional"]
NetClass = Literal["power", "ground", "signal"]


@dataclass(frozen=True)
class PortDef:
    name: str
    direction: Direction = "bidirectional"
    kind: Literal["signal", "bus"] = "signal"
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentDef:
    reference: str
    part_id: str
    value: str = ""
    on_board: bool = True


@dataclass(frozen=True)
class NetDef:
    name: str
    terminals: tuple[str, ...] = ()
    net_class: NetClass = "signal"
    global_name: str | None = None
    bus_member: str | None = None


@dataclass(frozen=True)
class InstanceDef:
    name: str
    definition_id: str
    port_bindings: dict[str, str] = field(default_factory=dict)
    same_page: bool = False
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleDef:
    id: str
    ports: dict[str, PortDef] = field(default_factory=dict)
    components: tuple[ComponentDef, ...] = ()
    instances: tuple[InstanceDef, ...] = ()
    nets: tuple[NetDef, ...] = ()
    buses: dict[str, tuple[str, ...]] = field(default_factory=dict)
    bus_aliases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UnitViewSpec:
    scope_name: str
    reference: str
    unit: int
    page_id: str | None = None


@dataclass(frozen=True)
class HierarchyProject:
    definitions: dict[str, ModuleDef]
    root_definition_id: str
    root_name: str = "TOP"
    locked_pages: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    unit_views: tuple[UnitViewSpec, ...] = ()


class _Compiler:
    def __init__(self, project: HierarchyProject) -> None:
        self.project = project
        self.components: list[Component] = []
        self.net_pins: dict[str, list[str]] = {}
        self.net_names: dict[str, str] = {}
        self.net_classes: dict[str, NetClass] = {}
        self.net_scopes: dict[str, str] = {}
        self.diagnostics: list[HierarchyDiagnostic] = []
        self.instance_paths: dict[str, str] = {}
        self.page_by_component: dict[str, str] = {}
        self.page_records: list[dict] = []
        self._scope_page: dict[str, str] = {}
        self._by_scope_ref: dict[tuple[str, str], Component] = {}
        self._diag_seq = 0

    # ------------------------------------------------------------------ helpers
    def diagnostic(self, code: str, message: str, *, path: str = "",
                   ref: str = "", severity: str = "error") -> None:
        self._diag_seq += 1
        self.diagnostics.append(HierarchyDiagnostic(
            id=f"hier_{self._diag_seq:04d}", code=code,
            severity=severity, instance_path=path, message=message,
            object_ref=ref))

    def _page(self, page_id: str, path: str, title: str) -> None:
        if any(row["page_id"] == page_id for row in self.page_records):
            return
        self.page_records.append({
            "page_id": page_id, "title": title, "instance_path": path,
            "page_index": len(self.page_records) + 1})

    def _net_key(self, name: str, scope: str, net_class: NetClass,
                 global_name: str | None = None) -> tuple[str, str, str]:
        if net_class in ("power", "ground") or global_name:
            label = global_name or name
            key = f"net_global_{slug(label)}"
            return key, label, "GLOBAL"
        key = f"net_{slug(scope)}_{slug(name)}"
        return key, f"{scope}/{name}", scope

    def _touch_net(self, key: str, name: str, scope: str,
                   net_class: NetClass) -> None:
        self.net_pins.setdefault(key, [])
        self.net_names.setdefault(key, name)
        self.net_classes.setdefault(key, net_class)
        self.net_scopes.setdefault(key, scope)

    def _resolve_expression(self, expression: str, scope_path: str,
                            module: ModuleDef,
                            local_nets: dict[str, str],
                            port_map: dict[str, str]) -> str | None:
        expr = expression.strip()
        if expr.startswith("BUS:"):
            return self._resolve_bus(expr[4:], scope_path, module)
        if expr.startswith("PORT:"):
            name = expr[5:]
            if name not in port_map:
                self.diagnostic("PORT_UNBOUND",
                                f"port {name} is not bound in scope {scope_path}",
                                path=scope_path, ref=name)
                return None
            return port_map[name]
        if expr.startswith("NET:"):
            expr = expr[4:]
        if expr in local_nets:
            return local_nets[expr]
        if expr in port_map:
            return port_map[expr]
        self.diagnostic("NET_UNBOUND",
                        f"net expression {expression!r} cannot be resolved",
                        path=scope_path, ref=expression)
        return None

    def _resolve_bus(self, expression: str, scope_path: str,
                     module: ModuleDef) -> str | None:
        if "[" not in expression or not expression.endswith("]"):
            self.diagnostic("BUS_SYNTAX", f"bad bus expression {expression!r}",
                            path=scope_path, ref=expression)
            return None
        head, _, member = expression[:-1].partition("[")
        bus_name = module.bus_aliases.get(head, head)
        members = module.buses.get(bus_name)
        if members is None:
            self.diagnostic("BUS_UNKNOWN", f"bus {bus_name!r} is not declared",
                            path=scope_path, ref=expression)
            return None
        if member not in members:
            self.diagnostic("BUS_MEMBER_MISSING",
                            f"bus {bus_name!r} has no member {member!r}",
                            path=scope_path, ref=expression)
            return None
        label = f"{bus_name}[{member}]"
        key, name, scope = self._net_key(label, scope_path, "signal")
        self._touch_net(key, name, scope, "signal")
        return key

    def _merge_nets(self, source: str, target: str,
                    local_nets: dict[str, str],
                    port_map: dict[str, str]) -> str:
        if source == target:
            return target
        pins = self.net_pins.pop(source, [])
        self.net_pins.setdefault(target, []).extend(pins)
        self.net_names.pop(source, None)
        self.net_classes.pop(source, None)
        self.net_scopes.pop(source, None)
        for name, key in list(local_nets.items()):
            if key == source:
                local_nets[name] = target
        for name, key in list(port_map.items()):
            if key == source:
                port_map[name] = target
        return target

    # -------------------------------------------------------------------- walk
    def walk(self, definition_id: str, path: tuple[str, ...], page_id: str,
             port_map: dict[str, str], stack: tuple[str, ...]) -> None:
        definition = self.project.definitions.get(definition_id)
        scope_path = "/".join(path)
        if definition is None:
            self.diagnostic("DEFINITION_MISSING",
                            f"module definition {definition_id!r} is missing",
                            path=scope_path, ref=definition_id)
            return
        if definition_id in stack:
            self.diagnostic("HIERARCHY_CYCLE",
                            f"module {definition_id!r} recursively contains itself",
                            path=scope_path, ref=definition_id)
            return
        stack = stack + (definition_id,)
        self._scope_page[scope_path] = page_id
        self._page(page_id, scope_path, definition.id)
        page_row = next(row for row in self.page_records
                        if row["page_id"] == page_id)
        page_row.setdefault("ports", [
            {"name": port.name, "direction": port.direction, "kind": port.kind}
            for port in definition.ports.values()])

        # Components in this sheet.
        for spec in definition.components:
            try:
                part = parts.resolve_part(spec.part_id)
            except NotFound:
                self.diagnostic("PART_UNRESOLVED",
                                f"part {spec.part_id!r} cannot be resolved",
                                path=scope_path, ref=spec.reference)
                continue
            comp_id = f"component_{slug(scope_path)}__{slug(spec.reference)}"
            component = Component(
                id=comp_id, reference=spec.reference, part_id=spec.part_id,
                symbol_id=part.symbol_id, footprint_id=part.footprint_id,
                pin_pad_map={key: list(value)
                             for key, value in part.pin_pad_map.items()},
                value=spec.value or part.value_label or part.mpn,
                placement=Placement(x_nm=0, y_nm=0),
                placement_origin="template",
                instance_path=scope_path,
                on_board=part.on_board and spec.on_board,
                parameter_bindings={})
            self.components.append(component)
            self._by_scope_ref[(scope_path, spec.reference)] = component
            self.instance_paths[comp_id] = scope_path
            self.page_by_component[comp_id] = page_id

        # Local nets and ports.
        local_nets: dict[str, str] = {}
        for net_def in definition.nets:
            if net_def.bus_member:
                key = self._resolve_bus(net_def.bus_member, scope_path, definition)
                if key is None:
                    continue
                name = self.net_names[key]
                scope = self.net_scopes[key]
                net_class = self.net_classes[key]
            else:
                key, name, scope = self._net_key(
                    net_def.name, scope_path, net_def.net_class,
                    net_def.global_name)
                net_class = net_def.net_class
                self._touch_net(key, name, scope, net_class)
            local_nets[net_def.name] = key

            # Ports first: a child port joins its local net to the parent net.
            for terminal in net_def.terminals:
                if not terminal.startswith(("PORT:", "BUS:")):
                    continue
                target = self._resolve_expression(
                    terminal, scope_path, definition, local_nets, port_map)
                if target is None:
                    continue
                current = local_nets.get(net_def.name, key)
                local_nets[net_def.name] = self._merge_nets(
                    current, target, local_nets, port_map)

            current_key = local_nets.get(net_def.name, key)
            for terminal in net_def.terminals:
                if terminal.startswith(("PORT:", "BUS:")):
                    continue
                if ":" not in terminal:
                    self.diagnostic("TERMINAL_SYNTAX",
                                    f"bad terminal {terminal!r}",
                                    path=scope_path, ref=terminal)
                    continue
                ref, _, pin = terminal.partition(":")
                component = self._by_scope_ref.get((scope_path, ref))
                if component is None:
                    self.diagnostic(
                        "TERMINAL_UNKNOWN",
                        f"{ref}:{pin} does not exist in scope {scope_path}",
                        path=scope_path, ref=terminal)
                    continue
                token = f"{component.id}:{pin}"
                pins = self.net_pins.setdefault(current_key, [])
                if token not in pins:
                    pins.append(token)

        # Child instances.
        for instance in definition.instances:
            child_path = path + (instance.name,)
            child_page = page_id if instance.same_page else \
                f"page_{slug('/'.join(child_path))}"
            child_bindings: dict[str, str] = {}
            child_def = self.project.definitions.get(instance.definition_id)
            if child_def is None:
                self.diagnostic("DEFINITION_MISSING",
                                f"module definition {instance.definition_id!r} "
                                "is missing", path=scope_path,
                                ref=instance.definition_id)
                continue
            for port in child_def.ports.values():
                expression = instance.port_bindings.get(port.name)
                if expression is None:
                    self.diagnostic(
                        "PORT_UNBOUND",
                        f"child port {port.name!r} of {instance.name!r} has no binding",
                        path=scope_path, ref=f"{instance.name}.{port.name}")
                    continue
                target = self._resolve_expression(
                    expression, scope_path, definition, local_nets, port_map)
                if target is not None:
                    child_bindings[port.name] = target
            self.walk(instance.definition_id, child_path, child_page,
                      child_bindings, stack)

    # ------------------------------------------------------------------ finish
    def circuit(self) -> CircuitIR:
        nets: list[Net] = []
        for key in sorted(self.net_pins):
            pins = sorted(set(self.net_pins[key]))
            if not pins:
                continue
            nets.append(Net(id=key, name=self.net_names[key], pins=pins,
                            net_class=self.net_classes[key]))
        return CircuitIR(
            components=list(self.components), nets=nets,
            modules=[{"module_id": self.project.root_definition_id,
                      "root": self.project.root_name}])


def compile_hierarchy(project: HierarchyProject) -> CircuitCompileResult:
    """Flatten the module tree; layout is applied separately by the caller."""
    compiler = _Compiler(project)
    root_page = f"page_{slug(project.root_name)}"
    root_bindings: dict[str, str] = {}
    root_def = project.definitions.get(project.root_definition_id)
    if root_def is None:
        compiler.diagnostic("DEFINITION_MISSING",
                            f"root definition {project.root_definition_id!r} "
                            "is missing", ref=project.root_definition_id)
    else:
        for port in root_def.ports.values():
            key, name, scope = compiler._net_key(port.name, project.root_name,
                                                 "signal", f"ROOT_{port.name}")
            compiler._touch_net(key, name, scope, "signal")
            root_bindings[port.name] = key
    compiler.walk(project.root_definition_id, (project.root_name,), root_page,
                  root_bindings, ())
    circuit = compiler.circuit()
    unit_views: list[dict] = []
    for spec in project.unit_views:
        component = compiler._by_scope_ref.get((spec.scope_name, spec.reference))
        if component is None:
            compiler.diagnostic(
                "UNIT_VIEW_UNKNOWN",
                f"unit view {spec.scope_name}/{spec.reference} has no component",
                path=spec.scope_name, ref=spec.reference)
            continue
        page_id = spec.page_id or compiler._scope_page.get(
            spec.scope_name, compiler.page_by_component.get(component.id, root_page))
        unit_views.append({"component_id": component.id, "unit": spec.unit,
                           "page_id": page_id})
    summary = {
        "components": sorted(
            (c.id, c.reference, c.part_id, c.instance_path,
             tuple(sorted((k, tuple(v)) for k, v in c.pin_pad_map.items())))
            for c in circuit.components),
        "nets": sorted((n.id, n.name, tuple(sorted(n.pins)))
                       for n in circuit.nets),
    }
    semantic_hash = hashlib.sha256(
        json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest()
    return CircuitCompileResult(
        circuit=circuit,
        schematic=MultiPageSchematicV2(page_w_nm=297_000_000,
                                       page_h_nm=210_000_000),
        diagnostics=compiler.diagnostics,
        instance_paths=compiler.instance_paths,
        page_by_component=compiler.page_by_component,
        page_records=compiler.page_records,
        unit_views=unit_views,
        net_scopes=compiler.net_scopes,
        semantic_hash=semantic_hash)
