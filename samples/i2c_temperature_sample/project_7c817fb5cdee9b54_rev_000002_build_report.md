# Build report — project_7c817fb5cdee9b54 rev_000002

- Created: 2026-10-21T04:17:24Z
- Review status: **READY_FOR_REVIEW**
- Template: i2c_temperature
- Commit id: commit_48d1da31a95ec873
- Run id: run_4befd3e94673
- Design hash: `2b21d96ad0ceae39b993a4726eee87cf7205eb82f2b5b0466b9b3d554afdc173`
- Components: 5 · Nets: 4
- Checks: 20 passed · 0 failed · 1 warnings · 1 not checked

## Requirements

An Analog Devices DS1621+ digital thermometer on a 3.3 V, 100 kHz 2-wire bus. A0/A1/A2 are tied low for address 0x48, TOUT is explicitly not connected, and SDA/SCL each have a 3.3k pull-up. The external master and cable are outside this board; the bus capacitance target is an interface assumption.

| Parameter | Value | Unit | Origin |
| --- | --- | --- | --- |
| address | 0x48 |  | user |
| board_height_mm | 30 | mm | user |
| board_thickness_mm | 1.6 |  | user |
| board_width_mm | 40 | mm | user |
| bus_frequency_khz | 100 | kHz | user |
| supply_voltage_v | 3.3 | V | user |

## Checks

| Status | Check | Required | Message |
| --- | --- | --- | --- |
| PASS | PIN_PAD_MAPPING | yes | All 5 components map pins to existing pads. |
| PASS | NET_COMPLETENESS | yes | 4 nets have complete endpoints; 1 pins are explicitly NC. |
| PASS | REQUIRED_CONNECTIONS | yes | 4 template nets match the design exactly. |
| PASS | VOLTAGE_DOMAIN | yes | Supply and part voltage limits are satisfied for the demo conditions. |
| PASS | SUPPLY_DECOUPLING | yes | Every power net has a capacitor to GND. |
| PASS | SCHEMATIC_PCB_CONSISTENCY | yes | 5 components share identity and nets across schematic and board. |
| PASS | VISIBLE_TEXT | yes | Symbol bodies, references and values do not overlap. |
| PASS | DS1621_PINMAP | yes | U1 pin names and pad mapping match the datasheet. |
| PASS | I2C_PULLUPS | yes | SDA and SCL each have a 3.3k pull-up to 3V3. |
| PASS | I2C_ADDRESS_STRAP | yes | A0, A1 and A2 are tied to GND; 7-bit address is 0x48. |
| PASS | DS1621_TOUT_NC | yes | TOUT (pin 3) is an explicit no-connect with a schematic marker. |
| WARN | INTERFACE_ASSUMPTION | no | Bus runs at 100 kHz; total bus capacitance <= 100 pF and the external master/cable are assumptions, not measured by Mota. |
| NOT_CHECKED | EMC | no | EMC and immunity are outside this demo's coverage. |
| PASS | ROUTING_COMPLETE | yes | All 4 nets have routed copper. |
| PASS | DRC_CLEARANCE | yes | No clearance violation below 0.25 mm. |
| PASS | COPPER_SHORTS | yes | No copper of different nets touches. |
| PASS | DRC_BOARD_EDGE | yes | Copper keeps 0.50 mm from the board edge. |
| PASS | DRC_TRACK_WIDTH | yes | Track widths meet the demo rules (0.30 / 0.60 mm). |
| PASS | DRC_VIA | yes | Via outer/drill/ring dimensions meet the demo rules. |
| PASS | DRC_HOLES | yes | Copper keeps 0.25 mm from NPTH holes on both layers. |
| PASS | COURTYARD | yes | Component courtyards are inside the board and do not overlap. |
| PASS | COPPER_CONNECTIVITY | yes | All 4 nets are connected by real copper. |

## Scope and limitations

- READY_FOR_REVIEW means the design is reviewable, not production ready.
- Thermal, EMC, external cable capacitance and physical assembly are outside this demo's verified scope where marked NOT_CHECKED.
- 3D component bodies are simplified unless marked exact_asset; the board mesh is not boolean-cut for holes.
- Capacitor derating/ESR evidence is not recorded for the demo parts; the power_3v3 output-capacitor checks are therefore NOT_CHECKED.
- KiCad export compatibility is NOT_VERIFIED in this environment.

## Sources

- [D3] Analog Devices DS1621 datasheet — https://www.analog.com/media/en/technical-documentation/data-sheets/DS1621.pdf
- [SPEC-R] Mota axial resistor engineering spec
- [SPEC-C] Mota radial capacitor engineering spec
- [SPEC-CONN] Mota 2.54 mm pin header engineering spec
