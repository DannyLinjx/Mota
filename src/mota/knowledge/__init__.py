"""Reusable circuit modules, platform packs and their electrical contracts."""

from mota.knowledge.modules import (
    MODULES, ComponentSpec, CompositionResult, ModuleDef, ModuleInstanceSpec,
    NetSpec, ParameterSpec, PortConnectionSpec, PortSpec, PowerSpec, ResourceSpec,
    SequenceSpec, compose_modules, format_composition_report, get_module,
    scenario_mcu_i2c_eeprom_uart, scenario_mcu_i2c_temp_uart,
    scenario_mcu_two_i2c_led_uart,
)
from mota.knowledge.platforms import (
    DEFAULT_PACK_IDS, PlatformPackInvalid, default_packs, list_pack_ids,
    load_pack, load_pack_file, load_packs, pack_summary, validate_pack_document,
)

__all__ = [
    "DEFAULT_PACK_IDS", "MODULES", "ComponentSpec", "CompositionResult",
    "ModuleDef", "ModuleInstanceSpec", "NetSpec", "ParameterSpec",
    "PlatformPackInvalid", "PortConnectionSpec", "PortSpec", "PowerSpec",
    "ResourceSpec", "SequenceSpec", "compose_modules", "default_packs",
    "format_composition_report", "get_module", "list_pack_ids", "load_pack",
    "load_pack_file", "load_packs", "pack_summary",
    "scenario_mcu_i2c_eeprom_uart", "scenario_mcu_i2c_temp_uart",
    "scenario_mcu_two_i2c_led_uart", "validate_pack_document",
]
