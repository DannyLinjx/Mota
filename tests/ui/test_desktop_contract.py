from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mota.desktop.controllers import RunController  # noqa: E402
from mota.desktop.widgets.canvas_2d import CanvasView  # noqa: E402
from mota.desktop.widgets.rulers import nice_step  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    # Destroy test widgets while the offscreen platform plugin is still alive;
    # leaving them to interpreter finalisation segfaults after the test session.
    for widget in list(instance.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    instance.sendPostedEvents(None, QEvent.DeferredDelete)
    instance.processEvents()


class FakeBridge:
    def __init__(self, epoch: int) -> None:
        self.epoch = epoch
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params, callback=None, timeout_ms=0):
        self.calls.append((method, params))
        return "req"


def test_run_controller_discards_old_epoch_and_duplicate_sequences(app):
    bridge = FakeBridge(epoch=5)
    controller = RunController(bridge)
    seen = []
    controller.run_event.connect(seen.append)
    base = {"run_id": "run_1", "project_id": "p1", "draft_id": "draft_1",
            "type": "run.state_changed", "payload": {"state": "ROUTING"}}
    controller.handle_event(dict(base, session_epoch=4, seq=1))
    assert seen == []
    controller.handle_event(dict(base, session_epoch=5, seq=1))
    assert len(seen) == 1
    controller.handle_event(dict(base, session_epoch=5, seq=1))
    assert len(seen) == 1
    controller.handle_event(dict(base, session_epoch=5, seq=2,
                                 type="run.state_changed",
                                 payload={"state": "VALIDATING_BOARD"}))
    assert len(seen) == 2
    assert controller.state == "VALIDATING_BOARD"


def test_canvas_world_to_screen_and_back_is_stable(app):
    view = CanvasView()
    view.resize(900, 620)
    view.show()
    dto = {
        "bounds": {"x": 0, "y": 0, "width": 100, "height": 60},
        "objects": [
            {"id": "board", "kind": "polygon",
             "points": [[0, 0], [100, 0], [100, 60], [0, 60], [0, 0]],
             "color": "#D7E2EA", "fill": "#173E35", "width": 0.3},
            {"id": "pad", "kind": "pad", "x": 12.5, "y": 8.25, "w": 1.6,
             "h": 1.6, "color": "#D68C6A", "fill": "#D68C6A",
             "meta": {"shape": "circle"}},
        ],
    }
    view.load_scene(dto)
    view.fit()
    tolerance_mm = 0.6 / view.transform().m11()
    for point in (QPointF(12.5, -8.25), QPointF(100, 0), QPointF(0, -60)):
        screen = view.mapFromScene(point)
        back = view.mapToScene(screen)
        assert back.x() == pytest.approx(point.x(), abs=tolerance_mm)
        assert back.y() == pytest.approx(point.y(), abs=tolerance_mm)
    before = view.transform().m11()
    view.zoom(1.5)
    assert view.transform().m11() > before
    scene_point = QPointF(20, -10)
    screen_before = view.mapFromScene(scene_point)
    view.mapToScene(screen_before)
    # Zooming does not change stored geometry, only the view transform.
    assert view._bounds[2] == 100


def test_auth_busy_state_uses_the_right_form(app):
    from mota.desktop.pages.auth import AuthPage

    page = AuthPage()
    page.set_busy(True, "register")
    assert page.register_button.text() == "Creating account…"
    assert page.login_button.text() == "Sign in"
    assert not page.register_button.isEnabled()
    assert not page.switch_to_login.isEnabled()
    page.show_login_form()
    assert page.stack.currentIndex() == 0  # busy forms cannot be switched away
    page.set_busy(False, "register")
    assert page.register_button.text() == "Create account"
    assert page.register_button.isEnabled()
    page.show_login_form()
    assert page.stack.currentIndex() == 0
    page.set_busy(True, "login")
    assert page.login_button.text() == "Signing in…"
    assert page.register_button.text() == "Create account"
    page.set_busy(False, "login")
    assert page.login_button.text() == "Sign in"


