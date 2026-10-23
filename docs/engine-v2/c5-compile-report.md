# C5 约束编译实测报告

生成方式：实际调用 `compile_constraints()`、`format_compile_report()`；电路来自
`scenario_mcu_i2c_temp_uart()`，板框 40×40 mm、两层，器件坐标由本脚本写入。

## 规则包

- `manufacturing_fr4_2layer_v1` v1 (manufacturing) · 6 条规则 · 来源 mota_demo_rules
- `platform_stm32g071_min_system_v1` v1 (platform) · 6 条规则 · 来源 d2_stm32g071, mota_demo_rules
- `user_demo_strict_v1` v1 (user) · 4 条规则 · 来源 user_strict_profile

## 1. 默认三包编译

```
# Constraint compile report

- status: **SAT**
- review ready: no
- packs: manufacturing_fr4_2layer_v1, platform_stm32g071_min_system_v1, user_demo_strict_v1
- backend: interval (complete=True, 0.65 ms / budget 2.00 s)
- content hash: `09a096c7fc8d59d5d586a5bac1105215e22a486db69880b757d86c528d47a431`

Hard constraint intervals intersect without contradiction.

## Hard constraints
- `board_edge_clearance` min 0.300 mm → layer:B.Cu, layer:F.Cu (manufacturing_fr4_2layer_v1:min_board_edge_clearance)
- `clearance` min 0.300 mm → layer:B.Cu, layer:F.Cu, net:3V3, net:GND, net:I2C_SCL, net:I2C_SDA, net:MCU/BOOT, net:MCU/GPIO, net:MCU/NRST, net:UART_RX, net:UART_TX, net:VIN, net_class:ground, net_class:power, net_class:signal (user_demo_strict_v1:strict_clearance)
- `hole_clearance` min 0.200 mm → layer:B.Cu, layer:F.Cu (manufacturing_fr4_2layer_v1:min_hole_clearance)
- `track_width` min 0.600 mm → layer:B.Cu, layer:F.Cu, net:3V3, net:GND, net:VIN, net_class:ground, net_class:power (platform_stm32g071_min_system_v1:power_track_width)
- `track_width` min 0.350 mm → layer:B.Cu, layer:F.Cu, net:I2C_SCL, net:I2C_SDA, net:MCU/BOOT, net:MCU/GPIO, net:MCU/NRST, net:UART_RX, net:UART_TX, net_class:signal (user_demo_strict_v1:strict_signal_track_width)
- `via_annular_ring` min 0.130 mm → vias (manufacturing_fr4_2layer_v1:min_via_annular_ring)
- `via_drill` min 0.300 mm → vias (user_demo_strict_v1:strict_via_drill)

## Soft targets
- `clearance` min 0.100 mm → layer:B.Cu, layer:F.Cu, net:3V3, net:GND, net:I2C_SCL, net:I2C_SDA, net:MCU/BOOT, net:MCU/GPIO, net:MCU/NRST, net:UART_RX, net:UART_TX, net:VIN, net_class:ground, net_class:power, net_class:signal

## Conflicts
- none

## Rule coverage
- `manufacturing_fr4_2layer_v1:min_clearance` (clearance) APPLIED / required / clamped — rule applied to 20 target(s).
  - effective: min 0.300 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:F.Cu, net:I2C_SCL, net_class:signal (+48 more)
- `manufacturing_fr4_2layer_v1:min_track_width` (track_width) APPLIED / required / clamped — rule applied to 20 target(s).
  - effective: min 0.600 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:F.Cu, net:I2C_SCL, net_class:signal (+48 more)
- `manufacturing_fr4_2layer_v1:min_via_drill` (via_drill) APPLIED / required / clamped — rule applied to 1 target(s).
  - effective: min 0.300 mm
  - objects: vias
- `manufacturing_fr4_2layer_v1:min_via_annular_ring` (via_annular_ring) APPLIED / required — rule applied to 1 target(s).
  - effective: min 0.130 mm
  - objects: vias
- `manufacturing_fr4_2layer_v1:min_board_edge_clearance` (board_edge_clearance) APPLIED / required — rule applied to 2 target(s).
  - effective: min 0.300 mm
  - objects: layer:F.Cu, layer:B.Cu
- `manufacturing_fr4_2layer_v1:min_hole_clearance` (hole_clearance) APPLIED / required — rule applied to 2 target(s).
  - effective: min 0.200 mm
  - objects: layer:F.Cu, layer:B.Cu
- `platform_stm32g071_min_system_v1:power_clearance` (clearance) APPLIED / required / clamped — rule applied to 6 target(s).
  - effective: min 0.300 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:B.Cu, net:3V3, net_class:power (+6 more)
- `platform_stm32g071_min_system_v1:signal_track_width` (track_width) APPLIED / required / clamped — rule applied to 14 target(s).
  - effective: min 0.350 mm
  - objects: layer:F.Cu, net:I2C_SCL, net_class:signal, layer:F.Cu, net:I2C_SDA, net_class:signal, layer:F.Cu, net:MCU/BOOT, net_class:signal, layer:F.Cu, net:MCU/GPIO, net_class:signal (+30 more)
- `platform_stm32g071_min_system_v1:power_track_width` (track_width) APPLIED / required — rule applied to 6 target(s).
  - effective: min 0.600 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:B.Cu, net:3V3, net_class:power (+6 more)
- `platform_stm32g071_min_system_v1:usb_impedance` (impedance) SKIPPED_NO_TARGET — the selector matched no object in this design; the rule is not applicable here.
- `platform_stm32g071_min_system_v1:uart_propagation_delay` (propagation_delay) NOT_CHECKED / required — stackup material data is not declared; delay/impedance rules remain applicable and are NOT_CHECKED, not skipped.
  - objects: board:stackup, net:UART_RX, net:UART_TX
- `platform_stm32g071_min_system_v1:led_delay_preference` (propagation_delay) SKIPPED_NO_TARGET / soft — the selector matched no object in this design; the rule is not applicable here.
- `user_demo_strict_v1:strict_clearance` (clearance) APPLIED / required — rule applied to 20 target(s).
  - effective: min 0.300 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:F.Cu, net:I2C_SCL, net_class:signal (+48 more)
- `user_demo_strict_v1:strict_signal_track_width` (track_width) APPLIED / required — rule applied to 14 target(s).
  - effective: min 0.350 mm
  - objects: layer:F.Cu, net:I2C_SCL, net_class:signal, layer:F.Cu, net:I2C_SDA, net_class:signal, layer:F.Cu, net:MCU/BOOT, net_class:signal, layer:F.Cu, net:MCU/GPIO, net_class:signal (+30 more)
- `user_demo_strict_v1:strict_via_drill` (via_drill) APPLIED / required — rule applied to 1 target(s).
  - effective: min 0.300 mm
  - objects: vias
- `user_demo_strict_v1:soft_clearance_preference` (clearance) APPLIED / soft — rule applied to 20 target(s).
  - effective: min 0.100 mm
  - objects: layer:F.Cu, net:3V3, net_class:power, layer:F.Cu, net:GND, net_class:ground, layer:F.Cu, net:VIN, net_class:power, layer:F.Cu, net:I2C_SCL, net_class:signal (+48 more)
```

