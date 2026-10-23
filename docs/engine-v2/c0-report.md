# Engine v2 — C0 交付报告（审计、合同、迁移与基线）

日期：2026-10-21
范围：C0（审计、`engine_v2` 合同、v1→v2 可回滚迁移、schema、三模板基线）。
原则：数值来自实际运行；未执行的检查保持 `NOT_CHECKED`；不把计划写成结果。

---

## 1. 实际修改 / 新增文件

| 文件 | 作用 |
| --- | --- |
| `docs/engine-v2/audit.md` | 真实代码审计：器件白名单、模板、两层假设、网表、路由成功判断及文件位置 |
| `docs/engine-v2/path-map.md` | v1 → v2 模块、身份、单位、检查状态、磁盘布局与错误码映射 |
| `src/mota/engine_v2/contracts.py` | `JobRequest` / `JobResult` / `JobBudget`；`SUCCEEDED/PARTIAL/INFEASIBLE/UNKNOWN/CANCELLED/FAILED` |
| `src/mota/engine_v2/schema.py` | `Stackup`、`PhysicalTerminal`、`ComponentV2`、`HierarchyIR`、`CircuitV2`、`SchematicV2`、`BoardV2`、`ConstraintIR`、`EngineDocumentV2` |
| `src/mota/engine_v2/migration.py` | v1→v2 派生迁移、语义哈希、错误码、回滚 |
| `src/mota/engine_v2/__init__.py` | 公共导出 |
| `scripts/engine_v2_baseline.py` | 生成三模板基线并校验 `semantic_hash_v1 == semantic_hash_v2` |
| `scripts/export_engine_v2_schema.py` | 导出 `schemas/engine-v2.json` |
| `docs/engine-v2/baseline/*.json` | 三模板基线（输入参数、hash、数量、检查状态） |
| `schemas/engine-v2.json` | 31 个 definition |
| `tests/migration/test_v1_baseline.py` | C0 回归与迁移测试 |

---

## 2. 实际执行的命令

```bash
.venv/bin/python scripts/engine_v2_baseline.py
.venv/bin/python scripts/export_engine_v2_schema.py
.venv/bin/python -m pytest tests/migration/test_v1_baseline.py -q
```

---

## 3. 输入样本与基线

| 模板 | 参数来源 | 结果 |
| --- | --- | --- |
| `power_3v3` | 模板默认参数 | PARTIAL / REVIEW_BLOCKED；PASS 17、FAIL 0、WARN 0、NOT_CHECKED 4；tracks 8、vias 0 |
| `led_timer` | 模板默认参数 | SUCCEEDED / READY_FOR_REVIEW；PASS 20、FAIL 0、WARN 1、NOT_CHECKED 1；tracks 19、vias 2 |
| `i2c_temperature` | 模板默认参数 | SUCCEEDED / READY_FOR_REVIEW；PASS 20、FAIL 0、WARN 1、NOT_CHECKED 1；tracks 13、vias 0 |

每个基线文件记录：`design_hash`、`review_status`、检查计数、器件/网络/pad/track/via 数量、逐项检查状态、v1 语义签名与 semantic hash。

`power_3v3` 的 `NOT_CHECKED` 是输出电容有效容量/ESR 证据缺失导致的真实阻塞，不是 PASS。

---

## 4. 测试结果

```
$ .venv/bin/python -m pytest tests/migration/test_v1_baseline.py -q
......                                                                   [100%]
6 passed
```

覆盖点：

1. 三模板的 design hash、review、计数、对象数量、逐项检查状态与基线完全一致。
2. v2 迁移后 `semantic_hash(v1) == semantic_hash(v2)`；stackup 层序与总厚等于 v1 板厚。
3. `physical_terminals` 与 v1 `pin_pad_map` 逐项一致；跨视图 ID 一致。
4. 迁移是派生且幂等：v1 五个源文件 hash 不变；`MIGRATED → ALREADY_MIGRATED → ROLLED_BACK → 再迁移` 得到相同 `document_hash`。
5. 错误输入（未知字段、类型错误、悬空引用、非 2 层）返回明确错误码，且不创建 `engine_v2/`、不改旧 revision。
6. `JobResult` 中 `UNKNOWN` 与 `INFEASIBLE` 保持不同状态；`DesignVersion` 约束被校验。

---

## 5. 迁移行为与回滚

- 输出目录：`revisions/<revision_id>/engine_v2/{engine_v2.json,migration.json}`。
- `current.json` 仍是唯一提交点；v2 目录不参与 v1 提交事务。
- 迁移完成前复核源文件 hash；若变化则删除 staging 并报 `MIGRATION_SOURCE_CHANGED`。
- 回滚：`rollback_revision(store, project_id, revision_id)` 删除派生目录；v1 文件字节不变。
- 未支持能力：阻抗/延迟/介质材料未建模，相关检查保持 `NOT_CHECKED`；v2 不重算 v1 路由结果，连通性仍由几何检查独立判定。

---

## 6. 已知限制

- C0 只覆盖 2 层板迁移；`layer_count != 2` 明确拒绝，不做静默近似。
- 未知顶层字段不被透传，迁移报 `MIGRATION_UNSUPPORTED_FIELD`。
- 基线依赖模板默认参数；`led_timer` 是路由最重的样例，测试环境负载高时会在墙钟预算处超时（UNKNOWN），测试已把该情况与设计回归区分开。
- C0 不实现自由器件编辑、层次/总线、多层路由；这些属于 C2/C3 与后续阶段。
