# Engine v2 — C6 交付报告（原生几何、多层数据与 worker）

日期：2026-10-21
范围：C6（`native/`、`src/mota/jobs/contracts.py`、`tests/jobs/test_native_worker.py`）。
原则：坐标是 int64 纳米；谓词用宽整数；近似必须带误差上界；worker 预算是 `UNKNOWN/PARTIAL`，不是“无解”。

---

## 1. 新增文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/types.hpp`、`src/geometry/types.cpp` | int64 坐标、__int128 中间值、溢出检查、共享 local→board 变换 |
| `native/include/mota/shapes.hpp`、`src/geometry/shapes.cpp` | point/segment/circle/round_rect/slot/polygon/arc；分类型距离、包含、AABB、变换、弧展平 |
| `native/include/mota/rtree.hpp` | packed R-tree（容量 16，递归中位切分），粗筛与内存统计 |
| `native/include/mota/board.hpp`、`src/board/board.cpp` | Stackup、PadStack、ViaDefinition（through/blind/buried/micro）、逐层 BoardIndex |
| `native/include/mota/json.hpp`、`src/worker/json.cpp` | 严格 JSON（int64 保留、重复键/尾随数据/超深拒绝） |
| `native/include/mota/sha256.hpp`、`src/worker/sha256.cpp` | 输入冻结哈希 |
| `native/include/mota/worker.hpp`、`src/worker/worker.cpp` | Job 执行、预算、取消检查点、错误隔离、JSON-RPC |
| `native/src/main.cpp` | `mota-kernel --stdio` / `--request` / `--version` |
| `native/CMakeLists.txt` | C++20，`mota-kernel`、`mota-kernel-tests` 与 ctest `geometry` |
| `native/tests/geometry_tests.cpp`、`worker_tests.cpp`、`test_common.hpp` | 无外部框架的 110 项断言 |
| `src/mota/jobs/__init__.py`、`contracts.py` | 复用 `engine_v2.contracts` 的 JobRequest/JobResult，增加冻结输入、子进程桥、JobResult 映射 |
| `tests/jobs/test_native_worker.py` | 8 项真实二进制端到端测试 |

未引入 Boost/Clipper2：Boost 未安装，R-tree 为 Mota 自有 packed 实现；多边形布尔/偏移留到 C7。构建工具 cmake/ninja 只装在 `.venv`，没有加入运行时依赖。

---

## 2. 几何语义

### 2.1 坐标与溢出

- 所有坐标为 int64 纳米，允许范围 `±1e15 nm`（1000 km）。
- `checked_add/sub/mul` 使用编译器溢出内建；越界坐标抛 `COORDINATE_OUT_OF_RANGE`，溢出抛 `OVERFLOW`，不返回值。
- 叉积、点积、平方距离用 `__int128`；例如 `(1e15,1e15)` 对角距离约 `2.828e15 nm`，不会溢出。
- 点到线段的内点投影用 long double 求比例，最终距离用整数平方根回代并校验；无法整除时声明 1 nm 舍入误差。

### 2.2 共享变换

`p_board = origin + R(rotation) · M(bottom) · p_local`

- 0/90/180/270 度用整数交换/取反，误差 0。
- 任意角度（如 37°）每坐标最多 1 nm 舍入，`rounding_error_nm()` 返回 1。
- 底面镜像固定为 `x → -x`；先镜像再旋转，PCB/3D/导出/DRC 共用这一个入口。
- RoundRect 在 90/270 度时交换半轴；非 90 度倍数展平为带误差的多边形。
- 弧的镜像/旋转会重量化到微弧度，误差声明为 `1 + ceil(radius × 0.5 µrad)`（10 mm 半径约 6 nm）。

### 2.3 距离与误差

