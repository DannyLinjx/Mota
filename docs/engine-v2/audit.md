# Engine v2 — C0 仓库审计

审计对象：`/Users/danny/Documents/Mota` 当前工作树（MOTA-DEMO-1.0 + 通用模型适配器改造之后）。
审计方式：逐文件静态阅读 + 实际运行基线脚本（`scripts/engine_v2_baseline.py`）。
审计日期：2026-10-21。
本文件只记录**代码中实际存在的事实**；规划中的能力不写入结论。

---

## 1. 结论摘要

| 审计项 | 实际情况 | 影响 |
| --- | --- | --- |
| 器件白名单 | 14 个 Part、7 个 Footprint、8 个 Symbol 全部硬编码在 Python dict | 新器件必须改源码，无法运行时导入 |
| 模板生成 | 3 个模板 ID 硬编码在 Python 列表，recipe 来自包内 YAML | 新增电路必须新增 Python 可见的模板 |
| 两层假设 | `BoardIR.layer_count=2`、`Layer = Literal["F.Cu","B.Cu"]`、router 用 `other = 1 - layer` 翻层 | 多层 / BGA / 高速约束无处安放 |
| 网表模型 | `CircuitIR.components[].pin_pad_map` 由 PartDef 直接拷贝，Net 只有名字与端点 | 无 `SymbolDef/FootprintDef/PartDef/ComponentInstance` 分层，无总线/层次/多单元 |
| 路由成功判断 | 不信任 router 记账反推：`ROUTING_COMPLETE` + `COPPER_CONNECTIVITY` 两道独立检查；超时只记 `unrouted` | 判定口径正确，但只有两层且超时没有独立状态 |
| 版本模型 | `DesignVersion`（revision XOR draft+seq），`current.json` 是唯一提交点 | v2 资产必须作为派生内容挂到 revision，不能改写提交点 |

---

## 2. 器件白名单

### 2.1 实际位置

- [`src/mota/catalog/builtin.py`](../../src/mota/catalog/builtin.py)
  - `FOOTPRINTS: dict[str, FootprintDef]` — 第 70 行，7 项。
  - `SYMBOLS: dict[str, SymbolDef]` — 第 146 行，8 项。
  - `PARTS: dict[str, PartDef]` — 第 221 行，14 项。
  - `get_part()` 第 345 行、`get_footprint()` 第 351 行、`get_symbol()` 第 355 行：直接 `dict[...]` 查表。
- [`src/mota/catalog/service.py`](../../src/mota/catalog/service.py)
  - `search()` 第 72 行，只遍历 `builtin.PARTS.values()`（第 78 行）。
  - `inspect()` 第 109 行，`builtin.PARTS.get(asset_id)`（第 110 行），未命中直接 `NotFound`。
  - `part_diagnostics()` 第 42 行，只查 `builtin.FOOTPRINTS` / `builtin.SYMBOLS`。
- [`src/mota/catalog/sync.py`](../../src/mota/catalog/sync.py)
  - 已有 `sync_local_tree` / `sync_official`，能统计 `discovered / indexed / parsed / placeable / unsupported / failures`。
  - 但第 124–126 行与第 202–204 行把 `coverage.mapped = 0`、`coverage.recipe_ready = 0` 写死，并注释 “Only curated Golden parts are mapped/recipe-ready in this build.”
  - 第 255 行把 `recipe_ready` 固定为 3 个模板名。
- [`src/mota/catalog/kicad_parser.py`](../../src/mota/catalog/kicad_parser.py)
  - 真正的 S-expression 解析器：`ParsedSymbol` 第 60 行、`ParsedPin` 第 49 行、`ParsedPad` 第 70 行、`ParsedFootprint` 第 83 行。
  - 多 unit 只做计数并打诊断：第 127–129 行 `sym.units > 1` → `MULTI_UNIT_SYMBOL: unit mapping requires explicit review`，不进入可放置集合。

### 2.2 事实与后果

1. 器件知识是**源码常量**，不是数据。任何新器件都要求修改 `builtin.py` 并重新发布。
2. `sync` 已经能把 KiCad 文件读进来做统计，但导入结果**不参与** `search` / `inspect` / 布局 / 布线，因此“扫描到”不等于“可用”。
3. 现有 14 个器件的 `pin_pad_map` 是人工维护的；`build_circuit` 只是把它复制进 `Component`（见 3.2）。

---

## 3. 模板生成逻辑

### 3.1 实际位置

