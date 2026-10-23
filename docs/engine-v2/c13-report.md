# Engine v2 — C13 交付报告（大场景原生界面与人工接管）

日期：2026-10-22
范围：C13（`desktop/scene_adapter.py`、`project_tree.py`、`job_panel.py`、`widgets/canvas_2d.py`、`cli/main.py`；`tests/desktop/test_versioned_scene.py`）。
原则：场景与作业结果都绑定 `design_version_key`；旧版本回包只能显示为 `STALE`，永远不会变成“新版本 Saved”；帧率与延迟是实测值，不做估算。

---

## 1. 新增与修改

| 文件 | 作用 |
| --- | --- |
| `src/mota/desktop/scene_adapter.py` | `SceneManifest` / `ChunkSpec` / `UnroutedPoint`；分块、LOD、层/网络类/未布通筛选；跨视图 net/器件定位；`VersionedSceneCache`；`VersionedJobBoard`；3D `MeshLibrary` |
| `src/mota/desktop/project_tree.py` | 树形导航：视图 / 层（可勾选）/ 网络（含 routed-unrouted 标记）/ 器件 / 未布通点 / 约束计数；点击发出跨视图定位载荷 |
| `src/mota/desktop/job_panel.py` | 作业列表与 Saved/Stale/Failed 状态；旧版本结果标记 STALE 并提供 Re-solve |
| `src/mota/desktop/widgets/canvas_2d.py` | `load_manifest` / `load_chunk` / `set_lod` / 视口剔除 / 缺块请求 / `frame_stats`；旧版块拒绝加载 |
| `src/mota/cli/main.py` | `mota design move/lock/undo/redo`，与桌面共用 `design.patch` / `design.undo` / `design.redo` |
| `tests/desktop/test_versioned_scene.py` | 9 个用例：分块完整性、跨视图定位、筛选、旧 scene/job 拒绝、LOD、树导航、CLI 与 UI 共用操作、1000 器件压力与 3D mesh 共享 |

---

## 2. SceneManifest 与分块

- 清单字段：`project_id`、`view`、`design_version_key`、`bounds`、`coordinate_system`、网格 (`cols/rows/cell_mm`)、`chunks[]`、层、net→class、未布通点、对象计数、mesh 表、`component_refs`、`net_counts`、`net_chunks`、`net_bounds`、`component_chunks`、`component_bounds`、`content_hash`。
- 每个 chunk：`chunk_id = view:col:row`、矩形、对象 id、类型计数、LOD 计数、对象内容 SHA-256。
- 分块目标尺寸默认 25 mm；网格上限 256 块，超过时按 1.25 倍递增单元，不丢对象。
- 对象按包围盒中心落格；无坐标对象进入 `(0,0)` 块，不会被删除。
- `content_hash` 绑定版本键、全部 chunk 哈希与 mesh 表键。

### 2.1 LOD

- LOD 0：板框、焊盘、铜线、via、3D 实体；
- LOD 1：丝印、courtyard、钻孔等结构细节；
- LOD 2：文本与小标记。

`CanvasView.set_lod()` 直接按对象 LOD 隐藏；`filter_objects(..., lod=n)` 供服务端/测试使用。缩小视图时可只画 LOD 0/1，放大后恢复。

### 2.2 筛选与定位

- `filter_objects(layers, net_classes, nets, only_unrouted, lod)`；未布通筛选使用清单中的 `unrouted` 点集。
- `locate_net` / `locate_component` 返回每个视图的 chunk id 列表与精确包围盒；清单阶段已建立 `net_chunks/net_bounds`、`component_chunks/component_bounds`，定位是字典查询，不遍历对象。
- 树面板点击网络或器件即发出 `{"view","x","y","chunks"}`，画布可据此 `center_on_world` 并按需加载块。

---

## 3. 1000 器件压力实测

环境：macOS 10 核，PySide6 offscreen 平台，视口 1280×800，`pytest tests/desktop -q`。
合成设计：1000 个器件、2000 个焊盘、1000 段铜线、500 条飞线。

