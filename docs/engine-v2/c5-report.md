# Engine v2 — C5 交付报告（约束编译器与平台包）

日期：2026-10-21
范围：C5（`constraints/compiler.py`、`knowledge/platforms.py`、`platform_packs/`、`tests/constraints/test_compile.py`）。
原则：规则先有量纲、选择和来源，再做交集；`UNKNOWN` 不等于 `INFEASIBLE`；缺证据的规则保持 `NOT_CHECKED`，不当作“不适用”跳过。

---

## 1. 实际新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/constraints/compiler.py` | 规则 DTO、单位/量纲校验、TypedSelector、前提、硬/软规则、目标解析、区间求交、冲突、覆盖、报告、状态映射 |
| `src/mota/constraints/__init__.py` | 导出编译合同 |
| `src/mota/knowledge/platforms.py` | 平台包加载、结构校验、默认包列表与摘要 |
| `src/mota/knowledge/__init__.py` | 导出平台包入口 |
| `platform_packs/schema.json` | 可分发规则包格式（JSON Schema 2020-12） |
| `platform_packs/manufacturing_fr4_2layer_v1.json` | 制造下限包（6 条硬规则，全部带来源） |
| `platform_packs/platform_stm32g071_min_system_v1.json` | 平台要求包（6 条；含硬/软与前提） |
| `platform_packs/user_demo_strict_v1.json` | 用户更严条件包（4 条；含一条故意更松的软规则） |
| `tests/constraints/test_compile.py` | 8 项编译/拒绝/冲突/预算测试 |
| `docs/engine-v2/c5-compile-report.md` | 实际生成的规则覆盖与冲突报告（三份） |

未新增依赖，未引入 OR-Tools；本阶段求解器是确定性区间求交。

---

## 2. 规则模型

### 2.1 单位与量纲

`QuantitySpec` 只保存原始 `value/unit`，编译时统一转成规范单位：

| 量纲 | 规范单位 | 接受单位 |
| --- | --- | --- |
| length | nm | nm / um / mm / mil / in |
| time | ps | ps / ns / us / ms / s |
| impedance | ohm | ohm / kohm |
| frequency | Hz | Hz / kHz / MHz / GHz |
| voltage | V | V / mV |
| current | A | A / mA |

- 缺单位 → `MISSING_UNIT`。
- 单位存在但量纲不符 → `UNIT_MISMATCH`，例如 `propagation_delay.max = 0.08 mm`。不做隐式换算，也不把 mm 当 ps 比较。
- 未知单位 → `UNKNOWN_UNIT`；负数/非有限值 → `INVALID_VALUE`。
- 长度用 `Decimal` 转成整数 nm，避免二进制浮点漂移。

### 2.2 Selector

`TypedSelector` 只有显式 `all=true` 或至少一个具体字段（layers / net_classes / nets / components / net_scopes）：

- 空 selector → `EMPTY_SELECTOR`，必需规则同样拒绝。
- `all` 与具体字段并存 → `SELECTOR_AMBIGUOUS`。
- 选择不存在的层 → `SELECTOR_UNKNOWN_LAYER`。
- 规则选择器在当前设计中没有对象 → `SKIPPED_NO_TARGET`（例如本设计没有 USB 网络）；这是“该设计不涉及”，与缺证据的 `NOT_CHECKED` 不同。

### 2.3 前提与来源

`ApplicabilityExpr`：`min/max_layer_count`、`requires_footprint`、`requires_stackup`、`requires_geometry`。

- 层数不满足 → `SKIPPED_PRECONDITION`（规则本身不适用）。
- 需要封装但未分配 → `NOT_CHECKED / MISSING_FOOTPRINT`，并给出 `component:*` 与 `net:*` 定位。
- 需要叠层介质数据但板未声明 → `NOT_CHECKED / MISSING_STACKUP`，同样保留网络定位。
- 需要铜几何但板上还没有 → `NOT_CHECKED / MISSING_GEOMETRY`。
- 每条规则必须至少引用一个包内声明过的来源；否则 `RULE_SOURCE_MISSING` / `UNKNOWN_SOURCE`。

---

## 3. 交集与状态

### 3.1 目标

- `clearance` / `track_width` / `keepout`：按 `(layer, net)` 求交；
- `board_edge_clearance` / `hole_clearance`：按 layer；
- `via_drill` / `via_outer` / `via_annular_ring`：按 via 对象；
- `impedance` / `propagation_delay`：按 net。

目标不是“规则文本”，而是可定位对象引用：`layer:F.Cu`、`net:3V3`、`net_class:power`、`component:U2`、`vias`，并附带由焊盘/走线/过孔算出的 `focus_nm` 包围盒。

### 3.2 求交

- 最小值类：`effective = max(制造下限, 平台要求, 用户值, ...)`。
- 区间类：`lo = max(min)`，`hi = min(max)`；`lo > hi` → 冲突。
- 用户值比下限更松时不会放宽：覆盖记录 `clamped=true`，并给出生效值。
- 软规则只进入 `soft_constraints`，不参与硬可行性；硬冲突不会因软规则而被抵消。

### 3.3 状态

| 状态 | 含义 |
| --- | --- |
| `SAT` | 硬约束区间均有非空交集；不代表设计已通过 DRC |
| `INFEASIBLE` | 至少一个硬约束目标区间为空，附双方 rule key、来源与定位 |
| `UNKNOWN` | 编译预算耗尽；`complete=false`，不做任何“不可能”声明 |
| `INVALID` | 规则包本身不合法（单位、空 selector、重复规则、缺来源等） |

