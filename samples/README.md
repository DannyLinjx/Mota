# Mota sample projects

These bundles were exported from real local builds (regression run ids are in
`docs/reports/regression.json`). Import one with:

```bash
mota auth login
mota project import samples/power_3v3.mota --name "Imported power board"
mota project open <project_id>
```

| Template | Revision | Review status | Checks |
| --- | --- | --- | --- |
| 3.3V Power Board (`power_3v3`) | rev_000002 | REVIEW_BLOCKED | {'PASS': 17, 'FAIL': 0, 'WARN': 0, 'NOT_CHECKED': 4} |
| LED Timer Board (`led_timer`) | rev_000002 | READY_FOR_REVIEW | {'PASS': 20, 'FAIL': 0, 'WARN': 1, 'NOT_CHECKED': 1} |
| I2C Temperature Board (`i2c_temperature`) | rev_000002 | READY_FOR_REVIEW | {'PASS': 20, 'FAIL': 0, 'WARN': 1, 'NOT_CHECKED': 1} |

`power_3v3` is intentionally `REVIEW_BLOCKED`: the demo catalog has no verified
effective-capacitance/ESR evidence for its output capacitor, so two required
checks are `NOT_CHECKED`. This is a real result, not a packaging error.

Each folder contains SVG views, BOM CSV and a build report for the same revision.
