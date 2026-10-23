#!/usr/bin/env python
"""Capture real UI screenshots from the running native desktop app.

The script drives the actual Qt application and the actual engine process.
Replay-only states are explicitly named `*_replay.png` and recorded in
`docs/screenshots/manifest.json`; they are not presented as live runs.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa" if sys.platform == "darwin" else "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mota.desktop.main import MainWindow  # noqa: E402
from mota.desktop.theme.tokens import stylesheet  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
DATA = Path(os.environ.get("MOTA_CAPTURE_DATA", "/tmp/mota-capture-data"))
PASSWORD = "Passw0rd!23"
USERNAME = "demo"


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    if os.environ.get("MOTA_CAPTURE_KEEP") != "1" and DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True, exist_ok=True)
    os.environ["MOTA_DATA_DIR"] = str(DATA)
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    window = MainWindow(DATA)
    window.show()
    wait(1800)
    manifest: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "data_root": str(DATA),
        "screenshots": [],
    }

    def shot(widget, name: str, note: str, *, gl=None) -> None:
        path = OUT / name
        if gl is not None and gl.available and gl.view is not None:
            base = widget.grab()
            try:
                frame = gl.view.grabFramebuffer()
                top_left = gl.view.mapTo(widget, QPoint(0, 0))
                painter = QPainter(base)
                painter.drawPixmap(top_left, frame.scaled(
                    gl.view.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                painter.end()
            except Exception:
                pass
            base.save(str(path))
        else:
            widget.grab().save(str(path))
        manifest["screenshots"].append({"file": name, "note": note})

    # Welcome -------------------------------------------------------------
    window.stack.setCurrentWidget(window.welcome)
    wait(2200)
    shot(window, "01_welcome.png", "Welcome page with Mota entry animation")
    window.welcome.set_reduced_motion(True)
    window.welcome.start()
    wait(500)
    shot(window, "02_welcome_reduced_motion.png",
         "Reduced motion: static content, no trace animation loop")
    window.welcome.set_reduced_motion(False)
    window.auth.set_reduced_motion(False)

    # Auth ----------------------------------------------------------------
    window.stack.setCurrentWidget(window.auth)
    wait(700)
    shot(window, "03_auth_sign_in.png", "Sign in page with sample design animation")
    window.auth.show_error("The username or password is incorrect.")
    wait(200)
    shot(window, "04_auth_error.png", "Inline authentication error")
    window.auth.clear_error()

    # Login ---------------------------------------------------------------
    window.session.login(USERNAME, PASSWORD, False)
    for _ in range(60):
        wait(200)
        if window.session.profile:
            break
    if not window.session.profile:
        window.session.register(USERNAME, PASSWORD, "Demo User")
        for _ in range(60):
            wait(200)
            if window.session.profile:
                break
    if not window.session.profile:
        print("Could not establish a demo session", file=sys.stderr)
        return 1
    window.workspace.show_page("build")
    wait(1200)
    shot(window, "05_build_empty.png", "Empty Build workspace with the three templates")

    from mota.desktop.pages.build import TemplateDialog  # noqa: E402

    if window.workspace.build.templates:
        preview = TemplateDialog(window.workspace.build.templates[0], window)
        preview.show()
        wait(700)
        shot(preview, "21_template_parameter_preview.png",
             "Template parameters are previewed before any project is created")
        preview.reject()
        preview.deleteLater()
        wait(300)

    projects: dict[str, dict] = {}

    def create_project(name: str, template_id: str) -> dict:
        created: dict = {}
        window.projects.create(name, template_id, callback=lambda r: created.update(r))
        for _ in range(50):
            wait(200)
            if created:
                break
        window.workspace.build.open_project(created["project_id"])
        wait(1500)
        template = next(t for t in window.workspace.build.templates
                        if t["template_id"] == template_id)
        window.workspace.build._start_template_run(template)
        for _ in range(160):
            wait(200)
            if window.runs.state in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"):
                break
        wait(1800)
        projects[template_id] = created
        return created

    create_project("I2C Temperature Demo", "i2c_temperature")
    power = create_project("3.3V Power Demo", "power_3v3")
    led = create_project("LED Timer Demo", "led_timer")

    # LED views -----------------------------------------------------------
    window.workspace.build.open_project(led["project_id"])
    wait(1800)
    view = window.workspace.build.design_view
    view.set_view("schematic")
    wait(900)
    shot(window, "06_design_schematic.png",
         "Schematic for the current saved revision (LED timer)")
    view.set_view("pcb2d")
    wait(900)
    shot(window, "07_design_pcb2d.png",
         "PCB 2D with real tracks, vias, pads and silkscreen")
    view.set_view("pcb3d")
    wait(2200)
    shot(window, "08_design_pcb3d.png",
         "PCB 3D generated from the same revision; simplified bodies are labelled",
         gl=view.board3d)

    # Timeline / issues ---------------------------------------------------
    view.set_view("pcb2d")
    wait(500)
    view.show_issues()
    wait(400)
    window.workspace.build.agent._scroll_bottom()
    wait(300)
    shot(window, "09_run_timeline_and_checks.png",
         "Real tool cards, saved revision and the checks panel for the same version")

    # Partial power result -------------------------------------------------
    window.workspace.build.open_project(power["project_id"])
    wait(2000)
    window.workspace.build.design_view.show_issues()
    wait(500)
    shot(window, "10_partial_power_checks.png",
         "power_3v3 is REVIEW_BLOCKED because required capacitor evidence is NOT_CHECKED")

    # Protocol-replay clarification state ---------------------------------
    payload = {
        "question_id": "replay_supply_question",
        "run_id": "run_replay_fixture",
        "prompt": "What will power this board?",
        "choices": [
            {"label": "Regulated 5 V", "description": "Matches this template."},
            {"label": "Another supply", "description": "Enter voltage and source details."},
        ],
        "fields": [{"name": "supply_voltage_v"}],
        "defaults": {"supply_voltage_v": 5.0},
    }
    window.runs.run_id = "run_replay_fixture"
    window.runs.state = "WAITING_FOR_USER"
    window.runs.question.emit(payload)
    wait(500)
    shot(window, "11_clarification_replay.png",
         "PROTOCOL REPLAY fixture: clarification card and paused progress; not a live model run")
    window.runs.reset()
    window.workspace.build.agent.finish()

    # Edit to create a third revision, then view an older built revision ---
    window.workspace.build.open_project(led["project_id"])
    wait(1600)
    before_revision = window.workspace.build.current_revision_id
    window.workspace.build._patch([
        {"op": "rotate_component", "component_id": "component_j1"},
    ], "Rotated J1 for the read-only history example")
    for _ in range(60):
        wait(250)
        if window.workspace.build.current_revision_id != before_revision and \
                window.workspace.build.scene_keys.get("pcb2d", "").startswith("rev:"):
            break
    window.workspace.build.open_project(led["project_id"], "rev_000002")
    wait(2800)
    shot(window, "12_revision_readonly.png",
         "Read-only older revision; the current revision is unchanged")
    window.workspace.build.open_project(led["project_id"])
    wait(1800)

    # History -------------------------------------------------------------
    window.workspace.show_page("history")
    wait(6000)
    shot(window, "13_history.png", "History: compact project list with a live preview pane")
    window.workspace.history._set_view("grid")
    wait(2500)
    shot(window, "19_history_grid.png", "History grid view built from the same project cards")
    window.workspace.history._set_view("list")

    # Settings ------------------------------------------------------------
    window.workspace.show_page("settings")
    wait(1600)
    shot(window, "14_model_settings.png",
         "AI connections: saved profiles, redacted key, model behavior and test suites")

    def add_deepseek_profile() -> None:
        state: dict = {}

        def loaded(result, error):
            state["config"] = (result or {}).get("config")

        window.bridge.call("config.get", {}, loaded)
        for _ in range(40):
            wait(150)
            if state.get("config"):
                break
        cfg = state.get("config") or {}
        changed: dict = {}

        def saved(result, error):
            changed["config"] = (result or {}).get("config")

        window.bridge.call("config.update", {
            "expected_config_version": cfg.get("config_version", 1),
            "changes": {"profiles": [{
                "profile_id": "deepseek-main", "name": "DeepSeek",
                "provider": "deepseek", "api_format": "chat_completions",
                "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                "stream": True, "thinking_mode": "disabled", "thinking_effort": None,
                "max_output_tokens": 8192, "request_timeout_seconds": 120.0,
                "strict_tool_schema": False, "supports_vision": True}]},
            "idempotency_key": "capture_deepseek"}, saved, 30000)
        for _ in range(40):
            wait(150)
            if changed.get("config"):
                break
        window.workspace.settings.load()
        for _ in range(40):
            wait(150)
            if any(p["profile_id"] == "deepseek-main"
                   for p in window.workspace.settings.profiles):
                break
        for row, profile in enumerate(window.workspace.settings.profiles):
            if profile["profile_id"] == "deepseek-main":
                window.workspace.settings.profile_list.setCurrentRow(row)

    add_deepseek_profile()
    wait(1200)
    shot(window, "18_ai_connections_deepseek.png",
         "DeepSeek connection: provider and API format are independent; suites below")

    # Memory --------------------------------------------------------------
    window.workspace.memory.set_project(led["project_id"])
    window.workspace.show_page("memory")
    wait(1200)
    window.workspace.memory.layer_list.setCurrentRow(2)
    wait(900)
    shot(window, "15_memory_experience.png",
         "Experience layer entry with project scope and verified evidence")

    # 1280x800 standard and compact ---------------------------------------
    window.resize(1280, 800)
    window.workspace.show_page("build")
    window.workspace.build.open_project(led["project_id"])
    window.workspace.build.design_view.set_view("pcb2d")
    wait(2200)
    shot(window, "16_build_1280x800.png",
         "1280x800 workspace: left resources, centre design, right agent")
    window.resize(1180, 760)
    wait(1800)
    shot(window, "17_build_compact.png",
         "Compact layout: resources and agent move behind the header toggles; canvas keeps "
         "priority")
    window.resize(1020, 720)
    wait(1400)
    window.workspace.build.toggle_agent()
    wait(700)
    shot(window, "20_build_narrow_agent_overlay.png",
         "Narrow layout: agent opens as an overlay with the input and its draft intact")

    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"Wrote {len(manifest['screenshots'])} screenshots to {OUT}")
    window.bridge.shutdown()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
