from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mota.desktop.widgets.canvas_2d import CanvasView  # noqa: E402

SCENE = {
    "bounds": {"x": 0, "y": 0, "width": 100, "height": 60},
    "objects": [
        {"id": "board", "kind": "polygon", "layer": "Edge.Cuts",
         "points": [[0, 0], [100, 0], [100, 60], [0, 60], [0, 0]],
         "color": "#D7E2EA", "fill": "#173E35", "width": 0.3},
        {"id": "part_c1", "kind": "rect", "component_id": "c1",
         "x": 10, "y": 10, "w": 4, "h": 4, "color": "#D68C6A", "fill": "#D68C6A"},
        {"id": "text_c1", "kind": "text", "component_id": "c1", "x": 10, "y": 16,
         "text": "R1", "font_size": 1.5, "color": "#E7EDF2"},
    ],
}


@pytest.fixture()
def view():
    app = QApplication.instance() or QApplication([])
    widget = CanvasView()
    widget.resize(900, 620)
    widget.show()
    app.processEvents()
    widget.load_scene(SCENE)
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def _mouse(kind, pos, button):
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, button,
                       Qt.KeyboardModifier.NoModifier)


def _drag(view, start, end, button=Qt.MouseButton.LeftButton):
    middle = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
    view.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, start, button))
    view.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, middle, button))
    view.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, end, button))
    view.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, end, button))


def test_zoom_keeps_the_scene_point_under_the_cursor(view):
    anchor = QPointF(240, 180)
    before = view.mapToScene(anchor.toPoint())
    view.zoom(1.8, anchor=anchor)
    after = view.mapToScene(anchor.toPoint())
    assert abs(after.x() - before.x()) < 0.15
    assert abs(after.y() - before.y()) < 0.15


def test_wheel_zoom_is_bounded_and_anchored(view):
    scale = view.transform().m11()
    pos = QPointF(300, 220)
    before = view.mapToScene(pos.toPoint())
    event = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    view.wheelEvent(event)
    after = view.mapToScene(pos.toPoint())
    assert abs(after.x() - before.x()) < 0.15
    assert 1.15 < view.transform().m11() / scale < 1.25


def test_middle_and_left_drag_pan_the_canvas(view):
    view.zoom(2.0)
    scale = view.transform().m11()
    before = view.mapToScene(view.viewport().rect().center())
    _drag(view, QPointF(450, 300), QPointF(550, 350),
          Qt.MouseButton.MiddleButton)
    after = view.mapToScene(view.viewport().rect().center())
    assert abs((after.x() - before.x()) + 100 / scale) < 0.2
    assert abs((after.y() - before.y()) + 50 / scale) < 0.2

    view.fit_page()
    app = QApplication.instance()
    app.processEvents()
    scale = view.transform().m11()
    before = view.mapToScene(view.viewport().rect().center())
    empty = QPointF(5, 5)
    assert view._pick(view.mapToScene(empty.toPoint())) is None
    _drag(view, empty, QPointF(empty.x() + 60, empty.y() - 30))
    after = view.mapToScene(view.viewport().rect().center())
    assert abs((after.x() - before.x()) + 60 / scale) < 0.2
    assert abs((after.y() - before.y()) - 30 / scale) < 0.2
    assert view.user_interacted


def test_component_drag_emits_snapped_world_coordinates(view):
    moves: list[tuple[str, int, int]] = []
    view.move_requested.connect(lambda cid, x, y: moves.append((cid, x, y)))
    view.fit_content()
    app = QApplication.instance()
    app.processEvents()
    start = view.mapFromScene(QPointF(12, -12))
    scale = view.transform().m11()
    _drag(view, QPointF(start), QPointF(start.x() + 10 * scale,
                                        start.y() + 5 * scale))
    assert moves == [("c1", 22_000_000, 7_000_000)]


def test_escape_cancels_component_drag_without_a_move(view):
    moves: list[tuple[str, int, int]] = []
    view.move_requested.connect(lambda cid, x, y: moves.append((cid, x, y)))
    start = view.mapFromScene(QPointF(12, -12))
    view.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(start),
                                Qt.MouseButton.LeftButton))
    view.mouseMoveEvent(_mouse(QEvent.Type.MouseMove,
                               QPointF(start.x() + 40, start.y() + 20),
                               Qt.MouseButton.LeftButton))
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                 Qt.KeyboardModifier.NoModifier))
    assert moves == []
    assert view._drag_ghost is None


def test_pan_and_zoom_survive_a_scene_reload(view):
    view.zoom(2.0)
    _drag(view, QPointF(450, 300), QPointF(520, 330),
          Qt.MouseButton.MiddleButton)
    scale = view.transform().m11()
    center = view.mapToScene(view.viewport().rect().center())
    view.load_scene(SCENE)
    assert abs(view.transform().m11() - scale) < 1e-6
    reloaded = view.mapToScene(view.viewport().rect().center())
    assert abs(reloaded.x() - center.x()) < 0.01
    assert abs(reloaded.y() - center.y()) < 0.01


def test_non_editable_canvas_does_not_move_board_objects(view):
    view.editable_move = False
    moves: list[tuple[str, int, int]] = []
    view.move_requested.connect(lambda cid, x, y: moves.append((cid, x, y)))
    start = view.mapFromScene(QPointF(12, -12))
    _drag(view, QPointF(start), QPointF(start.x() + 30, start.y() + 30))
    assert moves == []
