# Engine v2 — C9 交付报告（通用多层普通布线）

日期：2026-10-21
范围：C9（`native/src/routing/global/`、`detailed/`、`native/tests/routing_tests.cpp`、worker `route` 查询、`benchmarks/routing_single_ended/`、`mota benchmark`）。
原则：路由只认真实铜几何和独立连通检查；预算耗尽返回 PARTIAL/UNKNOWN；未布通网络始终留在冻结分母。

---

## 1. 新增文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/routing.hpp`、`src/routing/detailed/router.cpp` | 3D 稀疏网格、粗走廊、A*、合法 through via、多端树、rip-up 多轮、连通/U、semantic hash |
| `native/src/routing/global/corridor.hpp`、`corridor.cpp` | 粗 2D A* 走廊规划（软约束） |
| `native/src/worker/worker.cpp` | 解析 top-level `routing`；`route` 查询返回 tracks/vias/unrouted/net_reports/metrics |
| `native/tests/routing_tests.cpp` | 38 项布线断言 |
| `benchmarks/routing_single_ended/` | 冻结套件 `single-ended-v2`：6 个案例 + `manifest.json`（逐案 SHA-256） |
| `benchmarks/run.py` | 冻结校验、逐案运行、聚合报告；未布通网络不从分母删除 |
| `src/mota/cli/main.py` | `mota benchmark run --suite ... --report ...` 命令 |
| `tests/routing/test_native_routing.py` | 3 项 worker/benchmark 端到端测试 |
| `reports/single-ended-v2.json`、`.md` | 实际运行产物 |

---

## 2. 布线模型

### 2.1 网格与障碍

- 板框内按 `grid_nm`（默认 0.5 mm）建立 `columns × rows × copper_layers` 的 3D 网格；超过 `max_grid_cells` 返回 `UNKNOWN / GRID_TOO_LARGE`，不猜测。
- 每个单元记录 owner：`-1` 空闲、`-2` 硬障碍、`>=0` 网络索引。不同网络铜接触时标为硬障碍。
- 障碍来自：板边（edge + half width）、keepout、NPTH/孔、焊盘、既有走线、既有 via；均按 `clearance + track_width/2` 外扩。
- 同网焊盘/既有铜可被该网络复用；其他网络不可进入。锁定既有铜从不移除。
- 圆形/曲线障碍按真实 shape 距离逐格判断；网格本身是离散模型，报告不把它说成连续几何证明。

### 2.2 粗全局规划 + 详细 A*

- 粗走廊：把网格按 4×4 聚合，用 2D A* 规划端子间通道；详细 A* 对走廊外单元加 `corridor_penalty`，只作软引导。
- 详细 A*：同层 4 邻域 + through via 跳层；via 合法性要求 via pad 半径 + clearance 覆盖的所有层、3×3 邻域均空闲；via 成本 `via_cost_factor × grid`。
- 稀疏搜索：占用网格是全板**粗粒度**（默认 0.5 mm，且有 `max_grid_cells` 上限），不是超细 3D 网格；A* 的 g/parent 用 `unordered_map` 只保存实际访问状态。预算 `max_expansions`、`max_millis` 在 A* 内检查。
- 多端网络：按最近端顺序连接；已有树作为多源起点，目标是新端子；路径压缩为直线段，层变化处产生 through via。

### 2.3 Rip-up / reroute 与锁定对象

- 每轮从基础网格重建（等价于 rip-up 所有非锁定新路由），失败网络在下一轮优先。
- 最多 `max_rounds` 轮；每轮独立重新提取物理连通并计算 U，保留 `(U, 未布通数, 线长, via 数)` 最优候选。
- 既有锁定走线、锁定网络和既有 via 始终保留；锁定网络不重新布线，只由连通检查确认。
- 预算/超时：停止后续搜索，返回当前最佳候选；有已布通网络 → `PARTIAL`，否则 `UNKNOWN`。
- `INFEASIBLE` 只在有限网格内每个失败网络都被证明 `NO_PATH_PROVEN`/`TERMINAL_BLOCKED` 且没有预算中止时使用。

### 2.4 连通与冻结分母

- 每轮把焊盘、既有铜、新走线、via 构造成 `CopperNode`，调用 C7 的 `analyze_connectivity`；U 与短路是独立结果，不信任 A* 的“已找到路径”。
- `requested_nets` 来自冻结请求；`completed_nets` 是其中 U=0 的子集；`unrouted_nets = requested - completed`。
- 基准聚合始终用 `requested_targets` 作分母；失败/未布通案例不删除。

