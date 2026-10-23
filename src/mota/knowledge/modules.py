"""Composable circuit modules with typed operations and electrical contracts.

Three existing templates are represented as modules, plus an explicitly modeled
MCU system and interface/power modules. Composition is data-driven: parameters
are validated before any component is created; unknown parameters block the
composition instead of being ignored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from mota.circuit import parts
from mota.constraints.electrical import (
    ConnectionFacts, ContractDiagnostic, ContractReport, ModuleFacts, PortFact,
    PowerFact, ResourceFact, SequenceFact, evaluate_contract,
)
from mota.domain.models import CircuitIR, Component, Net, PatchOperation
from mota.errors import NotFound
from mota.generation.modules import slug

Direction = Literal["input", "output", "bidirectional", "power_in", "power_out"]
NetClass = Literal["power", "ground", "signal"]


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    unit: str
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[Any, ...] = ()
    default: Any = None
    required: bool = False


@dataclass(frozen=True)
class PortSpec:
    name: str
    direction: Direction
    protocol: str = "analog"
    voltage_v: float | None = None
    voltage_min_v: float | None = None
    voltage_max_v: float | None = None
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class ComponentSpec:
    role: str
    part_id: str
    value: str = ""


@dataclass(frozen=True)
class NetSpec:
    name: str
    terminals: tuple[str, ...]
    net_class: NetClass = "signal"
    protocol: str = "analog"
    voltage_v: float | None = None


@dataclass(frozen=True)
class ResourceSpec:
    kind: str
    name: str
    value: str | float


@dataclass(frozen=True)
class PowerSpec:
    rail: str
    voltage_v: float
    current_ma: float
    tolerance_pct: float = 5.0
    current_parameter: str | None = None


@dataclass(frozen=True)
class SequenceSpec:
    rail: str
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleDef:
    module_id: str
    version: int
    title: str
    description: str
    applicability: str
    parameters: tuple[ParameterSpec, ...] = ()
    ports: dict[str, PortSpec] = field(default_factory=dict)
    components: tuple[ComponentSpec, ...] = ()
    nets: tuple[NetSpec, ...] = ()
    resources: tuple[ResourceSpec, ...] = ()
    power: tuple[PowerSpec, ...] = ()
    sequence: tuple[SequenceSpec, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleInstanceSpec:
    instance_name: str
    module_id: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortConnectionSpec:
    a: str
    b: str
    net_name: str = ""
    protocol: str = ""
    voltage_v: float | None = None


@dataclass
class CompositionResult:
    scenario_id: str
    status: Literal["OK", "BLOCKED"]
    diagnostics: list[ContractDiagnostic]
    operations: list[PatchOperation]
    circuit: CircuitIR | None
    contract: ContractReport | None
    sources: dict[str, list[str]]
    module_instances: list[str]
    semantic_hash: str = ""


def _local_parts() -> None:
    parts.make_runtime_part(
        part_id="mota:mcu_stm32g071k8", mpn="STM32G071K8T6",
        description=("32-pin MCU modeled as a 9-pin composition subset: VDD, VSS, "
                     "NRST, BOOT0, PA9/PA10 UART, PB6/PB7 I2C, PA5 GPIO. "
                     "Package model is approximate."),
        pins=[
            ("1", "VDD", "power_in"), ("2", "VSS", "power_in"),
            ("3", "NRST", "input"), ("4", "BOOT0", "input"),
            ("5", "PA9", "bidirectional"), ("6", "PA10", "bidirectional"),
            ("7", "PB6", "bidirectional"), ("8", "PB7", "bidirectional"),
            ("9", "PA5", "bidirectional"),
        ], reference_prefix="U")
    parts.make_runtime_part(
        part_id="mota:at24c02", mpn="AT24C02",
        description="2 Kbit I2C EEPROM, SOIC-8 pin subset, approximate package.",
        pins=[
            ("1", "A0", "input"), ("2", "A1", "input"), ("3", "A2", "input"),
            ("4", "GND", "power_in"), ("5", "SDA", "bidirectional"),
            ("6", "SCL", "input"), ("7", "WP", "input"), ("8", "VCC", "power_in"),
        ], reference_prefix="U")


_local_parts()


def _module(
    module_id: str, title: str, description: str, applicability: str,
    *, version: int = 1,
    parameters: tuple[ParameterSpec, ...] = (),
    ports: dict[str, PortSpec] | None = None,
    components: tuple[ComponentSpec, ...] = (),
    nets: tuple[NetSpec, ...] = (),
    resources: tuple[ResourceSpec, ...] = (),
    power: tuple[PowerSpec, ...] = (),
    sequence: tuple[SequenceSpec, ...] = (),
    sources: tuple[str, ...] = ()) -> ModuleDef:
    return ModuleDef(module_id=module_id, version=version, title=title,
                     description=description, applicability=applicability,
                     parameters=parameters, ports=ports or {},
                     components=components, nets=nets, resources=resources,
                     power=power, sequence=sequence, sources=sources)


POWER_INPUT = _module(
    "power_input_5v", "5 V DC input",
    "Two-pin DC input header feeding VIN.",
    "5 V nominal input, 4.5–5.5 V operating window; connector current rating not verified.",
    parameters=(ParameterSpec("input_voltage_v", "V", minimum=4.5, maximum=5.5,
                              default=5.0),),
    ports={"VIN": PortSpec("VIN", "power_out", "power", 5.0),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0)},
    components=(ComponentSpec("J_PWR", "conn_1x02", "DC IN"),),
    nets=(NetSpec("VIN", ("J_PWR:1", "PORT:VIN"), "power", "power", 5.0),
          NetSpec("GND", ("J_PWR:2", "PORT:GND"), "ground", "power", 0.0)),
    resources=(ResourceSpec("power_rail", "VIN", 1000.0),),
    sequence=(SequenceSpec("VIN"),),
    sources=("Mota power_input_5v module v1 (connector rating NOT_VERIFIED)",))

POWER_3V3 = _module(
    "power_3v3", "3.3 V LDO rail",
    "MCP1700-3302E TO-92 regulator with input and output capacitors.",
    "VIN 4.5–6.0 V, output 3.3 V, up to 250 mA; thermal margin not verified.",
    parameters=(ParameterSpec("input_voltage_v", "V", minimum=4.5, maximum=6.0,
                              default=5.0),),
    ports={"VIN": PortSpec("VIN", "power_in", "power", voltage_min_v=4.5,
                          voltage_max_v=6.0),
           "3V3": PortSpec("3V3", "power_out", "power", 3.3),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0)},
    components=(ComponentSpec("U_REG", "mcp1700_3302e_to", "3.3 V LDO"),
                ComponentSpec("C_IN", "c_2u2_50v", "2.2uF"),
                ComponentSpec("C_OUT", "c_10u_16v", "10uF")),
    nets=(NetSpec("VIN", ("U_REG:2", "C_IN:1", "PORT:VIN"), "power", "power", 5.0),
          NetSpec("3V3", ("U_REG:3", "C_OUT:1", "PORT:3V3"), "power", "power", 3.3),
          NetSpec("GND", ("U_REG:1", "C_IN:2", "C_OUT:2", "PORT:GND"),
                  "ground", "power", 0.0)),
    resources=(ResourceSpec("power_rail", "3V3", 250.0),),
    sequence=(SequenceSpec("3V3", ("VIN",)),),
    sources=("D1 MCP1700-3302E datasheet", "Mota power_3v3 module v1"))

MCU = _module(
    "mcu_stm32g071", "STM32G071K8 MCU system",
    "Explicit MCU model with decoupling, BOOT0 bias and named interface ports.",
    "3.3 V nominal (1.8–3.6 V), 16 MHz modeled clock; only a 9-pin subset of "
    "the 32-pin package is modeled, so this is not a full-package pinout claim.",
    parameters=(
        ParameterSpec("supply_v", "V", minimum=1.8, maximum=3.6, default=3.3),
        ParameterSpec("clock_hz", "Hz", minimum=1_000_000, maximum=64_000_000,
                      default=16_000_000),
        ParameterSpec("package", "", enum=("LQFP-32",), default="LQFP-32"),
    ),
    ports={
        "3V3": PortSpec("3V3", "power_in", "power", 3.3),
        "GND": PortSpec("GND", "bidirectional", "power", 0.0),
        "SDA": PortSpec("SDA", "bidirectional", "i2c", 3.3),
        "SCL": PortSpec("SCL", "bidirectional", "i2c", 3.3),
        "TX": PortSpec("TX", "output", "uart", 3.3),
        "RX": PortSpec("RX", "input", "uart", 3.3),
        "NRST": PortSpec("NRST", "input", "gpio", 3.3, required=False),
        "GPIO": PortSpec("GPIO", "output", "gpio", 3.3, required=False),
    },
    components=(ComponentSpec("U_MCU", "mota:mcu_stm32g071k8", "STM32G071K8"),
                ComponentSpec("C_DEC1", "c_100n_50v", "100nF"),
                ComponentSpec("C_DEC2", "c_10u_16v", "10uF"),
                ComponentSpec("R_BOOT", "r_10k", "10k")),
    nets=(NetSpec("3V3", ("U_MCU:1", "C_DEC1:1", "C_DEC2:1", "PORT:3V3"),
                  "power", "power", 3.3),
          NetSpec("GND", ("U_MCU:2", "C_DEC1:2", "C_DEC2:2", "R_BOOT:2",
                          "PORT:GND"), "ground", "power", 0.0),
          NetSpec("BOOT", ("U_MCU:4", "R_BOOT:1")),
          NetSpec("SDA", ("U_MCU:8", "PORT:SDA"), protocol="i2c", voltage_v=3.3),
          NetSpec("SCL", ("U_MCU:7", "PORT:SCL"), protocol="i2c", voltage_v=3.3),
          NetSpec("TX", ("U_MCU:5", "PORT:TX"), protocol="uart", voltage_v=3.3),
          NetSpec("RX", ("U_MCU:6", "PORT:RX"), protocol="uart", voltage_v=3.3),
          NetSpec("NRST", ("U_MCU:3", "PORT:NRST"), protocol="gpio", voltage_v=3.3),
          NetSpec("GPIO", ("U_MCU:9", "PORT:GPIO"), protocol="gpio", voltage_v=3.3)),
    resources=(ResourceSpec("pin_function", "uart_tx", "PA9"),
               ResourceSpec("pin_function", "uart_rx", "PA10"),
               ResourceSpec("pin_function", "i2c_scl", "PB6"),
               ResourceSpec("pin_function", "i2c_sda", "PB7"),
               ResourceSpec("pin_function", "gpio_0", "PA5")),
    power=(PowerSpec("3V3", 3.3, 12.0),),
    sources=("D2 STM32G071x8 datasheet (subset pinout, NOT full package)",
             "Mota mcu_stm32g071 module v1"))

I2C_TEMPERATURE = _module(
    "i2c_temperature", "I2C temperature sensor",
    "DS1621 sensor with I2C pull-ups, decoupling and address straps.",
    "3.3 V bus, 100 kHz or 400 kHz; address 0x48 with A2/A1/A0 tied low.",
    parameters=(
        ParameterSpec("address", "7bit", enum=(0x48,), default=0x48),
        ParameterSpec("bus_speed_hz", "Hz", enum=(100_000, 400_000),
                      default=100_000),
        ParameterSpec("supply_v", "V", minimum=2.7, maximum=5.5, default=3.3),
    ),
    ports={"3V3": PortSpec("3V3", "power_in", "power", 3.3),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0),
           "SDA": PortSpec("SDA", "bidirectional", "i2c", 3.3),
           "SCL": PortSpec("SCL", "bidirectional", "i2c", 3.3)},
    components=(ComponentSpec("U_SENSOR", "ds1621_plus", "DS1621"),
                ComponentSpec("R_SDA", "r_10k", "10k"),
                ComponentSpec("R_SCL", "r_10k", "10k"),
                ComponentSpec("C_DEC", "c_100n_50v", "100nF")),
    nets=(NetSpec("3V3", ("U_SENSOR:8", "R_SDA:1", "R_SCL:1", "C_DEC:1",
                          "PORT:3V3"), "power", "power", 3.3),
          NetSpec("GND", ("U_SENSOR:4", "U_SENSOR:5", "U_SENSOR:6", "U_SENSOR:7",
                          "C_DEC:2", "PORT:GND"), "ground", "power", 0.0),
          NetSpec("SDA", ("U_SENSOR:1", "R_SDA:2", "PORT:SDA"), protocol="i2c",
                  voltage_v=3.3),
          NetSpec("SCL", ("U_SENSOR:2", "R_SCL:2", "PORT:SCL"), protocol="i2c",
                  voltage_v=3.3)),
    resources=(ResourceSpec("i2c_address", "sensor", 0x48),),
    power=(PowerSpec("3V3", 3.3, 1.0),),
    sources=("D3 DS1621 datasheet", "Mota i2c_temperature module v1"))

I2C_EEPROM = _module(
    "i2c_eeprom", "I2C EEPROM",
    "AT24C02 with pull-ups, decoupling and WP tied low.",
    "1.8–5.5 V, 100/400 kHz, address 0x50 with A2/A1/A0 tied low.",
    parameters=(
        ParameterSpec("address", "7bit", enum=(0x50,), default=0x50),
        ParameterSpec("bus_speed_hz", "Hz", enum=(100_000, 400_000),
                      default=100_000),
        ParameterSpec("supply_v", "V", minimum=1.8, maximum=5.5, default=3.3),
    ),
    ports={"3V3": PortSpec("3V3", "power_in", "power", 3.3),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0),
           "SDA": PortSpec("SDA", "bidirectional", "i2c", 3.3),
           "SCL": PortSpec("SCL", "bidirectional", "i2c", 3.3)},
    components=(ComponentSpec("U_EEPROM", "mota:at24c02", "AT24C02"),
                ComponentSpec("R_SDA", "r_10k", "10k"),
                ComponentSpec("R_SCL", "r_10k", "10k"),
                ComponentSpec("C_DEC", "c_100n_50v", "100nF")),
    nets=(NetSpec("3V3", ("U_EEPROM:8", "R_SDA:1", "R_SCL:1", "C_DEC:1",
                          "PORT:3V3"), "power", "power", 3.3),
          NetSpec("GND", ("U_EEPROM:1", "U_EEPROM:2", "U_EEPROM:3", "U_EEPROM:4",
                          "U_EEPROM:7", "C_DEC:2", "PORT:GND"),
                  "ground", "power", 0.0),
          NetSpec("SDA", ("U_EEPROM:5", "R_SDA:2", "PORT:SDA"), protocol="i2c",
                  voltage_v=3.3),
          NetSpec("SCL", ("U_EEPROM:6", "R_SCL:2", "PORT:SCL"), protocol="i2c",
                  voltage_v=3.3)),
    resources=(ResourceSpec("i2c_address", "eeprom", 0x50),),
    power=(PowerSpec("3V3", 3.3, 3.0),),
    sources=("D4 AT24C02 datasheet", "Mota i2c_eeprom module v1"))

UART_HEADER = _module(
    "uart_header", "UART header",
    "Four-pin header exposing VCC, GND, TX and RX.",
    "3.3 V or 5 V logic header; not a transceiver and no ESD protection modeled.",
    parameters=(ParameterSpec("voltage_v", "V", enum=(3.3, 5.0), default=3.3),
                ParameterSpec("flow_control", "", enum=(False, True),
                              default=False)),
    ports={"VCC": PortSpec("VCC", "power_in", "power", 3.3),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0),
           "TX": PortSpec("TX", "input", "uart", 3.3),
           "RX": PortSpec("RX", "output", "uart", 3.3)},
    components=(ComponentSpec("J_UART", "conn_1x04", "UART"),),
    nets=(NetSpec("VCC", ("J_UART:1", "PORT:VCC"), "power", "power", 3.3),
          NetSpec("GND", ("J_UART:2", "PORT:GND"), "ground", "power", 0.0),
          NetSpec("TX", ("J_UART:3", "PORT:TX"), protocol="uart", voltage_v=3.3),
          NetSpec("RX", ("J_UART:4", "PORT:RX"), protocol="uart", voltage_v=3.3)),
    resources=(ResourceSpec("pin_function", "uart_tx", "demand"),
               ResourceSpec("pin_function", "uart_rx", "demand")),
    sources=("Mota uart_header module v1 (header only; no transceiver)",))

LED_INDICATOR = _module(
    "led_indicator", "GPIO LED indicator",
    "LED with series resistor driven by a GPIO.",
    "3.3 V GPIO, 1–20 mA forward current; LED polarity must be checked physically.",
    parameters=(ParameterSpec("color", "", enum=("red",), default="red"),
                ParameterSpec("forward_current_ma", "mA", minimum=1, maximum=20,
                              default=5)),
    ports={"SIG": PortSpec("SIG", "input", "gpio", 3.3),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0)},
    components=(ComponentSpec("R_LED", "r_1k", "1k"),
                ComponentSpec("D_LED", "led_red_5mm", "red LED")),
    nets=(NetSpec("SIG", ("R_LED:1", "PORT:SIG"), protocol="gpio", voltage_v=3.3),
          NetSpec("MID", ("R_LED:2", "D_LED:1")),
          NetSpec("GND", ("D_LED:2", "PORT:GND"), "ground", "power", 0.0)),
    resources=(ResourceSpec("pin_function", "gpio_0", "demand"),),
    power=(PowerSpec("3V3", 3.3, 5.0, current_parameter="forward_current_ma"),),
    sources=("Mota led_indicator module v1; LED polarity NOT_CHECKED",))

LED_TIMER = _module(
    "led_timer", "NE555 LED timer",
    "Migrated led_timer template: NE555 astable with timing network and LED.",
    "5 V nominal, 0.5–10 Hz target; actual frequency tolerance NOT_VERIFIED "
    "without measurement.",
    parameters=(
        ParameterSpec("supply_voltage_v", "V", minimum=4.5, maximum=5.5,
                      default=5.0),
        ParameterSpec("target_frequency_hz", "Hz", minimum=0.5, maximum=10.0,
                      default=1.0),
        ParameterSpec("ra_kohm", "kohm", minimum=1, maximum=1000, default=10),
        ParameterSpec("rb_kohm", "kohm", minimum=1, maximum=1000, default=68),
        ParameterSpec("timing_cap_uf", "uF", minimum=0.1, maximum=1000,
                      default=10),
    ),
    ports={"VCC": PortSpec("VCC", "power_in", "power", 5.0),
           "GND": PortSpec("GND", "bidirectional", "power", 0.0),
           "OUT": PortSpec("OUT", "output", "gpio", 5.0, required=False)},
    components=(ComponentSpec("U_TIMER", "ne555p", "NE555"),
                ComponentSpec("R_A", "r_10k", "10k"),
                ComponentSpec("R_B", "r_68k", "68k"),
                ComponentSpec("C_T", "c_10u_16v", "10uF"),
                ComponentSpec("C_CTRL", "c_10n_50v", "10nF"),
                ComponentSpec("R_LED", "r_1k", "1k"),
                ComponentSpec("D_LED", "led_red_5mm", "red LED")),
    nets=(NetSpec("VCC", ("U_TIMER:8", "U_TIMER:4", "R_A:1", "PORT:VCC"),
                  "power", "power", 5.0),
          NetSpec("GND", ("U_TIMER:1", "C_T:2", "C_CTRL:2", "D_LED:2",
                          "PORT:GND"), "ground", "power", 0.0),
          NetSpec("DISCH", ("U_TIMER:7", "R_A:2", "R_B:1")),
          NetSpec("THRES", ("U_TIMER:6", "U_TIMER:2", "R_B:2", "C_T:1")),
          NetSpec("CTRL", ("U_TIMER:5", "C_CTRL:1")),
          NetSpec("TIMER_OUT", ("U_TIMER:3", "R_LED:1", "PORT:OUT"),
                  protocol="gpio", voltage_v=5.0),
          NetSpec("LED_MID", ("R_LED:2", "D_LED:1"))),
    power=(PowerSpec("VIN", 5.0, 15.0),),
    sequence=(SequenceSpec("VIN"),),
    sources=("D5 NE555 datasheet", "Mota led_timer template migrated to module v1"))


MODULES: dict[str, ModuleDef] = {
    module.module_id: module for module in (
        POWER_INPUT, POWER_3V3, MCU, I2C_TEMPERATURE, I2C_EEPROM, UART_HEADER,
        LED_INDICATOR, LED_TIMER,
    )
}


def get_module(module_id: str) -> ModuleDef:
    module = MODULES.get(module_id)
    if module is None:
        raise NotFound(f"module {module_id!r} is not defined",
                       code="MODULE_NOT_FOUND")
    return module


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #

def compose_modules(instances: list[ModuleInstanceSpec],
                    connections: list[PortConnectionSpec],
                    *, scenario_id: str = "composition",
                    registry: dict[str, ModuleDef] | None = None
                    ) -> CompositionResult:
    registry = registry or MODULES
    diagnostics: list[ContractDiagnostic] = []
    sources: dict[str, list[str]] = {}
    values: dict[str, dict[str, Any]] = {}
    definitions: dict[str, ModuleDef] = {}
    names: set[str] = set()

    for instance in instances:
        if instance.instance_name in names:
            diagnostics.append(ContractDiagnostic(
                code="INSTANCE_DUPLICATE",
                message=f"instance {instance.instance_name} is defined twice",
                scope=instance.instance_name))
            continue
        names.add(instance.instance_name)
        module = registry.get(instance.module_id)
        if module is None:
            diagnostics.append(ContractDiagnostic(
                code="MODULE_NOT_FOUND",
                message=f"module {instance.module_id} is not defined",
                scope=instance.instance_name))
            continue
        definitions[instance.instance_name] = module
        sources[instance.instance_name] = list(module.sources)
        values[instance.instance_name] = _validate_parameters(
            instance, module, diagnostics)

    if any(diag.severity == "error" for diag in diagnostics):
        return CompositionResult(
            scenario_id=scenario_id, status="BLOCKED",
            diagnostics=diagnostics, operations=[], circuit=None,
            contract=None, sources=sources,
            module_instances=[row.instance_name for row in instances])

    components: list[Component] = []
    component_by_role: dict[tuple[str, str], Component] = {}
    counters: dict[str, int] = {}
    for instance in instances:
        module = definitions[instance.instance_name]
        for spec in module.components:
            try:
                part = parts.resolve_part(spec.part_id)
            except NotFound:
                diagnostics.append(ContractDiagnostic(
                    code="PART_UNRESOLVED",
                    message=f"{instance.instance_name}.{spec.role} references "
                            f"unknown part {spec.part_id}",
                    scope=instance.instance_name))
                continue
            prefix = part.reference_prefix or "U"
            counters[prefix] = counters.get(prefix, 0) + 1
            component = Component(
                id=f"component_{slug(instance.instance_name)}__{slug(spec.role)}",
                reference=f"{prefix}{counters[prefix]}",
                part_id=spec.part_id, symbol_id=part.symbol_id,
                footprint_id=part.footprint_id,
                pin_pad_map={key: list(value)
                             for key, value in part.pin_pad_map.items()},
                value=spec.value or part.value_label or part.mpn,
                instance_path=instance.instance_name, on_board=part.on_board,
                parameter_bindings={key: str(value) for key, value in
                                    values[instance.instance_name].items()})
            components.append(component)
            component_by_role[(instance.instance_name, spec.role)] = component
    if any(diag.severity == "error" for diag in diagnostics):
        return CompositionResult(
            scenario_id=scenario_id, status="BLOCKED",
            diagnostics=diagnostics, operations=[], circuit=None,
            contract=None, sources=sources,
            module_instances=[row.instance_name for row in instances])

    net_data: dict[str, dict[str, Any]] = {}
    port_net: dict[tuple[str, str], str] = {}
    for instance in instances:
        module = definitions[instance.instance_name]
        for net_spec in module.nets:
            key, name = _net_key(instance.instance_name, net_spec)
            bucket = net_data.setdefault(key, {
                "name": name, "class": net_spec.net_class, "pins": set()})
            for terminal in net_spec.terminals:
                if terminal.startswith("PORT:"):
                    port = terminal[5:]
                    if port not in module.ports:
                        diagnostics.append(ContractDiagnostic(
                            code="PORT_NOT_FOUND",
                            message=f"{instance.instance_name}.{net_spec.name} "
                                    f"references unknown port {port}",
                            scope=instance.instance_name))
                    port_net[(instance.instance_name, port)] = key
                    continue
                role, _, pin = terminal.partition(":")
                component = component_by_role.get((instance.instance_name, role))
                if component is None:
                    diagnostics.append(ContractDiagnostic(
                        code="TERMINAL_UNKNOWN",
                        message=f"{instance.instance_name}.{net_spec.name} "
                                f"references unknown role {role}",
                        scope=instance.instance_name))
                    continue
                bucket["pins"].add(f"{component.id}:{pin}")
    if any(diag.severity == "error" for diag in diagnostics):
        return CompositionResult(
            scenario_id=scenario_id, status="BLOCKED",
            diagnostics=diagnostics, operations=[], circuit=None,
            contract=None, sources=sources,
            module_instances=[row.instance_name for row in instances])

    # Merge module ports through explicit connections.
    used_ports: set[tuple[str, str]] = set()
    for index, connection in enumerate(connections):
        for token in (connection.a, connection.b):
            name, _, port = token.partition(".")
            used_ports.add((name, port))
        key_a = port_net.get(tuple(connection.a.split(".", 1)))
        key_b = port_net.get(tuple(connection.b.split(".", 1)))
        if key_a is None or key_b is None:
            diagnostics.append(ContractDiagnostic(
                code="PORT_NOT_FOUND",
                message=f"connection {connection.a} ↔ {connection.b} references "
                        "an unknown port", scope=connection.net_name))
            continue
        if key_a == key_b:
            continue
        global_key = next((key for key in (key_a, key_b)
                           if key.startswith("net_global_")), None)
        target = global_key or (f"net_{slug(connection.net_name)}"
                                if connection.net_name else f"net_link_{index}")
        if global_key is None:
            net_data.setdefault(target, {
                "name": connection.net_name or f"{connection.a}__{connection.b}",
                "class": "signal", "pins": set()})
        for source_key in (key_a, key_b):
            if source_key == target:
                continue
            net_data[target]["pins"].update(net_data[source_key]["pins"])
            net_data.pop(source_key, None)
            for port_key, value in list(port_net.items()):
                if value == source_key:
                    port_net[port_key] = target

    for instance in instances:
        module = definitions[instance.instance_name]
        for port in module.ports.values():
            if port.required and (instance.instance_name, port.name) not in used_ports:
                diagnostics.append(ContractDiagnostic(
                    code="PORT_UNCONNECTED",
                    message=f"{instance.instance_name}.{port.name} is required but "
                            "has no connection",
                    scope=instance.instance_name, severity="warning"))

    nets: list[Net] = []
    for key, data in sorted(net_data.items()):
        pins = sorted(data["pins"])
        if not pins:
            continue
        if len(pins) == 1:
            diagnostics.append(ContractDiagnostic(
                code="NET_SINGLE_PIN",
                message=f"net {data['name']} has only one pin",
                scope=data["name"], severity="warning"))
        nets.append(Net(id=key, name=data["name"], pins=pins,
                        net_class=data["class"]))
    circuit = CircuitIR(components=components, nets=nets,
                        library_snapshot_id="module-composition")

    operations: list[PatchOperation] = []
    for component in components:
        operations.append(PatchOperation(
            op="add_component", part_id=component.part_id,
            reference=component.reference, value=component.value,
            instance_path=component.instance_path,
            parameter_bindings=dict(component.parameter_bindings)))
    ref_by_id = {component.id: component.reference for component in components}
    for net in nets:
        if len(net.pins) < 2:
            continue
        terminals = []
        for token in net.pins:
            component_id, _, pin = token.partition(":")
            terminals.append(f"{ref_by_id[component_id]}:{pin}")
        operations.append(PatchOperation(
            op="connect_terminals", terminals=terminals,
            net_name=net.name, net_class=net.net_class))

    module_facts = []
    for instance in instances:
        module = definitions[instance.instance_name]
        module_facts.append(ModuleFacts(
            instance_name=instance.instance_name, module_id=module.module_id,
            parameters=values[instance.instance_name],
            ports={name: PortFact(
                name=name, direction=port.direction, protocol=port.protocol,
                voltage_v=port.voltage_v, voltage_min_v=port.voltage_min_v,
                voltage_max_v=port.voltage_max_v)
                for name, port in module.ports.items()},
            power=tuple(PowerFact(
                rail=row.rail, voltage_v=row.voltage_v,
                current_ma=float(values[instance.instance_name].get(
                    row.current_parameter, row.current_ma))
                if row.current_parameter else row.current_ma)
                for row in module.power),
            resources=tuple(ResourceFact(kind=row.kind, name=row.name,
                                         value=row.value)
                            for row in module.resources),
            sequence=tuple(SequenceFact(rail=row.rail, after=row.after)
                           for row in module.sequence),
            sources=module.sources))
    connection_facts = [
        ConnectionFacts(a=row.a, b=row.b, net_name=row.net_name,
                        protocol=row.protocol, voltage_v=row.voltage_v)
        for row in connections]
    contract = evaluate_contract(module_facts, connection_facts)
    diagnostics.extend(contract.diagnostics)
    status = "BLOCKED" if any(diag.severity == "error"
                              for diag in diagnostics) else "OK"
    semantic_hash = _semantic_hash(circuit, values)
    return CompositionResult(
        scenario_id=scenario_id, status=status, diagnostics=diagnostics,
        operations=operations, circuit=circuit, contract=contract,
        sources=sources,
        module_instances=[row.instance_name for row in instances],
        semantic_hash=semantic_hash)


def _validate_parameters(instance: ModuleInstanceSpec, module: ModuleDef,
                         diagnostics: list[ContractDiagnostic]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    specs = {spec.name: spec for spec in module.parameters}
    for name in instance.parameters:
        if name not in specs:
            diagnostics.append(ContractDiagnostic(
                code="UNKNOWN_PARAMETER",
                message=f"{instance.instance_name}: module {module.module_id} has "
                        f"no parameter {name!r}",
                scope=instance.instance_name,
                evidence={"parameter": name}))
    for name, spec in specs.items():
        if name in instance.parameters:
            value = instance.parameters[name]
        elif spec.default is not None:
            value = spec.default
        elif spec.required:
            diagnostics.append(ContractDiagnostic(
                code="MISSING_PARAMETER",
                message=f"{instance.instance_name}: missing required parameter "
                        f"{name}", scope=instance.instance_name))
            continue
        else:
            continue
        if spec.enum and value not in spec.enum:
            diagnostics.append(ContractDiagnostic(
                code="PARAMETER_OUT_OF_RANGE",
                message=(f"{instance.instance_name}.{name}={value!r} is not in "
                         f"{list(spec.enum)}"),
                scope=instance.instance_name,
                evidence={"parameter": name, "value": value,
                          "allowed": [str(item) for item in spec.enum]}))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.minimum is not None and value < spec.minimum:
                diagnostics.append(ContractDiagnostic(
                    code="PARAMETER_OUT_OF_RANGE",
                    message=(f"{instance.instance_name}.{name}={value} is below "
                             f"minimum {spec.minimum} {spec.unit}"),
                    scope=instance.instance_name,
                    evidence={"parameter": name, "value": value,
                              "minimum": spec.minimum, "unit": spec.unit}))
            if spec.maximum is not None and value > spec.maximum:
                diagnostics.append(ContractDiagnostic(
                    code="PARAMETER_OUT_OF_RANGE",
                    message=(f"{instance.instance_name}.{name}={value} is above "
                             f"maximum {spec.maximum} {spec.unit}"),
                    scope=instance.instance_name,
                    evidence={"parameter": name, "value": value,
                              "maximum": spec.maximum, "unit": spec.unit}))
        values[name] = value
    return values


def _net_key(instance_name: str, net: NetSpec) -> tuple[str, str]:
    if net.net_class in ("power", "ground"):
        key = f"net_global_{slug(net.name)}"
        return key, net.name
    return f"net_{slug(instance_name)}_{slug(net.name)}", \
        f"{instance_name}/{net.name}"


def _semantic_hash(circuit: CircuitIR, values: dict[str, dict[str, Any]]) -> str:
    payload = {
        "components": sorted(
            (c.id, c.reference, c.part_id, c.instance_path,
             tuple(sorted((k, tuple(v)) for k, v in c.pin_pad_map.items())))
            for c in circuit.components),
        "nets": sorted((n.id, n.name, tuple(sorted(n.pins)))
                       for n in circuit.nets),
        "parameters": values,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Three original compositions
# --------------------------------------------------------------------------- #

def scenario_mcu_i2c_temp_uart() -> tuple[list[ModuleInstanceSpec],
                                          list[PortConnectionSpec]]:
    return _scenario_base(i2c=["TEMP"], include_led=False, uart_count=1)


def scenario_mcu_i2c_eeprom_uart() -> tuple[list[ModuleInstanceSpec],
                                            list[PortConnectionSpec]]:
    return _scenario_base(i2c=["EEPROM"], include_led=True, uart_count=1)


def scenario_mcu_two_i2c_led_uart() -> tuple[list[ModuleInstanceSpec],
                                             list[PortConnectionSpec]]:
    return _scenario_base(i2c=["TEMP", "EEPROM"], include_led=True, uart_count=1)


def _scenario_base(*, i2c: list[str], include_led: bool, uart_count: int
                   ) -> tuple[list[ModuleInstanceSpec], list[PortConnectionSpec]]:
    instances = [
        ModuleInstanceSpec("PWR", "power_input_5v"),
        ModuleInstanceSpec("REG", "power_3v3"),
        ModuleInstanceSpec("MCU", "mcu_stm32g071"),
    ]
    for name in i2c:
        instances.append(ModuleInstanceSpec(name, "i2c_temperature"
                                            if name == "TEMP" else "i2c_eeprom"))
    for index in range(uart_count):
        instances.append(ModuleInstanceSpec(f"UART{index + 1}", "uart_header"))
    if include_led:
        instances.append(ModuleInstanceSpec("LED", "led_indicator"))
    connections = [
        PortConnectionSpec("PWR.VIN", "REG.VIN", "VIN", "power", 5.0),
        PortConnectionSpec("PWR.GND", "REG.GND", "GND", "power", 0.0),
        PortConnectionSpec("REG.3V3", "MCU.3V3", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "MCU.GND", "GND", "power", 0.0),
        PortConnectionSpec("MCU.SDA", f"{i2c[0]}.SDA", "I2C_SDA", "i2c", 3.3),
        PortConnectionSpec("MCU.SCL", f"{i2c[0]}.SCL", "I2C_SCL", "i2c", 3.3),
        PortConnectionSpec("MCU.TX", "UART1.TX", "UART_TX", "uart", 3.3),
        PortConnectionSpec("MCU.RX", "UART1.RX", "UART_RX", "uart", 3.3),
        PortConnectionSpec("REG.3V3", "UART1.VCC", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "UART1.GND", "GND", "power", 0.0),
    ]
    for name in i2c:
        connections.extend([
            PortConnectionSpec("REG.3V3", f"{name}.3V3", "3V3", "power", 3.3),
            PortConnectionSpec("REG.GND", f"{name}.GND", "GND", "power", 0.0),
        ])
    for name in i2c[1:]:
        connections.extend([
            PortConnectionSpec(f"{i2c[0]}.SDA", f"{name}.SDA", "I2C_SDA", "i2c", 3.3),
            PortConnectionSpec(f"{i2c[0]}.SCL", f"{name}.SCL", "I2C_SCL", "i2c", 3.3),
        ])
    if include_led:
        connections.extend([
            PortConnectionSpec("MCU.GPIO", "LED.SIG", "LED_SIG", "gpio", 3.3),
            PortConnectionSpec("REG.GND", "LED.GND", "GND", "power", 0.0),
        ])
    return instances, connections


def format_composition_report(result: CompositionResult) -> str:
    lines = [f"# Composition {result.scenario_id}", ""]
    lines.append(f"- status: **{result.status}**")
    lines.append(f"- modules: {', '.join(result.module_instances)}")
    if result.circuit is not None:
        lines.append(f"- components: {len(result.circuit.components)}")
        lines.append(f"- nets: {len(result.circuit.nets)}")
    lines.append(f"- semantic hash: `{result.semantic_hash}`")
    if result.contract:
        lines.append("")
        lines.append("## Power budget")
        for rail, row in sorted(result.contract.power_budget.items()):
            lines.append(f"- {rail}: {row['used_ma']} / "
                         f"{row['limit_ma']} mA · {row['status']}")
        lines.append("")
        lines.append("## Pin allocation")
        for name, pins in sorted(result.contract.pin_allocation.items()):
            lines.append(f"- {name}: {', '.join(pins) or 'none'}")
        lines.append("")
        lines.append("## I2C addresses")
        for address, owners in sorted(result.contract.addresses.items()):
            lines.append(f"- {address}: {', '.join(owners)}")
        lines.append("")
        lines.append(f"## Power sequence: {' → '.join(result.contract.sequence_order)}")
    lines.append("")
    lines.append("## Diagnostics")
    if not result.diagnostics:
        lines.append("- none")
    else:
        for diag in result.diagnostics:
            lines.append(f"- `{diag.code}` {diag.severity} @ {diag.scope}: "
                         f"{diag.message}")
    lines.append("")
    lines.append("## Knowledge sources")
    for instance, sources in result.sources.items():
        for source in sources:
            lines.append(f"- {instance}: {source}")
    return "\n".join(lines) + "\n"