- [`src/mota/catalog/templates.py`](../../src/mota/catalog/templates.py)
  - `TEMPLATE_IDS = ["power_3v3", "led_timer", "i2c_temperature"]` — 第 23 行。
  - `list_templates()` 第 178 行，按 `TEMPLATE_IDS` 顺序加载。
  - `validation_profile` 默认 `mota_demo_2layer_v1` — 第 154 行。
- 模板数据：`src/mota/resources/templates/*.yaml` + `*.verification.json`。
- 校验规则：`src/mota/resources/rules/*.yaml`（含 `mota_demo_2layer_v1.yaml`）。
- [`src/mota/generation/modules.py`](../../src/mota/generation/modules.py)
  - `build_circuit()` 第 39 行；`component_id()` 第 31 行；`net_id()` 第 35 行。
  - 第 60 行 `pin_pad_map={k: list(v) for k, v in part.pin_pad_map.items()}`：**直接拷贝 PartDef**，不做封装/符号交叉校验。
- [`src/mota/cli/main.py`](../../src/mota/cli/main.py)
  - `templates list` 第 493 行；`catalog sync/status` 第 461 / 481 行。
  - `doctor` 第 229–233 行把 catalog 描述为 “builtin curated snapshot (14 parts, 7 footprints, 3 recipe-ready templates)”。

### 3.2 事实与后果

- 模板是**唯一**的电路入口：`build` 从 recipe 生成 `CircuitIR`，没有“空白工程 + 自由放置器件”的路径。
- `Component.pin_pad_map` 是 PartDef 的镜像，不是对 FootprintDef 的真实 pad 集合求解，导入器件无法自动得到映射。
- 三层（symbol / footprint / part / instance）目前压缩成两层常量（`PartDef` 携带 footprint_id、symbol_id、pin_pad_map）。

---

## 4. 两层假设

### 4.1 实际位置

- [`src/mota/domain/models.py`](../../src/mota/domain/models.py)
  - `SCHEMA_VERSION = 1` — 第 15 行。
  - `Layer = Literal["F.Cu", "B.Cu"]` — 第 27 行。
  - `BoardIR.layer_count` 默认 2。
- [`src/mota/generation/board.py`](../../src/mota/generation/board.py)
  - `build_board_ir()` 第 33 行；第 42 行 `layer_count=2` 硬编码。
  - 第 61 行 `route_board(board, RouteConstraints(), net_classes, ...)`。
  - 第 64 行 `validate_all(...)`；第 68 行把 `unrouted_nets` 写进 checks 上下文。
- [`src/mota/generation/routing.py`](../../src/mota/generation/routing.py)
  - `class Grid` 第 38 行，坐标 `Grid(layer, ix, iy)`，layer 只取 0/1。
  - 第 250 行 `other = 1 - layer`：换层是二元操作，无法表达 4/6/8 层或盲埋孔。
  - `RouteConstraints.budget_seconds = 30.0` — 第 31 行。
  - 超时/失败第 322、333、337 行一律 `unrouted.append(net_id)`，**不区分** “超时未证明” 与 “几何不可达”。
- 规则文件：`src/mota/resources/rules/mota_demo_2layer_v1.yaml`。

### 4.2 事实与后果

- Stackup、介质厚度、铜厚、阻抗、差分对、长度匹配在 v1 中没有数据结构承载。
- 换层只允许通孔（via），且层间关系由 `1 - layer` 隐含，无法扩展到任意层序。
- “未布线”是单一布尔语义，无法表达 `UNKNOWN`（预算耗尽）/ `INFEASIBLE`（已证明不可达）。

---

## 5. 网表模型

### 5.1 实际位置

- [`src/mota/domain/models.py`](../../src/mota/domain/models.py)
  - `Component`：`reference`、`part_id`、`footprint_id`、`symbol_id`、`pin_pad_map`、`pins`。
  - `Net`：`net_id`、`name`、`endpoints`（`component_id` + `pin`）。
  - `CircuitIR.schema_version: int = SCHEMA_VERSION`：注意该字段是**普通 int，不是 Literal**，因此 v2 可以覆盖为 2 而不破坏 v1 校验。
- [`src/mota/generation/modules.py`](../../src/mota/generation/modules.py) 第 60 行：`pin_pad_map` 从 PartDef 复制。
- [`src/mota/validation/connectivity.py`](../../src/mota/validation/connectivity.py)
  - `copper_groups()` 第 31 行：从 `board.tracks` / `board.vias` / `board.pads` 的**真实几何**重建连通（UnionFind）。
  - 文件头注释第 3–5 行明确：不信任 router 自己的成功记账。
