# Build report — project_a7767663b4a1d013 rev_000002

- Created: 2026-10-21T04:16:46Z
- Review status: **READY_FOR_REVIEW**
- Template: led_timer
- Commit id: commit_5e3a03c247271ddb
- Run id: run_b605a07caef9
- Design hash: `30241b9d71d307245125b600d2b869d09eb2d5935e9c9a7a77a346edd0fe46db`
- Components: 9 · Nets: 7
- Checks: 20 passed · 0 failed · 1 warnings · 1 not checked

## Requirements

A TI NE555P astable timer running at about 1 Hz from 5 V. RA=10k, RB=68k and C=10uF set the nominal frequency; the LED is wired from VCC through 1k into the output pin and lights when the output is low.

| Parameter | Value | Unit | Origin |
| --- | --- | --- | --- |
| board_height_mm | 35 | mm | user |
| board_thickness_mm | 1.6 |  | user |
| board_width_mm | 50 | mm | user |
| ra_kohm | 10 | kohm | user |
| rb_kohm | 68 | kohm | user |
| supply_voltage_v | 5.0 | V | user |
| target_frequency_hz | 1.0 | Hz | user |
| timing_cap_uf | 10 | uF | user |

## Checks

| Status | Check | Required | Message |
| --- | --- | --- | --- |
| PASS | PIN_PAD_MAPPING | yes | All 9 components map pins to existing pads. |
| PASS | NET_COMPLETENESS | yes | 7 nets have complete endpoints; 0 pins are explicitly NC. |
| PASS | REQUIRED_CONNECTIONS | yes | 7 template nets match the design exactly. |
| PASS | VOLTAGE_DOMAIN | yes | Supply and part voltage limits are satisfied for the demo conditions. |
| PASS | SUPPLY_DECOUPLING | yes | Every power net has a capacitor to GND. |
| PASS | SCHEMATIC_PCB_CONSISTENCY | yes | 9 components share identity and nets across schematic and board. |
| PASS | VISIBLE_TEXT | yes | Symbol bodies, references and values do not overlap. |
| PASS | NE555_PINMAP | yes | U1 pin names and pad mapping match the datasheet. |
| PASS | NE555_RESET_BIAS | yes | RESET (pin 4) is tied to VCC_5V. |
| PASS | NE555_TIMING | yes | Nominal astable frequency 0.986 Hz (calculated, not measured). |
| PASS | LED_POLARITY | yes | Anode goes to the 1k branch from VCC; cathode goes to OUT (pin 3). |
| WARN | LED_PHYSICAL_POLARITY | no | The footprint defines pad 1 as the anode; verify the chosen physical LED lead polarity at assembly. |
| NOT_CHECKED | EMC | no | EMC and immunity are outside this demo's coverage. |
| PASS | ROUTING_COMPLETE | yes | All 7 nets have routed copper. |
| PASS | DRC_CLEARANCE | yes | No clearance violation below 0.25 mm. |
| PASS | COPPER_SHORTS | yes | No copper of different nets touches. |
| PASS | DRC_BOARD_EDGE | yes | Copper keeps 0.50 mm from the board edge. |
| PASS | DRC_TRACK_WIDTH | yes | Track widths meet the demo rules (0.30 / 0.60 mm). |
| PASS | DRC_VIA | yes | Via outer/drill/ring dimensions meet the demo rules. |
| PASS | DRC_HOLES | yes | Copper keeps 0.25 mm from NPTH holes on both layers. |
| PASS | COURTYARD | yes | Component courtyards are inside the board and do not overlap. |
| PASS | COPPER_CONNECTIVITY | yes | All 7 nets are connected by real copper. |

## Scope and limitations

- READY_FOR_REVIEW means the design is reviewable, not production ready.
- Thermal, EMC, external cable capacitance and physical assembly are outside this demo's verified scope where marked NOT_CHECKED.
- 3D component bodies are simplified unless marked exact_asset; the board mesh is not boolean-cut for holes.
- Capacitor derating/ESR evidence is not recorded for the demo parts; the power_3v3 output-capacitor checks are therefore NOT_CHECKED.
- KiCad export compatibility is NOT_VERIFIED in this environment.

## Sources

- [D2] TI NE555 datasheet (SLFS022) — https://www.ti.com/lit/ds/symlink/ne555.pdf
- [SPEC-R] Mota axial resistor engineering spec
- [SPEC-C] Mota radial capacitor engineering spec
- [SPEC-LED] Mota 5 mm LED engineering spec
- [SPEC-CONN] Mota 2.54 mm pin header engineering spec
