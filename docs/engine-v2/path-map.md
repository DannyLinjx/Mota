# Engine v2 — C0 路径映射

本文件把 v1 的真实实现路径映射到 v2 目标路径，并标明**当前状态**。
状态只有两种：`已存在`（本次 C0 已落盘并通过测试）与 `待实现`（后续阶段任务，不得当作已完成）。

---

## 1. 模块路径

| 职责 | v1 实际路径 | v2 目标路径 | 状态 |
| --- | --- | --- | --- |
| 领域 DTO（单位、版本、检查状态） | `src/mota/domain/models.py`（`SCHEMA_VERSION = 1`） | 保留不动；v2 叠加层 `src/mota/engine_v2/schema.py` | 已存在 |
| 器件常量 | `src/mota/catalog/builtin.py`（14 parts / 7 footprints / 8 symbols） | `src/mota/library/catalog.py` + `kicad_symbols.py` + `kicad_footprints.py`；builtin 继续作为内置来源 | 已存在（C1） |
| 器件检视 | `src/mota/catalog/service.py`（只查 builtin） | 查询接口不变，后端合并“内置 + 导入快照”；`catalog.search` 增 `library_assets`，`catalog.inspect` 支持 `sym:`/`fp:` | 已存在（C1） |
| KiCad 扫描统计 | `src/mota/catalog/sync.py`（`mapped=0` 写死） | `library/catalog.py` 产出 `LibraryImportReport`，`discovered = parsed + unsupported + failed` | 已存在（C1） |
| KiCad 解析 | `src/mota/catalog/kicad_parser.py`（多 unit 仅诊断） | `src/mota/library/kicad_symbols.py`、`kicad_footprints.py`（继承、unit/style、字符串引脚、pad 类型/形状） | 已存在（C1） |
| 模板 | `src/mota/catalog/templates.py`（`TEMPLATE_IDS` 硬编码 3 项） | 保留 3 个模板作为回归样例；同时表达为 `knowledge/modules.py` 的 `power_3v3`/`led_timer`/`i2c_temperature` 模块 | 已冻结（回归样例）；模块已存在（C4） |
| 电路编译 | `src/mota/generation/modules.py`（`pin_pad_map` 直接拷贝 PartDef） | `src/mota/circuit/parts.py`（解析/映射）、`app/design.py`（typed patch）；Part/Instance 分离 | 已存在（C2） |
| 层次与总线 | 无 | `src/mota/circuit/hierarchy.py`、`src/mota/circuit/compiler.py`、`src/mota/schematic/layout.py` | 已存在（C3） |
| 实例模型 | `Component` 只有 part/symbol/footprint | `src/mota/ir/models_v2.py` 的 `ComponentInstanceV2`；`Component` 追加 instance_path/on_board/in_bom/assembly_state | 已存在（C2） |
| 电路模块/知识 | 无 | `src/mota/knowledge/modules.py`：ModuleDef、参数、端口、资源、电源/时序事实、三个原创组合 | 已存在（C4） |
| 电气契约 | 分散在 `validation/electrical.py` 的模板检查 | `src/mota/constraints/electrical.py`：RuleSpec、电源预算、接口方向/电平、pin mux、地址、时序 DAG | 已存在（C4） |
| 约束编译器 | 无（`validation/reports.py` 中硬编码的 0.25/0.30/0.60 mm 常量） | `src/mota/constraints/compiler.py`：量纲化 Quantity、TypedSelector、硬/软规则、前提、来源；制造下限 × 平台要求 × 用户更严条件求交集；SAT/INFEASIBLE/UNKNOWN/INVALID 区分 | 已存在（C5）；C11 新增 `compile_highspeed`（规则 → native highspeed request，显式 channel，覆盖报告）；尚未替换 DRC 常量 |
| 平台规则包 | 无 | `src/mota/knowledge/platforms.py` + `platform_packs/schema.json` + 三个 v1 包（制造下限 / 平台要求 / 用户条件） | 已存在（C5） |
| 原生几何内核 | 无 | `native/include/mota/{types,shapes,rtree}.hpp`、`native/src/geometry/`：int64 坐标、__int128 谓词、共享变换、分类型距离、误差界、packed R-tree | 已存在（C6） |
| 多层板/孔数据 | `BoardV2` 仅有类型 | `native/include/mota/board.hpp`、`native/src/board/`：stackup、逐层 padstack、through/blind/buried/micro via span、NPTH 语义、逐层索引 | 已存在（C6）；填铜/DRC 已接入（C7） |
| 原生 worker | `engine_v2/contracts.py` 仅合同 | `native/src/main.cpp`、`native/src/worker/`、`src/mota/jobs/contracts.py`：JSON-RPC、SHA-256 冻结、预算/取消、错误隔离、JobResult 映射 | 已存在（C6）；verify/place/route 已接入（C7/C8/C9） |
| 填铜/物理连通/独立 DRC | `validation/connectivity.py` 只按 pad/track/via 的几何并查集；DRC 常量硬编码 | `native/src/zones/`、`connectivity/`、`verify/`；`src/mota/verification/service.py`：实际铜矩形、thermal、island、U 指标、规则覆盖与 NOT_CHECKED | 已存在（C7）；仅支持直线轮廓填铜，任意多边形布尔待后续 |
| 板级构建 | `src/mota/generation/board.py`（`layer_count=2` 硬编码） | `ir/models_v2.py` 的 `BoardV2`（`src/mota/engine_v2/schema.py` 已定义类型） | 类型已存在；构建待实现 |
| 布局 | `src/mota/generation/placement.py`（模板锚点 + 1.27 mm 合法化） | `native/src/placement/`：模块硬区域/软带、关键件优先、keepout/去耦/ courtyard 硬约束、节点预算搜索、局部优化、拥塞与可逃逸性反馈；`src/mota/placement/service.py` 负责 IR 投影 | 已存在（C8）；未用 OR-Tools，HPWL 用器件中心近似 |
| 布线 | `src/mota/generation/routing.py`（`other = 1 - layer`，30 s 预算） | `native/src/routing/global|detailed/`：3D 稀疏网格、粗走廊、合法 through via、多端树、rip-up 多轮、独立连通/U；worker `route` 查询 | 已存在（C9）；盲/埋孔待后续；BGA 扇出已接入（C10） |
| BGA 扇出 | 无 | `native/src/routing/fanout/`：通道初筛、dogbone / via-in-pad site、组保留、层分配、真实 escape 几何与失败定位；`platform_packs/bga-fixtures/` 五个 fixture | 已存在（C10）；一个 STM32H743 TFBGA240 代表性封装，不泛化到全部 BGA |
| 差分/路径/延迟约束 | 无 | `native/src/routing/highspeed/`：端到端 channel（串联元件/分支/接收端）、差分共同搜索与角点/转层配对、按层+via+封装延迟、参考面裂缝、受控 tuning region；`constraints/compiler.py` 的 `compile_highspeed`；worker `highspeed` 查询 | 已存在（C11）；耦合为采样测量，tuning 是建议几何，SI/PI 恒为 NOT_CHECKED |
| 工程导入/往返 | `generation/kicad_writer.py` 只做尽力导出，无导入 | `src/mota/io/kicad_project.py`、`kicad_writer.py`：`.kicad_pro`/`.kicad_sch`/`.kicad_pcb` 导入、未知项分类记录、source/mota 分离的语义/几何/规则比较；固定 CM4 IO 包（zip SHA-256） | 已存在（C12）；不合成 `lib_symbols`，未在真实 KiCad 中打开验证 |
| 生产输出 | 无 | `src/mota/io/manufacturing.py`：层→Gerber 文件名映射、Excellon PTH/NPTH（含 G85 槽）、BOM/位置 CSV、独立回读与一致性检查 | 已存在（C12）；Gerber 工件未生成，外部 CAM 校验 `NOT_CHECKED` |
| 大场景界面 | `desktop/widgets/canvas_2d.py` 一次性加载整场景 | `desktop/scene_adapter.py`（SceneManifest/chunks/LOD/版本拒绝/共享 mesh）、`project_tree.py`（树形导航/筛选/跨视图定位）、`job_panel.py`（Saved/Stale）、`canvas_2d.py` 分块加载与剔除 | 已存在（C13）；控件尚未嵌入 Build 页最终布局，帧率为 offscreen 实测 |
| 人工接管 | 仅引擎 `design.patch` | 桌面 `ProjectController` 与 CLI `mota design move/lock/undo/redo` 共用 `ApplicationService`、操作 schema 与 `expected_revision_id` | 已存在（C13） |
| 冻结 benchmark | 无 | `benchmarks/routing_single_ended/`（6 案 + SHA-256 manifest）、`benchmarks/run.py`、`mota benchmark run --suite single-ended-v2 --report ./reports/` | 已存在（C9）；8/8 网络布通，冻结分母保留未布通项 |
| 连通性 | `src/mota/validation/connectivity.py`（几何 UnionFind） | v1 保留；`native/src/connectivity/` 从实际铜形状提取连通、短路、U、NPTH/net-tie/via span | 已存在（C7 原生）；v1 旧检查继续可用 |
| 检查与 review gate | `src/mota/validation/reports.py`（`ROUTING_COMPLETE`、`COPPER_CONNECTIVITY`） | 保留；`native/src/verify/` + `verification/service.py` 产出独立 DRC 与规则覆盖；空规则集不得 PASS | 已存在（C7 原生 DRC）；旧 gate 继续作为回归基线 |
| 工程事务 | `src/mota/projects/store.py`（`current.json` 唯一提交点） | 不变；v2 派生目录 `revisions/<id>/engine_v2/` | 已存在 |
| 合同 | 无 | `src/mota/engine_v2/contracts.py` | 已存在 |
| 迁移 | 无 | `src/mota/engine_v2/migration.py` | 已存在 |
| Schema 导出 | 无 | `schemas/engine-v2.json` | 已存在 |
| 基线证据 | 无 | `docs/engine-v2/baseline/{power_3v3,led_timer,i2c_temperature,index}.json` | 已存在 |

