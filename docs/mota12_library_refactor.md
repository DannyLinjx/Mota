# MoTA 12：元件资源目录统一与可插拔管理 — 实施报告

日期：2026-10-23 · 依据：`Mota_12_Pluggable_Component_Library_Refactor_Trae.md`

> 契约包 `Mota_12_Catalog_Contract_Kit.zip` 未随本轮上传（工作区与附件目录均无该文件），
> 因此字段按任务书 §4–§6 的约定实现，并以运行时校验器为准；没有用
> `inventory_migration_seed.json`（同样不在附件中）充当索引或几何来源。

## 1. 实际库目录

`library_root` 可配置：`MOTA_LIBRARY_ROOT`（或 `MOTA_COMPONENT_LIBRARY`）/ 默认
`<data_root>/component_library`。当前实际值：

```
/Users/danny/Library/Application Support/Mota/component_library
  index.json                     # 自动生成，34,237 条摘要
  .catalog/
    generations/g000001..g000006/registry.json
    migration-report.json
    write.lock
  components/mota/…             # 14 个内置器件包
  components/workspace-acme/…   # 本轮真实导入的第三方示例包
  symbols/kicad/…               # 20,724 个 KiCad 符号包
  footprints/kicad/…            # 13,484 个 KiCad 封装包
  .staging/                     # 导入事务临时区（不参与检索）
```

一个真实器件目录示例（可直接查看全部文件与哈希）：

```
components/workspace-acme/acme-max3485-soic-8--8973d42974/versions/1.0.0/
  manifest.json      usage.md      parameters.json
  bindings.json      rules.json    pins.json
```

同一份源包也保存在仓库内：`docs/library_example/pack/`（符号 + 封装 + 器件三个包）。

## 2. 代码改动

| 文件 | 职责 |
| --- | --- |
| `src/mota/library/packages.py`（新增） | `PackageWrite`/manifest、`digest = sha256(manifest 原始字节)`、文件哈希清单、路径越界检查、原子写入、`iter_package_manifests` |
| `src/mota/library/registry.py`（新增） | 库根解析、generation、`index.json` 原子发布、别名表、可用性、写锁、`install_packages` |
| `src/mota/library/resolver.py`（新增） | 唯一解析入口：`search` / `inspect` / `record` / `parameter_rows` / `rule_rows` / `canonical_id` / `project_lock` / `ensure_project_assets` |
| `src/mota/library/management.py`（新增） | `install`（staging→校验→发布）、`set_enabled`、`remove`（依赖检查）、`refresh`、`gc`、`status` |
| `src/mota/library/providers.py`（新增） | `native_folder`（目录/zip）、`kicad_snapshot`（复用现有导入器）、EasyEDA 两个入口显式 `implemented=false` |
| `src/mota/library/migration.py`（新增） | 内置 14 件与 KiCad 快照迁移、断点/幂等、`migration-report.json` |
| `src/mota/catalog/service.py` | `search`/`inspect` 改为 resolver；保留旧快照兼容视图 |
| `src/mota/circuit/parts.py` | `resolve_part` / `resolve_symbol` / `resolve_footprint` 统一走 resolver，保留未迁移快照与内置定义的兼容回退 |
| `src/mota/validation/electrical.py` | 供电范围读包内 `parameters.json`；焊盘映射比较用 `canonical_id` |
| `src/mota/projects/store.py` | 提交时写 `library.lock.json` 并把用到的包 vendor 到 `assets/packages/<digest>` |
| `src/mota/app/service.py` | `catalog.inspect`/`catalog.status` 走统一解析；`project.open`/`revision.open` 先按锁恢复 |
| `src/mota/validation/reports.py` | 修复 `validate_electrical` 未定义 `profile`（顺带修复模板运行） |
| `src/mota/cli/main.py` | 新增 `library list/info/install/enable/disable/remove/refresh/gc/migrate` |
| `src/mota/desktop/widgets/resource_panel.py` | 用 `kind` 判断是否需要先绑定封装，兼容新稳定 ID |
| `tests/library/test_component_library.py`（新增） | 四项验收 |
| `tests/integration/test_agent_contract.py` | 工具数 12→13（`begin_design` 为上一轮新增） |

## 3. 迁移统计

来源：当前 `kicad-7.0.11` 快照 `3bd01972dc2209bb` 的真实 `index.json` + `objects/`
原始文件，以及 `catalog/builtin.py` 的内置定义。

| 项 | 数量 | 说明 |
| --- | --- | --- |
| KiCad 已解析 | 34,004 | 符号 20,688 + 封装 13,316 |
| unsupported | 203 | 保留真实状态，`package_mode=metadata_only`，能力只有 catalog |
| failed | 1 | `sym:kicad-7.0.11:Isolator:VOA300#2`，单独成包，不可放置 |
| KiCad 迁移包 | 34,208 | `missing_content=0` |
| 内置器件 / 符号 / 封装 | 14 / 8 / 7 | 29 个包 |
| 当前索引（generation `g000006`） | 34,240 | component 15 · symbol 20,733 · footprint 13,492 |
| 库目录体积 | 1.5 GB | 34,240 个包；payload 为缩进 JSON，可再压缩 |
| 旧 ID 对账 | 34,208 / 34,208 | 测试逐条断言别名表覆盖 |

迁移可重复执行：第二次运行 `skipped=34,124`、无冲突，耗时从 103 s 降到 1.4 s。

## 4. 身份与兼容

