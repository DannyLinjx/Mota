# Engine v2 — C12 交付报告（大工程导入、往返与生产文件）

日期：2026-10-22
范围：C12（`src/mota/io/kicad_project.py`、`kicad_writer.py`、`manufacturing.py`；`tests/io/test_project_roundtrip.py`、`test_manufacturing.py`）。
原则：未知电气内容必须被记录，不能静默丢弃；源工程问题与 Mota 导出问题分开统计；未生成的 Gerber 与未执行的外部校验保持 `NOT_CHECKED`。

---

## 1. 新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/io/kicad_project.py` | `.kicad_pro` / `.kicad_pcb` / 全部 `.kicad_sch` 导入；层、网络、封装、焊盘、线段、via、zone、图形、netclass、设计规则；未知项清单；source/mota 分离的往返比较；`import_project` / `export_project` |
| `src/mota/io/kicad_writer.py` | KiCad 6 S-expression 导出（`generator mota`，不冒充 pcbnew/eeschema） |
| `src/mota/io/manufacturing.py` | 层→Gerber 文件名映射、Excellon PTH/NPTH（含 G85 槽）、BOM/位置 CSV、独立回读与一致性检查 |
| `src/mota/io/__init__.py` | 对外接口 |
| `tests/io/kicad_fixture.py` | 合成 KiCad 工程（旋转封装、槽孔、custom pad、zone fill、层次图页、规则） |
| `tests/io/test_project_roundtrip.py` | 10 个用例，含固定 CM4 IO 包 |
| `tests/io/test_manufacturing.py` | 8 个用例，含厂商钻孔交叉验证 |
| `docs/engine-v2/c12-cm4io.json` | CM4 IO 固定包的实测证据（zip hash、文件 hash、计数、往返、生产、交叉验证） |

---

## 2. 固定的公开工程

| 项 | 值 |
| --- | --- |
| 设计 | Raspberry Pi Compute Module 4 IO Board（公开设计包） |
| 页面 | `https://pip.raspberrypi.com/categories/1210-design-files` |
| 文件 | `CM4IO-KiCAD.zip`（RP-008173-DS-1） |
| 大小 | 6,819,369 bytes |
| SHA-256 | `5ea867e17968cb9c117fbce7a982ea395d66a6ee605253a9a0879483bbfcb0aa` |
| 格式 | KiCad 6：`.kicad_pcb (version 20211014) (generator pcbnew)`；`.kicad_sch (version 20211123) (generator eeschema)` |
| 许可 | 包内 `README.TXT` **没有**许可证声明；使用受 Raspberry Pi 产品信息门户条款约束；包内 3D 模型版权归各原厂（README 列出的外部 STEP 需另行下载） |
| 资源依赖 | `CM4IO.pretty/`、`fp-lib-table`、`CM4IO.3dshapes/`（部分 STEP 缺失，README 给出下载来源）、`RPI-CM4IO-Gerber/`（厂商 2021 年输出） |

导入规模（实测，见 `c12-cm4io.json`）：

```
22 layers（4 copper） 248 nets 151 footprints 944 pads
5,655 segments 781 vias 27 zones 25 graphics
7 schematic files 225 symbols 1,002 wires 417 labels
```

固定包不提交进仓库；测试在本地缓存缺失时跳过，存在时先校验 zip SHA-256 再导入。

---

## 3. 导入

### 3.1 坐标约定

统一使用 KiCad 原生坐标：`x_nm = round(x_mm × 1e6)`，y 轴向下。不虚构板原点，因此任意工程导出后数值可逐位复现。`ImportedProject.coordinate_system = "kicad_mm_y_down"` 显式记录该约定。

### 3.2 未知内容的处理

`ImportedProject.unknown` 逐项记录 `(area, path, detail, severity, count)`，严重度分为 `electrical / geometry / metadata`。CM4 IO 的 21 组记录包括：

- `electrical`：234 个 zone fill 铜多边形未导入（只保留 zone 外框与 net）、1 个 custom pad 的铜 primitive 未导入；
- `geometry`：28 个 `dimension`、2 个 `target`、15 个槽孔（按宽/高记录，导出为 G85 槽）；
- `metadata`：未导出的工程段（`pcbnew`/`cvpcb`/`erc`/`schematic`/`boards`/`text_variables`）、netclass 中未编译的字段（`microvia_*`、`diff_pair_via_gap`、颜色、`bus_width` 等）、28 个无编号焊盘。

没有一条被静默丢弃；同一类问题会聚合计数，便于按类修复。

### 3.3 层次原理图

导入全部 `.kicad_sch`：symbol 实例（lib_id、Reference、Value、Footprint、位置、旋转、unit、mirror、pin）、wire、label/global_label/hierarchical_label、junction、no_connect、sheet 子页。声明了但缺失的子页文件、总线（bus）连接性都会进入未知项。原理图**不推导 netlist**——PCB 已带 net/pad 映射，该能力边界在报告中明确，而不是假装已检查。

---

## 4. 导出与往返比较

`export_project()` 输出 `.kicad_pcb` / 全部 `.kicad_sch` / `.kicad_pro`，`generator` 固定为 `mota`。

`compare_projects()` 比较：

