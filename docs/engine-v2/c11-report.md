# Engine v2 — C11 交付报告（差分、路径与延迟约束）

日期：2026-10-22
范围：C11（`native/src/routing/highspeed/`、`src/mota/constraints/compiler.py`、`native/tests/highspeed_tests.cpp`、`platform_packs/highspeed-fixtures/`）。
原则：长度相等不等于延迟相等；缺少层／via／封装模型时检查保持 `NOT_CHECKED`；几何与长度 PASS 永远不会升级为 SI／PI PASS。

---

## 1. 新增与修改文件

| 文件 | 作用 |
| --- | --- |
| `native/include/mota/highspeed.hpp` | 端到端 channel、差分对、层传播模型、参考面、封装模型、tuning region 与结果 DTO |
| `native/src/routing/highspeed/internal.hpp` | 共享几何帮助函数与跨编译单元声明 |
| `native/src/routing/highspeed/channel.cpp` | 铜图构建、端子落铜、串联元件、分支、Dijkstra 路径长度、拓扑检查 |
| `native/src/routing/highspeed/differential.cpp` | 差分共同搜索（采样耦合）、角点配对、成对转层 |
| `native/src/routing/highspeed/delay.cpp` | 按层传播、via／封装延迟、参考面裂缝、受控 tuning patch |
| `native/src/routing/highspeed/highspeed.cpp` | 状态汇总、违规／未检查清单、semantic hash |
| `native/src/routing/highspeed/highspeed_json.cpp`、`native/include/mota/highspeed_json.hpp` | 唯一 JSON 绑定；worker 与 C++ fixture 共用，避免两套 schema 漂移 |
| `native/tests/highspeed_tests.cpp` | 193 项断言：fixture 逐个校验、seed 重放、篡改、预算、未配对转层、缺模型、空契约 |
| `platform_packs/highspeed-fixtures/` | 7 个 fixture（4 个正例上下文 + 3 个明确负例） |
| `src/mota/constraints/compiler.py` | 新增 `compile_highspeed`：规则 → native request；显式 channel；覆盖报告 |
| `tests/constraints/test_highspeed_compile.py` | 14 个 pytest，含一次真实 `mota-kernel` 端到端查询 |
| `native/src/worker/worker.cpp` | 新增 `kind=highspeed` 查询与 job 输入 `highspeed` 块 |
| `native/CMakeLists.txt` | `mota-highspeed-tests` 与 ctest `highspeed` |

---

## 2. 输入与输出

### 2.1 端到端 channel

`Channel` 明确声明：

- `driver`：唯一的驱动节点；
- `receivers`：一个或多个接收端；
- `series`：串联元件引脚（每个元件两个引脚，由 `kind=component` 的 `ChannelLink` 连接，`internal_length_nm` 为元件内部长度）；
- `branches`：声明的分支节点；
- `links`：`copper` 链路用于核验“声明拓扑确实由铜实现”，`component` 链路给出串联元件内部路径。

节点带 `net`、`component_id`、`pin`、位置与层。没有任何一项由 net 名字推断。

### 2.2 差分对与模型

- `DiffPair`：`gap_nm`、`gap_tolerance_nm`、`skew_tolerance_nm`、`max_uncoupled_nm`、`delay_skew_tolerance_ps`、是否要求成对转层；
- `LayerDelayModel`：层、厚度、参考层、`propagation_ps_per_mm`、来源；
- `HighspeedVia`：钻孔、无用 stub、`delay_ps` 与 `has_delay`（显式模型，不默认 0）；
- `PackageModel`：器件／引脚、内部长度、延迟、`present`（缺模型是显式状态）；
- `TuningRegion`：网络、层、起止、宽度与可增加长度上限。

### 2.3 输出

`HighspeedResult`：

- `checks[]`：`channel_topology / length_match / delay / return_path / diff_coupling / diff_skew / diff_turn / pair_layer / delay_skew / tuning`，每项含 `PASS/FAIL/NOT_CHECKED/SKIPPED`、类型化 `reason`、实测值与限值、对象引用；
- `bundles[]`：耦合长度、非耦合长度、长度差、角点差、未配对转层数；
- `tuning[]`：实际锯齿折线、每齿间距、增加长度与增加延迟、是否受控；
- `si_status`、`pi_status`：恒为 `NOT_CHECKED`；
- `violations`、`not_checked`、计数、`search_nodes`、`seed`、`semantic_hash`。

---

## 3. 算法

### 3.1 Channel 求解（真实铜）