def test_ruler_steps_follow_one_two_five():
    for pixels_per_mm in (0.5, 1.0, 2.5, 6.0, 12.0):
        step = nice_step(200.0, pixels_per_mm)
        mantissa = step / (10 ** __import__("math").floor(
            __import__("math").log10(step)))
        assert mantissa == pytest.approx(1.0, abs=1e-6) or \
            mantissa == pytest.approx(2.0, abs=1e-6) or \
            mantissa == pytest.approx(5.0, abs=1e-6)
        assert step * pixels_per_mm >= 40


class RecordingBridge:
    def __init__(self, epoch: int = 1) -> None:
        self.epoch = epoch
        self.pending: list[tuple[str, dict, object]] = []

    def call(self, method, params, callback=None, timeout_ms=0):
        self.pending.append((method, params, callback))
        return "req"


def _part_payload(mpn: str) -> dict:
    return {
        "part": {"asset_id": f"asset_{mpn}", "mpn": mpn, "description": "demo part",
                 "package": "SOT-23", "limits": []},
        "pins": [], "capability_labels": ["placeable"], "sources": [],
        "diagnostics": [], "placeable": True,
    }


def test_template_dialog_returns_confirmed_parameters(app):
    from PySide6.QtWidgets import QDialog

    from mota.desktop.pages.build import TemplateDialog

    template = {
        "title": "LED timer", "description": "NE555P blinker", "status": "verified",
        "board": {"width_mm": 50, "height_mm": 35},
        "component_count": 6, "net_count": 5,
        "parts": [{"reference": "U1"}, {"reference": "R1"}],
        "defaults": {"supply_voltage_v": 5.0, "board_width_mm": 50,
                     "board_height_mm": 35},
        "required_fields": ["supply_voltage_v"],
        "constraints": {"supply_voltage_v": {"min": 3.0, "max": 6.0}},
    }
    dialog = TemplateDialog(template)
    assert set(dialog.fields) == {"supply_voltage_v", "board_width_mm", "board_height_mm"}
    dialog.fields["board_width_mm"].setText("60")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.output_parameters == {"supply_voltage_v": 5.0,
                                        "board_width_mm": 60.0,
                                        "board_height_mm": 35.0}


def test_late_inspector_reply_cannot_overwrite_a_newer_selection(app):
    from PySide6.QtWidgets import QListWidgetItem

    from mota.desktop.widgets.resource_panel import ROLE_ASSET, ResourcePanel

    bridge = RecordingBridge()
    panel = ResourcePanel(bridge)
    bridge.pending.clear()
    item_a = QListWidgetItem()
    item_a.setData(ROLE_ASSET, "asset_a")
    item_b = QListWidgetItem()
    item_b.setData(ROLE_ASSET, "asset_b")
    panel._on_result_selected(item_a, None)
    (_, _, callback_a) = bridge.pending[-1]
    panel._on_result_selected(item_b, None)
    (_, _, callback_b) = bridge.pending[-1]
    callback_b(_part_payload("B-PART"), None)
    callback_a(_part_payload("A-PART"), None)  # late reply for A
    assert panel.selected_part["part"]["mpn"] == "B-PART"
    assert "B-PART" in panel.inspector.toHtml()
    assert "A-PART" not in panel.inspector.toHtml()


def test_history_defaults_to_compact_list_and_keeps_a_preview(app):
    from mota.desktop.controllers import ProjectController, SessionController
    from mota.desktop.pages.history import HistoryPage

    bridge = RecordingBridge()
    page = HistoryPage(bridge, SessionController(bridge, None),
                       ProjectController(bridge, None))
    page.cards = [{"project_id": "project_1", "name": "LED timer demo",
                   "current_revision_id": "rev_000002", "component_count": 6,
                   "updated_at": "2026-10-21T10:00:00Z",
                   "review_status": "READY_FOR_REVIEW",
                   "latest_run_state": "SUCCEEDED"}]
    page._reflow()
    assert page._view_mode == "list"
    assert page.views.currentWidget() is page.project_list
    assert page.project_list.count() == 1
    assert page.detail.name.text() == "LED timer demo"
    assert page.list_button.isChecked() and not page.grid_button.isChecked()
    page._set_view("grid")
    assert page.views.currentWidget() is page.scroll
    assert page.grid_button.isChecked()


