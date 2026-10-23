# Engine v2 — C7 交付报告（填铜、物理连通与独立 DRC）

日期：2026-10-21
范围：C7（`native/src/zones/`、`connectivity/`、`verify/`、`src/mota/verification/service.py`、`native/tests/connectivity_tests.cpp`、`drc_tests.cpp`）。
原则：连通只认实际铜形状，不认 router 的完成标志；规则没查过的对象是 `NOT_CHECKED`，空规则集永远不是 PASS。

---

## 1. 新增/修改文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/zones.hpp`、`src/zones/zones.cpp` | 直线轮廓区域填充：优先级、clearance、thermal spoke、island 移除、keepout、孔、板框裁剪 |
| `native/include/mota/connectivity.hpp`、`src/connectivity/connectivity.cpp` | 实际铜节点并查集、U、缺端点、短路、NPTH、net-tie、via span、plane island、thermal 断开 |
| `native/include/mota/verify.hpp`、`src/verify/verify.cpp` | 独立 DRC：short、clearance、track width、via drill/ring、board edge、hole clearance；逐规则覆盖 |
| `native/include/mota/board.hpp`、`src/board/board.cpp` | PadStack/Track/Keepout 的 net 与层字段；via net |
| `native/src/worker/worker.cpp` | 解析 zones/tracks/holes/keepouts/net_ties/rules；`verify` 查询返回 fill + connectivity + drc |
| `src/mota/verification/service.py`、`__init__.py` | 冻结 BoardIR＋规则 → 原生 verify；映射到 `ValidationReport`；`verify_design()`；可选 KiCad 探测 |
| `native/tests/connectivity_tests.cpp` | 43 项连通/填铜断言 |
| `native/tests/drc_tests.cpp` | 25 项 DRC 断言 |
| `tests/verification/test_service.py` | 7 项端到端服务测试（含真实 BoardIR 投影） |
| `docs/engine-v2/c7-fill-report.md`、`c7-fill.json` | 干净板 / 断线 / 短接 / 空规则集的实测产物 |

---

## 2. 填铜

- 输入：板框、zone 轮廓、clearance、thermal gap/spoke 宽度/数量、`remove_islands`、优先级。
- 轮廓只接受**直线轮廓**（每条边水平或垂直）。任意斜边/曲线轮廓返回 `ZONE_NON_RECTILINEAR`，不静默近似；服务把它映射为 `NOT_CHECKED` 并阻断审阅。
- 填充结果是实际轴对齐铜矩形集合，不是显示图案：
  - 按所有 x/y 顶点带做 scanline，交板框、减 keepout、孔、异网铜的 clearance 外包；
  - 同网 pad 生成 thermal exclusion + 4 条 spoke；`thermal_spokes=0` 时为实心连接；
  - 高优先级 zone 的已提交矩形从低优先级填充中扣除；
  - 连通分量无同网 pad/track/via 锚点时作为孤岛移除，记录数量与面积；
  - 输出 `FillComponent` 列表和 `fill_hash`。
- 圆弧孔/圆形障碍按 AABB 扣除，属于保守近似（多扣铜，不会造成假短路）；报告明确此边界。

实测：20×20 mm 板、GND 18×18 mm 区域，1 个锚定分量、24 个铜矩形、4 条 spoke、0 孤岛，`fill_hash` 记录在 `c7-fill.json`。

---

## 3. 物理连通与 U

节点：pad、track、via、fill 分量。只在同一层、真实形状接触（≤1 nm）时合并；不同层只能通过镀通孔。

- `U = Σ max(0, groups(net) - required_groups(net))`，默认 `required_groups=1`；`required_groups` 可由 `net_requirements` 覆盖（例如合法的多岛接触要求）。
- 缺失必需端点单独计入 `MISSING_ENDPOINT`，不从 U 的分母里删除。
- 同网标签但铜断开 → `NET_OPEN`，列出未连接的端点。
- 异网真实接触 → `COPPER_SHORT`；net-tie 必须显式声明 `{id,a,b,allowed}`，允许时才合并，否则仍是短接。
- NPTH 不产生层间连接；把 NPTH 当 net via 会报 `VIA_SPAN_INVALID`。
- 同网对象在 via 跨度之外接触 via pad → `VIA_SPAN_OPEN`。
- 同网 pad 位于 zone 内但没有任何 thermal/实心铜连接 → `THERMAL_DISCONNECT`。
- zone 孤岛被移除 → `PLANE_ISLAND` 警告，附数量与面积。

实测：干净板 U=0；断线注入 U=1 且 `NET_OPEN`；异网注入 4 个对象对短接，审阅阻断。

---

## 4. 独立 DRC 与覆盖

规则类型：`short`、`clearance`、`track_width`、`via_drill`、`via_annular_ring`、`board_edge_clearance`、`hole_clearance`。

- 每层用 R-tree 粗筛，再用真实 shape 距离；间距用 `distance - error` 与要求比较。
- 每条规则记录 `checked_objects`、`coverage`、`violations`、`status`。
- 没有任何适用对象 → `NOT_CHECKED`，不是 PASS（例如没有 via 时的 via drill 规则）。
- 没有任何规则 → 整份报告 `NOT_CHECKED`，并产生 `NO_RULES`；服务额外生成 `DRC_RULES` NOT_CHECKED 并阻断审阅。
- 可选规则失败也会使报告 `FAIL` 并阻断审阅；可选规则“没有适用对象”的 `NOT_CHECKED` 不阻断。
- 板边间距按真实板框边计算；铜在板外返回 0。孔间距用 NPTH shape 距离。
- 测试证明：断开铜即使 router 声称完成，也会在连通检查中失败；短路、宽度、孔径、环宽、板边、孔间距均能捕获。