1. 为 channel 触及的每个 net 建立铜图：track 顶点与边长、via 在起止层同坐标处连边（跨层平面长度为 0，延迟另计）。
2. 端子必须落在本网络铜顶点 `terminal_snap_nm` 内；否则 `TERMINAL_NOT_ON_COPPER`，该 channel 不产生长度结论。
3. 先只用铜边做并查集：`copper` 链路两端必须同组分，否则 `TOPOLOGY_OPEN`。
4. `component` 链路把串联元件两个引脚连成一个边，权重为内部长度。
5. 从 driver 做 Dijkstra，得到每个 receiver 的真实路径长度；不可达为 `UNREACHABLE_RECEIVER`。
6. `length_match` 取所有 receiver 路径长度的极差与窗口比较；单接收端为 `SINGLE_RECEIVER`（不冒充通过）。

### 3.2 差分共同搜索

- 两条网络分别按 `sample_step_nm`（默认 0.1 mm）重采样；
- 每个采样点只在**同层**另一条网络上找最近线段；距离 ≤ `gap + gap_tolerance` 记为耦合，否则记为非耦合；双向采样后取一半，避免重复计算；
- `gap` 未声明（0）时不做耦合结论（`MISSING_GAP`）；
- 角点按转角 > 30° 提取，逐一与另一条网络的角点配对（同层，窗口 `2*gap` 或 3 个采样步长的较大者）；未配对或弧长差超过采样步长／skew 窗口即 `diff_turn` FAIL；
- 转层按 via 配对：同起止层且中心距离在 gap 窗口内才算成对；否则 `pair_layer` FAIL 并列出未配对 via；
- 采样或距离搜索超过预算时返回 `SEARCH_BUDGET`，整体为 `UNKNOWN`，不写成“未耦合”。

### 3.3 延迟估计

```
delay = Σ (层铜长 mm × 层 propagation_ps_per_mm) + Σ via.delay_ps + Σ package.delay_ps
```

- 任何有铜的层没有模型（或 `propagation_ps_per_mm ≤ 0`）→ `MISSING_MODEL`，检查 `NOT_CHECKED`；
- 任何 via 没有 `has_delay`，或声明了 `stub_nm > 0` 却没有 stub 模型 → `NOT_CHECKED`；
- channel 的 driver／receiver／串联元件引脚都必须有封装模型，缺一个即 `NOT_CHECKED`；
- 只有模型完整且声明了 target 时才比较：`|delay - target| ≤ tolerance`；没有 target 时状态是 `SKIPPED / NO_LIMIT`，不是 PASS。

### 3.4 参考面裂缝

对每段铜的采样点（中点）取所在层的 `reference_layer_id`：

- 层没有参考层、参考层没有声明铜多边形 → `NOT_CHECKED / MISSING_REFERENCE`；
- 采样点不在任何参考面多边形内 → `return_path` FAIL `REFERENCE_PLANE_GAP`，并给出具体坐标。

### 3.5 受控 tuning region

- 仅当 `diff_skew` 或 `delay_skew` 越限时生成；没有声明 region 直接 `NO_TUNING_REGION`（不在区域外放蛇形）；
- 需要补偿的额外长度由长度差或 `delay_skew / propagation_ps_per_mm` 换算；
- 生成**单侧基线锯齿**：每齿从中轴线抬升 `amplitude` 后回落，方向选在离另一条网络更远的一侧；
- 齿数按 `2 × amplitude` 初估，实际折线长度不足 95% 时递增齿数，受 `tuning_min_pitch_nm` 与 region 长度约束；
- 每个折线顶点都要与另一条网络保持 ≥ `gap`，否则 `TUNING_COUPLING`；间距小于 `gap + margin` 时写警告；
- 输出 patch 的 `added_length_nm / added_delay_ps / controlled`。patch 是**建议几何**，不会回写测量值：实测 skew 在应用前仍然违规。

---

## 4. Fixture 实测

命令：`ctest --test-dir build -R highspeed --output-on-failure`

| fixture | 期望 | 实测 | 关键证据 |
| --- | --- | --- | --- |
| `channel_series_matched` | SUCCEEDED | SUCCEEDED，4 PASS | 串联元件两侧 25 mm/25 mm；延迟 238 ps 对 238 ± 1 ps；参考面 PASS |
| `pair_matched` | SUCCEEDED | SUCCEEDED，5 PASS | 耦合全段、skew 0、角点全配对、1 对转层、延迟差 0 ps |
| `same_length_different_layer` | FAILED | FAILED，2 PASS / 2 FAIL | `diff_skew` PASS（0 nm），`delay_skew` FAIL（−60 ps，F.Cu 6.0 vs In1.Cu 9.0 ps/mm） |
| `reference_split` | FAILED | FAILED，1 PASS / 1 FAIL / 1 SKIPPED | 轨迹跨 15–17 mm 参考面裂缝；`return_path` FAIL；`delay` SKIPPED（无 target） |
| `local_uncoupled` | FAILED | FAILED，1 PASS / 2 FAIL | 长度为 15 mm vs 17.6 mm（窗口 5 mm，PASS）；非耦合 6.2 mm > 1 mm；4 个角点无配对 |
| `missing_package_model` | PARTIAL | PARTIAL，3 PASS / 1 NOT_CHECKED | 两条路径等长（PASS）；U3:1 无封装模型 → `delay` NOT_CHECKED；状态不可能是 SUCCEEDED |
| `tuning_required` | FAILED | FAILED，2 PASS / 1 FAIL / 3 SKIPPED | skew 2 mm 仍 FAIL；tuning PASS：补偿 2,248,192 nm（8 齿、齿距 0.5 mm），受控且满足最小齿距 |