---

## 2. 身份映射

| 概念 | v1 身份 | v2 身份 | 兼容规则 |
| --- | --- | --- | --- |
| 工程 | `project_id` | 不变 | v2 不改工程 ID |
| 版本 | `DesignVersion(revision_id)` 或 `DesignVersion(draft_id, draft_seq)` | 不变，作为 `JobRequest.base_version` / `JobResult.base_version` | 所有 v2 任务绑定同一 DesignVersion |
| 器件定义 | `PartDef.part_id`（与 footprint/symbol 同层常量） | `SymbolDef.asset_id` / `FootprintDef.asset_id` / `PartDef.asset_id` 三层 | 旧 `part_id` 作为 `PartDef.asset_id` 保留 |
| 器件实例 | `Component.component_id` | `ComponentV2.component_id` + `instance_path` | 顶层实例 `instance_path` 为空；层次实例引用同一 `component_id` 模板 |
| 物理端点 | 隐式 `(component_id, pin)` → `pin_pad_map[pin]` | `PhysicalTerminal(component_id, pin, pad, layer)` | 迁移时由 `pin_pad_map` 生成，顺序与内容完全一致 |
| 网络 | `Net.net_id` / `Net.name` | 不变；层次/总线展开后仍以稳定 `net_id` 去重 | 顶层网络 ID 不变 |
| 符号单元 | 无 | `ComponentV2.symbol_units` | v1 迁移固定为单 unit |

