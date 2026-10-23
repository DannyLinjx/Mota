"""Electrical contracts for composed modules: power, interfaces, pins, addresses.

The checks operate on explicit facts supplied by module definitions. A missing
fact produces a diagnostic instead of a silent pass. Units are declared per
rule so mA/V mismatches are visible rather than compared as bare numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    kind: str
    scope: str
    parameters: dict[str, Any] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    source: str = ""
    severity: Severity = "error"


@dataclass(frozen=True)
class ContractDiagnostic:
    code: str
    message: str
    scope: str = ""
    severity: Severity = "error"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PowerFact:
    rail: str
    voltage_v: float
    current_ma: float
    tolerance_pct: float = 5.0


@dataclass(frozen=True)
class ResourceFact:
    kind: str
    name: str
    value: str | float


@dataclass(frozen=True)
class SequenceFact:
    rail: str
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortFact:
    name: str
    direction: str
    protocol: str = "analog"
    voltage_v: float | None = None
    voltage_min_v: float | None = None
    voltage_max_v: float | None = None


@dataclass(frozen=True)
class ModuleFacts:
    instance_name: str
    module_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    ports: dict[str, PortFact] = field(default_factory=dict)
    power: tuple[PowerFact, ...] = ()
    resources: tuple[ResourceFact, ...] = ()
    sequence: tuple[SequenceFact, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionFacts:
    a: str
    b: str
    net_name: str
    protocol: str = "analog"
    voltage_v: float | None = None


@dataclass(frozen=True)
class ContractReport:
    status: Literal["OK", "BLOCKED"]
    diagnostics: list[ContractDiagnostic] = field(default_factory=list)
    rules: list[RuleSpec] = field(default_factory=list)
    power_budget: dict[str, dict[str, Any]] = field(default_factory=dict)
    pin_allocation: dict[str, list[str]] = field(default_factory=dict)
    addresses: dict[str, list[str]] = field(default_factory=dict)
    sequence_order: list[str] = field(default_factory=list)


def evaluate_contract(modules: list[ModuleFacts],
                      connections: list[ConnectionFacts]) -> ContractReport:
    diagnostics: list[ContractDiagnostic] = []
    rules: list[RuleSpec] = []

    # ------------------------------------------------------------ power budget
    capacity: dict[str, float] = {}
    capacity_source: dict[str, str] = {}
    consumers: dict[str, list[tuple[str, float, str]]] = {}
    rail_voltage: dict[str, float] = {}
    for module in modules:
        for resource in module.resources:
            if resource.kind == "power_rail":
                capacity[resource.name] = capacity.get(resource.name, 0.0) + \
                    float(resource.value)
                capacity_source.setdefault(
                    resource.name, module.sources[0] if module.sources else module.module_id)
        for fact in module.power:
            consumers.setdefault(fact.rail, []).append(
                (module.instance_name, fact.current_ma, module.module_id))
            rail_voltage.setdefault(fact.rail, fact.voltage_v)
            rules.append(RuleSpec(
                rule_id=f"POWER_BUDGET:{module.instance_name}:{fact.rail}",
                kind="power_budget", scope=module.instance_name,
                parameters={"rail": fact.rail, "used_ma": fact.current_ma,
                            "limit_ma": capacity.get(fact.rail, 0.0)},
                units={"used_ma": "mA", "limit_ma": "mA", "voltage_v": "V"},
                source=module.sources[0] if module.sources else module.module_id))
            supply = module.parameters.get("supply_v")
            if supply is not None:
                expected = float(supply)
                tolerance = max(0.01, expected * fact.tolerance_pct / 100.0)
                if abs(expected - fact.voltage_v) > tolerance:
                    diagnostics.append(ContractDiagnostic(
                        code="POWER_RAIL_LEVEL",
                        message=(f"{module.instance_name} expects {expected:.2f} V but "
                                 f"rail {fact.rail} is {fact.voltage_v:.2f} V"),
                        scope=module.instance_name,
                        evidence={"rail": fact.rail, "expected_v": expected,
                                  "actual_v": fact.voltage_v}))
    for rail, rows in sorted(consumers.items()):
        used = sum(row[1] for row in rows)
        limit = capacity.get(rail)
        status = "UNKNOWN" if limit is None else \
            ("FAIL" if used > limit else "PASS")
        if limit is None:
            diagnostics.append(ContractDiagnostic(
                code="POWER_RAIL_UNKNOWN",
                message=f"rail {rail} has consumers but no declared supply capacity",
                scope=rail, severity="warning",
                evidence={"rail": rail, "used_ma": used}))
        elif used > limit:
            diagnostics.append(ContractDiagnostic(
                code="POWER_BUDGET_EXCEEDED",
                message=(f"rail {rail} needs {used:.1f} mA but supply provides "
                         f"{limit:.1f} mA"),
                scope=rail,
                evidence={"rail": rail, "used_ma": used, "limit_ma": limit,
                          "consumers": [row[0] for row in rows]}))
        rules.append(RuleSpec(
            rule_id=f"POWER_BUDGET_RAIL:{rail}", kind="power_budget", scope=rail,
            parameters={"rail": rail, "used_ma": used,
                        "limit_ma": limit if limit is not None else -1},
            units={"used_ma": "mA", "limit_ma": "mA"},
            source=capacity_source.get(rail, "composition"),
            severity="error" if status == "FAIL" else "warning"))
    power_budget = {
        rail: {"used_ma": sum(row[1] for row in rows),
               "limit_ma": capacity.get(rail),
               "status": ("UNKNOWN" if capacity.get(rail) is None else
                          ("FAIL" if sum(row[1] for row in rows) > capacity[rail]
                           else "PASS"))}
        for rail, rows in consumers.items()}

    # --------------------------------------------------------------- interfaces
    port_index: dict[str, PortFact] = {}
    for module in modules:
        for name, port in module.ports.items():
            port_index[f"{module.instance_name}.{name}"] = port
    for connection in connections:
        a = port_index.get(connection.a)
        b = port_index.get(connection.b)
        if a is None or b is None:
            diagnostics.append(ContractDiagnostic(
                code="PORT_NOT_FOUND",
                message=f"connection {connection.a} ↔ {connection.b} references "
                        "an unknown port",
                scope=connection.net_name))
            continue
        rules.append(RuleSpec(
            rule_id=f"INTERFACE:{connection.net_name}", kind="interface",
            scope=connection.net_name,
            parameters={"a": connection.a, "b": connection.b,
                        "protocol": connection.protocol,
                        "voltage_v": connection.voltage_v or 0.0},
            units={"voltage_v": "V"}, source="interface contract"))
        if connection.protocol not in (a.protocol, b.protocol) and \
                "analog" not in (a.protocol, b.protocol):
            diagnostics.append(ContractDiagnostic(
                code="INTERFACE_PROTOCOL",
                message=(f"{connection.a} ({a.protocol}) cannot connect to "
                         f"{connection.b} ({b.protocol})"),
                scope=connection.net_name))
        if not _directions_compatible(a.direction, b.direction):
            diagnostics.append(ContractDiagnostic(
                code="INTERFACE_DIRECTION",
                message=(f"{connection.a} is {a.direction} but {connection.b} is "
                         f"{b.direction}"),
                scope=connection.net_name))
        if not _levels_overlap(a, b, connection.voltage_v):
            diagnostics.append(ContractDiagnostic(
                code="INTERFACE_LEVEL",
                message=(f"{connection.a} and {connection.b} have no common "
                         "voltage range"),
                scope=connection.net_name,
                evidence={"a": a.voltage_v, "b": b.voltage_v}))

    # ----------------------------------------------------------------- pin mux
    providers: dict[str, list[str]] = {}
    demands: dict[str, list[str]] = {}
    pins_seen: dict[str, str] = {}
    for module in modules:
        for resource in module.resources:
            if resource.kind != "pin_function":
                continue
            value = str(resource.value)
            if value in ("required", "demand"):
                demands.setdefault(resource.name, []).append(module.instance_name)
            else:
                providers.setdefault(resource.name, []).append(module.instance_name)
                if value in pins_seen:
                    diagnostics.append(ContractDiagnostic(
                        code="PIN_DUPLICATE",
                        message=f"pin {value} is claimed by both "
                                f"{pins_seen[value]} and {module.instance_name}",
                        scope=value))
                pins_seen[value] = module.instance_name
    pin_allocation: dict[str, list[str]] = {}
    for name, requesters in sorted(demands.items()):
        available = providers.get(name, [])
        pin_allocation[name] = available[:len(requesters)]
        rules.append(RuleSpec(
            rule_id=f"PIN_MUX:{name}", kind="pin_mux", scope=name,
            parameters={"demand": len(requesters), "providers": len(available)},
            units={"demand": "count", "providers": "count"},
            source="pin mux contract"))
        if not available:
            diagnostics.append(ContractDiagnostic(
                code="PIN_FUNCTION_UNRESOLVED",
                message=f"no module provides pin function {name}",
                scope=name))
        elif len(requesters) > len(available):
            diagnostics.append(ContractDiagnostic(
                code="PIN_CONFLICT",
                message=(f"{len(requesters)} modules request {name} but only "
                         f"{len(available)} pin(s) are available"),
                scope=name,
                evidence={"requesters": requesters, "pins": available}))

    # --------------------------------------------------------------- addresses
    addresses: dict[str, list[str]] = {}
    for module in modules:
        for resource in module.resources:
            if resource.kind != "i2c_address":
                continue
            address = int(resource.value)
            if address < 0x08 or address > 0x77:
                diagnostics.append(ContractDiagnostic(
                    code="ADDRESS_INVALID",
                    message=f"{module.instance_name} uses reserved I2C address "
                            f"0x{address:02X}",
                    scope=module.instance_name))
            addresses.setdefault(f"0x{address:02X}", []).append(module.instance_name)
    for address, owners in sorted(addresses.items()):
        rules.append(RuleSpec(
            rule_id=f"I2C_ADDRESS:{address}", kind="address", scope=address,
            parameters={"address": address, "owners": len(owners)},
            units={"address": "7bit", "owners": "count"},
            source="I2C address contract"))
        if len(owners) > 1:
            diagnostics.append(ContractDiagnostic(
                code="ADDRESS_CONFLICT",
                message=f"I2C address {address} is used by {', '.join(owners)}",
                scope=address, evidence={"owners": owners}))

    # --------------------------------------------------------- power sequencing
    sequence: dict[str, set[str]] = {}
    sources: dict[str, ModuleFacts] = {}
    for module in modules:
        for fact in module.sequence:
            sequence.setdefault(fact.rail, set()).update(fact.after)
            sources.setdefault(fact.rail, module)
    for rail, after in sequence.items():
        for dependency in after:
            if dependency not in sequence and dependency not in {
                    row.rail for module in modules for row in module.power}:
                diagnostics.append(ContractDiagnostic(
                    code="POWER_SEQUENCE_UNKNOWN",
                    message=f"rail {rail} depends on unknown rail {dependency}",
                    scope=rail, severity="warning"))
    order, cycle = _topological(sequence)
    for rail in sequence:
        rules.append(RuleSpec(
            rule_id=f"POWER_SEQUENCE:{rail}", kind="power_sequence", scope=rail,
            parameters={"after": ",".join(sorted(sequence[rail]))},
            units={"after": "rail"}, source=sources[rail].sources[0]
            if rail in sources and sources[rail].sources else "sequence contract"))
    if cycle:
        diagnostics.append(ContractDiagnostic(
            code="POWER_SEQUENCE_CYCLE",
            message="power sequencing cycle: " + " → ".join(cycle),
            scope="power_sequence"))

    status: Literal["OK", "BLOCKED"] = (
        "BLOCKED" if any(diag.severity == "error" for diag in diagnostics)
        else "OK")
    return ContractReport(
        status=status, diagnostics=diagnostics, rules=rules,
        power_budget=power_budget, pin_allocation=pin_allocation,
        addresses=addresses, sequence_order=order)


def _directions_compatible(a: str, b: str) -> bool:
    if a == "bidirectional" or b == "bidirectional":
        return True
    if a == "power_out" and b == "power_in":
        return True
    if b == "power_out" and a == "power_in":
        return True
    if a == "output" and b == "input":
        return True
    if b == "output" and a == "input":
        return True
    return False


def _levels_overlap(a: PortFact, b: PortFact,
                    connection_voltage: float | None) -> bool:
    def bounds(port: PortFact) -> tuple[float, float] | None:
        if port.voltage_v is not None:
            return port.voltage_v, port.voltage_v
        if port.voltage_min_v is not None and port.voltage_max_v is not None:
            return port.voltage_min_v, port.voltage_max_v
        return None

    left, right = bounds(a), bounds(b)
    if left is None or right is None:
        return True
    return not (left[1] < right[0] or right[1] < left[0])


def _topological(edges: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    nodes = set(edges)
    for dependencies in edges.values():
        nodes.update(dependencies)
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []
    cycle: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle.extend(stack[stack.index(node):] + [node])
            return
        visiting.add(node)
        for dependency in sorted(edges.get(node, ())):
            visit(dependency, stack + [node])
        visiting.discard(node)
        visited.add(node)
        order.append(node)

    for node in sorted(nodes):
        visit(node, [])
    return order, cycle