---

## 5. 服务与审阅门

`verify_board(project_id, version, board, rules)`：

1. 通过 `GeometryJobInput` 冻结 board + rules，交给原生 worker 的 `verify` 查询；
2. 返回 `fill`（含 `fill_hash`）、`connectivity`、`drc`；
3. 映射为既有 `ValidationReport`/`CheckResult`，每个 check 绑定 `design_version_key`；
4. 增加 `COPPER_CONNECTIVITY`、`ZONE_FILL`、`EXTERNAL_KICAD` 检查；
5. `READY_FOR_REVIEW` 仅当所有 required check 为 PASS；否则 `REVIEW_BLOCKED`。

`verify_design(project_id, version, CircuitIR, BoardIR, rules)` 提供真实 IR 入口：

- `board_spec_from_ir()` 把 v1 `BoardPad`（rect/circle/oval）、`Track`、`Via`、`Hole`、板框、net class 投影为原生 board；
- 明确标注：未在 v1 中表达的 rounded pad 细节不会被臆造；THT/SMD 的铜层来自 `pad.layers`。

`profile_id = native-verify-v1:<rule_hash>`，避免规则换版本后沿用旧报告。

### 可选 KiCad 交叉验证

`external_kicad_cross_check()` 只探测 `kicad-cli`：

- 未安装 → `NOT_CHECKED`，原因写入 `EXTERNAL_KICAD` 检查；
- 已安装也不会伪造 DRC 结果：KiCad DRC 需要导出的 `.kicad_pcb`，确定性导出在后续阶段；报告明确“未运行对比”。

本机未安装 `kicad-cli`，因此没有任何“外部一致”声明。

---

## 6. 实际执行的命令

```bash
.venv/bin/cmake --build build -j 4
.venv/bin/ctest --test-dir build -R "connectivity|drc" --output-on-failure
./build/mota-connectivity-tests
./build/mota-drc-tests
.venv/bin/python -m pytest tests/verification -q
.venv/bin/python -m pytest -q --durations=10
```

结果：

```
ctest connectivity:        Passed
ctest drc:                 Passed
connectivity checks:       43, 0 failures
drc checks:                25, 0 failures
pytest tests/verification: 7 passed
全量 pytest:               142 passed, EXIT=0
```

产物：[c7-fill-report.md](file:///Users/danny/Documents/Mota/docs/engine-v2/c7-fill-report.md)、[c7-fill.json](file:///Users/danny/Documents/Mota/docs/engine-v2/c7-fill.json)。

---

## 7. 与 C7 清单的对应

| 清单项 | 证据 |
| --- | --- |
| net 标签相同但铜断开 | `test_broken_track_is_an_open`；服务断线注入 U=1 |
| 异网相接 | `test_different_net_touch_is_a_short`；DRC short/clearance 测试 |
| plane island | `test_zone_island_is_removed`、`PLANE_ISLAND` |
| thermal 断开 | `test_thermal_and_plane_islands`、`THERMAL_DISCONNECT` |
| via 跨度错误 | `test_via_span_open`、`VIA_SPAN_INVALID`、`VIA_SPAN_OPEN` |
| NPTH | `test_npth_never_connects_and_via_does` |
| net-tie | `test_net_tie_is_explicit` 正负例 |
| 实际铜连通、缺端点、U | `analyze_connectivity()`；43 项断言 |
| 必须规则覆盖、空规则集不得通过 | `run_drc()` 的 `checked_objects/coverage`；`NO_RULES`；服务 `DRC_RULES` |
| `ctest -R "connectivity|drc"` | 第 6 节 2/2 passed |
| 注入断线/短路均被捕获 | 第 6 节与 `c7-fill-report.md` 第 2/3 节 |
| 可选 KiCad 交叉比较 | `external_kicad_cross_check()`，本机 NOT_CHECKED，未伪造结果 |

---

## 8. 已知边界

- 填铜只支持直线轮廓；斜边/圆弧 zone 返回 `ZONE_NON_RECTILINEAR`，在服务中阻断审阅。任意多边形布尔/偏移（Clipper2 一类）留到后续阶段。
- 圆弧孔、圆形 pad、圆形 keepout 的填铜扣除使用 AABB，偏保守；连通/DRC 本身使用真实形状距离。
- 填铜不处理铜厚、阻抗、电流密度或 IR drop；“网表/铜接通”不等于电源完整性通过。
- 连通节点数量与 U 只按有 pad 的网络统计；纯走线/纯平面网络没有“必需端点”时不产生 U，但仍会报告短路、孤岛与 thermal 问题。
- net-tie 是显式节点对；没有自动识别封装内部的 net-tie 组。
- DRC 规则由调用方提供；C5 `CompiledConstraints` 到原生规则列表的自动映射尚未接通（规则选择器/层/网类已支持，但未做自动投影）。
- 可选 KiCad DRC 未安装/未运行，报告保持 `NOT_CHECKED`；不能据此声称与 KiCad 一致。
