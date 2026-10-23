# MoTA 画板链路审计（R0）

日期：2026-10-22
范围：现有 Mota 工程的空白工程、手动放置、Agent 编辑、保存/显示版本一致性。
证据来源：源码、真实引擎进程、PySide6 GUI（offscreen 平台）、持久工程文件。

## 1. 已复现的最小链路

在真实 GUI（QApplication + QMainWindow + EngineBridge 的真实 QProcess 引擎）中执行：

1. `File → New Blank Board…` → 新 `project_id`，`rev_000001`，画布 4 个空场景图元（页面/标题栏/网格），无器件。
2. 左侧选中 `r_10k` → `Add to design` → 画布出现 ghost。
3. 在画布点击 → `design.patch` 提交 → `rev_000002`，`displayed_version == persisted_version`。
4. `Edit → Undo` → `rev_000003`；`Edit → Redo` → `rev_000004`。
5. 关闭窗口与引擎，重启 GUI + 引擎并打开同一工程：
   `rev_000004`，画布 11 个图元，1 个器件对象，`scene_load_state = idle`。
6. 独立登录同一数据根目录读取持久工程：
   `components=1, footprints=1, pads=2, outline=0, symbols=1`。

命令：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -u /tmp/gui_smoke.py
```

实际输出：

```text
blank project: project_ebecc1e016e61da5 rev rev_000001 objects 4
placed revision: rev_000002 displayed: rev_000002 canvas objects: 11 component objects: 7
undo -> rev_000003 redo -> rev_000004
disk revision: rev_000004 components: 1 footprints: 1 pads: 2 outline: 0 symbols: 1
restored revision: rev_000004 canvas objects: 11 component objects: 7 state: idle
```

## 2. 已确认的根因

### 2.1 空白工程无法添加器件（P0）

- 失败环节：`design.patch` → `mota.app.design.DesignService.apply`。
- 已确认行为：`apply` 在 `recipe is None` 时抛出 `NotFound("TEMPLATE_NOT_FOUND")`，而空白工程没有模板实例。
- 影响：新建空白工程可以保存，但任何 `add_component` 都返回模板错误，GUI 只显示错误，不产生 revision。
- 修复：
  - `DesignService.apply` 允许 `recipe=None`，并使用 `draft.board` 作为 `base_board` 重建，保留空白板尺寸与空板框。
  - `rebuild()` 接受 `base_board`，不再要求模板提供板框。
  - `_empty_design()` 的 `BoardIR` 使用 `outline=[]`，空白板不再伪造板框。

### 2.2 画布把页面/标题栏算进内容范围（P1）

- 失败环节：`CanvasView.fit()`。
- 已确认行为：旧的适配范围包含页面矩形与标题栏，导致一个电阻或空板被缩到很小。
- 修复：`content_bounds()` 排除 `page/title_block/title_text/title_meta/frame`，并把工具栏拆成
  `Fit Content (F)`、`Fit Page (Shift+F)`、`Fit Selection`、`PCB Fit Board`。

### 2.3 “添加器件”没有预览与真实落点（P1）

- 已确认行为：左侧 `Add to design` 之前直接触发一次添加，没有 ghost、没有落点，也不校验符号/封装；
  失败时用户只看到状态变化。
- 修复：
  - `CanvasView.begin_placement()` 进入 PREVIEW，ghost 随鼠标吸附网格，Esc 取消且不产生 revision。
  - 点击后由 `BuildPage._place_component()` 生成独立 `operation_id`（`place_<uuid>`）并调用
    `ProjectController.patch`。
  - `_add_part()` 先校验 `symbol_id` 与 footprint courtyard，缺失时报 `SYMBOL_UNAVAILABLE`/`FOOTPRINT_UNASSIGNED`。

### 2.4 保存版本与显示版本没有分开建模（P1）

- 已确认行为：GUI 只有 `current_revision_id`，场景回包晚于提交时无法表达“已保存但画布未更新”。
- 修复：`BuildPage` 持有
  `persisted_version / displayed_version / displayed_target / pending_operation_id / scene_load_state`；
  `_apply_scene()` 只在当前视图安装成功后更新 `displayed_version`；
  `_emit_meta()` 输出“正在添加器件… / 设计已保存 · 画布更新中 / 已保存并显示”等真实状态。

### 2.5 Agent 与手动编辑未共用空白板路径（P0）

- 已确认行为：Agent 的 `apply_design_patch` 同样走 `DesignService.apply`，因此修复前在空白工程上也会
  得到 `TEMPLATE_NOT_FOUND`。
- 修复后验证：`tests/integration/test_agent_blank_board.py` 以真实 run、真实 draft、真实 commit 驱动
  Agent 工具 `apply_design_patch`，在空白工程提交 `R1`，持久 revision 中
  `components=["R1"]`、`footprints=1`，事件包含 `design.patch_applied`。

## 3. 模板行为

- 启动只进入欢迎页/工作区空状态，不实例化模板，不自动调用模型。
- 左侧 `Templates` 标签只列目录；单击模板只更新详情文本，不写当前工程、不改变版本。
- `Use template / 使用此模板` 创建新的 `project_id`，并记录 `template_id`，不覆盖当前工程。
- 未知需求不会自动落到 LED Timer：`AgentRunner` 只在模型显式调用 `prepare_design(template_id=...)`
  时使用模板；`get_template` 对未知 id 抛 `TEMPLATE_NOT_FOUND`，没有默认回退分支。

## 4. 菜单与命令

`MainWindow._menus()` 建立真实 `QMenuBar/QMenu/QAction`：File、Edit、View、Place、Design、Tools、Help。
每个动作连接到已有业务方法（`BuildPage`、`DesignViewPanel`、`ProjectController`、`SessionController`），
未实现的入口禁用并给出原因（New Schematic Sheet、Wire、Net Label、Power、No Connect、Duplicate、
Select All、Assign Footprints）。

快捷键按焦点路由：

- 文本控件有焦点时 Undo/Redo/Cut/Copy/Paste/Delete 只作用于文本。
- 画布有焦点时 A/W/F/Shift+F/R/1/2/3 才触发画布命令。
- `Esc` 先取消放置，再回到选择工具。

## 5. 剩余问题

- 多原理图页（A03）尚未实现：当前数据模型一块板对应一张原理图，菜单项禁用并解释。
- 真实模型在线调用需用户 API Key；本轮验证了 Agent 工具→领域服务→提交的真实路径（使用确定性测试
  Provider），未验证外部模型网络调用。
- Gerber/外部制造校验仍为 NOT_CHECKED（沿用 04 的边界）。
