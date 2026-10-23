# MoTA R0–R5 修复与验收报告

日期：2026-10-22
数据根目录（GUI 验收）：临时目录，测试结束后删除；未修改用户真实工程与历史数据。

## R0 定位与复现

- 产物：[canvas-audit.md](canvas-audit.md)、[path-map.md](path-map.md)。
- 已确认根因：`DesignService.apply` 在 `recipe is None` 时抛 `TEMPLATE_NOT_FOUND`，导致空白工程无法添加器件。
- 已确认显示问题：`CanvasView.fit()` 把页面/标题栏算入内容范围。
- 已确认交互缺口：放置没有 ghost/落点；GUI 没有 persisted/displayed 两套版本状态。

## R1 空白工程与显式模板

修改：

- `src/mota/app/design.py`：允许无模板编辑；`base_board` 复用当前板。
- `src/mota/generation/board.py`：`rebuild(..., base_board=...)`。
- `src/mota/desktop/pages/build.py`：`new_blank_board`、空状态、模板确认。
- `src/mota/desktop/widgets/resource_panel.py`：Templates 仅左侧展示，单击只更新详情。

验证：

- 启动后无模板实例、无自动模型调用、无假 Saved。
- 新建空白板产生新 `project_id`；`components/nets/footprints/pads/outline = 0/0/0/0/0`。
- 模板点击预览不改变当前版本与对象数；使用模板创建新工程。

## R2 手动放置与真实提交

修改：

- `src/mota/desktop/widgets/canvas_2d.py`：ghost、吸附、Esc、`content_bounds`、Fit 拆分。
- `src/mota/desktop/widgets/design_view.py`：`place_requested`、Fit 系列、Problems 切换。
- `src/mota/desktop/pages/build.py`：`_add_part` → PREVIEW；`_place_component` → 独立 operation id → `_patch`。
- `src/mota/desktop/controllers.py`：`patch(operation_id=...)`。

GUI 实测（真实引擎 + 持久数据）：

```text
blank project: project_ebecc1e016e61da5 rev rev_000001 objects 4
placed revision: rev_000002 displayed: rev_000002 canvas objects: 11
undo -> rev_000003 redo -> rev_000004
disk revision: rev_000004 components: 1 footprints: 1 pads: 2 outline: 0 symbols: 1
restored revision: rev_000004 canvas objects: 11 state: idle
```

- 重复提交：`test_design_patch_idempotency_replay_and_conflict` 验证同键回放原 revision、不同参数返回
  `IDEMPOTENCY_CONFLICT`。
- 并发写：Agent 运行中手动编辑返回 `PROJECT_BUSY`，GUI 显示中文提示；忙碌判断按
  `run.project_id == active_project_id` 限定，切换到另一工程后可正常编辑。
- Agent 提交事件带 `project_id`；旧工程的 `revision_committed` / `run.completed` 不会改写当前画布
  （A16）。
- 缺符号/缺封装：`SYMBOL_UNAVAILABLE` / `FOOTPRINT_UNASSIGNED` 路径保留。

## R3 场景版本与 Agent

修改：

- `BuildPage`：`persisted_version/displayed_version/displayed_target/pending_operation_id/scene_load_state`。
- `_apply_scene()`：校验 `design_version_key`、当前 revision/draft、旧场景拒收；仅当前视图安装成功后更新
  `displayed_version`。
- `_emit_meta()`：中文真实状态；`save_now()` 无待提交操作返回“已是最新”。

验证：

- GUI 中放置后 `persisted == displayed == rev_000002`。
- 重启后仍为 `rev_000004` 且画布可选中恢复的器件。
- Agent 空白板路径：`tests/integration/test_agent_blank_board.py` 通过。真实 run 调用
  `apply_design_patch`，提交 revision，持久数据为 `R1` + 1 个封装；事件含 `design.patch_applied`。
- 旧 epoch 事件：`test_run_controller_discards_old_epoch_and_duplicate_sequences` 继续通过。

