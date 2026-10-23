# Engine v2 — C2 交付报告（PartDef / 实例分离与自由编辑）

日期：2026-10-21
范围：C2（`circuit/parts.py`、`ir/models_v2.py`、现有 PatchService、两个测试文件）。
原则：错误映射不允许进入 `READY_FOR_REVIEW`；未支持的自动能力不阻止合法手工编辑；数值均为实际运行。

---

## 1. 实际修改 / 新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/circuit/parts.py` | Symbol / Footprint / Part / ComponentInstance 解析；字符串引脚、多 unit、同号多 pad、pin-pad 映射与诊断；NC 三态 |
| `src/mota/circuit/__init__.py` | 公共入口 |
| `src/mota/ir/models_v2.py` | `ComponentInstanceV2`、`PinPadMappingV2`、`ComponentInstanceSet` |
| `src/mota/ir/__init__.py` | 公共入口 |
| `src/mota/domain/models.py` | `Component` 增加 `instance_path/on_board/in_bom/assembly_state/footprint_instance_id/parameter_bindings`；`FootprintDef/Instance` 增加 `courtyard_approximate`；`PatchOperation` 增加 5 类操作与字段 |
| `src/mota/app/design.py` | AddComponent / AssignFootprint / ConnectTerminals / ChangePart / PlaceFootprint / SetAssemblyState；映射检查注入 |
| `src/mota/generation/placement.py` | 通过 resolver 支持导入封装；跳过非投板/缺封装器件 |
| `src/mota/generation/schematic.py` | 通过 resolver 支持导入符号 |
| `src/mota/generation/kicad_writer.py` | 导出时解析导入符号/封装 |
| `src/mota/projects/export.py` | BOM/Bundle 支持导入资产与 DNP 标记 |
| `src/mota/projects/bundle.py` | 资源完整性检查改用 resolver |
| `src/mota/validation/electrical.py` | NET_COMPLETENESS 支持导入器件；模板连接允许额外编辑引脚；导入电容 ESR/容量保持 NOT_CHECKED |
| `src/mota/cli/main.py` | `mota design patch --project ... --operations ops.json` |
| `tests/circuit/test_pin_pad_mapping.py` | 6 项映射测试 |
| `tests/circuit/test_free_edit.py` | 4 项自由编辑/CLI 一致性测试 |
| `tests/fixtures/library/{stacked,nc}/` | 同号多 pad、三种 NC、非投板符号样本 |

---

## 2. 实际执行的命令

```bash
.venv/bin/python -m pytest tests/circuit -q
.venv/bin/python -m pytest -q --durations=10
```

---

## 3. 固定样本

| 样本 | 覆盖 |
| --- | --- |
| `sym:fx:Device:DualAmp` | 字符串球号 A1/B1、unit 1/2、body style 1/2 |
| `fp:stack:MyParts:StackedPad` | 两个几何共享 pad number 1；`pads_for` 保留两个 BoardPad 且同网络 |
| `sym:nc:NC:NCPart` + `fp:nc:MyParts:NCPart` | 普通未连接、显式 NC、器件定义 `no_connect` 三态 |
| `sym:nc:NC:PWR_FLAG` | Reference `#PWR`、合法非投板符号，无封装不报错 |
| `fp:fxfp:MyParts:EmptyGraphic` | 错封装（无 pad）负例 |
| 缺封装 | 可保存原理图，PCB 前 `FOOTPRINT_MISSING` 阻止 review |
| DNP | `assembly_state="dnp"`，焊盘和几何保留，BOM 标注 DNP |
| 旋转 | 90° 通过；45° 返回 `ROTATION_UNSUPPORTED` |

---

## 4. 测试结果

```
$ .venv/bin/python -m pytest tests/circuit -q
13 passed
```

其中 C2 相关 10 项：

- 符号/封装/Part 分离；导入符号的 `features` 与 footprint 不互相冒充。
- 同号多 pad 几何不被覆盖。
- 缺封装、错封装、显式 pad 不存在均有诊断。
- 非投板符号不要求封装。
- 三种 NC 可区分；器件定义 NC 被连线时产生 `NC_PIN_CONNECTED`。
- `ComponentInstanceV2` 保留 `instance_path/assembly_state`。
- 五个操作串联：Add → AssignFootprint → ConnectTerminals → PlaceFootprint → SetAssemblyState。
- 缺封装时 `PIN_PAD_MAPPING` FAIL + `REVIEW_BLOCKED`；补封装后 PASS。
- ChangePart + remap；旧引脚上的网络被正确清理。
- CLI `design patch` 与 Service 调用得到相同规范化 CircuitIR（组件、引脚映射、网络、DNP 状态逐项相等）。

实际补丁结果（power_3v3 + 6 个操作）：

| 指标 | 值 |
| --- | --- |
| 基线构建 | 1.25 s |
| design.patch（含重建、布线、检查） | 1.75 s |
| 组件 / 网络 / 焊盘 / 走线 | 6 / 3 / 13 / 9 |
| 未布网络 | 0 |
| 检查 | PASS 17 / FAIL 0 / WARN 0 / NOT_CHECKED 4 |
| `PIN_PAD_MAPPING` | PASS |

`NOT_CHECKED` 是 power_3v3 原本缺少输出电容有效容量/ESR 证据，未因编辑被改写为 PASS。

---

## 5. 失败项与已知限制

1. 任意角度封装旋转未实现：`PlaceFootprint` 只接受 90° 倍数，45° 返回 `ROTATION_UNSUPPORTED`。板级几何仍假设正交旋转。
2. 导入封装的 courtyard 由 pad 包围盒外扩 0.25 mm 生成，`courtyard_approximate=True`；不是厂家装配间距验证。
3. 导入封装的非 90° pad 用轴对齐包围盒近似，pad 自身旋转不进入 DRC 几何。
4. 没有自动 pin-pad 合成：符号引脚号与 pad 号不一致时必须显式 `pin_pad_mapping`；未匹配会阻塞 review，但合法手工编辑不被阻止。
5. 导入器件的电气检查大多保持 `NOT_CHECKED`（如电容 derating、ESR），没有编造通过。
6. 桌面 UI 尚未新增 AddComponent/AssignFootprint 等按钮；CLI 与 Service 使用同一 `design.patch` 路径并已做等价性测试，但“GUI 操作”目前是后端调用层，不是可点击控件。
7. 普通未连接引脚会使 `NET_COMPLETENESS` FAIL；需要显式 `set_no_connect` 或连线。三种 NC 已可区分，但 UI 尚未提供三态选择器。
8. DNP 只影响 BOM/装配语义，不移除焊盘或几何（符合规则），也尚未实现按装配变体重算内部连接。
9. `design.patch` 立即提交 revision；没有“预览 patch 后再提交”的独立事务层。

---

## 6. 回滚方式

- 新增文件独立：`src/mota/circuit/parts.py`、`src/mota/ir/`；删除后 v1 模板路径仍可运行。
- `Component` 新字段均有默认值，旧 revision 继续可读；不需要数据迁移。
- 新增 `PatchOperation` 类型是追加；旧 CLI/Agent 操作不变。
- 若回退 `validation/electrical.py` 的导入器件解析，导入器件会重新表现为 `PART_UNKNOWN`，但不会误报 PASS。
- 不修改 `current.json` 的提交规则；C2 补丁仍走 `design.patch` 的既有事务。