---

## 3. 实测

```bash
.venv/bin/cmake --build build -j 4
.venv/bin/ctest --test-dir build -R routing --output-on-failure
./build/mota-routing-tests
.venv/bin/python -m pytest tests/routing -q
.venv/bin/mota benchmark run --suite single-ended-v2 --report ./reports/
.venv/bin/python -m pytest -q --durations=10
```

结果：

```
ctest routing:            Passed
mota-routing-tests:       38 checks, 0 failures
pytest tests/routing:     3 passed
benchmark:                6 cases, routed 8/8, unrouted 0, U=0, frozen=ok
全量 pytest:              149 passed, EXIT=0
```

### 3.1 冻结套件逐案结果

| case | layers | requested | routed | U | wirelength | vias | runtime |
| --- | --- | --- | --- | --- | --- | --- | --- |
| two_layer_three_terminal | 2 | 1 | 1 | 0 | 45.0 mm | 0 | 0.7 ms |
| two_layer_obstacle | 2 | 1 | 1 | 0 | 33.0 mm | 0 | 0.3 ms |
| two_layer_two_nets | 2 | 2 | 2 | 0 | 50.0 mm | 2 | 0.4 ms |
| four_layer_smd | 4 | 1 | 1 | 0 | 30.0 mm | 2 | 0.6 ms |
| six_layer_smd | 6 | 1 | 1 | 0 | 30.0 mm | 2 | 0.8 ms |
| locked_track | 2 | 2 | 2 | 0 | 40.0 mm | 0 | 0.1 ms |

- 6/6 案例 SHA-256 与 manifest 一致。
- 分母 8 个网络、完成 8 个、未布通 0；锁定走线案例中既有 `LOCK1` 保留。
- 4/6 层案例使用 SMD 端子 + keepout 强制换层，均产生 2 个合法 through via。
- `reports/single-ended-v2.json` 含逐案 semantic hash、诊断与聚合；`.md` 为可读表。

### 3.2 负例

- 两块被全高墙隔开的板：A* 穷尽 → `INFEASIBLE`，U>0，`unrouted_nets` 保留。
- `max_expansions=0`：`UNKNOWN`，不是 `INFEASIBLE`。
- 交叉双网络：两个网络都布通，`short_count=0`，至少 1 个 via。
- 预算/超时不会被写成“设计无解”。

---

## 4. 与 C9 清单的对应

| 清单项 | 证据 |
| --- | --- |
| 未依赖模板的两层多端网络 | `test_two_layer_three_terminal`、benchmark 前三案 |
| 同一模型扩到 4/6 层 | `four_layer_smd`、`six_layer_smd`，`grid_layers=4/6` |
| 粗全局规划、稀疏局部搜索 | `global/corridor.cpp` + `detailed/router.cpp` 的走廊惩罚与稀疏 A* |
| 合法 via | through via 全层 3×3 + pad/clearance 检查；4/6 层案例产生 2 个 via |
| rip-up / reroute | 每轮重建、失败优先、保留最佳候选；`max_rounds` |
| 锁定对象保护 | `locked_track` 案例；既有铜作为障碍/源，从不删除 |
| 每轮重提取连通、保留最佳 | `evaluate()` 调用 `analyze_connectivity`；候选比较 U/未布通/线长/via |
| 预算用尽 → PARTIAL/UNKNOWN | `test_budget_is_unknown`、状态分支 |
| `ctest -R routing` | 第 3 节 Passed |
| `mota benchmark run --suite single-ended-v2 --report ./reports/` | 第 3 节实际命令与输出 |
| 冻结集逐案结果、未连通不删分母 | manifest SHA-256 + 报告表 + `aggregate.requested_targets=8` |

---

## 5. 已知边界

- 布线基于离散网格（默认 0.5 mm），不是连续几何精确布线；`INFEASIBLE` 是有限网格内的证明。占用网格本身是全板粗粒度，A* 状态表才是稀疏的。
- 只生成 through via；blind/buried/micro via 与 BGA 扇出属于 C10。
- 全作业使用统一 track width / clearance；按网络类变宽尚未接入。
- 走廊是软引导；尚未实现协商拥塞（negotiated congestion）和局部拆线重布。
- 既有非锁定铜按固定障碍/可复用源处理，不会被自动移除或重路由。
- 端子取焊盘中心附近的可用网格；异形焊盘可能偏保守。
- 基准套件是开发者资产，随仓库分发，不承诺打进 wheel；`benchmarks/` 通过 CLI 动态加载。
