#!/usr/bin/env python
"""Capture the workspace under Qt's 150% scale factor (DPI layout evidence)."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa" if sys.platform == "darwin" else "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mota.desktop.main import MainWindow  # noqa: E402
from mota.desktop.theme.tokens import stylesheet  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
DATA = Path(os.environ.get("MOTA_CAPTURE_DATA", "/tmp/mota-capture-data"))


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    window = MainWindow(DATA)
    window.resize(1100, 720)
    window.show()
    wait(1400)
    if not window.session.profile:
        window.session.login("demo", "Passw0rd!23", False)
        for _ in range(40):
            wait(200)
            if window.session.profile:
                break
    projects: dict = {}
    window.bridge.call("project.list", {}, lambda r, e: projects.update(r or {}))
    for _ in range(30):
        wait(200)
        if projects:
            break
    led = next((p for p in projects.get("projects", []) if p["name"] == "LED Timer Demo"), None)
    if led:
        window.workspace.show_page("build")
        window.workspace.build.open_project(led["project_id"])
        wait(2200)
        window.workspace.build.design_view.set_view("pcb2d")
        wait(1400)
    window.grab().save(str(OUT / "18_build_150pct.png"))
    scale = app.devicePixelRatio()
    manifest = {
        "file": "18_build_150pct.png",
        "note": f"Qt scale factor 1.5 (devicePixelRatio {scale}); layout, text and ruler check",
        "scale_factor": os.environ.get("QT_SCALE_FACTOR"),
        "device_pixel_ratio": scale,
        "platform": platform.platform(),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = OUT / "18_build_150pct.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {OUT / '18_build_150pct.png'} (DPR {scale})")
    window.bridge.shutdown()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
