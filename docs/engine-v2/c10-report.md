# Engine v2 — C10 交付报告（BGA 扇出求解器）

日期：2026-10-21
范围：C10（`native/src/routing/fanout/`、`native/tests/fanout_tests.cpp`、`platform_packs/bga-fixtures/`）。
原则：每个球只有存在合法 via 模板和真实 escape 几何时才接受；失败球带定位与可改变因素；工艺非法 via 不输出。

---

## 1. 新增文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/fanout.hpp`、`src/routing/fanout/fanout.cpp` | 球图、工艺/叠层、通道初筛、via site、组保留、层分配、真实 dogbone / via-in-pad 几何、独立连通检查、失败定位 |
| `native/tests/fanout_tests.cpp` | 3381 项断言：fixture 加载、工艺合法性、几何连通、组关系、seed 重放、平台封装 |
| `platform_packs/bga-fixtures/` | 5 个 fixture：0.8/0.4 与 0.5/0.3 各可行/不可行，加 STM32H743 TFBGA240 平台封装 |
| `native/CMakeLists.txt` | `mota-fanout-tests` 与 ctest `fanout` |

---

## 2. 输入与输出

输入（`FanoutInput`）：

- 球图：球 id、net、行列、中心、焊盘直径、组与顺序、是否必需、电源/地标记；
- 工艺：pitch、焊盘、线宽、间距、through via / microvia 钻孔与焊盘、最小环宽、是否允许 dogbone / via-in-pad / through via、板边间距、搜索预算、seed；
- 叠层出口：可用信号层（stackup 索引与名字）；
- 组：差分、byte lane、clock 的球顺序、是否同层、是否相邻。

输出（`FanoutResult`）：

- `vias`：每个逃逸球的 via site、drill、pad、起止层、kind（dogbone / via_in_pad）；
- `tracks`：dogbone 的真实线段（球中心 → via site，含线宽）；
- `failures`：球 id、net、原因、限制因素（pitch/pad/via/clearance/channel/group）；
- `unescaped_balls`、`channel_capacity`、dogbone/via-in-pad 计数、搜索节点、semantic hash。

---

## 3. 算法

### 3.1 通道初筛

相邻焊盘通道 `gap = pitch - pad_diameter`；每根线需要 `width + clearance`，外侧再留 `clearance`：

```
k_max = floor((gap - clearance) / (width + clearance))
```

结果写入 `channel_capacity`。0.8/0.4 fixture 为 1；0.5/0.3 fixture 为 0。通道为 0 时，只有 via-in-pad 或外圈 dogbone 可能逃逸，不能因此直接判全板无解。

### 3.2 via site 生成

- **dogbone**：四角对角 site（±pitch/2, ±pitch/2）；外圈球额外生成向外 site（`pitch/2 + via_pad/2 + clearance`）。
- **via-in-pad**：球中心，使用 microvia 模板；要求 microvia pad 能放入球焊盘且环宽合法。
- 每个 site 都做真实几何检查：在板框/边距内、via pad 与所有球焊盘（dogbone 包括自己的球）满足 clearance、track slot 不碰其他球焊盘、via 不与其他已分配 via/track 冲突。
- 工艺非法（钻孔/焊盘/环宽）直接不生成候选；不会输出超工艺 via。

### 3.3 出口与层分配

- 组优先：差分 / byte lane 先分配。对每个目标信号层做有限 DFS，要求同层、站点不冲突；差分要求相邻，byte lane 要求同方向且沿球序单调。
- 非组球：按候选数量从少到多（最受限优先）贪心选择最低代价候选。
- 搜索预算 `max_search_nodes`；耗尽时剩余球失败 `BUDGET`，状态不会写成无解。
- 目标层来自声明的信号层；dogbone 为 through via 落到目标层，via-in-pad 为 microvia 落到第一信号层。

### 3.4 独立检查

- 每个 via 检查钻孔、焊盘、环宽、目标层；
- 每个 dogbone 检查球焊盘—track—via 真实接触；via-in-pad 检查球焊盘—via 重叠；
- 所有逃逸铜做一次 `analyze_connectivity`，要求 `short_count=0`；
- 失败球列出原因与因素，便于定位到具体球和工艺/层/组。

### 3.5 状态

- 所有必需球逃逸且无短路 → `SUCCEEDED`；
- 有逃逸但未全覆盖 → `PARTIAL`；
- 全失败且每个失败都是有限候选/工艺证明（`NO_VIA_SITE`、`CHANNEL_CAPACITY`、`VIA_PROCESS`、`GROUP_RESERVATION_FAILED`）→ `INFEASIBLE`；
- 预算耗尽且未证明 → `UNKNOWN`。

---

## 4. Fixtures 与实测

