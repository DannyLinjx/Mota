# Engine v2 — C1 交付报告（通用库导入与覆盖）

日期：2026-10-21
范围：C1（`library/catalog.py`、`kicad_symbols.py`、`kicad_footprints.py`、`tests/library/test_import_snapshot.py`）。
原则：所有数字来自本机实际运行；未执行的检查不写 PASS；未实现能力单列。

---

## 1. 实际修改 / 新增文件

| 文件 | 作用 |
| --- | --- |
| `src/mota/library/sexpr.py` | 带字符偏移的 S-expression 读取器，错误可定位到 file/line/column |
| `src/mota/library/records.py` | `AssetRecord`（解析结果）、`AssetSummary`（索引）、`PinRecord`、`PadRecord`、`Diagnostic`、稳定 asset id |
| `src/mota/library/kicad_symbols.py` | packed/unpacked `.kicad_sym`、`extends` 继承、unit/body-style、字符串引脚号、unknown token / unknown electrical type 定位 |
| `src/mota/library/kicad_footprints.py` | `.kicad_mod` / legacy `module` 根、pad 类型/形状/钻孔/层/旋转、复数同号 pad、3D model 引用、unknown token |
| `src/mota/library/catalog.py` | `SourceSpec`、内容寻址快照、增量索引、`LibraryImportReport`、search/inspect/preview/rollback |
| `src/mota/library/__init__.py` | 库层公共入口 |
| `src/mota/catalog/service.py` | `search` 合并导入资产（`library_assets` / `library_total` / `library_sources`）；`inspect` 支持 `sym:`/`fp:` |
| `src/mota/app/service.py` | `library.import/status/search/inspect/preview/rollback` 六个命令；`catalog.inspect` 分派库资产 |
| `src/mota/cli/main.py` | `mota library import/status/search/inspect/preview/rollback` |
| `scripts/library_import_scan.py` | 官方快照全量扫描，输出 `docs/engine-v2/library-scan.json` |
| `tests/library/test_import_snapshot.py` | C1 验收测试（6 项） |
| `tests/fixtures/library/` | packed、unpacked、继承链、unknown token、同名不同来源、legacy、坏语法、pad 形状/重复号等样本 |
| `docs/engine-v2/library-scan.json` | 官方 9.0.0 快照的真实导入报告 |
| `docs/engine-v2/path-map.md` | 更新 C1 路径状态 |

未改动：`src/mota/catalog/builtin.py`、三个旧模板、任何 v1 工程文件。

---

## 2. 实际执行的命令

```bash
# 锁定官方快照（下载到 /tmp，不进入仓库）
curl -L --fail -o kicad-symbols-9.0.0.tar.gz \
  https://gitlab.com/kicad/libraries/kicad-symbols/-/archive/9.0.0/kicad-symbols-9.0.0.tar.gz
curl -L --fail -o kicad-footprints-9.0.0.tar.gz \
  https://gitlab.com/kicad/libraries/kicad-footprints/-/archive/9.0.0/kicad-footprints-9.0.0.tar.gz
tar -xzf kicad-symbols-9.0.0.tar.gz
tar -xzf kicad-footprints-9.0.0.tar.gz

# 全量导入 + 增量复导入
.venv/bin/python scripts/library_import_scan.py \
  --symbols /tmp/mota-kicad/kicad-symbols-9.0.0 \
  --footprints /tmp/mota-kicad/kicad-footprints-9.0.0 \
  --root /tmp/mota-kicad/mota-data \
  --out docs/engine-v2/library-scan.json \
  --store-content

# C1 测试
.venv/bin/python -m pytest tests/library/test_import_snapshot.py -q
```

`--store-content` 把源文件按 SHA-256 存入 `catalog/library/<source_id>/objects/`，因此即使源目录被修改或删除，历史快照仍可 inspect/preview。

---

## 3. 输入样本

### 3.1 官方锁定快照

| 源 | 版本 | 文件数 | 许可 |
| --- | --- | --- | --- |
| `kicad-symbols-9.0.0` | 9.0.0 | 224 个 `.kicad_sym` | CC-BY-SA-4.0 WITH KiCad-Library-Exception |
| `kicad-footprints-9.0.0` | 9.0.0 | 14,216 个 `.kicad_mod` | 同上 |

