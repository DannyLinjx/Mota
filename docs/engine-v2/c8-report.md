# Engine v2 — C8 交付报告（约束驱动布局与增量修复）

日期：2026-10-21
范围：C8（`native/src/placement/`、`native/tests/placement_tests.cpp`、worker `place` 查询、`src/mota/placement/service.py`）。
原则：硬约束不进入代价函数；预算耗尽只能是 PARTIAL/UNKNOWN；INFEASIBLE 只在有限候选空间穷尽或固定对象已矛盾时报告。

---

## 1. 新增文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/placement.hpp`、`src/placement/placement.cpp` | 候选网格、硬约束检查、模块分区、关键件优先、有限搜索、局部优化、HPWL/拥塞/可逃逸性代价、semantic hash |
| `native/src/worker/worker.cpp` | 解析 top-level `placement`；`place` 查询返回 patch + congestion + metrics |
| `native/tests/placement_tests.cpp` | 51 项布局断言 |
| `src/mota/placement/service.py`、`__init__.py` | `CircuitIR/BoardIR → 原生 placement`；C5 `CompiledConstraints` 规则映射；补丁应用 |
| `tests/placement/test_placement_service.py` | 4 项端到端服务测试 |
| `docs/engine-v2/c8-placement-report.md`、`c8-placement.json` | 合法布局、seed 重放、预算与硬约束实测 |

---

## 2. 算法

### 2.1 分区与顺序

- **显式模块区域**（`module_regions`）是硬约束：模块内器件的 courtyard 必须落在区域内。
- 未给显式区域时，按模块成员数把板内可用宽度划成**软带**；模块内聚与软带中心距离进入代价，不阻断可行解。
- 顺序：固定件（只校验，不移动）→ 非去耦关键件（`critical` 优先、引脚多者优先、id 决定平局）→ 去耦电容。去耦电容在目标之后再放。

### 2.2 合法化与硬约束

候选位置：板内缩进 `edge_clearance_nm` 的网格（默认 1.27 mm），旋转 0/90（正方形只 0）；显式模块区域进一步限制范围。每个候选检查：

- courtyard 四角在板框内，且满足板边间距；
- 不与 keepout 相交；
- 不与已放置 courtyard 重叠（含 `courtyard_gap_nm`）；
- 模块区域（硬）内；
- 去耦距离：已放置的目标/电容中心距 ≤ `decoupling_max_nm`。

固定件在搜索前逐项校验：板外、keepout、模块区域、固定件相互重叠、固定去耦超距 → 直接 `INFEASIBLE`，并给出具体对象。

### 2.3 有限搜索与状态

- 关键件优先的深度优先回溯，节点预算 `max_search_nodes`（默认 20,000）。这不是 OR-Tools CP-SAT；它是 Mota 自有的有限 CP 风格搜索，报告不声称使用 OR-Tools。
- 找到第一组合法完整布局后停止搜索，进入局部优化；因此 `search_exhausted=false` 表示“未证明最优”，不是失败。
- 没有完整布局时：
  - 预算耗尽，且已有部分放置 → `PARTIAL`（附已放置 patch 与未放置列表）；
  - 预算耗尽，尚无有效放置 → `UNKNOWN`；
  - 候选空间穷尽且无完整布局 → `INFEASIBLE`；
  - 固定件矛盾 → `INFEASIBLE`。
- 局部优化：按固定顺序对非固定件逐个尝试候选，只在硬约束仍成立且总代价下降时接受；默认 4 轮。

### 2.4 代价与反馈

```
cost =
  w1 * HPWL / board_diagonal
+ w2 * (congestion_max/4 + congestion_mean/2)
+ w3 * escape_pressure
+ w4 * module_cohesion
+ 0.5*w4 * module_band_distance
+ move_weight * normalized_distance_from_initial_position
```

- 最后一项让增量修复优先保留原位，避免无收益的大范围搬移；它不是硬约束，硬约束始终单独判定。

- HPWL 以器件中心近似（当前没有 pin-level 逃逸模型）。
- 拥塞图：把板划分为 `congestion_cell_nm` 网格，沿网络中心连线累加负载，再把每个器件的 escape pressure 加到其中心格；输出 max/mean 与最多 10 个 hot cells。
- `escape_pressure = pins * pin_pitch / courtyard_perimeter`；细间距、多引脚器件在拥挤区域的代价更高。
- 硬约束不进入 cost；因此线长收益不能换取 keepout/重叠/去耦违规。

