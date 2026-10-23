# MoTA 09 交付说明（真实资源 + 场景二/三）

本轮直接在现有工程上开发，未新建项目、未套用整板模板。以下内容均为实际执行结果。

## 1. 资源库

- 来源：Ubuntu noble universe 的 `kicad-symbols` / `kicad-footprints` **7.0.11-1**
  （与 KiCad 官方 7.0.11 库同版本），由清华镜像下载，`.deb` 解包后保存在
  `~/Library/Application Support/Mota/catalog/sources/kicad-7.0.11/`。
- 通过产品自带导入器 `mota.library.catalog.import_library` 导入，source id
  `kicad-7.0.11`，快照 `3bd01972dc2209bb`，**reference-only**（记录哈希，源目录保留）。
- 真实覆盖：发现 34208，解析 34004，unsupported 203，failed 1，可映射 33937。
  状态为 `PARTIAL`（未把失败项静默算作成功）。
- 目前仍由 34k 资产索引驱动搜索/检查；内置 14 件不再作为可搜索范围的总集。

## 2. 产品修改（使导入器件可真实放置）

| 文件 | 修改 | 原因 |
| --- | --- | --- |
| `src/mota/agent/tools.py` | 补丁参数新增 `footprint_id`、`pin_pad_mapping`；落盘 `draft.requirement` | 导入符号本身不带封装；模型需要显式绑定真实封装 |
| `src/mota/app/design.py` | `PatchOutcome` 增加 `requirement` | 之前 `set_board_size` 的 requirement 变更被丢弃 |
| `src/mota/app/service.py` | 提交时保存 `draft.requirement`；空白工程写入 60×40 默认尺寸；新增 `project.watch` | 板尺寸持久化；画板跟随外部真实提交 |
| `src/mota/generation/board.py` | 板尺寸以 requirement 为准，只有尺寸一致才复用旧 outline | 修复 `set_board_size` 被 `base_board` 覆盖（I-02） |
| `src/mota/catalog/service.py` | `inspect` 支持 `sym:` / `fp:` 导入资产，返回真实引脚表与焊盘 | 模型可读取真实引脚后再连线 |
| `src/mota/validation/electrical.py` | `check_pin_pad_mapping` 改用 `resolve_part` / `resolve_footprint` | 恢复/复制/导入路径此前只用内置 14 件表，导入器件会被误判 FAIL |
| `src/mota/desktop/pages/build.py` | 1.5 s 轮询 `project.watch`，检测到新 revision 后重载画板 | 真实提交后画板增量更新；运行中/待提交时让位于当前操作 |

## 3. 场景二基线：MoTA_Sensor_Control_Node

工作区原本没有该工程，按已确认的方案先生成基线。

- 工程：`project_b19b36a021c008de`，基线 `rev_000011`。
- 37 个器件 / 24 网络 / 120 mm × 90 mm（写入 requirement 与 board.json）。
- 器件全部来自 KiCad 7.0.11 真实符号 + 封装：
  - `U1` STM32F103C8Tx / LQFP-48_7x7mm_P0.5mm
  - `U2` TLV75533PDBV / SOT-23-5
  - `U3` THVD1500 / SOIC-8（**替代说明**：KiCad 7.0.11 官方库没有 SN65HVD72，
    选用同封装、3.3 V 的 THVD1500）
  - `Q1/Q2` AO3400A / SOT-23
  - 0805 电阻/电容、2.54 mm 排针
- 基线功能：5 V 输入 + 3.3 V LDO、4 路模拟（PA0–PA3，1k 串联 + 100nF 对地）、
  RS-485（USART3：PB10/PB11，DE=/RE=PB12）、调试 UART（PA9/PA10）、
  SWD（PA13/PA14）、2 路低边 MOSFET（PB13/PB14）。

## 4. 场景三：扩展到 8 路模拟 + 24LC256

