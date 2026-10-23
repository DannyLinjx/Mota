from __future__ import annotations

from mota.circuit import parts
from mota.domain.models import Component, Placement
from mota.generation.schematic import generate_schematic
from mota.knowledge.modules import (
    ModuleDef, ModuleInstanceSpec, PortConnectionSpec, SequenceSpec,
    compose_modules, format_composition_report, get_module,
    scenario_mcu_i2c_eeprom_uart, scenario_mcu_i2c_temp_uart,
    scenario_mcu_two_i2c_led_uart,
)


def _compose(instances, connections, *, scenario_id="test", registry=None):
    return compose_modules(instances, connections, scenario_id=scenario_id,
                           registry=registry)


def _base_instances():
    return [
        ModuleInstanceSpec("PWR", "power_input_5v"),
        ModuleInstanceSpec("REG", "power_3v3"),
        ModuleInstanceSpec("MCU", "mcu_stm32g071"),
    ]


def _power_connections():
    return [
        PortConnectionSpec("PWR.VIN", "REG.VIN", "VIN", "power", 5.0),
        PortConnectionSpec("PWR.GND", "REG.GND", "GND", "power", 0.0),
        PortConnectionSpec("REG.3V3", "MCU.3V3", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "MCU.GND", "GND", "power", 0.0),
    ]


def test_three_original_compositions_are_distinct_and_sourced():
    builders = [
        scenario_mcu_i2c_temp_uart(),
        scenario_mcu_i2c_eeprom_uart(),
        scenario_mcu_two_i2c_led_uart(),
    ]
    hashes = set()
    component_counts = []
    net_signatures = []
    for index, (instances, connections) in enumerate(builders):
        result = _compose(instances, connections, scenario_id=f"scenario_{index}")
        assert result.status == "OK", result.diagnostics
        assert result.circuit is not None
        assert result.operations
        assert all(source for sources in result.sources.values() for source in sources)
        schematic = generate_schematic(result.circuit)
        assert len(schematic.symbols) == len(result.circuit.components)
        assert schematic.wires
        hashes.add(result.semantic_hash)
        component_counts.append(len(result.circuit.components))
        net_signatures.append(tuple(sorted(
            (net.name, len(net.pins)) for net in result.circuit.nets)))
        report = format_composition_report(result)
        assert "## Knowledge sources" in report
        assert "Power budget" in report
    assert len(hashes) == 3
    assert len(set(component_counts)) == 3
    assert len(set(net_signatures)) == 3


def test_unknown_parameter_blocks_before_building():
    instances = _base_instances()
    instances[2] = ModuleInstanceSpec("MCU", "mcu_stm32g071",
                                      {"does_not_exist": 1})
    result = _compose(instances, _power_connections())
    assert result.status == "BLOCKED"
    assert result.circuit is None
    assert any(diag.code == "UNKNOWN_PARAMETER" for diag in result.diagnostics)


def test_out_of_range_and_level_preconditions_block():
    instances = _base_instances()
    instances[2] = ModuleInstanceSpec("MCU", "mcu_stm32g071", {"supply_v": 5.0})
    blocked = _compose(instances, _power_connections())
    assert blocked.status == "BLOCKED"
    assert any(diag.code == "PARAMETER_OUT_OF_RANGE"
               for diag in blocked.diagnostics)

    instances = _base_instances() + [
        ModuleInstanceSpec("TEMP", "i2c_temperature",
                           {"bus_speed_hz": 999_999})]
    blocked = _compose(instances, _power_connections())
    assert blocked.status == "BLOCKED"
    assert any(diag.code == "PARAMETER_OUT_OF_RANGE"
               for diag in blocked.diagnostics)

    instances = _base_instances() + [
        ModuleInstanceSpec("TEMP", "i2c_temperature", {"supply_v": 5.0})]
    connections = _power_connections() + [
        PortConnectionSpec("MCU.SDA", "TEMP.SDA", "I2C_SDA", "i2c", 3.3),
        PortConnectionSpec("MCU.SCL", "TEMP.SCL", "I2C_SCL", "i2c", 3.3),
        PortConnectionSpec("REG.3V3", "TEMP.3V3", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "TEMP.GND", "GND", "power", 0.0),
    ]
    blocked = _compose(instances, connections)
    assert blocked.status == "BLOCKED"
    assert any(diag.code == "POWER_RAIL_LEVEL"
               for diag in blocked.diagnostics)