| 类型对 | 方法 | 误差 |
| --- | --- | --- |
| point–point / point–segment | 整数投影 + 整数平方根 | 0 或 1 nm |
| segment–segment | __int128 定向 + 4 个端点距离 | 0 或 1 nm |
| circle / slot / round_rect | 核形状 Minkowski 半径（减半径/圆角） | 继承核距离 |
| polygon | 边距离 + 整数射线包含 | 0 或 1 nm |
| 任意含 arc | 按请求的 sagitta 展平 | 展平误差 + 2 nm |

`DistanceResult.error_nm` 与 `conservative_distance_nm = max(0, distance - error)` 同时返回；清除检查必须比较保守值。弧展平默认目标误差 100 nm，测试用 50 nm 验证实际边界。

---

## 3. R-tree 与逐层索引

- 每层一个 packed R-tree，叶容量 16；对象按 max(宽,高) 轴中位切分。
- 查询先用 AABB 粗筛，返回对象 ID；精确距离/间距必须再调用形状距离。
- `query(area)` 不做隐式外扩：调用者必须把查询框按 clearance 扩大，否则会漏掉“框外但间距不足”的对象。
- 当前是 bulk build；增量更新、局部重建留到 C7/C8。

### 3.1 大对象集实测（`docs/engine-v2/c6-metrics.json`）

| 指标 | 实测 |
| --- | --- |
| 对象数 | 100,000 |
| 节点 / 叶 | 16,383 / 8,192 |
| 最大深度 | 14 |
| 索引内存 | 8,400,072 B（约 8.01 MiB） |
| 构建 | 52.1 ms（同机负载下 22–52 ms 波动） |
| 200 次查询 | 132.4 ms（命中 271,972 个对象；负载下 66–132 ms） |
| 进程 RSS | 19,283,968 B（约 18.4 MiB，含 100k 输入框） |
| 正确性 | 200 个随机区域与暴力枚举结果完全一致 |

单对象索引开销约 84 B（含 AABB、order、节点与对象记录），不含字符串与形状点集堆内存（报告中另计）。

---

## 4. 多层数据与孔/过孔语义

- `Stackup`：层 id/order/kind/thickness/material_ref；顺序严格递增、至少两层铜、厚度为正；总厚可与板厚比对。
- `PadStack`：`copper_by_layer` 逐层铜形状，`drill` 只能是 circle/slot；镀通孔必须至少有一层铜。
- `ViaDefinition`：
  - through：跨越首末铜层；
  - blind：一端外层、一端内层；
  - buried：两端都在内层；
  - micro：在铜层序列中相邻（不是矢量为相邻）。
- `plated=false` 的 NPTH 即使有铜形状也返回 `electrically_connects=false`；C6 只提供事实，C7 才用它切断连通。
- `BoardIndex` 只接受铜层对象；未知层查询抛 `INVALID_INPUT`，避免拼写错误被当成空结果。

---

## 5. worker 合同

### 5.1 输入冻结

- Python `freeze_input()` 把规范化 JSON 写入临时 artifact，计算 SHA-256。
- 请求携带 `input_path` 与 `input_sha256`；C++ 在解析前重算哈希，不一致返回 `FAILED / STALE_JOB_INPUT`。
- 输入上限 4 MiB、对象上限 200,000、查询上限 10,000；超过返回 `INPUT_TOO_LARGE`。
- 测试通过“冻结后篡改文件一个字节”验证真实拒收。

### 5.2 预算、取消与状态

| 条件 | 状态 |
| --- | --- |
| 全部查询完成 | `SUCCEEDED` |
| `max_operations` 用尽且至少完成 1 个 | `PARTIAL` |
| `max_operations=0` / 尚未完成任何查询 | `UNKNOWN` |
| 索引内存超过 `max_memory_mb` | `UNKNOWN`（预算停止，不是无解） |
| 取消文件存在（每个查询前 + 建索引时检查） | `CANCELLED` |
| 输入/模型错误、全部查询失败 | `FAILED` |
| 证明无解 | C6 几何不产生；不使用 `INFEASIBLE` |