- 当前版本 `rev_000018`（内容同 `rev_000015`；期间在历史页执行过一次真实
  “Restore” 回到 `rev_000014`，随后恢复回 EEPROM 版本），53 个器件 / 38 网络 /
  板框仍为 120 × 90 mm。
- 新增 4 路模拟（PA4–PA7，`J11–J14`、`R12–R15`、`C11–C14`），结构同为
  2Pin → 1kΩ → ADC 节点，100nF 对地；串阻两侧为独立网络（`AINn_IN` / `AINn`），未短接。
- 新增 `U4` 24LC256-I/SN（SOIC-8）：
  - SDA=PB7（pin43），SCL=PB6（pin42）；
  - `R16/R17` 4.7k 上拉到 3V3；
  - `C15` 100nF 去耦；
  - `A0/A1/A2` 与 `WP` 均接 GND（地址 0x50，写保护关闭）。
- 未改动项核验：基线 37 个器件的 **id、part_id、value、locked、坐标、旋转全部一致**
  （37/37），仅 GND 与 VCC_3V3 因新增器件扩展；RS-485 / UART / SWD / MOSFET / 电源
  网络成员完全不变。

## 5. 实际校验结果

最终 `rev_000018` 的检查：PASS 11，FAIL 4，WARN 0，NOT_CHECKED 1（EMC）。

- 仍 FAIL（如实保留，未改成通过）：
  1. `VISIBLE_TEXT`：原理图 53 器件超出 A4 单页，文字重叠/出界（I-05 未修）。
  2. `ROUTING_COMPLETE` / `COPPER_CONNECTIVITY`：38 个网络未完成铜连接，
     板上有 174 个焊盘、9 段走线。原因：演示路由器 0.25 mm 间距规则遇到
     0.5 mm 间距 LQFP 时逃逸通道被完全阻塞，且共享 30 s 预算在首个大网络耗尽。
  3. `DRC_CLEARANCE`：LQFP-48 相邻焊盘实际 0.200 mm，小于演示规则 0.25 mm
     （封装本身如此，不是布线造成）。
- 画板证据：`rev_000018/thumbnail.svg` 已渲染核对，可见 8 路接口、MCU、RS-485、
  SWD/UART、2 路 MOSFET、EEPROM 与全部无源件。

## 6. 未完成/边界

- 铜布线未收敛（见上），未用任何预生成文件或静态图替代。
- 33 个未解析资产（203 unsupported + 1 failed）保留在库状态中，未被隐藏。
- 原理图分页/自动布局未改造。

## 7. MoTA 10：原理图越界修复（同一工程）

### 根因

`src/mota/generation/schematic.py` 的 `generate_schematic` 把器件固定排成 4 列、
行距 40 mm、起始 y=176 mm，且从不检查纸张。`rev_000018` 的 53 个器件因此排成
14 行，y 坐标从 176 mm 一直到 **−344 mm**（A4 高 210 mm，越出下边 134 mm）。
`CanvasView.load_scene` 在用户未手动缩放时按“内容范围”自动 fit，内容延伸到
页外 344 mm，于是整图被缩到约 29%。

### 代码修改

| 文件 | 修改 |
| --- | --- |
| `src/mota/schematic/geometry.py`（新增） | 纸张列表、页边距、标题栏禁布区，以及符号完整占位（本体+引脚+编号+位号+参数+网络标签）的唯一计算源 |
| `src/mota/generation/schematic.py` | 按功能分组（电源、MCU、模拟输入、RS-485、调试、负载、EEPROM）用真实尺寸分块摆放；依次尝试 A4/A3/A2，放不下不向页外追加 |
| `src/mota/validation/electrical.py` | `VISIBLE_TEXT` 改用完整占位，逐项报告“哪一页、哪个对象、越过哪条边、多少毫米、是否压标题栏” |
| `src/mota/app/design.py`、`domain/models.py`、`agent/tools.py` | 新增 `relayout_schematic`（只重排原理图，不触碰 PCB）与 `move_schematic_symbol`（移动单符号，超界直接拒绝） |
| `src/mota/desktop/widgets/canvas_2d.py` | 同一纸张尺寸下重新加载不再反复整图 fit；Fit Page/Content/Selection 仍只改相机 |
| `src/mota/desktop/widgets/design_view.py`、`pages/build.py` | 原理图拖动接到 `move_schematic_symbol`；被拒绝的拖动自动回读已提交几何 |
| `src/mota/catalog/service.py`、`desktop/widgets/resource_panel.py`、`pages/workspace.py` | 搜索合并导入库资产；底部状态改为真实构成；导入符号需显式绑定封装才允许 UI 放置 |