def test_two_modules_conflict_on_address_or_pin_function():
    instances = _base_instances() + [
        ModuleInstanceSpec("TEMP1", "i2c_temperature"),
        ModuleInstanceSpec("TEMP2", "i2c_temperature"),
        ModuleInstanceSpec("UART1", "uart_header"),
    ]
    connections = _power_connections() + [
        PortConnectionSpec("MCU.SDA", "TEMP1.SDA", "I2C_SDA", "i2c", 3.3),
        PortConnectionSpec("MCU.SCL", "TEMP1.SCL", "I2C_SCL", "i2c", 3.3),
        PortConnectionSpec("TEMP1.SDA", "TEMP2.SDA", "I2C_SDA", "i2c", 3.3),
        PortConnectionSpec("TEMP1.SCL", "TEMP2.SCL", "I2C_SCL", "i2c", 3.3),
        PortConnectionSpec("REG.3V3", "TEMP1.3V3", "3V3", "power", 3.3),
        PortConnectionSpec("REG.3V3", "TEMP2.3V3", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "TEMP1.GND", "GND", "power", 0.0),
        PortConnectionSpec("REG.GND", "TEMP2.GND", "GND", "power", 0.0),
        PortConnectionSpec("MCU.TX", "UART1.TX", "UART_TX", "uart", 3.3),
        PortConnectionSpec("MCU.RX", "UART1.RX", "UART_RX", "uart", 3.3),
        PortConnectionSpec("REG.3V3", "UART1.VCC", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "UART1.GND", "GND", "power", 0.0),
    ]
    result = _compose(instances, connections)
    codes = {diag.code for diag in result.diagnostics}
    assert "ADDRESS_CONFLICT" in codes
    assert result.status == "BLOCKED"

    instances = _base_instances() + [
        ModuleInstanceSpec("UART1", "uart_header"),
        ModuleInstanceSpec("UART2", "uart_header"),
    ]
    connections = _power_connections() + [
        PortConnectionSpec("MCU.TX", "UART1.TX", "UART_TX", "uart", 3.3),
        PortConnectionSpec("MCU.RX", "UART1.RX", "UART_RX", "uart", 3.3),
        PortConnectionSpec("MCU.TX", "UART2.TX", "UART_TX2", "uart", 3.3),
        PortConnectionSpec("MCU.RX", "UART2.RX", "UART_RX2", "uart", 3.3),
        PortConnectionSpec("REG.3V3", "UART1.VCC", "3V3", "power", 3.3),
        PortConnectionSpec("REG.3V3", "UART2.VCC", "3V3", "power", 3.3),
        PortConnectionSpec("REG.GND", "UART1.GND", "GND", "power", 0.0),
        PortConnectionSpec("REG.GND", "UART2.GND", "GND", "power", 0.0),
    ]
    result = _compose(instances, connections)
    assert any(diag.code == "PIN_CONFLICT" for diag in result.diagnostics)
    assert result.status == "BLOCKED"


def test_power_budget_and_interface_level_are_real_checks():
    instances = _base_instances() + [
        ModuleInstanceSpec(f"LED{i}", "led_indicator",
                           {"forward_current_ma": 20})
        for i in range(1, 16)
    ]
    connections = _power_connections() + [
        PortConnectionSpec("MCU.GPIO", "LED1.SIG", "LED_SIG", "gpio", 3.3),
        PortConnectionSpec("REG.GND", "LED1.GND", "GND", "power", 0.0),
    ]
    result = _compose(instances, connections)
    assert result.status == "BLOCKED"
    assert any(diag.code == "POWER_BUDGET_EXCEEDED" for diag in result.diagnostics)
    assert result.contract is not None
    assert result.contract.power_budget["3V3"]["status"] == "FAIL"

    instances = [
        ModuleInstanceSpec("PWR", "power_input_5v"),
        ModuleInstanceSpec("MCU", "mcu_stm32g071"),
    ]
    level = _compose(instances, [
        PortConnectionSpec("PWR.VIN", "MCU.3V3", "BAD_5V", "power", 5.0)])
    assert any(diag.code == "INTERFACE_LEVEL" for diag in level.diagnostics)
    assert level.status == "BLOCKED"


def test_power_sequence_cycle_is_blocked():
    def module(module_id, after):
        return ModuleDef(
            module_id=module_id, version=1, title=module_id,
            description="sequence fixture", applicability="test",
            sequence=(SequenceSpec(module_id, (after,)),))

    registry = {"A": module("A", "B"), "B": module("B", "A")}
    result = _compose(
        [ModuleInstanceSpec("IA", "A"), ModuleInstanceSpec("IB", "B")],
        [], registry=registry)
    assert result.status == "BLOCKED"
    assert any(diag.code == "POWER_SEQUENCE_CYCLE"
               for diag in result.diagnostics)


def test_extra_component_is_not_whitelisted_away():
    instances, connections = scenario_mcu_i2c_temp_uart()
    result = _compose(instances, connections)
    assert result.status == "OK"
    extra = parts.resolve_part("ne555p")
    result.circuit.components.append(Component(
        id="component_extra_timer", reference="U99", part_id=extra.part_id,
        symbol_id=extra.symbol_id, footprint_id=extra.footprint_id,
        pin_pad_map={key: list(value) for key, value in extra.pin_pad_map.items()},
        placement=Placement(x_nm=1_000_000, y_nm=1_000_000),
        instance_path="EXTRA"))
    issues = parts.mapping_issues_for_circuit(result.circuit)
    assert not any(issue.code == "PART_UNKNOWN" for issue in issues)
    assert any(component.reference == "U99"
               for component in result.circuit.components)


def test_three_templates_are_migrated_modules():
    for module_id in ("power_3v3", "led_timer", "i2c_temperature"):
        module = get_module(module_id)
        assert module.components
        assert module.sources
    instances = [
        ModuleInstanceSpec("PWR", "power_input_5v"),
        ModuleInstanceSpec("TIMER", "led_timer"),
    ]
    result = _compose(instances, [
        PortConnectionSpec("PWR.VIN", "TIMER.VCC", "VIN", "power", 5.0),
        PortConnectionSpec("PWR.GND", "TIMER.GND", "GND", "power", 0.0),
    ])
    assert result.status == "OK", result.diagnostics
    assert result.contract.power_budget["VIN"]["status"] == "PASS"


def test_typed_operations_cover_every_component_and_net():
    instances, connections = scenario_mcu_two_i2c_led_uart()
    result = _compose(instances, connections)
    assert result.status == "OK"
    adds = [op for op in result.operations if op.op == "add_component"]
    links = [op for op in result.operations if op.op == "connect_terminals"]
    assert len(adds) == len(result.circuit.components)
    assert len(links) == len([net for net in result.circuit.nets
                              if len(net.pins) >= 2])
    assert len({op.reference for op in adds}) == len(adds)
    assert all(op.instance_path for op in adds)
    assert all(len(op.terminals) >= 2 and op.net_name for op in links)
    module = get_module("mcu_stm32g071")
    assert "9-pin subset" in module.applicability