- 稳定 ID：`mota:component:r_1k`、`kicad:symbol:Memory_EEPROM/24LC256`、
  `kicad:footprint:Package_SO/SOIC-8_3.9x4.9mm_P1.27mm`、
  `workspace_acme:component:acme_max3485`。
- 目录名 = 可读 slug + `sha256(asset_id)[:10]`，`4xxx:4001` 与 `4xxx_IEEE:4001` 不会互相覆盖。
- 同名重复项保留源序号：`kicad:symbol:Isolator/VOA300#2`。
- 每个包的 `legacy_ids` 保存旧 `part_id` / `sym:` / `fp:`，发布时汇总成别名表；
  索引里出现两项不同目标时不会静默覆盖。

## 5. 管理命令（实际可运行）

```bash
mota library list  --kind component --namespace workspace_acme
mota library info  workspace_acme:component:acme_max3485
mota library install docs/library_example/pack
mota library disable kicad:symbol:Interface_UART/MAX3485
mota library enable  kicad:symbol:Interface_UART/MAX3485
mota library remove  workspace_acme:component:acme_max3485
mota library refresh
mota library gc --dry-run
mota library migrate [--source <旧快照根>] [--builtin-only]
```

## 6. 真实操作结果

1. **导入 + 手动放置 + Agent 放置**（`test_install_place_manual_and_agent`）
   - `management.install` 装入 3 个包，`generation` 前进一次，`rejected=0`。
   - `catalog.search("ACME MAX3485")` 命中；`catalog.inspect` 返回 8 个引脚、
     `pin_pad_map 1→1`、`capabilities` 含 `schematic_place`/`pcb_place`。
   - 手动：`design.patch` 的 `add_component` 提交成 `rev_000002`，工程可重新打开。
   - Agent：`ToolExecutor.execute("apply_design_patch", …)` 同一命令返回
     `affected_object_ids=["component_u1"]`，`parts.resolve_part` 得到真实封装与 8 个引脚。
2. **卸载后旧工程可开**（`test_uninstall_keeps_saved_project_readable`）
   - 提交时 `library.lock.json` 记录 `asset_id + version + digest`，并把包复制到
     `project/assets/packages/<digest>`。
   - `remove` 后搜索不再出现该器件、`resolve_part` 抛 `NOT_FOUND`；
     `project.open` 按锁把包复制回共享库，旧版本再次可解析。
3. **冲突与中断不污染**（`test_conflicting_and_broken_pack_do_not_publish`）
   - 重复导入同一包 → `already-installed`，generation 不变。
   - 同 ID + 同版本、manifest 不同 → `same id and version with a different digest`，不发布。
   - 包内文件哈希不符 → `PackageInvalid` 直接拒绝，旧 generation 继续可用。
4. **迁移对账**（`test_migration_covers_every_legacy_id`）：34,208 条旧 ID 全部可解析。
5. **原生窗口**（`docs/screenshots/mota12_library.png`）：左侧搜索 `MAX3485` 命中 3 项，
   第一项是导入的 `ACME MAX3485 SOIC-8`；详情显示功能描述、ACME 封装、
   符号尺寸 25.4 × 33.02 mm、能力标签（Component / Placeable / PCB placeable / Documented）
   与引脚表；底部状态为 `Catalog: 34240 assets · 3 templates`。

`pytest tests/library tests/circuit tests/schematic tests/integration tests/routing`
当前全绿（48 项集成 + 10 项库/资源 + 25 项电路/原理图/构建/布线）。

## 7. 三方 Provider 支持范围

| Provider | 状态 | 能力 |
| --- | --- | --- |
| `native_folder` | 已实现 | 目录或 zip 的标准包导入、依赖包一起装入、逐包校验 |
| `kicad_snapshot` | 已实现 | 复用现有 7.0.11 导入器，把快照转成包（`library migrate`） |
| `easyeda_standard` | 未实现 | 明确 `implemented=false`，调用会返回原因 |
| `easyeda_pro` | 未实现 | 同上；不当成 Standard JSON 解析 |

Provider 只负责枚举/转换；发布、校验和工程写入都在管理服务里。包内 `.py` 不会被扫描执行，
`assets/` 只作为资料引用。

## 8. 已知限制 / 阻塞

- 契约包的 `schemas/` 与 `inventory_migration_seed.json` 未提供，未做逐字段 JSON Schema
  校验；运行时校验覆盖 schema_version、kind、package_mode、路径越界、文件哈希和身份冲突。
- KiCad 符号包的 `assets/` 存的是「不可变源对象引用」（相对路径，`runtime_dependency=false`），
  放置所需的引脚/焊盘已复制进 `pins.json` / `pads.json`；没有把 318 MB 源文件按资产复制。
- 迁移生成的说明卡多数是 `stub`/`basic`：336 个无描述符号明写“功能说明待补充”，
  没有编造参数。
- 只有 5 个器件有手册转录的供电范围；其余多脚 IC 的供电检查仍为 `NOT_CHECKED`。
- 同一 asset 的多版本可共存于磁盘，但根索引对每个 asset 只发布一个默认版本（最新版本号）；
  CLI 暂未提供“按版本选择”的入口。
- `remove` 只删除当前版本目录；`gc` 默认 dry-run，且只回收没有任何 generation 引用的包。
- 桌面端管理操作（安装/启停/卸载按钮）本轮未加，只有 CLI 与现有左侧列表/详情；
  列表与详情已走同一 resolver 与同一 generation。