## 2. 硬冲突示例（I2C_SDA 阻抗 ≥60 Ω 与 ≤50 Ω）

```
# Constraint compile report

- status: **INFEASIBLE**
- review ready: no
- packs: report_min, report_max
- backend: interval (complete=True, 0.08 ms / budget 2.00 s)
- content hash: `49924fa2fa56e3dcb05f69c3b1f7d60b93f7cd24ba11f5c264f71ab5fb12638a`

A hard constraint intersection is empty. This is a contract contradiction, not a routing result.

## Hard constraints
- `impedance` max 50.0 ohm, min 60.0 ohm → net:I2C_SDA (report_max:max_impedance, report_min:min_impedance)

## Soft targets
- none

## Conflicts
- `cf_6845a60e30` impedance on net:I2C_SDA requires >= 60.0 ohm and <= 50.0 ohm.
  - rules: report_max:max_impedance, report_min:min_impedance
  - objects: net:I2C_SDA
  - focus: (0.39, 12.40) – (16.98, 25.61) mm
  - sources: demo

## Rule coverage
- `report_min:min_impedance` (impedance) APPLIED / required — rule applied to 1 target(s).
  - effective: max 50.0 ohm, min 60.0 ohm
  - objects: net:I2C_SDA
- `report_max:max_impedance` (impedance) APPLIED / required — rule applied to 1 target(s).
  - effective: max 50.0 ohm, min 60.0 ohm
  - objects: net:I2C_SDA
```

## 3. 预算耗尽示例（budget_seconds=0）

```
# Constraint compile report

- status: **UNKNOWN**
- review ready: no
- packs: manufacturing_fr4_2layer_v1, platform_stm32g071_min_system_v1, user_demo_strict_v1
- backend: interval (complete=False, 0.10 ms / budget 0.00 s)
- content hash: `09a096c7fc8d59d5d586a5bac1105215e22a486db69880b757d86c528d47a431`

Time budget exhausted; no feasibility claim is made.

## Hard constraints
- none

## Soft targets
- none

## Conflicts
- none

## Rule coverage
- `manufacturing_fr4_2layer_v1:min_clearance` (clearance) NOT_COMPILED / required — budget exhausted
- `manufacturing_fr4_2layer_v1:min_track_width` (track_width) NOT_COMPILED / required — budget exhausted
- `manufacturing_fr4_2layer_v1:min_via_drill` (via_drill) NOT_COMPILED / required — budget exhausted
- `manufacturing_fr4_2layer_v1:min_via_annular_ring` (via_annular_ring) NOT_COMPILED / required — budget exhausted
- `manufacturing_fr4_2layer_v1:min_board_edge_clearance` (board_edge_clearance) NOT_COMPILED / required — budget exhausted
- `manufacturing_fr4_2layer_v1:min_hole_clearance` (hole_clearance) NOT_COMPILED / required — budget exhausted
- `platform_stm32g071_min_system_v1:power_clearance` (clearance) NOT_COMPILED / required — budget exhausted
- `platform_stm32g071_min_system_v1:signal_track_width` (track_width) NOT_COMPILED / required — budget exhausted
- `platform_stm32g071_min_system_v1:power_track_width` (track_width) NOT_COMPILED / required — budget exhausted
- `platform_stm32g071_min_system_v1:usb_impedance` (impedance) NOT_COMPILED — budget exhausted
- `platform_stm32g071_min_system_v1:uart_propagation_delay` (propagation_delay) NOT_COMPILED / required — budget exhausted
- `platform_stm32g071_min_system_v1:led_delay_preference` (propagation_delay) NOT_COMPILED / soft — budget exhausted
- `user_demo_strict_v1:strict_clearance` (clearance) NOT_COMPILED / required — budget exhausted
- `user_demo_strict_v1:strict_signal_track_width` (track_width) NOT_COMPILED / required — budget exhausted
- `user_demo_strict_v1:strict_via_drill` (via_drill) NOT_COMPILED / required — budget exhausted
- `user_demo_strict_v1:soft_clearance_preference` (clearance) NOT_COMPILED / soft — budget exhausted

## Diagnostics
- `BUDGET_EXHAUSTED` (warning) Constraint compilation stopped at the time budget. This is UNKNOWN, not a proof that the design is impossible.
```