| 类别 | 内容 |
| --- | --- |
| semantic | 层名/类型/序号、网络号与名、封装 reference 集合与身份属性、schematic symbol/wire/label/sheet 结构 |
| geometry | 封装位置与旋转、焊盘（编号/net/类型/形状/尺寸/钻孔/层集合）、线段（net/layer/width/端点）、via、zone（net/layer/外框/优先级/keepout）、图形（含 arc 的原始 start/mid/end） |
| rule | netclass 的 6 个已编译字段、16 项设计规则、板厚 |

差异带 `origin`：`source`（源工程自身问题，如重复 reference）与 `mota`（导出引入）。状态：

- `FAIL`：存在任何 `mota` 差异；
- `PASS_WITH_SOURCE_ISSUES`：只有源问题；
- `PASS`：无差异且无源问题。

CM4 IO 实测：**PASS_WITH_SOURCE_ISSUES，mota 差异 0**，覆盖层/网络/封装/焊盘/线段/via/zone/图形/netclass/规则/原理图全部 11 类。

已知的导出取舍（写入 `RoundtripReport.warnings`）：

- 不合成 `lib_symbols` 定义：结构可再导入，但用 KiCad 打开需要原始库；
- 不重发 zone fill 铜；
- 不重发 custom pad 的 primitive；
- 未导出的工程段（见上）。

---

## 5. 生产输出

- **层映射**：`LAYER_FILE_MAP` 覆盖 F/B/In1–In6.Cu、SilkS、Mask、Paste、Fab、Adhesive、Edge.Cuts、Margin；任何带铜或生产图形却没有映射的层 → `LAYER_MAP FAIL`（测试用 `Mid.Cu` 验证）。
- **钻孔**：PTH / NPTH 两个 Excellon 文件，圆孔按工具直径分组；椭圆钻孔按 `(drill oval W H)` 记为槽，写为标准 `G85` 线段；坐标 4 位小数（0.1 µm）。
- **独立回读**：`read_drill()` 是与写入器分离的解析器，回读孔数/槽数并与清单比对（`DRILL_ROUNDTRIP`）。
- **BOM / 位置**：CSV 输出 reference、value、footprint、side、in-BOM 与坐标/旋转/镜像；检查引用集合一致、引用唯一、BOM 行有 value 与 footprint。
- **板框**：Edge.Cuts 必须能串成闭合链，否则 `BOARD_OUTLINE FAIL`。
- **未做即未通过**：`GERBER_ARTWORK`、`EXTERNAL_VALIDATION`、底部旋转的装配厂约定、槽方向外部验证 均为 `NOT_CHECKED`。

### 5.1 CM4 IO 生产清单（实测）

```
LAYER_MAP PASS（9 个生产层）
BOARD_OUTLINE PASS（69 点闭合）
DRILL_CONSISTENCY PASS（968 圆孔 + 15 槽；966 plated / 17 non-plated；15 把刀）
SLOT_GEOMETRY PASS（端点长度与钻孔长度一致）
BOM_POSITION_REFERENCES / BOM_UNIQUE_REFERENCES / BOM_COMPLETENESS PASS（151 行）
SLOT_VALIDATION / POSITION_CONVENTION / GERBER_ARTWORK / EXTERNAL_VALIDATION NOT_CHECKED
整体 status = NOT_CHECKED
```

### 5.2 厂商钻孔交叉验证

包内 `RPI-CM4IO-Gerber/CM4IOv5.drl`（KiCad 5.99，2021-04-16）是独立来源：

- 187 个钻孔焊盘**全部**与厂商文件坐标一致（换算 y 轴后）；
- 9 个不一致孔全部是 via（当前板文件相对 2021 年 Gerber 有改动）；
- 厂商钻孔不含槽记录，因此 15 个槽无法外部比对 → `SLOT_VALIDATION NOT_CHECKED`。

焊盘绝对坐标使用 `-rotation` 的旋转矩阵（KiCad y-down 约定），底部封装先镜像 x；该约定正是由上述 187/187 的匹配验证的。

---

## 6. 测试

```
.venv/bin/python -m pytest tests/io -q
18 passed
```

覆盖：导入语义与计数、未知项分类、往返语义/几何/规则、导出确定性（两次导出字节一致）、重复 reference 归为源问题、缺文件/格式错误、缺失子页、CM4 固定包往返；层映射失败、焊盘旋转后的绝对坐标、钻孔与槽回读、BOM/位置一致性、缺失板框、CM4 生产清单与厂商交叉验证。

---

## 7. 已知边界

- 不生成 Gerber 铜层/阻焊/丝印；相关检查恒为 `NOT_CHECKED`，不会因为钻孔/BOM 通过而变成 PASS。
- 没有 `kicad-cli`/GerbView 等外部工具参与，导出文件未在真实 KiCad 中打开验证；报告明确写为未执行。
- 原理图只保留结构与几何，不推导 pin 级 netlist，也不做 ERC。
- zone fill 铜、custom pad primitive 不参与往返几何比较；它们作为源问题列出。
- 槽方向所用的旋转约定与圆孔相同并已验证焊盘中心，但槽端点没有外部基准可比对。
- 设计规则只编译 16 个数值项与 7 个 netclass 的 6 个字段；其余字段以 metadata 记录，不假装已应用。
