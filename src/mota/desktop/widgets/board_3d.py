"""Native OpenGL 3D board view (PyQtGraph GLViewWidget)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from mota.desktop.theme.tokens import COLORS

try:  # pragma: no cover - availability depends on the machine
    import pyqtgraph.opengl as gl

    GL_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    gl = None
    GL_IMPORT_ERROR = str(exc)


class Board3D(QWidget):
    object_selected = Signal(str)
    status_message = Signal(str)
    request_2d = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.view = None
        self.fallback: QWidget | None = None
        self.mesh_items: list[tuple[Any, dict[str, Any]]] = []
        self._base_colors: dict[int, tuple[float, float, float, float]] = {}
        self._selected_component: str | None = None
        self._bounds = (50.0, 35.0, 1.6)
        self._press_pos = None
        self._build()

    def _build(self) -> None:
        if gl is None:
            self._build_fallback(f"OpenGL view is unavailable: {GL_IMPORT_ERROR}")
            return
        try:
            view = gl.GLViewWidget()
            view.setBackgroundColor(QColor(COLORS["background"]))
            view.opts["distance"] = 90
            view.setCameraPosition(elevation=32, azimuth=-48, distance=95)
            self.grid = gl.GLGridItem()
            self.grid.setSize(120, 120)
            self.grid.setSpacing(10, 10)
            self.grid.setColor((50, 70, 84, 110))
            view.addItem(self.grid)
            axis = gl.GLLinePlotItem(
                pos=np.array([[0, 0, 0], [15, 0, 0]], dtype=float), color=(240, 126, 138, 255),
                width=1.5, antialias=True)
            view.addItem(axis)
            axis_y = gl.GLLinePlotItem(
                pos=np.array([[0, 0, 0], [0, 15, 0]], dtype=float), color=(130, 212, 161, 255),
                width=1.5, antialias=True)
            view.addItem(axis_y)
            axis_z = gl.GLLinePlotItem(
                pos=np.array([[0, 0, 0], [0, 0, 12]], dtype=float), color=(121, 184, 242, 255),
                width=1.5, antialias=True)
            view.addItem(axis_z)
            self.view = view
            self.layout.addWidget(view)
        except Exception as exc:  # noqa: BLE001
            self._build_fallback(f"OpenGL context could not be created: {exc}")

    def _build_fallback(self, reason: str) -> None:
        panel = QWidget()
        panel.setStyleSheet(f"background: {COLORS['panel']};")
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("3D view unavailable")
        title.setObjectName("SectionTitle")
        body = QLabel(reason + "\n\nPCB 2D and the CLI remain available.")
        body.setObjectName("Muted")
        body.setWordWrap(True)
        body.setMaximumWidth(460)
        button = QPushButton("Open PCB 2D")
        button.clicked.connect(self.request_2d.emit)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (title, body, button):
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.layout.addWidget(panel)
        self.fallback = panel
        self.view = None

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(600, self._verify_context)

    def _verify_context(self) -> None:
        if self.view is not None and not self.view.isValid():
            self._build_fallback("The OpenGL context could not be created on this display.")

    @property
    def available(self) -> bool:
        return self.view is not None

    def select_component(self, component_id: str | None) -> None:
        self._selected_component = component_id
        selected = QColor(COLORS["selected"])
        for mesh, obj in self.mesh_items:
            if obj.get("layer") != "Component":
                continue
            if component_id and obj.get("component_id") == component_id:
                mesh.setColor((selected.redF(), selected.greenF(),
                               selected.blueF(), 1.0))
            else:
                base = self._base_colors.get(id(mesh))
                if base:
                    mesh.setColor(base)
        if self.view is not None:
            self.view.update()

    def load_scene(self, dto: dict[str, Any]) -> None:
        if self.view is None:
            return
        for item, _ in self.mesh_items:
            try:
                self.view.removeItem(item)
            except Exception:
                pass
        self.mesh_items.clear()
        self._base_colors.clear()
        bounds = dto.get("bounds") or {}
        width = float(bounds.get("width", 50))
        height = float(bounds.get("height", 35))
        thickness = abs(float(bounds.get("z", -1.6)))
        self._bounds = (width, height, thickness)
        for obj in dto.get("objects", []):
            if obj.get("kind") != "mesh":
                continue
            vertices = np.array(obj.get("vertices") or [], dtype=float)
            faces = np.array(obj.get("faces") or [], dtype=int)
            if vertices.size == 0 or faces.size == 0:
                continue
            matrix = obj.get("world_matrix") or []
            if len(matrix) == 16:
                m = np.array(matrix, dtype=float).reshape(4, 4)
                hom = np.concatenate([vertices, np.ones((vertices.shape[0], 1))], axis=1)
                vertices = (m @ hom.T).T[:, :3]
            color = QColor(obj.get("color") or "#3C5A52")
            mesh = gl.GLMeshItem(
                vertexes=vertices, faces=faces,
                color=(color.redF(), color.greenF(), color.blueF(), 1.0),
                shader=None, smooth=False, drawEdges=True,
                edgeColor=(0.06, 0.09, 0.11, 0.9), glOptions="opaque")
            self.view.addItem(mesh)
            self.mesh_items.append((mesh, obj))
            self._base_colors[id(mesh)] = (color.redF(), color.greenF(),
                                           color.blueF(), 1.0)
        target = (width / 2, height / 2, -thickness / 2)
        try:
            from pyqtgraph import Vector

            self.view.opts["center"] = Vector(*target)
        except Exception:
            self.view.opts["center"] = np.array(target)
        self.fit()

    def set_view(self, kind: str) -> None:
        if self.view is None:
            return
        width, height, thickness = self._bounds
        distance = max(width, height) * 1.7 + 20
        if kind == "top":
            self.view.setCameraPosition(elevation=90, azimuth=-90, distance=distance)
        elif kind == "bottom":
            self.view.setCameraPosition(elevation=-90, azimuth=-90, distance=distance)
        elif kind == "iso":
            self.view.setCameraPosition(elevation=32, azimuth=-48, distance=distance)
        else:
            self.fit()

    def fit(self) -> None:
        if self.view is None:
            return
        width, height, thickness = self._bounds
        self.view.opts["distance"] = max(width, height) * 1.6 + 12
        try:
            from pyqtgraph import Vector

            self.view.opts["center"] = Vector(width / 2, height / 2, -thickness / 2)
        except Exception:
            self.view.opts["center"] = np.array([width / 2, height / 2, -thickness / 2])
        self.view.update()

    # ------------------------------------------------------------------ picking
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is not None and self.view is not None and \
                (event.position() - self._press_pos).manhattanLength() < 4:
            self._pick(event.position())
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _pick(self, position) -> None:
        try:
            projection = self.view.projectionMatrix()
            view_matrix = self.view.viewMatrix()
            matrix = projection @ view_matrix
        except Exception:
            return
        width = self.view.width()
        height = self.view.height()
        best = None
        best_distance = 1e9
        for _, obj in self.mesh_items:
            if not obj.get("component_id"):
                continue
            matrix_data = obj.get("world_matrix") or []
            if len(matrix_data) != 16:
                continue
            m = np.array(matrix_data, dtype=float).reshape(4, 4)
            local = np.array(obj.get("vertices") or [[0, 0, 0]], dtype=float)
            center_local = local.mean(axis=0)
            world = m @ np.array([center_local[0], center_local[1], center_local[2], 1.0])
            clip = matrix @ world
            if abs(clip[3]) < 1e-9:
                continue
            ndc = clip[:3] / clip[3]
            screen_x = (ndc[0] + 1) * 0.5 * width
            screen_y = (1 - ndc[1]) * 0.5 * height
            distance = math.hypot(screen_x - position.x(), screen_y - position.y())
            if distance < best_distance:
                best_distance = distance
                best = obj
        if best and best_distance < 60:
            self.object_selected.emit(best["component_id"])
            self.status_message.emit(
                f"Selected {best.get('ref')} in 3D · {best.get('fidelity')} model")
        else:
            self.object_selected.emit("")
            self.status_message.emit("No component at this position")