def test_ai_connections_lists_profiles_and_keeps_provider_and_format_separate(app):
    from mota.desktop.controllers import ConfigController, SessionController, TaskController
    from mota.desktop.pages.settings import SettingsPage

    bridge = RecordingBridge()
    page = SettingsPage(bridge, SessionController(bridge, None),
                        ConfigController(bridge, None), TaskController(bridge, None))
    page._apply_config({
        "schema_version": 2, "config_version": 3,
        "default_profile_id": "openai-main",
        "secret_storage_capabilities": {"keychain": True},
        "profiles": [
            {"profile_id": "openai-main", "name": "OpenAI", "provider": "openai",
             "api_format": "responses", "base_url": "https://api.openai.com/v1",
             "model": "gpt-5.4", "stream": True, "thinking_mode": "disabled",
             "thinking_effort": None, "max_output_tokens": 8192,
             "request_timeout_seconds": 120.0, "strict_tool_schema": True,
             "supports_vision": True, "api_key_configured": False,
             "secret_storage": "none", "test_results": {}, "model_verified": False},
            {"profile_id": "deepseek-main", "name": "DeepSeek", "provider": "deepseek",
             "api_format": "chat_completions", "base_url": "https://api.deepseek.com",
             "model": "deepseek-flash", "stream": True, "thinking_mode": "disabled",
             "thinking_effort": None, "max_output_tokens": 8192,
             "request_timeout_seconds": 120.0, "strict_tool_schema": False,
             "supports_vision": True, "api_key_configured": False,
             "secret_storage": "none", "test_results": {}, "model_verified": False},
        ],
    })
    assert page.profile_list.count() == 2
    assert page.default_button.text().startswith("Set as default")
    assert page.probe_button.text() == "Test connection"
    page.profile_list.setCurrentRow(1)
    assert page.provider.currentData() == "deepseek"
    assert page.api_format.currentData() == "chat_completions"
    assert page.model.text() == "deepseek-flash"
    assert page.thinking_effort.isEnabled() is False


def test_locate_buttons_show_references_but_emit_real_object_ids(app):
    from mota.desktop.widgets.action_card import ActionCard

    card = ActionCard("call_2", "prepare_design", {})
    card.complete({"affected_object_ids": ["component_1"],
                   "affected_refs": {"component_1": "U1"}})
    button = card.locate_row.itemAt(0).widget()
    assert button.text() == "U1"
    assert "Affected: U1" in card.detail.text()
    seen: list[str] = []
    card.locate.connect(seen.append)
    button.click()
    assert seen == ["component_1"]


def test_completed_tool_rows_collapse_and_failures_expand(app):
    from mota.desktop.widgets.action_card import ActionCard

    card = ActionCard("call_1", "prepare_design", {"template_id": "led_timer"})
    assert card.body.isHidden() is False
    assert card._expanded is True
    card.complete({"components": 6, "nets": 5, "draft_seq": 1,
                   "affected_object_ids": ["U1", "R1"]}, duration_ms=42)
    assert card.status.text() == "Completed"
    assert card._expanded is False
    assert card.body.isHidden() is True
    assert "Affected: U1, R1" in card.detail.text()
    buttons = [card.locate_row.itemAt(i).widget()
               for i in range(card.locate_row.count())]
    labels = [b.text() for b in buttons if b is not None]
    assert labels == ["U1", "R1"]
    located: list[str] = []
    card.locate.connect(located.append)
    buttons[0].click()
    assert located == ["U1"]
    card.fail({"message": "Routing failed"}, retryable=False)
    assert card.status.text() == "Failed"
    assert card._expanded is True
    assert card.body.isHidden() is False
    assert "Routing failed" in card.detail.text()
