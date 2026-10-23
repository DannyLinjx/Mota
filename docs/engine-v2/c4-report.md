# Engine v2 — C4 交付报告（模块、接口与电源契约）

日期：2026-10-21
范围：C4（`knowledge/modules.py`、`constraints/electrical.py`、`tests/knowledge/test_module_composition.py`）。
原则：未知参数阻断组合；电源/接口/地址/引脚事实必须有显式来源；不把“模块能拼起来”当作电气正确。

---

## 1. 实际修改 / 新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/knowledge/modules.py` | ModuleDef / ModuleInstance / Port / Net / Resource / Power / Sequence 定义；6 个内置模块与 MCU 系统；参数校验、组合、typed operations、报告 |
| `src/mota/constraints/electrical.py` | `RuleSpec`、`ContractDiagnostic`、`ModuleFacts`；电源预算、接口方向/电平、pin mux、I2C 地址、电源时序 DAG |
| `src/mota/constraints/__init__.py` | 公共入口 |
| `src/mota/knowledge/__init__.py` | 公共入口 |
| `src/mota/circuit/parts.py` | 运行期模块本地器件：`register_runtime_part` / `make_runtime_part`；解析顺序为 runtime → builtin → imported |
| `tests/knowledge/test_module_composition.py` | 9 项模块组合/契约测试 |
| `docs/engine-v2/compositions/*.svg/json` | 三个原创组合的原理图与规范化数据 |
| `docs/engine-v2/composition-report.md` | 三个组合的电源、引脚、地址、时序、诊断与知识来源 |

未新增电路模板；三个旧模板被表达为模块（`power_3v3`、`led_timer`、`i2c_temperature`），不替代引擎。

---

## 2. 实际执行的命令

```bash
.venv/bin/python -m pytest tests/knowledge -q
.venv/bin/python -m pytest -q --durations=10
```

组合产物由脚本实际调用 `compose_modules`、`generate_schematic`、`schematic_scene`、`scene_to_svg` 生成。

---

## 3. 模块与知识来源

| 模块 | 角色 | 关键事实/来源 |
| --- | --- | --- |
| `power_input_5v` | 供电输入 | 5 V、4.5–5.5 V、VIN 容量 1000 mA；连接器额定值 NOT_VERIFIED |
| `power_3v3` | 3.3 V LDO | MCP1700，4.5–6.0 V 输入、250 mA 容量；D1 datasheet |
| `mcu_stm32g071` | 明确型号 MCU 系统 | STM32G071K8T6，1.8–3.6 V、12 mA、16 MHz；**仅 9 脚组合子集**，不是 32 脚完整 pinout 声明；D2 datasheet |
| `i2c_temperature` | 传感器/接口 | DS1621，0x48，100/400 kHz，2.7–5.5 V；D3 datasheet |
| `i2c_eeprom` | 存储/接口 | AT24C02，0x50，100/400 kHz，1.8–5.5 V；D4 datasheet；封装为近似模型 |
| `uart_header` | 接口 | 4-pin VCC/GND/TX/RX；仅 header，不含收发器/ESD |
| `led_indicator` | 接口/负载 | GPIO LED，1–20 mA；极性 NOT_CHECKED |
| `led_timer` | 旧模板迁移 | NE555 astable，5 V，0.5–10 Hz；D5 datasheet；频率容差未实测 |

每个模块都带 `applicability` 与 `sources`；组合报告逐条列出。

---

## 4. 三个原创组合（拓扑不同）

| 场景 | 器件 | 网络 | 组成 |
| --- | --- | --- | --- |
| `mcu_i2c_temp_uart` | 13 | 10 | 5 V 输入 → 3.3 V LDO → MCU + DS1621 + UART header |
| `mcu_i2c_eeprom_uart_led` | 15 | 11 | 同上，换 AT24C02 并增加 GPIO LED 支路 |
| `mcu_two_i2c_led_uart` | 19 | 11 | MCU + DS1621 + AT24C02（同 I2C 总线不同地址）+ LED + UART |

实际编译 0.3–0.4 ms/次（3 次中位），三个 semantic hash、器件数、网络 pin 签名均不同。原理图与数据见 `docs/engine-v2/compositions/`。

电源预算（场景 1）：3V3 13.0 / 250.0 mA PASS；地址 0x48；pin allocation `uart_tx/uart_rx → MCU`；时序 `VIN → 3V3`。

---

## 5. 测试结果

```
$ .venv/bin/python -m pytest tests/knowledge -q
9 passed
```

覆盖：

1. 三个原创组合均 OK，生成原理图，semantic hash/器件数/网络签名三者不同，报告含知识来源。
2. 未知参数 `does_not_exist` → `UNKNOWN_PARAMETER`，**不创建 CircuitIR**。
3. `supply_v=5.0`（MCU 最大 3.6）与 `bus_speed_hz=999999` → `PARAMETER_OUT_OF_RANGE`。
4. 传感器声明 5 V 但实际 3V3 母线 → `POWER_RAIL_LEVEL`。
5. 两个 0x48 传感器 → `ADDRESS_CONFLICT`；两个 UART header 争用同一组 MCU 引脚 → `PIN_CONFLICT`。
6. 15 个 20 mA LED → `POWER_BUDGET_EXCEEDED`，3V3 预算 FAIL；5 V 直接接 MCU 3V3 → `INTERFACE_LEVEL`。
7. 电源时序 A↔B 循环 → `POWER_SEQUENCE_CYCLE`。
8. 旧三模板均可作为模块取到，`led_timer` 与 5 V 输入组合后 VIN 预算 PASS。
9. 组合后追加非模块内置器件（NE555）不被白名单拒绝；typed operations 覆盖每个器件与网络。

---

## 6. 失败项与已知限制

1. MCU 是 **9 脚建模子集**，不是 STM32G071K8 的完整 32 脚 pinout；不宣称完整封装/全部复用功能。
2. 模块本地器件的符号/封装是程序生成的近似矩形与单排焊盘，`courtyard_approximate=True`；不是厂家封装。
3. pin mux 只按“功能需求量 vs 提供量”计数，不建模完整复用表、电气约束或实际 AF 编号。
4. I2C 地址检查假设所有 I2C 器件在同一条总线；未实现多总线/多实例作用域。
5. 电源预算使用模块声明的典型电流，未做热、浪涌、LDO 压差/功耗或线束压降计算。
6. 电源时序只建模 rail 依赖与环检测，未建模使能引脚、PG 信号或上电斜率。
7. 接口检查限于方向、协议与电压范围；未覆盖驱动能力、上下拉、共模/ESD。
8. 模块来源是 datasheet/模块说明，未在本机做器件级实测；相关结论保持“未验证”措辞。
9. 组合结果尚未写入 ProjectStore/UI；原理图为静态 SVG，未接入桌面页导航。
10. `led_timer` 目标频率未通过实测验证，参数范围是知识约束，不是已测容差。

---

## 7. 回滚方式

- `knowledge/`、`constraints/` 为新增目录；删除后 v1 模板路径不受影响。
- `circuit/parts.py` 的 runtime overlay 只在模块导入时注册，不写入用户工程或全局 catalog；删除 knowledge 导入即可恢复 builtin/imported 两源解析。
- 未改动 `current.json`、revision 事务或旧三模板构建路径。
- 组合产物在 `docs/engine-v2/compositions/`，可按目录删除；不影响运行。