| fixture | pitch / pad | 工艺 | 结果 | 逃逸 | 失败 | 通道 |
| --- | --- | --- | --- | --- | --- | --- |
| synthetic_0p8_feasible | 0.8 / 0.4 mm | 0.2/0.4 through via | SUCCEEDED | 64/64（全 dogbone） | 0 | 1 |
| synthetic_0p8_infeasible | 0.8 / 0.4 mm | 0.5/0.6 via，环宽 0.05 < 0.08 | INFEASIBLE | 0/64 | 64 | 1 |
| synthetic_0p5_feasible | 0.5 / 0.3 mm | 0.1/0.2 microvia via-in-pad | SUCCEEDED | 100/100（36 dogbone + 64 via-in-pad） | 0 | 0 |
| synthetic_0p5_infeasible | 0.5 / 0.3 mm | 禁 dogbone、禁 via-in-pad | INFEASIBLE | 0/100 | 100 | 0 |
| platform_stm32h743_tfbga240 | 0.8 / 0.4 mm | 0.2/0.4 dogbone + 0.1/0.2 via-in-pad | SUCCEEDED | 240/240（全 dogbone） | 0 | 1 |

- 0.8 不可行变体输出 0 个 via，失败原因包含 `VIA_PROCESS` / `GROUP_RESERVATION_FAILED`，不会出现环宽不足的 via。
- 0.5 可行变体中，内圈 64 球无通道可用，改用 via-in-pad；外圈 36 球用外部 dogbone，说明两种出口资源都被使用。
- 组保留：可行 fixture 中每个差分/byte lane 的所有逃逸 via 落在同一目标层；不可行 fixture 的组失败定位到具体球。
- 每个 fixture 的失败球都有 id、原因与限制因素。

### 平台封装

`platform_stm32h743_tfbga240` 使用公开封装轮廓：

- ST STM32H743XIH6，TFBGA 240+25，14×14×1.2 mm，pitch 0.8 mm；
- fixture 采用 16×16 网格去掉四个 2×2 角块 = 240 个有效球，球位/网络/组为**代表性**映射，不是厂商生产 pinout；
- 全部 240 球实际逃逸，属于“一个具体平台封装验证”，不外推为所有 BGA 支持。

---

## 5. 实际执行

```bash
.venv/bin/cmake --build build -j 4
.venv/bin/ctest --test-dir build -R fanout --output-on-failure
./build/mota-fanout-tests
.venv/bin/ctest --test-dir build --output-on-failure
.venv/bin/python -m pytest -q --durations=10
```

结果：

```
ctest fanout:              1/1 Passed
ctest all:                 6/6 Passed
mota-fanout-tests:         3381 checks, 0 failures
全量 pytest:               149 passed, EXIT=0
```

---

## 6. 与 C10 清单的对应

| 清单项 | 证据 |
| --- | --- |
| 两种 pitch/pad、各含可行/不可行变体、尺寸写入 fixture | 0.8/0.4 与 0.5/0.3 四个 synthetic fixture + 平台 fixture |
| 通道初筛 | `channel_capacity`：0.8 为 1，0.5 为 0 |
| via site 生成 | 对角/外圈 dogbone + 中心 via-in-pad；真实形状 clearance |
| 出口/层分配 | 组 DFS + 最受限优先贪心；`target_layer`/`end_layer` |
| 真实 escape 几何 | `tracks` / `vias` 输出；球—track—via 接触检查 |
| 差分/byte lane 组保留 | 组 DFS 同层/相邻/顺序；测试检查同层 |
| 禁止超工艺 via | 0.8 不可行 fixture 输出 0 via；逐 via 环宽检查 |
| `ctest -R fanout` | 第 5 节 1/1 Passed |
| 所有必需 terminal 实际连通、失败球定位 | 3381 项断言：几何接触、短路、失败原因/因素 |
| 一个具体公开平台封装 | STM32H743 TFBGA240 fixture，240/240 逃逸，来源写入 fixture |

---

## 7. 已知边界

- 分配是 Mota 自有的贪心 + 组内有限 DFS，不是 min-cost flow / OR-Tools CP-SAT；不声称全局最优。
- dogbone 只在顶层 F.Cu 生成；via-in-pad 只落到第一信号层；不支持多层 microvia stack、盲埋孔堆叠或背面 BGA。
- 工艺模型只覆盖 pitch/pad/线宽/间距/via 模板/环宽/板边；未覆盖填孔、teardrop、aspect ratio、激光钻孔上限等全部板厂规则。
- 电源/地只做 escape，不建模平面连接或去耦。
- 平台 fixture 的球位/网络/组是代表性映射；不是 STM32H743 的生产 pinout。
- `INFEASIBLE` 是当前候选生成与工艺模型内的证明，不是对所有板厂/所有 escape 模板的断言。
