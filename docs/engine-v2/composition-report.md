# C4 原创模块组合报告

生成日期：2026-10-21

三个组合均由 `knowledge/modules.py` 的模块定义生成；不复制现成工程。

---

# Composition mcu_i2c_temp_uart

- status: **OK**
- modules: PWR, REG, MCU, TEMP, UART1
- components: 13
- nets: 10
- semantic hash: `30b88371fcab8abca21b136aaa78110d2917e33ef9d4966d74006627f42e9d07`

## Power budget
- 3V3: 13.0 / 250.0 mA · PASS

## Pin allocation
- uart_rx: MCU
- uart_tx: MCU

## I2C addresses
- 0x48: TEMP

## Power sequence: VIN → 3V3

## Diagnostics
- `NET_SINGLE_PIN` warning @ MCU/GPIO: net MCU/GPIO has only one pin
- `NET_SINGLE_PIN` warning @ MCU/NRST: net MCU/NRST has only one pin

## Knowledge sources
- PWR: Mota power_input_5v module v1 (connector rating NOT_VERIFIED)
- REG: D1 MCP1700-3302E datasheet
- REG: Mota power_3v3 module v1
- MCU: D2 STM32G071x8 datasheet (subset pinout, NOT full package)
- MCU: Mota mcu_stm32g071 module v1
- TEMP: D3 DS1621 datasheet
- TEMP: Mota i2c_temperature module v1
- UART1: Mota uart_header module v1 (header only; no transceiver)

- compile median: 0.4 ms
- schematic symbols: 13 · wires: 41 · labels: 41

---

# Composition mcu_i2c_eeprom_uart_led

- status: **OK**
- modules: PWR, REG, MCU, EEPROM, UART1, LED
- components: 15
- nets: 11
- semantic hash: `99407fa91570177a20e593a031b483ae161012a434a6d967fb113074399e84fb`

## Power budget
- 3V3: 20.0 / 250.0 mA · PASS

## Pin allocation
- gpio_0: MCU
- uart_rx: MCU
- uart_tx: MCU

## I2C addresses
- 0x50: EEPROM

## Power sequence: VIN → 3V3

## Diagnostics
- `NET_SINGLE_PIN` warning @ MCU/NRST: net MCU/NRST has only one pin

## Knowledge sources
- PWR: Mota power_input_5v module v1 (connector rating NOT_VERIFIED)
- REG: D1 MCP1700-3302E datasheet
- REG: Mota power_3v3 module v1
- MCU: D2 STM32G071x8 datasheet (subset pinout, NOT full package)
- MCU: Mota mcu_stm32g071 module v1
- EEPROM: D4 AT24C02 datasheet
- EEPROM: Mota i2c_eeprom module v1
- UART1: Mota uart_header module v1 (header only; no transceiver)
- LED: Mota led_indicator module v1; LED polarity NOT_CHECKED

- compile median: 0.3 ms
- schematic symbols: 15 · wires: 46 · labels: 46

---

# Composition mcu_two_i2c_led_uart

- status: **OK**
- modules: PWR, REG, MCU, TEMP, EEPROM, UART1, LED
- components: 19
- nets: 11
- semantic hash: `88555c276942c40c55951b1b80c9de78d257b510081ea7d849ff7fc10cf647f6`

## Power budget
- 3V3: 21.0 / 250.0 mA · PASS

## Pin allocation
- gpio_0: MCU
- uart_rx: MCU
- uart_tx: MCU

## I2C addresses
- 0x48: TEMP
- 0x50: EEPROM

## Power sequence: VIN → 3V3

## Diagnostics
- `NET_SINGLE_PIN` warning @ MCU/NRST: net MCU/NRST has only one pin

## Knowledge sources
- PWR: Mota power_input_5v module v1 (connector rating NOT_VERIFIED)
- REG: D1 MCP1700-3302E datasheet
- REG: Mota power_3v3 module v1
- MCU: D2 STM32G071x8 datasheet (subset pinout, NOT full package)
- MCU: Mota mcu_stm32g071 module v1
- TEMP: D3 DS1621 datasheet
- TEMP: Mota i2c_temperature module v1
- EEPROM: D4 AT24C02 datasheet
- EEPROM: Mota i2c_eeprom module v1
- UART1: Mota uart_header module v1 (header only; no transceiver)
- LED: Mota led_indicator module v1; LED polarity NOT_CHECKED

- compile median: 0.3 ms
- schematic symbols: 19 · wires: 59 · labels: 59