## R4 菜单、布局与 Problems

修改：

- `src/mota/desktop/main.py`：真实 File/Edit/View/Place/Design/Tools/Help；未实现项禁用并附原因。
- 焦点路由：文本控件优先处理 Undo/Redo/Cut/Copy/Paste/Delete；F/Shift+F/A/R/G/1/2/3/+/- 只在画布内生效。
- `workspace.py`：状态栏 Problems 计数（FAIL+WARN），点击打开/关闭；View → Problems（Ctrl+Shift+P）。
- `_close_project`、`_reset_layout`、`BuildPage.show_resources/reset_layout`。
- Problems 不再因出现 FAIL 自动展开；默认隐藏。
- Agent 完成步骤折叠沿用既有 `ActionCard`（`test_completed_tool_rows_collapse_and_failures_expand`）。

验证：

- 菜单动作全部连接到可调用方法；无法实现的入口禁用并显示原因。
- 实际菜单树（真实 QAction）：
  File: New Blank Board… / New Schematic Sheet…(disabled) / Open Project… / Close Project / Save /
  Save As… / Export… / Sign out / Quit MoTA
  Edit: Undo / Redo / Cut / Copy / Paste / Delete / Duplicate(disabled) / Select All(disabled) /
  Deselect / Find… / Properties
  View: Schematic / PCB · 2D / PCB · 3D / Zoom In / Zoom Out / Fit Content / Fit Page /
  Fit Selection / PCB Fit Board / Toggle Grid / Toggle Units / Resources Panel / Agent Panel /
  Problems / Reset Layout
  Place: Component… / Wire(disabled) / Net Label(disabled) / Power Symbol(disabled) /
  No Connect(disabled) / Cancel Tool
  Design: Run Electrical Check / PCB Check / Project Settings / Assign Footprints…(disabled)
  Tools: Stop Run / Component Library / Agent Settings / Preferences… / Diagnostics…
  Help: Getting Started / Keyboard Shortcuts / Documentation / About MoTA
- A22：在 Agent 输入框键入 `awfr`，画布未进入放置、视图与网格状态不变。
- A06：Esc 取消 ghost，`persisted_version` 不变。
- A26：Problems 默认隐藏；状态栏显示 `Problems 1`，点击打开、再次点击关闭。
- `Fit Content / Page / Selection / Board` 均可执行且互不覆盖。
- 状态栏计数随检查结果更新。
- `Fit Content` 排除页面/标题栏；`Fit Page` 使用页面矩形；小电路最大放大 12×。

## R5 图标与应用包

修改：

- 图标资产：`src/mota/desktop/assets/`（PNG 16–1024、`Mota.icns`、`Mota.ico`）。
- `src/mota/desktop/resources.qrc` + 编译产物 `mota_resources_rc.py`。
- `src/mota/desktop/icon.py`：`QIcon.isNull` 与实际 `pixmap(64,64)` 双重校验，失败抛错。
- `main.py`：应用与主窗口图标、`MoTA` 展示名、标题“工程名 — MoTA”。
- `welcome.py`：启动页品牌图标。
- `scripts/mota_desktop_entry.py`：同一可执行文件支持 GUI 与 `engine --stdio`。
- `src/mota/desktop/bridge.py`：`sys.frozen` 时以 `engine --stdio` 启动引擎。
- `MoTA.spec`：macOS BUNDLE，`com.mota.desktop`，ICNS 图标。

实际验证：

```text
mota_icon: isNull False, available 1024x1024, pixmap 64x64 valid
plutil -lint Info.plist: OK
CFBundleIconFile: Mota.icns
CFBundleExecutable: MoTA
CFBundleIdentifier: com.mota.desktop
Resources/Mota.icns: Mac OS X icon, 1276497 bytes
```

打包产物启动：