许可来源：<https://www.kicad.org/libraries/license/>（写入 `manifest.json` 与 `library-scan.json`）。

### 3.2 本地回归 fixture

- `packed/Device.kicad_sym`：R、双单元 DualAmp（unit 1/2、body style 1/2、字符串引脚 A1/B1）、`extends` 派生、unknown electrical type、unknown token、无引脚 Logo、重复定义 R。
- `unpacked/Custom.kicad_sym`：单文件 unpacked 库，符号名带 `Custom:` 前缀。
- `source_a` / `source_b`：同名 `Shared`，内容不同，来自两个 source。
- `footprints/MyParts.pretty`：正常 0603、未知 pad 形状/类型 + 重复 pad 号 + unknown token、无 pad 图形封装。
- `legacy/Old.lib`：KiCad 5 旧格式。
- `broken/Broken.kicad_sym`：未闭合括号。

---

## 4. 测试结果

```
$ .venv/bin/python -m pytest tests/library/test_import_snapshot.py -q
......                                                                   [100%]
6 passed

$ .venv/bin/python -m pytest -q
119 passed, EXIT=0
```

C1 阶段全量为 95 项；C2/C3/C4 完成后为 119 项，均 0 失败（见 `test-results.txt`）。

覆盖点：

| 测试 | 断言 |
| --- | --- |
| `test_packed_symbols_inheritance_and_unknown_semantics` | 6 discovered = 4 parsed + 1 unsupported + 1 failed；继承后 pins/units/styles 正确；unknown electrical/token 有 file+line；重复定义生成 `#2` failed；mapped=3 |
| `test_reimport_incremental_change_rollback_and_objects` | 二次导入 `files_reused=1`、`bytes_parsed=0`、`unchanged=6`；CAS 字节与源文件一致；改内容后 `changed≥1`；rollback 恢复旧快照内容 |
| `test_unpacked_legacy_broken_and_same_name_sources` | 前缀剥离；legacy `LEGACY_LIB_FORMAT`；坏语法 `SYNTAX_ERROR` 带行列；同名不同 source 得到两个不同 asset id |
| `test_footprints_pads_unknown_shapes_and_on_demand_preview` | pad 数量/3D model/重复 pad 号/未知形状与类型；`rendered=0`，preview 实际按需生成 SVG |
| `test_service_catalog_surfaces_and_cli` | `catalog.search` 返回 `library_assets`；`catalog.inspect` 识别 `sym:`；`library.*` 六个命令；CLI `library status/search` 可运行；未登录 import 抛 `AUTH_REQUIRED` |
| `test_invalid_sources_do_not_replace_active_snapshot` | 缺失/空/格式不匹配都不切换 current；未知 asset 抛 `LIBRARY_ASSET_NOT_FOUND` |

---

## 5. 官方快照实测（`docs/engine-v2/library-scan.json`）

| 指标 | kicad-symbols 9.0.0 | kicad-footprints 9.0.0 |
| --- | --- | --- |
| discovered | 21,806 | 14,216 |
| parsed | 21,771 | 14,047 |
| unsupported | 35（全部 `NO_PINS`，图形符号） | 169（全部 `NO_PADS`，纯图形封装） |
| failed | 0 | 0 |
| coverage 恒等式 | 21,806 = 21,771 + 35 + 0 | 14,216 = 14,047 + 169 + 0 |
| mapped（结构性） | 21,771 | 13,979 |
| 首次导入耗时 | 57.4 s | 37.4 s |
| 增量复导入 | 0.65 s（224/224 文件复用） | 3.11 s（14,216/14,216 文件复用） |
| 扫描字节 | 219,889,181 B | 183,360,838 B |
| 进程峰值 RSS | 689 MB（两源同一进程，取进程最大值，不按源分摊） | 同左 |

补充诊断（footprints）：

