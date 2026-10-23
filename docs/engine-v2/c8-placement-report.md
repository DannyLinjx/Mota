# C8 约束驱动布局实测报告

生成方式：实际调用 `mota.placement.service.place_board()` → 原生 `mota-kernel job.run(place)`。
板框 40×30 mm，J1 锁定；U1/C1 可移动；keepout 位于板中央。

## 1. 合法布局

- status: **SUCCEEDED**（native SUCCEEDED）
- semantic hash: `d5cc75feeffac153bd5c8ba7df5e876c74be09a9876545ac2f4325d31ea8b4c0`
- wirelength: 15740000 nm；escape: 0.473
- search nodes: 2；search_exhausted: False
- congestion: 20×15 cells，max=2.03，mean=0.024

| component | fixed | x_nm | y_nm | rotation |
| --- | --- | --- | --- | --- |
| c_c1 | no | 4040000 | 12160000 | 90 |
| c_j1 | yes | 5000000 | 5000000 | 0 |
| c_u1 | no | 8580000 | 12390000 | 0 |

J1 保持 (5.0, 5.0) mm；U1/C1 避开 keepout `[12,4]–[20,16] mm`。

## 2. 固定 seed 重放

- 第一次 hash: `d5cc75feeffac153bd5c8ba7df5e876c74be09a9876545ac2f4325d31ea8b4c0`
- 第二次 hash: `d5cc75feeffac153bd5c8ba7df5e876c74be09a9876545ac2f4325d31ea8b4c0`
- patch 完全一致: yes

## 3. 硬约束与预算

C++ 测试覆盖：固定件重叠 → `INFEASIBLE / FIXED_OVERLAP`；固定件在 keepout → `FIXED_IN_KEEPOUT`；整板 keepout → `NO_LEGAL_PLACEMENT`；keepout 夹在两器件之间时线长收益不能换取穿越；移动代价使增量修复优先保留原位。

- `max_search_nodes=0` → **UNKNOWN**（不是 INFEASIBLE）
- `max_search_nodes=1` → **PARTIAL**，未放置：['U2', 'U3']

## 4. 产物

- `docs/engine-v2/c8-placement.json`：patch、congestion、semantic hash。