- [`src/mota/projects/store.py`](../../src/mota/projects/store.py)
  - revision 目录：`revisions/<id>/{revision,circuit,schematic,board,checks,artifacts}.json` + `scene/` + `exports/`。
  - `current.json` 替换是唯一提交点；`_write_revision` 走 staging → `current.json` → COMMITTED。

### 5.2 事实与后果

- 网表以 `(component_id, pin)` 为端点，**没有** physical terminal（pad）级别的稳定 ID，也没有 instance path。
- 总线、层次实例、多单元符号在数据模型中不存在。
- `CircuitIR.schema_version` 可被 v2 覆盖，是 C0 迁移能“不复制结构”的关键前提（已在 `engine_v2/schema.py` 中使用）。

---

## 6. 路由成功判断

### 6.1 实际位置

- [`src/mota/validation/reports.py`](../../src/mota/validation/reports.py)
  - `ROUTING_COMPLETE`：第 129 行 `if board.unrouted_nets or board.ratsnest` → FAIL，evidence 里记录 `unrouted_nets` 与 `ratsnest_lines`。
  - `COPPER_CONNECTIVITY`：第 198 行，独立检查，基于几何连通组。
  - review gate：第 112–114 行、第 219–221 行，所有 required 检查 PASS 才 `READY_FOR_REVIEW`，否则 `REVIEW_BLOCKED`。
- [`src/mota/generation/routing.py`](../../src/mota/generation/routing.py) 第 250 行、322–337 行（见第 4 节）。
- [`src/mota/validation/connectivity.py`](../../src/mota/validation/connectivity.py) 第 31 行。

### 6.2 事实与后果

- 没有把 net 标签、飞线（ratsnest）或模型文本当作布线成功：`ROUTING_COMPLETE` 在存在 `ratsnest` 时直接 FAIL。
- 连通性由几何独立重建，router 记账错误不会变成 PASS。
- 但**超时**只体现为 `unrouted_nets`，报告里无法区分 “预算内未找到解（UNKNOWN）” 与 “已证明不可行（INFEASIBLE）”。这正是 C0 `JobResult` 要固定下来的语义（`engine_v2/contracts.py`，`JOB_STATUSES`）。

---

## 7. 已建立的 C0 事实（代码与证据）

| 产物 | 路径 | 状态 |
| --- | --- | --- |
| v2 合同 | `src/mota/engine_v2/contracts.py` | 已实现，`JobRequest/JobResult` + 6 种状态 |
| v2 schema | `src/mota/engine_v2/schema.py` | 已实现，`Stackup/ConstraintIR/HierarchyIR/BoardV2/...` |
| v1→v2 迁移 | `src/mota/engine_v2/migration.py` | 已实现，派生写入 `revisions/<id>/engine_v2/`，可回滚 |
| schema 导出 | `schemas/engine-v2.json` | 已生成，31 definitions |
| 基线证据 | `docs/engine-v2/baseline/*.json` | 已生成，三模板 `semantic_hash_v1 == semantic_hash_v2` |
| 迁移测试 | `tests/migration/test_v1_baseline.py` | 6 passed |

实际执行的基线结果（来自 `docs/engine-v2/baseline/index.json`）：

| 模板 | review | checks (PASS/FAIL/WARN/NOT_CHECKED) | tracks | vias |
| --- | --- | --- | --- | --- |
| power_3v3 | REVIEW_BLOCKED | 17 / 0 / 0 / 4 | 8 | 0 |
| led_timer | READY_FOR_REVIEW | 20 / 0 / 1 / 1 | 19 | 2 |
| i2c_temperature | READY_FOR_REVIEW | 20 / 0 / 1 / 1 | 13 | 0 |

---

## 8. 未覆盖能力（明确不在 C0 范围）

- 运行时导入 KiCad 资源并进入 `search/inspect/place`：C1。
- `SymbolDef / FootprintDef / PartDef / ComponentInstance` 分离与自由编辑：C1 + C2。
- 多单元、层次实例、总线展开、pin-pad 自动求解：C2 + C3。
- 原生几何内核、独立 DRC、多层路由、BGA、高速约束：C0 之后的依赖阶段，尚未实现。
- 本审计**没有**运行 KiCad，也没有把任何外部 PCB 复制结果当作本仓库能力。