附加断言（非 fixture）：193 checks / 0 failures，覆盖

- 删除 N1 铜后 `channel_topology` FAIL `TERMINAL_NOT_ON_COPPER`（标签不能代替铜）；
- `max_pair_samples=1` → `budget_hit`，耦合不 PASS，整体 `UNKNOWN`；
- 移动一条网络的 via 2 mm → `pair_layer` FAIL `UNPAIRED_TRANSITION`；
- 删除 In1.Cu 层模型 → `delay_skew` NOT_CHECKED `MISSING_MODEL`；
- 空输入 → `NOT_CHECKED`，不发明检查项。

---

## 5. 状态语义

| 条件 | `HighspeedResult.status` |
| --- | --- |
| 任一检查 FAIL | `FAILED`（`violations` 列出 check id） |
| 搜索/采样预算耗尽且无 FAIL | `UNKNOWN` |
| 没有声明任何 channel/pair | `NOT_CHECKED` |
| 存在 NOT_CHECKED 或 SKIPPED | `PARTIAL` |
| 全部检查 PASS | `SUCCEEDED` |

`si_status` 与 `pi_status` 恒为 `NOT_CHECKED`，并在 `diagnostics` 中写明“几何／长度／延迟检查不构成信号或电源完整性签核”。

---

## 6. Python 编译器与端到端查询

`compile_highspeed(circuit, board, packs, channels=..., layer_models=..., reference_planes=..., package_models=..., tuning_regions=..., rules=...)`：

- 规则类型：`length_match`（`max`/`min`，长度）、`propagation_delay`（`target`/`min`/`max`，时间）、`differential_pair`（`gap`/`gap_tolerance`/`skew`/`uncoupled` 长度，`delay_skew` 时间）；
- 复用既有 `_parse_quantity`：缺单位、量纲不符、未知参数都进 `INVALID`，不会被静默当成数字；
- channel 必须显式声明；节点按 `(component_id, pin)` 在 `board.pads` 查真实坐标。引脚写错**不会**回退到同器件其它引脚；查不到就整条 channel 不进入 request，并记 `NOT_CHECKED / MISSING_PAD`；
- 规则命中但没有任何显式 channel → `NOT_CHECKED / NO_CHANNEL`；
- `propagation_delay` 命中但没有层模型 → `NOT_CHECKED / MISSING_STACKUP_MODEL`；
- `differential_pair` 选中的 net 不是正好 2 个 → `NOT_CHECKED / PAIR_NET_COUNT`；
- 空规则集 → `INVALID`，`request` 为空；
- 输出 `content_hash` 绑定 circuit / board / packs / channel / 模型；`review_ready` 恒为 false。

端到端测试 `test_compiled_request_runs_in_the_kernel` 把编译结果直接交给 `mota-kernel` 的 `kind=highspeed` 查询，验证：`SUCCEEDED` 任务、`length_match` 与 `channel_topology` 检查存在、SI/PI 仍为 `NOT_CHECKED`。

---

## 7. 已知边界

- 耦合是采样测量（默认 0.1 mm 步长），不是场求解；报告中的 `coupled_nm / uncoupled_nm` 是采样值。
- 通道延迟按**该 channel 全部网络铜长**累计；存在分支时包含分支铜，属保守上界，不按单条路径拆分。
- `length_match` 只比较 driver→receiver 的路径长度，不评价阻抗、串扰或眼图。
- 参考面连续性按 **channel** 检查；未声明 channel 的差分网络不做回流路径判断。
- 参考面只做平面连续性检查，不做跨分割回流路径的电感／阻抗估计。
- tuning patch 只是建议几何，不自动写入布线结果；应用前违规仍然成立。
- via 延迟必须显式给出；有 stub 却无 stub 模型时不会估算。
- 未接入 KiCad DRC / SI 仿真器，因此不会声称做过 SI/PI 签核。