`checkpoint` 返回 `completed_queries`、`next_query_index`、`cancelled`、`budget_exhausted`。预算/取消诊断明确写明“不是不可能”。

### 5.3 错误隔离

- 单条查询的坐标越界或形状错误只把该条标为 `FAILED`，其余继续；整体为 `PARTIAL` 或 `FAILED`。
- 格式错误的 JSON 返回 `FAILED / INVALID_INPUT`，进程不崩溃。
- JSON-RPC 传输错误（缺参数、未知方法）返回 `-32602/-32601`，与 job 失败区分。

### 5.4 与既有 JobRequest/JobResult 的关系

`src/mota/jobs/contracts.py` 不新造同义 DTO，而是复用 `engine_v2.contracts.JobRequest/JobResult`，并提供 `to_job_result()`：

- `requested_targets` 保持请求冻结分母；
- `completed_targets` 来自 `status=OK` 的查询；
- `unimplemented_targets` 保留未完成/失败目标；
- `read_set` 记录输入 artifact 的 sha256。

---

## 6. 实际执行的命令

```bash
.venv/bin/cmake -S native -B build -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release
.venv/bin/cmake --build build --target mota-kernel-tests
.venv/bin/cmake --build build --target mota-kernel
.venv/bin/ctest --test-dir build -R geometry --output-on-failure
./build/mota-kernel-tests --report docs/engine-v2/c6-metrics.json
.venv/bin/python -m pytest tests/jobs -q
```

结果：

```
ctest geometry:        1/1 passed
mota-kernel-tests:     110 checks, 0 failures
pytest tests/jobs:     8 passed
全量 pytest:           135 passed, EXIT=0
```

`mota-kernel-tests` 覆盖：溢出/越界、90°/180°/270°/37° 旋转、底面镜像、弧端点与 45° 距离、弧展平误差、圆角矩形/槽孔/多边形距离、100k R-tree 与暴力对拍、stackup/padstack/via span/NPTH/custom pad、冻结哈希、预算、取消、错误隔离、SHA-256 向量、JSON-RPC。

---

## 7. 与 C6 清单的对应

| 清单项 | 证据 |
| --- | --- |
| 旋转/底面镜像、弧、custom pad、槽孔、NPTH、逐层 padstack、via span fixtures | `geometry_tests.cpp` 中的 transform / distance / board 测试与 `test_board_structures` |
| 整数几何、宽中间值、R-tree 粗筛、精确距离、近似误差控制 | `types.cpp`、`shapes.cpp`、`rtree.hpp`；`DistanceResult.error_nm`；弧展平测试 |
| worker 输入冻结、资源预算、取消检查点、错误隔离 | `sha256.cpp`、`worker.cpp`、`worker_tests.cpp`、`tests/jobs` |
| `cmake --build ... --target mota-kernel-tests`、`ctest -R geometry` | 第 6 节实际命令与 1/1 passed |
| 准确性与大对象集内存报告、越界坐标报错 | `c6-metrics.json`、第 3.1 节、`COORDINATE_OUT_OF_RANGE` 测试 |

---

## 8. 已知边界

- R-tree 只有 bulk build；局部编辑后的增量更新尚未实现（C7/C8）。
- 没有多边形布尔/偏移、铜皮填充、真实 DRC；这些属于 C7。
- 任意角度旋转使用 double 三角函数，声明 1 nm 舍入；弧角量化到微弧度，误差随半径线性增长并已返回。
- 弧距离基于展平多边形，返回保守误差；不能用于声称“0 nm 精确间距”。
- worker 是单次 job 执行；`--stdio` 可连续处理多行，但取消依赖取消文件/预算，不是常驻进程信号。
- `jobs/contracts.py` 复用既有 JobRequest/JobResult，不在 C6 新建调度器、检查点持久化或 artifact 存储；这些在后续 job/DAG 阶段接入。