跨视图 ID 约束（C0 测试已锁定）：同一 revision 内 `component_id`、`net_id`、`pad.id`、`track.id`、`via.id` 在 circuit / schematic / board / checks 之间必须一致。

---

## 3. 单位与数值

| 项 | v1 | v2 |
| --- | --- | --- |
| 长度 | integer nanometre（1 mm = 1,000,000 nm） | 不变 |
| 铜厚 | 无显式结构 | `StackupLayer.thickness_nm`，迁移默认 35,000 nm |
| 板厚 | `BoardIR.thickness_nm` | 不变；作为 stackup 总厚校验 |
| 坐标 | `x_nm` / `y_nm` | 不变 |

迁移生成的 stackup 层序（v1 两层板）：

```
F.Mask → F.Cu → Dielectric_1 → B.Cu → B.Mask
```

各层厚度之和必须等于 `board.thickness_nm`，否则 `MIGRATION_INVALID`。

---

## 4. 检查状态与任务状态

### 4.1 检查状态（不变）

`PASS` / `FAIL` / `WARN` / `NOT_CHECKED`。
未执行的检查必须保持 `NOT_CHECKED`，不得写成 `PASS`。

### 4.2 任务状态（v2 新增）

`JobResult.status` ∈ `SUCCEEDED / PARTIAL / INFEASIBLE / UNKNOWN / CANCELLED / FAILED`
（`src/mota/engine_v2/contracts.py`）。

