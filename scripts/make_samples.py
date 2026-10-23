#!/usr/bin/env python
"""Export three real sample projects from the regression workspace."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mota.app.service import ApplicationService  # noqa: E402
from mota.app.state import SessionContext  # noqa: E402

DATA = Path(os.environ.get("MOTA_SAMPLE_DATA", "/tmp/mota-regression"))
OUT = ROOT / "samples"


def main() -> int:
    service = ApplicationService(DATA)
    result = service.dispatch("auth.login", {
        "username": "regression", "password": "MotaRegression!2026"}, None)
    info = result["session"]
    session = SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                             username=info["username"], epoch=1)
    cards = service.dispatch("project.list", {}, session)["projects"]
    OUT.mkdir(parents=True, exist_ok=True)
    names = {"power_3v3": "3.3V Power Board", "led_timer": "LED Timer Board",
             "i2c_temperature": "I2C Temperature Board"}
    rows = []
    for template_id, title in names.items():
        card = next((c for c in cards if c["name"] == f"Regression {template_id} 1"), None)
        if card is None:
            print(f"missing sample project for {template_id}", file=sys.stderr)
            return 1
        opened = service.dispatch("project.open", {"project_id": card["project_id"]},
                                  session)
        revision = opened["current_revision_id"]
        folder = OUT / f"{template_id}_sample"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        exports = {}
        for fmt in ("mota", "svg", "bom", "report"):
            exported = service.dispatch("project.export", {
                "project_id": card["project_id"], "revision_id": revision,
                "format": fmt, "destination_path": str(folder)}, session)
            exports[fmt] = exported["files"]
        bundle = next((f for f in exports["mota"]
                       if f["path"].endswith(".mota")), None)
        if bundle:
            source = folder / bundle["path"]
            renamed = OUT / f"{template_id}.mota"
            if renamed.exists():
                renamed.unlink()
            shutil.copy2(source, renamed)
        rows.append({
            "template_id": template_id,
            "title": title,
            "project_id": card["project_id"],
            "revision_id": revision,
            "review_status": opened["checks"]["review_status"],
            "checks": opened["checks_summary"],
            "folder": folder.name,
            "bundle": f"{template_id}.mota",
        })
        print(f"{template_id}: {revision} {opened['checks']['review_status']} -> {folder}")
    readme = [
        "# Mota sample projects",
        "",
        "These bundles were exported from real local builds (regression run ids are in",
        "`docs/reports/regression.json`). Import one with:",
        "",
        "```bash",
        "mota auth login",
        "mota project import samples/power_3v3.mota --name \"Imported power board\"",
        "mota project open <project_id>",
        "```",
        "",
        "| Template | Revision | Review status | Checks |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        readme.append(f"| {row['title']} (`{row['template_id']}`) | {row['revision_id']} | "
                      f"{row['review_status']} | {row['checks']} |")
    readme += [
        "",
        "`power_3v3` is intentionally `REVIEW_BLOCKED`: the demo catalog has no verified",
        "effective-capacitance/ESR evidence for its output capacitor, so two required",
        "checks are `NOT_CHECKED`. This is a real result, not a packaging error.",
        "",
        "Each folder contains SVG views, BOM CSV and a build report for the same revision.",
    ]
    (OUT / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(f"Wrote samples to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
