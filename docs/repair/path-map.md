# MoTA 真实请求路径映射

本文记录本轮修复涉及的实际文件与函数，不依据规格文档猜测目录。

## 1. 新建空白工程

```text
QAction "New Blank Board…"
  src/mota/desktop/main.py: MainWindow._menus
  → src/mota/desktop/pages/build.py: BuildPage.new_blank_board
      QInputDialog.getText
      self._pending_blank = True
  → src/mota/desktop/controllers.py: ProjectController.create
  → engine project.create
      src/mota/app/service.py: ApplicationService.cmd_project_create
      src/mota/app/service.py: ApplicationService._empty_design
      src/mota/projects/store.py: ProjectStore.create
  ← project_changed
  → BuildPage._on_project_changed（仅当 _pending_blank/_pending_template 等为用户动作）
  → ProjectController.open → project.open
  → BuildPage._on_project_opened
      persisted_version = current_revision_id
      displayed_version = None
      scene_load_state = "loading"
      _request_revision_scenes()
```

空板语义：`SchematicIR` 无符号、`CircuitIR` 无器件/网络、`BoardIR.outline == []`。

## 2. 手动放置器件

```text
ResourcePanel.add_button
  src/mota/desktop/widgets/resource_panel.py: _add_part
  → add_part_requested(asset_id)
  → src/mota/desktop/pages/build.py: BuildPage._add_part
      parts.resolve_part / parts.resolve_footprint
      _pending_place_part = part_id
  → src/mota/desktop/widgets/design_view.py: DesignViewPanel.begin_placement
  → src/mota/desktop/widgets/canvas_2d.py: CanvasView.begin_placement
      ghost QGraphicsRectItem, z=1200, 虚线
      鼠标移动吸附 snap_mm；Esc → cancel_placement（不产生 revision）
  左键点击
  → CanvasView.mousePressEvent → place_requested(world_x, world_y)
  → BuildPage._place_component
      operation_id = f"place_{uuid4}"
  → BuildPage._patch
      pending_operation_id = operation_id
      scene_load_state = "loading"
  → ProjectController.patch → engine design.patch
      src/mota/app/service.py: cmd_design_patch
      src/mota/app/design.py: DesignService.apply
          recipe=None 时使用 draft.board 作为 base_board
          src/mota/generation/board.py: rebuild
      src/mota/projects/store.py: ProjectStore.commit + 原子更新 current
  ← COMMITTED revision
  → _request_revision_scenes → scene.get
  → BuildPage._apply_scene
      rev 场景与 viewing/current revision 不匹配则丢弃
      当前视图安装后 displayed_version = revision
      scene_load_state = "idle"
  → ProjectMetaChanged → 状态栏/标题
```

## 3. 撤销 / 重做

```text
QAction Edit→Undo / Undo 按钮 / Cmd+Z
  src/mota/desktop/main.py: MainWindow._undo（文本焦点优先）
  → BuildPage._undo → ProjectController.undo → engine design.undo
  → 新的不可变 revision
  → _request_revision_scenes → _apply_scene
```

Redo 同路径，方法为 `design.redo`。

## 4. 场景显示

```text
engine scene.get
  → src/mota/desktop/controllers.py: ProjectController.scene
  → BuildPage._apply_scene
  → DesignViewPanel.set_scene
  → CanvasView.load_scene（先 cancel_placement）
  → CanvasView._build_item
```

版本/来源校验：

- `dto["design_version_key"]` 必须与 `expected_key` 一致。
- `rev:<id>` 必须等于当前查看/提交版本。
- `draft:<draft_id>:<seq>` 必须等于当前 run 的 draft 与 seq。
- 视口剔除与分块加载见 `visible_chunk_ids` / `load_chunk`（C13）。

## 5. Agent 编辑

```text
AgentPanel.send_requested
  → BuildPage.send_prompt
  → RunController.start → engine run.start
      src/mota/app/runs.py: RunService.start
      AgentRunner.run（src/mota/agent/runner.py）
  → 模型 tool call: apply_design_patch
      src/mota/agent/tools.py: ToolExecutor._apply_patch
      → DesignService.apply（与手动放置同一入口）
      → draft_seq += 1
  → run 完成 → commit draft
      原子更新 current，产生新 revision
  → GUI 收到 committed → _on_committed → _request_revision_scenes
```

`tests/integration/test_agent_blank_board.py` 使用真实 `ApplicationService`、真实 run、真实 draft 与
真实 commit 验证空白工程上的 Agent 编辑。

## 6. 菜单/快捷键/按钮共用入口

| 动作 | 菜单 | 工具栏/按钮 | 快捷键 | 业务函数 |
| --- | --- | --- | --- | --- |
| 新建空白板 | File | — | Cmd/Ctrl+N | `BuildPage.new_blank_board` |
| 保存 | File | — | Cmd/Ctrl+S | `BuildPage.save_now` |
| 撤销/重做 | Edit | Undo/Redo | Cmd/Ctrl+Z / Shift+Z | `MainWindow._undo/_redo` |
| 删除 | Edit | — | Delete | `BuildPage.delete_selected` |
| 旋转 | — | — | R | `BuildPage.rotate_selected` |
| Fit Content | View | Fit | F | `DesignViewPanel.fit_content` |
| Fit Page | View | Page | Shift+F | `DesignViewPanel.fit_page` |
| Fit Selection | View | Sel | Cmd/Ctrl+Shift+F | `DesignViewPanel.fit_selection` |
| 放置器件 | Place | Add to design | A | `BuildPage._add_part` |
| Problems | View | 状态栏计数 | Cmd/Ctrl+Shift+P | `DesignViewPanel.toggle_issues` |
| 检查 | Design | — | Cmd/Ctrl+Shift+V | `MainWindow._run_checks` |

## 7. 打包入口

```text
scripts/mota_desktop_entry.py
  argv[1] == "engine" → mota.cli.main:app（stdio JSON-RPC 引擎）
  否则 → mota.desktop.main:run_desktop

MoTA.spec
  Analysis(scripts/mota_desktop_entry.py)
  BUNDLE(icon=src/mota/desktop/assets/Mota.icns, identifier=com.mota.desktop)
```

`EngineBridge.start()` 在 `sys.frozen` 时以 `engine --stdio` 启动同一可执行文件，避免 PyInstaller
环境里 `-m mota.cli.main` 重新拉起 GUI。