### 实际执行

| 版本 | 操作 | 结果 |
| --- | --- | --- |
| `rev_000018` | 原始状态（证据快照） | A4，53 器件，y 低至 −344 mm，VISIBLE_TEXT FAIL |
| `rev_000019` | `relayout_schematic` | 自动选 A3 420×297；53 器件、38 网络不变；VISIBLE_TEXT PASS |
| `rev_000020` | 移动 R5 +6 mm（合法） | 导线端点跟随，PCB 板图哈希不变 |
| `rev_000021` | Undo | 回到 R5 原位 |
| `rev_000022` | Redo | 再次回到 +6 mm |
| `rev_000023` | Undo（最终） | 当前版本，保持重排结果 |

另：把 R5 移到 x=1000 mm 的请求被拒绝，返回
`CONSTRAINT_CONFLICT · R5 cannot move there: beyond the right edge by 593.9 mm`，
版本号不变。

### 校验

- 保存重开：`project.open` 读回 A3、53 符号、38 网络，与画板一致。
- 电气不变：器件 `(id, reference, part_id, value)` 与 38 个网络的成员集合逐一相同。
- PCB 不变：`board.json` 全字段哈希与重排前一致（174 焊盘、9 段走线、板框 120×90）。
- Fit（真实 `Canvas2D` 离屏运行）：Fit Page 2.5455、Fit Content 3.3922、
  Fit Selection 12.0；画面只变相机，模型坐标不变；sceneRect 840×717（页框+留白），
  不是无限网格。
- 回归测试：`tests/schematic`、`test_validation_negatives`、`test_build_pipeline`、
  `test_versioned_scene` 共 30 项通过。

### 剩余项

- PCB 仍有 3 项既有 FAIL（38 网络未布完、0.2 mm LQFP 焊盘 vs 0.25 mm 演示规则、
  铜连通性），与本次原理图修复无关，未篡改。
- 导入符号在左侧可搜索、可查看真实引脚，但 UI 放置仍需先绑定 `fp:` 封装；
  当前按钮会禁用并说明原因，不静默生成无封装器件。
- 原理图仍为单页；A3 可容纳本工程，更大工程需分页能力。

## 8. MoTA 11：电气、检查、布局、布线、产品 Agent

本轮在原工程 `MoTA_Sensor_Control_Node` 上继续，不重建、不回退用户修改。

### 8.1 电路：THVD1500 与 12V 负载

| 项 | 实际改动 |
| --- | --- |
| U3 供电 | 原 `sym:kicad-7.0.11:Interface_UART:THVD1500`（4.5–5.5 V）接在 `VCC_3V3` 上。用 `replace_component_asset` 换成 `sym:kicad-7.0.11:Interface_UART:MAX3485`（3.0–3.6 V），封装 `fp:kicad-7.0.11:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm`；8 个引脚网络按 `pin_role_mapping` 迁移，part/symbol/footprint/pin_pad_map/value 一起更新，不是只改 value |
| 负载电源 | J9:1、J10:1 原在 `VCC_5V`。用 `disconnect_terminal` 断开，新增 J15（12V_IN）与 C16，新建 `VCC_12V`；现成员为 `J9.1 / J10.1 / J15.1 / C16.1`，与逻辑 5V/3.3V 只共地 |
| 新增能力 | `replace_component_asset`、`disconnect_terminal`、`set_rule_profile`、`move_schematic_symbol`、`relayout_schematic` 都是 `design.patch` 操作；连接冲突只回滚该操作，工程不重建 |