| 诊断码 | 次数 | 含义 |
| --- | --- | --- |
| `UNNUMBERED_NON_NPTH_PAD` | 13,665 | 机械/安装 pad 无数号；不阻塞编号 pad 的映射 |
| `DUPLICATE_PAD_NUMBERS` | 2,530 | 同号多 pad（堆叠 pad），已记录但不视为错误 |
| `UNKNOWN_TOKEN` | 1,031 | 未建模的制造/电气字段，见下节 |

`UNKNOWN_TOKEN` 的实际 token 分布（独立扫描 14,216 个封装）：`solder_paste_margin`、`solder_mask_margin`、`solder_paste_margin_ratio`、`clearance`、`zone_connect`、`dimension`、`die_length`、`solder_paste_ratio`。这些字段已保留原始值但**未进入** BoardIR，相关检查必须保持 `NOT_CHECKED`。

未映射的 68 个封装：没有编号 pad，或编号 pad 使用了未支持的形状/类型。它们不会被计为“可映射”。

---

## 6. 失败项与已知限制

1. 官方快照 0 个语法失败；失败路径由 fixture 覆盖（`SYNTAX_ERROR`、legacy、重复定义）。没有把 fixture 结果冒充实盘统计。
2. 符号只建模 pin / unit / body-style / property，**未建模**图形几何（rectangle/polyline/arc 等）；原理图预览仅画引脚。
3. 封装只建模 pad 与 model 引用，**未建模** `fp_line/fp_arc/zone` 图形与阻焊/助焊层余量、clearance、zone_connect；这些 token 以 `UNKNOWN_TOKEN` 定位。
4. C1 的 `mapped` 是“结构可映射”（有编号 pad、类型/形状受支持），**不是** symbol pin ↔ footprint pad 的最终映射；配对与自动合成属于 C2。
5. 索引是完整 JSON（symbols 20 MB、footprints 15 MB），每次 import 重写全量索引；没有 SQLite/FTS，也没有分片写入。搜索为线性扫描（21k+14k 行），未做基准压测。
6. `inspect/preview` 会按需重解析所在源文件（Device 单文件 0.34 s）；没有按文件 LRU 缓存。
7. 进程峰值 RSS 约 689 MB；这是 Python 导入层的实测值，不是 C++ worker 数据，也未做内存优化。
8. 预览是几何近似 SVG（引脚线/矩形 pad），不是 KiCad 图形还原。
9. 同名不同源已用 `source_id` 区分；同一 `source_id` 下不同文件内容冲突会变成 failed `#N`，但跨文件 `extends` 不解析（KiCad 要求同库继承，当前库边界 = 文件）。
10. `auto_synthesis_ready` 在 C1 明确为 `null`（未评估），没有用 0 冒充结果。
11. 回归环境观测到同机连续构建时 led_timer 的墙钟从 4–8 s 膨胀到 50–70 s（CPU time 与 wall time 同步增长，线程数保持 1，未见线程泄漏）。测试通过 `MOTA_ROUTE_BUDGET_SECONDS=300` 给预算留余量；生产默认仍是 30 s。超时会被明确标成 UNKNOWN，不与“无解”混淆。

---

## 7. 回滚方式

- 单个来源回滚：`mota library rollback --source-id <id>`，只改 `catalog/library/<id>/current.json` 指向上一个 `snapshot_id`。
- 彻底移除：删除 `catalog/library/<id>/`；不影响用户工程、v1 revision 或内置 catalog。
- 导入失败（缺路径、空目录、格式不匹配、解析异常）不会写 `current.json`，旧快照保持活动。
- `catalog.search` / `catalog.inspect` 对内置器件的返回结构保持不变；新增字段是追加，不移除旧键。

---

## 8. 与后续阶段的边界

- C1 已做到：新资源无需改源码即可**检索、查看、按需预览**，并有稳定 asset id、内容 hash、增量索引与可回滚快照。
- C1 **未做到**：把导入资源直接放进工程、自由连网、换封装、导出。这需要 C2 的 `PartDef` / `ComponentInstance` 与映射校验，不能在本阶段宣称完成。
- 没有新增电路模板来替代引擎能力；三个旧模板仍只作为回归样例。
