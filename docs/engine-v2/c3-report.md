# Engine v2 — C3 交付报告（层次网表与多页原理图）

日期：2026-10-21
范围：C3（`circuit/hierarchy.py`、`circuit/compiler.py`、`schematic/layout.py`、两个测试文件）。
原则：16 个重复通道的局部网络不得串连；bus 是网络集合，不得把成员短接；锁定页面不得因新增页面而移动。

---

## 1. 实际修改 / 新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/circuit/hierarchy.py` | ModuleDef / ModuleInstance / Port / Net / Bus 定义；实例路径递归展开；作用域网络；bus alias/成员展开；循环与缺端口诊断 |
| `src/mota/circuit/compiler.py` | `compile_circuit(project)` 汇总编译与布局；`compile_report` 生成可审阅页面索引与失败定位报告 |
| `src/mota/schematic/layout.py` | 20 页网格布局、每页符号/正交引脚 stub/网络标签/端口、锁定页保持、`roundtrip_nets` 反解析 |
| `src/mota/schematic/__init__.py` | 公共入口 |
| `src/mota/ir/models_v2.py` | `SchematicPageV2`、`MultiPageSchematicV2`、`SymbolUnitViewV2`、`HierarchyDiagnostic`、`CircuitCompileResult` |
| `src/mota/circuit/parts.py` | `PartView.pin_units`，用于跨页多 unit 视图筛选 |
| `tests/circuit/test_hierarchy.py` | 3 项层次/总线/失败定位测试 |
| `tests/schematic/test_roundtrip.py` | 2 项导出反解析与锁定页测试 |

---

## 2. 实际执行的命令

```bash
.venv/bin/python -m pytest tests/circuit tests/schematic -q
.venv/bin/python -m pytest -q --durations=10
```

---

## 3. 固定样本（16 通道 / 5 级 / 20 页）

```
TOP
├─ GROUP1..GROUP16 (same page as TOP)
│   └─ CH (new page) ── BLOCK (same page) ── LEAF (same page)
└─ MON1..MON3 (new pages)
```

- 16 个通道页；3 个 monitor 页；1 个 TOP 页，共 20 页。
- 每个通道包含 `R_C/R_G/U_DUAL/R_B/R_L`；`R_G:2` 连接全局 `GND`。
- 总线：`din[0..15]`、`dout[0..15]`，alias `DIN`/`DOUT`；每个通道的 `sig/out` 经端口链只合并到自己的 bus member。
- 跨页多 unit：`U_DUAL` unit 1 在通道页，unit 2 在 TOP 页，共享同一个 `component_id`。
- 负例：未绑定端口 → `PORT_UNBOUND`；A↔B 定义循环 → `HIERARCHY_CYCLE`，编译终止该分支而不递归爆栈。

---

## 4. 测试结果

```
$ .venv/bin/python -m pytest tests/circuit tests/schematic -q
15 passed
```

C3 关键断言：

1. `page_records == 20`，实例路径最大深度 5。
2. 16 个 `TOP/din[k]` 网络各自只包含本通道的 `R_C/R_B/R_L` 与顶部 `RBk`，互不相交；`GND` 汇总 16 个 `R_G:2`。
3. bus alias 成员不会被短接：4 成员样本中 4 个 pin 集合互不相交。
4. `U_DUAL` 在两个不同 page 上有两个 `SymbolUnitViewV2`，`component_id` 相同。
5. `roundtrip_nets` 与 CircuitIR 网络逐项相等；`MultiPageSchematicV2` 经 JSON 导出再反序列化后仍相等。
6. 锁定通道页后新增第 3 个通道，锁定页组件坐标完全不变，新页正常生成。
7. `compile_report` 输出 20 页索引与诊断定位（无诊断时为 `none`）。

实际编译指标（16 通道样本，3 次）：

| 指标 | 值 |
| --- | --- |
| 编译+布局耗时 | 9 / 10 / 13 ms，中位 10 ms |
| 组件 | 99 |
| 网络 | 34 |
| 页面 | 20 |
| 页面符号 / wire / label | 100 / 147 / 147 |
| 诊断 | 0 |

---

## 5. 失败项与已知限制

1. 未实现 KiCad `.kicad_sch` 导入；C3 输入是 Mota 自己的 `HierarchyProject` 定义，不是从真实工程反解析。
2. 布局是确定性格子，不做交叉最小化、功率流/信号流分区或人工页内微调；页面可读性有限。
3. Bus 只支持显式成员列表和简单 alias；未实现向量表达式、group bus、bus entry 几何绑定。
4. 多 unit 仅实现显示视图与共享物理 `component_id`；未把不同 unit 的 physical terminals 合并进一个已展开网表，也未处理隐藏电源脚。
5. 端口绑定只支持 `PORT:`、`NET:`、`BUS:` 表达式；未实现接口方向冲突、成员宽度不匹配、全局电源符号版本差异。
6. Net-tie、内部恒定连接、0 Ω/跳线/开关的差异尚未建模；不同逻辑网不会被合并，但也没有 net-tie 区域约束。
7. 编译结果尚未写入 `ProjectStore` 的 revision，也没有接入桌面 UI 页面导航；`page_records`/`SchematicPageV2` 是独立产物。
8. 锁定页只接受调用方传入坐标；没有“只重排受影响区域”的增量布局算法。
9. 普通未连接引脚与显式 NC 在 C3 布局中分别处理（前者无 wire，后者生成 `__nc__` stub），但未改变 C2 的 review 策略。

---

## 6. 回滚方式

- C3 全部为新增文件：`circuit/hierarchy.py`、`circuit/compiler.py`、`schematic/`；删除后不影响 v1 模板构建。
- `ir/models_v2.py` 只追加类型；`PartView.pin_units` 有默认值，删除字段前需同步 `schematic/layout.py`。
- 未修改 `ProjectStore` 事务、`current.json` 提交点或现有三模板路径。
- 编译结果是独立对象，不落盘；删除派生文件即可回滚。