结果：`VOLTAGE_DOMAIN PASS · Supply rails match the verified part ranges (4 checks)`，
`NET_COMPLETENESS PASS · 39 nets ... 18 pins explicitly NC`。

### 8.2 检查：统一解析器 + NOT_CHECKED

- `validation/electrical.py` 的供电、去耦、引脚映射全部走 `catalog.parts.resolve_part` /
  `resolve_footprint`，不再依赖 builtin 表。
- 供电检查用 `catalog/verified.py` 的手册转录（`STM32F103C8Tx` 2.0–3.6 V、
  `MAX3485` 3.0–3.6 V 等）。已知不符 → `FAIL`；缺器件数据 → `NOT_CHECKED`；
  不会因为不在 builtin 表就判 `PASS`。
- 去耦检查按真实网络成员找电容；找不到证据时给 `NOT_CHECKED` 并写明原因。

### 8.3 原理图布局

`generation/schematic.py` 改为按功能分组（电源 / MCU / 模拟输入 / RS-485 / 调试 /
负载输出 / EEPROM）分块摆放，依次尝试 A4/A3/A2。当前 55 个符号落在
420 × 297 mm（A3），`VISIBLE_TEXT PASS · All 55 symbols, labels and wires fit the 420 × 297 mm sheet`。
纸张选择与占位计算共用 `schematic/geometry.py`，Fit 只改视图。

### 8.4 PCB 规则与布线

工艺规则落在 `resources/rules/mota_2layer_1oz_v1.yaml`，路由与 DRC 共用同一份
`RuleProfile`（`RouteConstraints.from_profile`、`validation/reports.py`），不再有代码里的
0.25 mm 常量。

| 修改 | 说明 |
| --- | --- |
| 供电线宽 0.50 → 0.30 mm | 0.50 mm 无法从 0.5 mm 间距的 LQFP 焊盘间引出（间隙只有 0.20 mm）。0.30 mm 在 1 oz 下约 0.8 A@10 °C，覆盖本板 ≤0.5 A 支路；规则内写明依据与来源 |
| 细间距扇出 | 对 ≥8 个 SMD 焊盘且间距 ≤0.8 mm 的封装，先按焊盘法向放 3.0/2.0/1.2/0.5/0.25 mm 短引线，逐条过 DRC，失败则缩短；LQFP 的 48 条逃逸通道在任何长线之前被占用 |
| 真实焊盘几何 | 阻挡与 via 判定统一走 `domain.geometry.pad_distance`；修掉 via/pad 混用 mm 与 nm 的错误 |
| 起点焊盘 | 多锚点网络原来按“最近焊盘”猜起点，会把短引线拉成横穿板面的斜线；现在记录节点→焊盘映射 |
| via 尺寸 | `_build_tracks` 原来用 `Via` 默认 0.80/0.40 mm，与规则 0.60/0.30 mm 不一致，DRC 必然拒绝；现在按规则生成 |
| 搜索 | 有界 A*（包围盒 +12 mm）、加权启发、`array` 存 g/parent，单次搜索快约 3 倍；子连接 6 s 时间片、多轮公平调度，成功铜皮全部保留 |
| 失败不隐藏 | 每个候选先做 `check_clearance`，不合格即弃；`MOTA_ROUTE_DEBUG=1` 打印拒绝原因 |

结果（`rev_000029`，55 器件 / 39 网络 / 142 段铜 / 44 个过孔）：

| 检查 | 状态 |
| --- | --- |
| DRC_CLEARANCE / DRC_TRACK_WIDTH / DRC_VIA / DRC_HOLES / DRC_BOARD_EDGE | PASS |
| COPPER_SHORTS | PASS |
| COPPER_CONNECTIVITY / ROUTING_COMPLETE | **FAIL**：GND、UART_TX、VCC_3V3、VCC_5V 未完全连通 |
| EMC | NOT_CHECKED（本轮不覆盖） |