### 2.5 固定 seed 重放

候选排序使用确定的 `(score, x, y, rotation)` 平局规则；seed 进入结果和 semantic hash。同一输入 + 同一 seed 的 patch 与 hash 完全一致；不依赖浮点平台字节一致。

---

## 3. 接口

```python
place_board(project_id, version, circuit, board,
            rules=None, keepouts=None, module_regions=None, decoupling=None,
            compiled=None, seed=0, binary=None, budget=None) -> PlacementOutcome
```

- `board.footprints` 提供 courtyard 与当前位置；`circuit.components` 提供 reference、locked、module、pin map。
- `rules_from_compiled(compiled)` 从 C5 硬约束中取 clearance 与 board-edge 最大值，作为 courtyard gap / edge clearance 的下界；C5 非 SAT 时拒绝启动。
- 结果：`patch`（component_id/x/y/rotation/fixed）、`unplaced_components`、`hard_violations`、`congestion`、`wirelength_nm`、`escape_pressure`、`search_nodes`、`semantic_hash`。
- `apply_placement_to_circuit()` 只更新器件 placement；既有走线/焊盘不会被伪造移动。

---

## 4. 实测

```bash
.venv/bin/cmake --build build -j 4
.venv/bin/ctest --test-dir build -R placement --output-on-failure
./build/mota-placement-tests
.venv/bin/python -m pytest tests/placement -q
.venv/bin/python -m pytest -q --durations=10
```

结果：

```
ctest placement:           Passed
mota-placement-tests:      51 checks, 0 failures
pytest tests/placement:    4 passed
全量 pytest:               146 passed, EXIT=0
```

合法布局（40×30 mm，J1 锁定，中央 keepout）：

- status `SUCCEEDED`；J1 保持 (5.0, 5.0) mm；
- U1 在 (8.58, 12.39) mm、C1 在 (4.04, 12.16) mm，均避开 keepout；
- HPWL 15.74 mm；拥塞 20×15 格，max 2.03，mean 0.024；
- search nodes 2；`semantic_hash` 见产物；
- 同 seed 第二次运行 patch 与 hash 完全一致。

预算：

- `max_search_nodes=0` → `UNKNOWN`，不是 `INFEASIBLE`；
- `max_search_nodes=1` → `PARTIAL`，未放置 `['U2','U3']`。

负例：固定件重叠 → `FIXED_OVERLAP`；固定件在 keepout → `FIXED_IN_KEEPOUT`；整板 keepout → `NO_LEGAL_PLACEMENT`；keepout 夹在两器件之间时不允许用穿越换取线长。

---

## 5. 与 C8 清单的对应

| 清单项 | 证据 |
| --- | --- |
| 固定连接器/孔位置、局部禁布区 | `test_keepout_and_fixed`、`test_fixed_keepout_is_infeasible`、服务 keepout 测试 |
| 模块关系、去耦约束 | 模块区域硬约束 + 软带；`test_module_region_and_decoupling` |
| 合法/无解场景 | `test_no_space_is_infeasible`、`test_fixed_overlap_is_infeasible` |
| 分区、关键件优先、合法化、有限 CP、局部优化 | 第 2 节；`placement.cpp` |
| 可逃逸性/通道容量反馈 | `escape_pressure`、拥塞图、cost 第 2/3 项 |
| `ctest -R placement` | 第 4 节 Passed |
| 硬约束不被线长抵消 | `test_hard_keepout_beats_wirelength` |
| 固定 seed 重放 | `test_seed_replay`、服务测试、第 4 节两次 hash 一致 |

---

## 6. 已知边界

- 未安装 OR-Tools；当前是 Mota 自有的节点预算回溯搜索，不声称 CP-SAT 或最优性。
- HPWL 与拥塞用器件中心近似，没有真实引脚逃逸/通道容量模型；BGA/细间距扇出仍属 C10。
- 候选旋转只生成 0/90；固定件可保留任意初始旋转。
- 板边间距用板框 AABB + 四角包含检查；非矩形板框的凹口应显式建模为 keepout。
- 显式模块区域是硬约束；自动分区只是软带 + 内聚，不是图分割最优解。
- C5 只映射 clearance/board-edge 下界；keepout 区域、去耦配对需调用方显式传入。
- 布局 patch 只更新器件位置；已布线的铜对象必须重新布局/重建，不能把旧走线当作仍然有效。