- `dist/MoTA.app/Contents/MacOS/MoTA engine --stdio` 返回 `engine.hello`（protocol_version 1）。
- 启动 `.app` 主程序后进程树包含：
  `dist/MoTA.app/Contents/MacOS/MoTA engine --stdio`，说明冻结包内的引擎分支可用。

## 回归

- Python：`.venv/bin/python -m pytest -q` → 全部通过（190 项原有 + 新增
  `tests/integration/test_agent_blank_board.py`）。
- 原生：`.venv/bin/ctest --test-dir build --output-on-failure` → 7/7 通过。
- GUI 链路：`/tmp/gui_smoke.py`（真实 QProcess 引擎）→ 见 R2 输出。
- 菜单/焦点：`/tmp/gui_menu_probe.py` → 见 R4 输出。

## 补充修复：画布缩放与拖拽（2026-10-22）

已确认原因：

1. `CanvasView._pick()` 把 scene 坐标直接传给 `QGraphicsView.items(QPoint)`，而该 API 接收 viewport 像素。
   结果是点击/悬停/拖拽几乎永远命中不到对象，器件无法拖动。
2. 平移使用滚动条数值，当场景小于视口时范围为 0，拖动画布无反应；场景也没有可平移的边距。
3. 缩放最小比例被硬限制为 0.4 px/mm，大板在更小比例下滚轮缩小会反而放大；按钮缩放受“鼠标锚点”影响。
4. 空白画布没有内容边界时 `fit` 退回 1 px/mm，板框显示过小。
5. 场景每次刷新（编辑、撤销、Agent 提交）都重置视口，用户缩放/平移丢失。
6. 原理图拖拽会向 PCB 写入原理图坐标，导致封装跳到错误位置。

修改：

- `src/mota/desktop/widgets/canvas_2d.py`
  - `_pick` 改用 `mapFromScene`；点击、选择、双击定位、拖动恢复正常。
  - 平移改为 `centerOn(scene)`，支持中键与空白处左键拖动；场景增加可平移边距。
  - `zoom(factor, anchor)` 显式保持锚点；范围 0.02–240 px/mm；滚轮每步限制在 0.75–1.33；
    支持触控板捏合（`NativeGesture`）；工具栏缩放以视口中心为锚点。
  - 空内容 `Fit Content` 回退到页面矩形；移除 1 px/mm 的极端默认。
  - 平移/缩放/拖动标记为用户交互，后续场景刷新保留视口；切换工程时 `reset_view()`。
  - 加载场景前清理拖动 ghost、测量图元与 pan 状态，避免刷新时悬挂对象。
  - Esc 取消拖动/平移；拖拽仅在选择工具且画布可编辑时启动。
- `src/mota/desktop/widgets/design_view.py`
  - 原理图画布 `editable_move = False`，不再用符号坐标移动 PCB 封装。

验证（`tests/desktop/test_canvas_zoom_drag.py`，7 项全部通过）：

```text
fit scale 8.429（100×60 mm 页面）
zoom anchor error 0.066 mm（约 1 px 滚动条取整）
wheel ratio 1.197 / anchor error 0.059 mm
middle pan delta -5.56, -2.81 mm（期望 -100, -50 px / scale）
left-drag pan delta -6.95, +3.30 mm（期望 -60, +30 px / scale）
component drag -> 22.0, 7.0 mm（从 12,12 拖动 +10,+5 mm，0.25 mm 吸附）
Esc 取消拖动不产生 move_requested
场景重载后 zoom/pan 保持不变
不可编辑画布不产生板级移动
```

## 尚未完成 / 限制

- 多原理图页（A03）：当前模型一板一页，菜单禁用并解释。
- 真实外部模型在线调用：需要用户 API Key；本轮用确定性 Provider 验证 Agent 工具链与提交。
- Finder/Dock 的真实视觉检查：已校验 Info.plist 与 ICNS 文件，未代替人工桌面视觉确认。
- Windows EXE 图标：`.ico` 已随包提供，未在 Windows 主机构建。