剩余 4 个网络是因为两层板上 GND 有 47 个焊盘、又没有铺铜：按现有预算先保证信号/电源
后，GND 还差约 40 条支路。下一步应做 B.Cu 地平面（zone），而不是继续加时间或把焊盘改窄。

### 8.5 产品 Agent

- 新增 `begin_design`：自由设计在空白工程上建真实草稿，写板尺寸与 `rule_profile`；
  未指定时默认 `mota_2layer_1oz_v1`（避免再用 0.25 mm 演示规则去卡 0.20 mm 焊盘缝）。
- `runner.py` 的 system prompt 改为自由设计优先；`RequestBudget` 提到
  60 轮模型 / 240 次工具 / 1800 s，并在草稿为空时给出一次推进提示。
- 工具只调用通用能力（`search_catalog` / `inspect_part` / `apply_design_patch` /
  `build_board` / `validate_design`），没有任何写死的板级生成脚本。

### 8.6 运行中画板

- `agent/tools.py` 在草稿真正保存后发 `component.added`、`net.connected`、
  `footprint.assigned`、`board.track_created`、`design.patch_applied`。
- `desktop/pages/build.py` 用 120 ms 合并窗口按对象事件刷新对应视图；1.5 s
  `project.watch` 只作为外部提交的补充同步。
- 事件里的 `seq` 才算序号（`runs.inspect` 按它过滤）；控制器已按 `seq` 递增。

### 8.7 一次真实产品演示

需求文本就是 `Mota_11_Electrical_Routing_and_Real_Agent_Completion_Trae.md`
第 6 节那段完整生成需求（STM32F103C8T6 / 8 路模拟 / 3.3V RS-485 /
24LC256 / 两路 AO3400A / 独立 12V）。它通过产品自身的运行入口
（`run.start`，`kind="prompt"`，就是聊天框发送时调用的同一条命令）交给当前模型，
没有调用任何板级写死脚本。

| 阶段 | 实际结果 |
| --- | --- |
| 空白工程 | `project.create` → `project_5f28d15a702aa113`，0 器件 |
| 放件 | 模型自己 `search_catalog` / `inspect_part` 后 `add_component`，最终 60 个器件 |
| 连接 | `connect_pins` 形成 33 个网络，21 个引脚显式 NC |
| 原理图 | 60 个符号落在 A3，`VISIBLE_TEXT PASS` |
| PCB | 120 × 90 mm，`build_board` 产出 125 段铜、28 个过孔（换成 1 oz 规则后） |
| 保存 | 运行结束自动提交 `rev_000002`；随后一次 `set_rule_profile` 编辑提交 `rev_000003`，证明工程可继续编辑 |
| 剩余 | `BOOT0`、`VDD_3V3` 未完全连通；`SUPPLY_DECOUPLING` 报 5V/3.3V/12V 缺少去耦电容；`U3 = THVD1420D` 无手册转录，供电检查为 `NOT_CHECKED` |

原生窗口截图（真实 `MainWindow` + 真实引擎）：

- `docs/screenshots/mota11_agent_schematic.png`
- `docs/screenshots/mota11_agent_pcb.png`
- 参照工程 `docs/screenshots/mota11_control_schematic.png`、
  `docs/screenshots/mota11_control_pcb.png`

原工程 `MoTA_Sensor_Control_Node` 未删除，仍在 `rev_000029`。

### 8.8 已知问题

- 集成测试 `tests/integration/test_api_flow.py` 仍有多项沿用旧模板流程的断言失败
  （`DesignVersion` 只给 `revision_id=None`、提交故障码、运行状态期望），
  与本轮功能无关，未在本轮改动。`tests/routing` 全部通过。
- 两层板没有铺铜，GND 这类 47 焊盘网络在预算内只能完成一部分；正确解法是
  B.Cu 地平面，而不是继续加时间或改窄焊盘。
- 原理图仍是单页 A3，没有真实分页。

