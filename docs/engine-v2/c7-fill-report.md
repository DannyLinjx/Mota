# C7 填铜、物理连通与独立 DRC 实测报告

生成方式：实际调用 `mota.verification.service.verify_board()` → 原生 `mota-kernel job.run(verify)`。
板框 20×20 mm、两层 FR4；GND 区域填充，N 网络两个焊盘经一条走线与一个通孔连接，M 网络为独立对照走线。

## 1. 干净板

- report: **READY_FOR_REVIEW**（native SUCCEEDED）
- U: **0**；shorts: 0；missing endpoints: 0
- fill hash: `41017f0b36a513f4470f2dd38a9394ad69c1a9eb10739f66b42528f903a37acc`
- fill: 1 个连通分量，24 个铜矩形，4 条 thermal spoke，removed islands 0，removed area 0 nm²，kept area 286890000000000 nm²
- 耗时: fill 0.05 ms，connectivity 0.14 ms，drc 0.07 ms

| check | status | required | message |
| --- | --- | --- | --- |
| `COPPER_SHORTS` | PASS | yes | all examined objects meet the rule |
| `DRC_CLEARANCE` | PASS | yes | all examined objects meet the rule |
| `DRC_TRACK_WIDTH` | PASS | yes | all examined objects meet the rule |
| `DRC_VIA_DRILL` | PASS | yes | all examined objects meet the rule |
| `DRC_VIA_RING` | PASS | yes | all examined objects meet the rule |
| `DRC_EDGE` | PASS | yes | all examined objects meet the rule |
| `COPPER_CONNECTIVITY` | PASS | yes | all net pads are connected by real copper |
| `ZONE_FILL` | PASS | no | 1 anchored fill component(s) |
| `EXTERNAL_KICAD` | NOT_CHECKED | no | kicad-cli is not installed; no external cross-check was run |

## 2. 断线注入

- U: **1**；report: **REVIEW_BLOCKED**
- issues: NET_OPEN

## 3. 异网短接注入

- shorts: **4**；U: 0；report: **REVIEW_BLOCKED**
- issues: COPPER_SHORT

## 4. 空规则集

- report: **REVIEW_BLOCKED**；drc: **NOT_CHECKED**
- 空规则集不会被写成 PASS；`DRC_RULES` 与各必需规则保持 NOT_CHECKED。

## 5. 可选 KiCad 交叉验证

- status: **NOT_CHECKED**
- reason: kicad-cli is not installed; no external cross-check was run

本机没有运行 KiCad DRC 对比；报告不声称已完成外部交叉验证。

## 6. 填充产物

- `docs/engine-v2/c7-fill.json`：区域、实际铜矩形、thermal spoke、island 统计与 fill_hash。
