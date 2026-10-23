# Build report — project_8b9ebb2b9a999db7 rev_000002

- Created: 2026-10-21T04:16:38Z
- Review status: **REVIEW_BLOCKED**
- Template: power_3v3
- Commit id: commit_c68db2dea62cef64
- Run id: run_db9eb63a1a45
- Design hash: `99d5ba046b9c105d3490f2f574e0e7f52f1671f6847a1009cda1c1b0236363c9`
- Components: 5 · Nets: 3
- Checks: 17 passed · 0 failed · 0 warnings · 4 not checked

## Requirements

A 5 V nominal input feeding an MCP1700-3302E/TO fixed 3.3 V LDO with input and output capacitors and two 2-pin headers. Intended for a 50 mA demonstration load.

| Parameter | Value | Unit | Origin |
| --- | --- | --- | --- |
| board_height_mm | 30 | mm | user |
| board_thickness_mm | 1.6 |  | user |
| board_width_mm | 40 | mm | user |
| input_voltage_v | 5.0 | V | user |
| max_load_ma | 50 | mA | user |
| output_voltage_v | 3.3 |  | user |

## Checks

| Status | Check | Required | Message |
| --- | --- | --- | --- |
| PASS | PIN_PAD_MAPPING | yes | All 5 components map pins to existing pads. |
| PASS | NET_COMPLETENESS | yes | 3 nets have complete endpoints; 0 pins are explicitly NC. |
| PASS | REQUIRED_CONNECTIONS | yes | 3 template nets match the design exactly. |
| PASS | VOLTAGE_DOMAIN | yes | Supply and part voltage limits are satisfied for the demo conditions. |
| PASS | SUPPLY_DECOUPLING | yes | Every power net has a capacitor to GND. |
| PASS | SCHEMATIC_PCB_CONSISTENCY | yes | 5 components share identity and nets across schematic and board. |
| PASS | VISIBLE_TEXT | yes | Symbol bodies, references and values do not overlap. |
| PASS | MCP1700_TO92_PINMAP | yes | TO-92 pins 1=GND, 2=VIN, 3=VOUT map one-to-one to pads. |
| NOT_CHECKED | OUTPUT_CAP_EFFECTIVE | yes | C2 has no verified effective-capacitance evidence (>= 1.0 uF required). CAPACITOR_DERATING_NOT_CHECKED. |
| NOT_CHECKED | OUTPUT_CAP_ESR | yes | C2 has no verified ESR evidence (<= 2 ohm required). CAPACITOR_DERATING_NOT_CHECKED. |
| NOT_CHECKED | THERMAL_ESTIMATE | no | Estimated worst-case LDO dissipation 0.0975 W at 50 mA. Thermal design is outside this demo's coverage. |
| NOT_CHECKED | EMC | no | EMC and immunity are outside this demo's coverage. |
| PASS | ROUTING_COMPLETE | yes | All 3 nets have routed copper. |
| PASS | DRC_CLEARANCE | yes | No clearance violation below 0.25 mm. |
| PASS | COPPER_SHORTS | yes | No copper of different nets touches. |
| PASS | DRC_BOARD_EDGE | yes | Copper keeps 0.50 mm from the board edge. |
| PASS | DRC_TRACK_WIDTH | yes | Track widths meet the demo rules (0.30 / 0.60 mm). |
| PASS | DRC_VIA | yes | Via outer/drill/ring dimensions meet the demo rules. |
| PASS | DRC_HOLES | yes | Copper keeps 0.25 mm from NPTH holes on both layers. |
| PASS | COURTYARD | yes | Component courtyards are inside the board and do not overlap. |
| PASS | COPPER_CONNECTIVITY | yes | All 3 nets are connected by real copper. |

## Scope and limitations

- READY_FOR_REVIEW means the design is reviewable, not production ready.
- Thermal, EMC, external cable capacitance and physical assembly are outside this demo's verified scope where marked NOT_CHECKED.
- 3D component bodies are simplified unless marked exact_asset; the board mesh is not boolean-cut for holes.
- Capacitor derating/ESR evidence is not recorded for the demo parts; the power_3v3 output-capacitor checks are therefore NOT_CHECKED.
- KiCad export compatibility is NOT_VERIFIED in this environment.

## Sources

- [D1] Microchip MCP1700 datasheet DS20001826F — https://ww1.microchip.com/downloads/aemDocuments/documents/APID/ProductDocuments/DataSheets/MCP1700-Data-Sheet-20001826F.pdf
- [SPEC-R] Mota axial resistor engineering spec
- [SPEC-C] Mota radial capacitor engineering spec
- [SPEC-CONN] Mota 2.54 mm pin header engineering spec