| 指标 | 实测（单次运行，波动约 ±20%） |
| --- | --- |
| 场景对象 | 8,501 |
| 分块（25 mm 单元） | 54 |
| 设计构造 | 23.9 ms |
| 场景生成 `pcb2d_scene` | 18.4 ms |
| 清单/分块 + LOD + 索引 | 73.8 ms |
| 单网络跨视图定位 | 0.01 ms |
| 总览可见块 → 放大 6× 后可见块 | 54 → 4 |
| 连续 12 帧渲染 | 85.0 fps，p50 10.59 ms，p95 24.31 ms |
| 3D 共享 mesh（200 器件） | 1001 个 mesh 对象 → 4 个唯一 mesh、801 个实例；mesh 表 1,172 B，未去重 232,296 B |

说明：offscreen 渲染不含真实 GPU 合成与显示器刷新，数值用于回归与相对比较；不是对实际窗口流畅度的承诺。人工目视检查没有执行，不能据此声称“人工验收通过”。

---

## 4. 3D 共享 mesh

`MeshLibrary` 以 `(vertices, faces)` 的内容哈希作为 key：

- 相同几何只保留一份顶点/面表，对象只保留 `mesh_ref`；
- 清单 `mesh_table` 记录字节数与实例数，可直接计算去重收益；
- 200 个器件、1001 个 mesh 对象的压力集里，唯一 mesh 为 4 个，表体积约为未去重的 0.5%。

---

## 5. 版本语义与旧回包

### 5.1 Scene

- `VersionedSceneCache.begin(key)` 生成带序号的令牌；`accept(token, scene)` 只在 `key == 当前版本` 且令牌是该 key 的最新一次请求时返回 `APPLIED`；
- 旧版本令牌 → `STALE`，当前场景不变；
- 同版本被后续请求取代 → `SUPERSEDED`，同样不写入；
- 画布 `load_manifest` 记录版本，`load_chunk` 对不匹配版本直接返回 `False`，不会把旧块画进新场景。

### 5.2 Job

- `VersionedJobBoard.accept(job, version, state)` 只在版本等于当前版本时接受结果并允许 `saved=True`；
- 旧版本成功结果被标记 `STALE`、`saved=False`，消息明确写出“属于 rev A，不是当前 rev B”；
- `JobPanel` 显示 “STALE · ignored”，Saved 计数不增加，操作按钮变为 Re-solve；
- 测试模拟“先发 rev1、切到 rev2、rev1 结果才回来”，确认不会显示为 Saved。

已有 `build.py` 的 `_apply_scene` 仍保留 revision/draft 键校验，与本层形成双重保护。

---

## 6. 树形导航与人工接管

- `ProjectTree` 分区：Views、Layers、Nets、Components、Unrouted、Constraints；层可勾选，网络按 routed/unrouted 着色，未布通点带坐标。
- 过滤条件通过 `filters_changed` 发给画布/查询层；支持按层、net class、只显示未布通。
- 锁定 / 移动 / 撤销 / 重新求解：
  - 桌面 `ProjectController.patch/undo/redo` → `design.patch` / `design.undo` / `design.redo`；
  - CLI 新增 `mota design move|lock|undo|redo`，走同一 `ApplicationService` 与同一操作 schema（`place_footprint`、`lock_component`）；
  - 操作仍由既有 `DesignService.apply` 执行，锁定件受布局/布线保护；
  - `JobPanel.resolve_requested` 用于对失败或过期作业发起重新求解，宿主决定具体 run 输入。

---

## 7. 测试

```
.venv/bin/python -m pytest tests/desktop -q
9 passed
```

覆盖：分块覆盖全部对象与 DTO 往返、跨视图 net/器件定位、层/类/未布通筛选、旧 scene 拒绝与 supersede、旧 job 不标 Saved、画布旧块拒绝与 LOD、树导航与过滤、CLI/UI 共用 `design.patch`、1000 器件压力与 mesh 去重。

---

## 8. 已知边界

- 树面板与作业面板是独立原生控件，尚未嵌入当前 Build 页的最终布局；页面改造属于后续 UI 收口，不影响其行为测试。
- 帧率在 offscreen 平台测量，未包含真实 GPU 与显示器刷新；没有执行人工目视检查。
- 3D 视图仍由既有 `board_3d` 组件渲染；本阶段提供共享 mesh 数据与去重统计，未替换 3D 渲染后端。
- 约束筛选目前使用校验报告计数与 net class，不解析任意 DRC 规则表达式。
- 旧回包防护覆盖 scene 清单、chunk、job 结果三层；文件/工程级并发仍由 `expected_revision_id` 与提交索引保证。