| v1 现象 | v2 表达 |
| --- | --- |
| 全部网络布通 | `SUCCEEDED` |
| 部分网络布通 | `PARTIAL`，`completed_targets` / `unimplemented_targets` 可对账 |
| 预算耗尽仍有未布网络 | `UNKNOWN`（**不得**写成 “已证明无解”） |
| 已证明不可达 | `INFEASIBLE`，必须有完成全部搜索的检查证据 |
| 用户取消 | `CANCELLED` |
| 内部错误 | `FAILED` + `error_code` / `error_message` |

`requested_targets` 在请求时冻结，作为分母；`completed_targets + unimplemented_targets ⊆ requested_targets`，测试已校验 `UNKNOWN ≠ INFEASIBLE`。

---

## 5. 磁盘布局映射

### 5.1 v1（保持不变）

```
workspace/projects/<project_id>/
  project.json  commit_index.json  current.json
  revisions/<revision_id>/{revision,circuit,schematic,board,checks,artifacts}.json
  drafts/<run_id>/draft.json
  runs/<run_id>/{run.json,events.jsonl,checkpoint.json,metrics.json}
  sources/  sessions/  exports/  trash/
```

### 5.2 v2 派生目录（新增，不改写 v1）

```
revisions/<revision_id>/engine_v2/
  engine_v2.json     # EngineDocumentV2（CircuitV2/SchematicV2/BoardV2/ConstraintIR/...）
  migration.json     # source_hash / document_hash / semantic_hash / assumptions / rollback
```

规则：

- 派生目录写入前先 staging，成功后才 rename；失败不留半成品。
- 迁移完成后复核 5 个 v1 源文件 hash 未变，否则删除 staging 并报 `MIGRATION_SOURCE_CHANGED`。
- `current.json` 仍是唯一提交点；v2 目录不参与提交判定。
- 回滚 = 删除 `engine_v2/`；v1 文件字节不变。

---

## 6. 迁移错误码

| 错误码 | 触发条件 | 是否改动旧 revision |
| --- | --- | --- |
| `MIGRATION_INVALID` | 结构/类型错误、stackup 厚度不一致 | 否 |
| `MIGRATION_UNSUPPORTED_FIELD` | 出现未知顶层字段 | 否 |
| `MIGRATION_UNSUPPORTED_LAYER_COUNT` | `layer_count != 2` | 否 |
| `MIGRATION_DANGLING_REFERENCE` | pad/track/via 引用不存在的 net 或 component | 否 |
| `MIGRATION_STALE` | 已存在派生目录但源字节不同 | 否 |
| `MIGRATION_SOURCE_CHANGED` | 迁移过程中源文件被改动 | 否（删除 staging） |

---

## 7. 保留不变的对外入口

- CLI：`mota build`、`mota templates list`、`mota catalog sync/status`、`mota config ...`。
- 桌面：PySide6 原生页面（Build / Workspace / History / Memory / Settings）。
- 用户隔离：`workspace/paths.py` 的 `user_dir/workspace_dir/projects_dir/memory_dir`。
- 三层 Markdown 记忆：`src/mota/memory/*`。
- 模型适配器：`src/mota/agent/provider.py`（Responses / ChatCompletions 两路），`config schema v2` 的 profile 与 `ProviderBinding`。

v2 只新增数据与任务路径，不改上述入口的调用方式。