`map_solver_status()` 预留外部求解器映射：`OPTIMAL/FEASIBLE → SAT`，`INFEASIBLE → INFEASIBLE`，`MODEL_INVALID → INVALID`，`UNKNOWN` 及其他字符串一律 `UNKNOWN`。当前 `solver.backend = "interval"`，没有声称运行过 OR-Tools。

---

## 4. 规则覆盖与冲突报告

`format_compile_report()` 输出：状态、包列表、solver 时间、content hash、硬约束、软目标、冲突（rule key / objects / focus / sources）、逐条规则覆盖（APPLIED / NOT_CHECKED / SKIPPED_NO_TARGET / REJECTED / NOT_COMPILED）、诊断。

同时 `CompiledConstraints` 的机器可读字段包含：

- `coverage[].status/reason/object_refs/sources/constraint_ids/clamped`
- `conflicts[].rule_keys/object_refs/focus_nm/effective/sources`
- `unresolved_rules` 与 `review_ready`

UI 可以直接按 `object_refs` 选中对象、按 `focus_nm` 缩放；不需要解析自然语言。

---

## 5. 实际执行的命令

```bash
.venv/bin/python -m pytest tests/constraints -q
.venv/bin/python -m pytest tests/constraints tests/knowledge tests/circuit -q
.venv/bin/python -m pytest -q --durations=10
```

结果：

```
tests/constraints                                  8 passed
tests/constraints + tests/knowledge + tests/circuit 30 passed
全量回归                                          127 passed, EXIT=0
```

C4 场景 1（13 器件 / 10 网络）上的默认三包编译：

| 项 | 实测 |
| --- | --- |
| status | `SAT` |
| 硬约束目标组 | 7 |
| 冲突 | 0 |
| clearance | 0.300 mm（0.15 / 0.25 / 0.30 取交集） |
| track_width | signal 0.350 mm；power/ground 0.600 mm |
| via_drill | 0.300 mm |
| 制造下限覆盖 | `APPLIED` + `clamped` |
| UART 延迟 | `NOT_CHECKED / MISSING_STACKUP`，`review_ready=false` |
| USB 阻抗 | `SKIPPED_NO_TARGET`（无 USB 网络，required=false） |
| 软去耦偏好 | `soft_constraints` 中 0.10 mm，硬下限仍 0.300 mm |
| 重复编译 | `content_hash` 一致 |

负例（测试内断言）：

- `mm` 用在 delay 参数 → `UNIT_MISMATCH` + `INVALID`；
- 缺单位 → `MISSING_UNIT`；
- 必需规则空 selector → `EMPTY_SELECTOR` + 覆盖 `REJECTED`；
- 无规则包 → `NO_RULES` + `INVALID`；
- 60 Ω 下限 vs 50 Ω 上限 → `INFEASIBLE`，冲突含双方规则、`net:I2C_SDA` 与 focus；
- 同样两条改为 soft → `SAT`，0 冲突，软目标保留两个界；
- `budget_seconds=0` → `UNKNOWN`，诊断明确“不是设计不可能”；
- 有叠层材料 + 封装后，原 `NOT_CHECKED` 的 UART 延迟规则变为 `APPLIED`，`review_ready=true`。

---

## 6. 与规范 C5 清单的对应

| 清单项 | 证据 |
| --- | --- |
| 单位类型、selector、硬/软规则、前提与来源 | 第 2 节；`tests/constraints/test_compile.py` |
| 制造下限 / 平台要求 / 用户更严条件求交集 | 第 3.2 节与 `c5-compile-report.md` 第 1 节 |
| 生成冲突集合 | 硬冲突测试与报告第 2 节 |
| 区分 UNKNOWN / INFEASIBLE，超时不标为不可能 | `UNKNOWN` 测试、`map_solver_status`、报告第 3 节 |
| `pytest tests/constraints/test_compile.py` | 8 passed |
| mm/ps 混用被拒绝 | `UNIT_MISMATCH` |
| 必需规则空选择器被拒绝 | `EMPTY_SELECTOR` |
| 缺封装/叠层时延迟规则仍适用并 NOT_CHECKED | `MISSING_FOOTPRINT` / `MISSING_STACKUP`，非 `SKIPPED_PRECONDITION` |
| UI 可定位的覆盖与冲突报告 | `object_refs` / `focus_nm` / `format_compile_report` |

---

## 7. 已知边界

- 编译器没有替换 `validation/reports.py` 中的 DRC 常量，也没有把 `CompiledConstraints` 注入现有检查流水线；这属于后续约束布局/DRC 阶段，当前阶段只交付可验证的编译合同。
- 区间求交是确定性求解，不依赖 CP-SAT；外部求解器状态映射是预留合同，不是“已运行 OR-Tools”的证据。
- `platform_packs/` 位于仓库根目录，加载器支持 `MOTA_PLATFORM_PACKS` 指向外部目录；wheel 目前只打包 `src/mota`，分发时需把该目录纳入打包流程（尚未做）。
- 阻抗/延迟规则在缺叠层介质参数时只能 `NOT_CHECKED`；测试用 `BoardV2.stackup` 提供了“有证据即可 APPLIED”的正例。
- 软规则之间也可以形成矛盾区间，但按定义不影响 `SAT/INFEASIBLE`；报告将其保留在 `soft_constraints` 供人工决定。
