"""Native 2D schematic/PCB canvas (QGraphicsView) with rulers and editing."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
    QGraphicsView, QGridLayout, QLabel, QWidget,
)

from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.rulers import RulerWidget, nice_step


def _color(value: str | None, fallback: str = "#E7EDF2") -> QColor:
    return QColor(value or fallback)


MIN_SCALE = 0.02
MAX_SCALE = 240.0
FIT_MAX_SCALE = 12.0
PAN_THRESHOLD_PX = 3


class CanvasView(QGraphicsView):
    cursor_moved = Signal(float, float)
    zoom_changed = Signal(float)
    object_selected = Signal(str, str)
    move_requested = Signal(str, int, int)
    place_requested = Signal(float, float)
    status_message = Signal(str)
    view_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(COLORS["background"])))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.objects: list[dict[str, Any]] = []
        self.item_objects: dict[QGraphicsItem, dict[str, Any]] = {}
        self.item_original: dict[QGraphicsItem, tuple[QPen, QBrush]] = {}
        self.component_items: dict[str, list[QGraphicsItem]] = {}
        self.net_items: dict[str, list[QGraphicsItem]] = {}
        self.selected_kind: str | None = None
        self.selected_id: str | None = None
        self.tool = "select"
        self.units = "mm"
        self.grid_visible = True
        self.read_only = False
        self.run_active = False
        self.snap_mm = 0.25
        self._drag_component: str | None = None
        self._drag_start_scene: QPointF | None = None
        self._drag_ghost: QGraphicsRectItem | None = None
        self._drag_origin: tuple[float, float] | None = None
        self._drag_moved = False
        self._measure_points: list[QPointF] = []
        self._measure_items: list[QGraphicsItem] = []
        self._panning = False
        self._pan_candidate = False
        self._pan_start = QPointF()
        self._pan_center = QPointF()
        self.editable_move = True
        self.user_interacted = False
        self._fit_pending = False
        self._page_key: tuple[float, float] | None = None
        self._bounds = (0.0, 0.0, 10.0, 10.0)
        self.manifest: dict[str, Any] | None = None
        self.scene_version: str | None = None
        self.chunk_items: dict[str, list[QGraphicsItem]] = {}
        self._chunk_loader = None
        self._requested_chunks: set[str] = set()
        self.lod = 2
        self._placement_active = False
        self._placement_label = ""
        self._placement_size = (4.0, 3.0)
        self._placement_ghost: QGraphicsRectItem | None = None

    # -------------------------------------------------------------------- scene
    def _reset_interaction(self) -> None:
        self.cancel_placement()
        if self._drag_ghost is not None:
            self._scene.removeItem(self._drag_ghost)
            self._drag_ghost = None
            self.viewport().releaseMouse()
        self._drag_component = None
        self._drag_start_scene = None
        self._drag_origin = None
        self._drag_moved = False
        self._panning = False
        self._pan_candidate = False
        self.clear_measure()

    def load_scene(self, dto: dict[str, Any]) -> None:
        self._reset_interaction()
        self._scene.clear()
        self.manifest = None
        self.scene_version = None
        self.chunk_items.clear()
        self._requested_chunks.clear()
        self.item_objects.clear()
        self.item_original.clear()
        self.component_items.clear()
        self.net_items.clear()
        self.objects = dto.get("objects", [])
        bounds = dto.get("bounds") or {}
        width = float(bounds.get("width") or 10)
        height = float(bounds.get("height") or 10)
        self._bounds = (float(bounds.get("x", 0)), float(bounds.get("y", 0)), width, height)
        for obj in self.objects:
            items = self._build_item(obj)
            for item in items:
                self._scene.addItem(item)
                item.setData(0, obj)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
                             bool(obj.get("selectable", True)))
                self.item_objects[item] = obj
                self.item_original[item] = (item.pen() if hasattr(item, "pen") else QPen(),
                                            item.brush() if hasattr(item, "brush") else QBrush())
                if obj.get("component_id"):
                    self.component_items.setdefault(obj["component_id"], []).append(item)
                if obj.get("net_id"):
                    self.net_items.setdefault(obj["net_id"], []).append(item)
        rect = QRectF(self._bounds[0], -(self._bounds[1] + height), width, height)
        # Keep a pan margin around the page so the canvas can be dragged even
        # when the board itself fits inside the viewport.
        pad = max(20.0, max(width, height) * 0.5)
        self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        # Fit the actual drawing content, not the empty page margin.
        content = QRectF()
        for item, obj in self.item_objects.items():
            if obj.get("id") in ("page", "title_block", "title_text", "title_meta"):
                continue
            r = item.mapToScene(item.boundingRect()).boundingRect()
            content = r if content.isNull() else content.united(r)
        self._content_rect = content.adjusted(-6, -6, 6, 6) if not content.isNull() else rect
        self._page_rect = rect
        page_key = (round(width, 2), round(height, 2))
        first_for_page = self._page_key != page_key
        self._page_key = page_key
        if not self.user_interacted and first_for_page:
            self._auto_fit()
        self.apply_highlight()
        self.view_changed.emit()

    # ------------------------------------------------------------- chunked mode
    def set_chunk_loader(self, loader) -> None:
        """``loader(chunk_id)`` must call :meth:`load_chunk` when data arrives."""

        self._chunk_loader = loader

    def load_manifest(self, manifest: dict[str, Any]) -> None:
        """Reset the canvas for one design version and draw nothing yet."""

        self._reset_interaction()
        self._scene.clear()
        self.item_objects.clear()
        self.item_original.clear()
        self.component_items.clear()
        self.net_items.clear()
        self.chunk_items.clear()
        self._requested_chunks.clear()
        self.objects = []
        self.manifest = manifest
        self.scene_version = str(manifest.get("design_version_key") or "")
        bounds = manifest.get("bounds") or {}
        width = float(bounds.get("width") or 10)
        height = float(bounds.get("height") or 10)
        self._bounds = (float(bounds.get("x", 0)), float(bounds.get("y", 0)),
                        width, height)
        rect = QRectF(self._bounds[0], -(self._bounds[1] + height), width, height)
        pad = max(20.0, max(width, height) * 0.5)
        self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        self._page_rect = rect
        self._content_rect = rect
        self.user_interacted = False
        self._fit_rect(rect.adjusted(-4, -4, 4, 4), mark_interacted=False)
        self.view_changed.emit()
        self.request_visible_chunks()

    def load_chunk(self, dto: dict[str, Any]) -> bool:
        """Add one chunk if (and only if) it belongs to the loaded version."""

        if not self.manifest:
            return False
        if str(dto.get("design_version_key") or "") != (self.scene_version or ""):
            return False
        chunk_id = str(dto.get("chunk_id") or "")
        if not chunk_id or chunk_id in self.chunk_items:
            return False
        items: list[QGraphicsItem] = []
        for obj in dto.get("objects") or []:
            if int(obj.get("lod", 0)) > self.lod:
                continue
            if obj.get("kind") == "mesh" or obj.get("mesh_ref"):
                continue
            for item in self._build_item(obj):
                item.setData(0, obj)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
                             bool(obj.get("selectable", True)))
                self._scene.addItem(item)
                self.item_objects[item] = obj
                self.item_original[item] = (
                    item.pen() if hasattr(item, "pen") else QPen(),
                    item.brush() if hasattr(item, "brush") else QBrush())
                if obj.get("component_id"):
                    self.component_items.setdefault(
                        obj["component_id"], []).append(item)
                if obj.get("net_id"):
                    self.net_items.setdefault(obj["net_id"], []).append(item)
                items.append(item)
        self.chunk_items[chunk_id] = items
        self._requested_chunks.discard(chunk_id)
        self._update_chunk_visibility()
        return True

    def set_lod(self, level: int) -> None:
        self.lod = max(0, min(2, int(level)))
        for items in self.chunk_items.values():
            for item in items:
                obj = self.item_objects.get(item) or {}
                item.setVisible(int(obj.get("lod", 0)) <= self.lod)
        self._update_chunk_visibility()

    def viewport_world(self) -> dict[str, float]:
        viewport = self.viewport().rect()
        top_left = self.mapToScene(viewport.topLeft())
        bottom_right = self.mapToScene(viewport.bottomRight())
        min_x = min(top_left.x(), bottom_right.x())
        max_x = max(top_left.x(), bottom_right.x())
        top_y = min(top_left.y(), bottom_right.y())
        bottom_y = max(top_left.y(), bottom_right.y())
        return {"x": min_x, "y": -bottom_y,
                "width": max(0.0, max_x - min_x),
                "height": max(0.0, bottom_y - top_y)}

    def visible_chunk_ids(self) -> list[str]:
        if not self.manifest:
            return []
        view = self.viewport_world()
        out: list[str] = []
        for spec in self.manifest.get("chunks") or []:
            x, y = float(spec["x"]), float(spec["y"])
            width, height = float(spec["w"]), float(spec["h"])
            if x >= view["x"] + view["width"] or x + width <= view["x"]:
                continue
            if y >= view["y"] + view["height"] or y + height <= view["y"]:
                continue
            out.append(str(spec["chunk_id"]))
        return out

    def request_visible_chunks(self) -> list[str]:
        if not self.manifest or self._chunk_loader is None:
            return []
        missing: list[str] = []
        for chunk_id in self.visible_chunk_ids():
            if chunk_id in self.chunk_items:
                continue
            if chunk_id in self._requested_chunks:
                continue
            self._requested_chunks.add(chunk_id)
            missing.append(chunk_id)
            self._chunk_loader(chunk_id)
        return missing

    def _update_chunk_visibility(self) -> None:
        if not self.manifest:
            return
        visible = set(self.visible_chunk_ids())
        for chunk_id, items in self.chunk_items.items():
            show = chunk_id in visible
            for item in items:
                obj = self.item_objects.get(item) or {}
                item.setVisible(show and int(obj.get("lod", 0)) <= self.lod)

    def frame_stats(self, frames: int = 20) -> dict[str, float]:
        """Render real frames offscreen and report achieved fps and latency."""

        import statistics
        import time

        sizes: list[float] = []
        for _ in range(max(1, frames)):
            started = time.perf_counter()
            image = self.viewport().grab().toImage()
            sizes.append((time.perf_counter() - started) * 1000.0)
            image = None
        sizes.sort()
        mean = statistics.fmean(sizes) if sizes else 0.0
        p50 = sizes[len(sizes) // 2] if sizes else 0.0
        p95 = sizes[min(len(sizes) - 1, int(len(sizes) * 0.95))] if sizes else 0.0
        return {"frames": float(len(sizes)), "mean_ms": mean, "p50_ms": p50,
                "p95_ms": p95, "fps": (1000.0 / mean) if mean > 0 else 0.0}

    def _build_item(self, obj: dict[str, Any]) -> list[QGraphicsItem]:
        kind = obj.get("kind")
        color = _color(obj.get("color"))
        fill = obj.get("fill")
        pen = QPen(color)
        pen.setWidthF(max(0.06, float(obj.get("width") or 0.15)))
        pen.setCosmetic(False)
        if obj.get("dashed"):
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([3.0, 2.5])
        brush = QBrush(_color(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush)
        items: list[QGraphicsItem] = []
        if kind == "rect":
            item = QGraphicsRectItem(QRectF(obj["x"], -(obj["y"] + obj.get("h", 0)),
                                            obj.get("w", 0), obj.get("h", 0)))
            item.setPen(pen)
            item.setBrush(brush)
            items.append(item)
        elif kind == "polygon":
            points = [QPointF(p[0], -p[1]) for p in obj.get("points", [])]
            item = QGraphicsPolygonItem(QPolygonF(points))
            item.setPen(pen)
            item.setBrush(brush)
            items.append(item)
        elif kind in ("polyline", "line"):
            path = QPainterPath()
            points = obj.get("points", [])
            if points:
                path.moveTo(points[0][0], -points[0][1])
                for point in points[1:]:
                    path.lineTo(point[0], -point[1])
            item = QGraphicsPathItem(path)
            item.setPen(pen)
            item.setBrush(Qt.BrushStyle.NoBrush)
            items.append(item)
        elif kind == "circle":
            radius = float(obj.get("r") or 0)
            item = QGraphicsEllipseItem(QRectF(obj["x"] - radius, -obj["y"] - radius,
                                               radius * 2, radius * 2))
            item.setPen(pen)
            item.setBrush(brush)
            items.append(item)
        elif kind == "pad":
            w = float(obj.get("w") or 0)
            h = float(obj.get("h") or 0)
            shape = (obj.get("meta") or {}).get("shape", "rect")
            if shape == "circle":
                item = QGraphicsEllipseItem(QRectF(obj["x"] - w / 2, -obj["y"] - w / 2, w, w))
            else:
                item = QGraphicsRectItem(QRectF(obj["x"] - w / 2, -obj["y"] - h / 2, w, h))
            item.setPen(pen)
            item.setBrush(brush)
            items.append(item)
        elif kind == "text":
            text = str(obj.get("text") or "")
            if not text:
                return []
            size = float(obj.get("font_size") or 1.5)
            font = QFont()
            font.setPixelSize(max(2, int(round(size * 4))))
            item = QGraphicsSimpleTextItem(text)
            item.setFont(font)
            item.setBrush(QBrush(color))
            item.setPen(QPen(Qt.PenStyle.NoPen))
            item.setScale(0.25)
            x = float(obj.get("x") or 0)
            y = float(obj.get("y") or 0)
            item.setPos(x, -y - size * 0.9)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
            items.append(item)
        return items

    def apply_highlight(self) -> None:
        for item, obj in self.item_objects.items():
            original_pen, original_brush = self.item_original.get(item, (QPen(), QBrush()))
            if not hasattr(item, "setPen"):
                continue
            matches = False
            if self.selected_kind == "component" and obj.get("component_id") == self.selected_id:
                matches = True
            elif self.selected_kind == "net" and obj.get("net_id") == self.selected_id:
                matches = True
            if matches:
                pen = QPen(QColor(COLORS["selected"]))
                pen.setWidthF(max(0.18, original_pen.widthF() * 2.2))
                item.setPen(pen)
            else:
                item.setPen(original_pen)

    def select(self, kind: str | None, object_id: str | None) -> None:
        if (kind, object_id) == (self.selected_kind, self.selected_id):
            return
        self.selected_kind = kind
        self.selected_id = object_id
        self.apply_highlight()

    def clear_measure(self) -> None:
        for item in self._measure_items:
            self._scene.removeItem(item)
        self._measure_items.clear()
        self._measure_points.clear()

    # -------------------------------------------------------------------- tools
    def begin_placement(self, label: str, width_mm: float = 4.0,
                        height_mm: float = 3.0) -> None:
        """Enter PREVIEW: a ghost follows the pointer until a click or Esc."""

        self.cancel_placement()
        self._placement_active = True
        self._placement_label = label
        self._placement_size = (max(0.2, width_mm), max(0.2, height_mm))
        ghost = QGraphicsRectItem(QRectF(-width_mm / 2, -height_mm / 2,
                                         width_mm, height_mm))
        pen = QPen(QColor(COLORS["draft"]))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(0.18)
        ghost.setPen(pen)
        ghost.setBrush(QBrush(QColor(134, 190, 232, 36)))
        ghost.setZValue(1200)
        ghost.setVisible(False)
        self._scene.addItem(ghost)
        self._placement_ghost = ghost
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.status_message.emit(f"放置 {label} · 点击画布确认 · Esc 取消")

    def cancel_placement(self) -> None:
        if self._placement_ghost is not None:
            self._scene.removeItem(self._placement_ghost)
            self._placement_ghost = None
        if self._placement_active:
            self.status_message.emit("已取消放置")
        self._placement_active = False
        self._placement_label = ""
        if self.tool != "measure":
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def placement_active(self) -> bool:
        return self._placement_active

    def set_tool(self, tool: str) -> None:
        if self._panning or self._pan_candidate:
            self._end_pan()
        if self._drag_component:
            self._cancel_drag()
        if tool != "place":
            self.cancel_placement()
        self.tool = tool
        if tool != "measure":
            self.clear_measure()
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "select"
                       else Qt.CursorShape.CrossCursor)

    def set_units(self, units: str) -> None:
        self.units = units

    def set_grid_visible(self, visible: bool) -> None:
        self.grid_visible = visible
        self.viewport().update()

    def set_read_only(self, read_only: bool) -> None:
        self.read_only = read_only

    def set_run_active(self, active: bool) -> None:
        self.run_active = active

    # --------------------------------------------------------------------- view
    def _fit_rect(self, target: QRectF | None, *, mark_interacted: bool = True) -> None:
        if target is None or target.isEmpty():
            target = getattr(self, "_page_rect", None)
        self.resetTransform()
        if target is None or target.isEmpty():
            # Empty scene: a stable default scale instead of identity 1 px/mm.
            self.scale(4.0, 4.0)
            self.centerOn(0.0, 0.0)
        else:
            self.fitInView(target, Qt.AspectRatioMode.KeepAspectRatio)
            scale = self.transform().m11()
            if scale > FIT_MAX_SCALE:
                factor = FIT_MAX_SCALE / scale
                self.scale(factor, factor)
            self.centerOn(target.center())
        self.user_interacted = mark_interacted
        self.zoom_changed.emit(self.transform().m11())
        self.view_changed.emit()

    def _auto_fit(self) -> None:
        target = self.content_bounds()
        if target.isEmpty():
            target = getattr(self, "_page_rect", None)
        self._fit_rect(target, mark_interacted=False)

    def content_bounds(self) -> QRectF:
        content = QRectF()
        for item, obj in self.item_objects.items():
            if obj.get("id") in ("page", "title_block", "title_text", "title_meta"):
                continue
            if obj.get("layer") == "frame":
                continue
            rect = item.mapToScene(item.boundingRect()).boundingRect()
            content = rect if content.isNull() else content.united(rect)
        if content.isNull():
            return QRectF()
        return content.adjusted(-3, -3, 3, 3)

    def fit_content(self) -> None:
        target = self.content_bounds()
        self._fit_rect(target if not target.isEmpty()
                       else getattr(self, "_page_rect", None))

    def fit_page(self) -> None:
        self._fit_rect(getattr(self, "_page_rect", None))

    def fit_selection(self) -> None:
        if self.selected_kind not in ("component", "net"):
            self.status_message.emit("未选择对象")
            return
        rect = QRectF()
        for item, obj in self.item_objects.items():
            if self.selected_kind == "component" and \
                    obj.get("component_id") != self.selected_id:
                continue
            if self.selected_kind == "net" and obj.get("net_id") != self.selected_id:
                continue
            value = item.mapToScene(item.boundingRect()).boundingRect()
            rect = value if rect.isNull() else rect.united(value)
        if rect.isNull():
            self.status_message.emit("选中对象不在当前视图中")
            return
        self._fit_rect(rect.adjusted(-2, -2, 2, 2))

    def fit_board(self) -> None:
        rect = QRectF()
        for item, obj in self.item_objects.items():
            if obj.get("id") == "board" or obj.get("layer") == "Edge.Cuts":
                value = item.mapToScene(item.boundingRect()).boundingRect()
                rect = value if rect.isNull() else rect.united(value)
        if rect.isNull():
            rect = self.content_bounds()
        self._fit_rect(rect.adjusted(-2, -2, 2, 2) if not rect.isNull()
                       else getattr(self, "_page_rect", None))

    def fit(self) -> None:
        self.fit_content()

    def zoom(self, factor: float, *, anchor: QPointF | None = None) -> None:
        """Zoom by ``factor`` keeping ``anchor`` (viewport pixels) stable."""

        if factor <= 0:
            return
        factor = max(0.2, min(5.0, factor))
        scale = self.transform().m11()
        target = max(MIN_SCALE, min(MAX_SCALE, scale * factor))
        if abs(target - scale) < 1e-9:
            return
        viewport = self.viewport().rect()
        if anchor is None or not viewport.contains(anchor.toPoint()):
            anchor = QPointF(viewport.center())
        before = self.mapToScene(anchor.toPoint())
        # Scale about the view centre, then shift the centre so the scene point
        # that was under the cursor stays under the cursor.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(target / scale, target / scale)
        after = self.mapToScene(anchor.toPoint())
        center = self.mapToScene(viewport.center())
        self.centerOn(center - (after - before))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.user_interacted = True
        self._fit_pending = False
        self.zoom_changed.emit(self.transform().m11())
        self._update_chunk_visibility()
        self.request_visible_chunks()
        self.view_changed.emit()

    def center_on_world(self, x_mm: float, y_mm: float) -> None:
        self.centerOn(QPointF(x_mm, -y_mm))
        self.user_interacted = True
        self._fit_pending = False
        self.view_changed.emit()

    def reset_view(self) -> None:
        """Forget the previous project's zoom/pan before the next scene load."""

        self.user_interacted = False
        self._fit_pending = False
        self.select(None, None)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta == 0:
            event.ignore()
            return
        # One wheel notch is ~120 units. Clamp per-event zoom so high-resolution
        # wheels and trackpads cannot jump across the whole zoom range.
        factor = max(0.75, min(1.33, 1.0015 ** delta))
        self.zoom(factor, anchor=event.position())
        event.accept()

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.NativeGesture and \
                hasattr(event, "gestureType") and \
                event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom(1.0 + float(event.value()), anchor=event.position())
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.MouseButton.MiddleButton:
            self._begin_pan(event.position())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._placement_active:
                snap = max(0.001, self.snap_mm)
                world_x = round(pos.x() / snap) * snap
                world_y = round(-pos.y() / snap) * snap
                label = self._placement_label
                self.cancel_placement()
                self.status_message.emit(f"正在添加 {label}…")
                self.place_requested.emit(world_x, world_y)
                event.accept()
                return
            if self.tool == "measure":
                self._add_measure_point(pos)
                event.accept()
                return
            item = self._pick(pos)
            obj = self.item_objects.get(item) if item else None
            if item and obj and obj.get("component_id") and self.tool == "select" \
                    and self.editable_move and not self.read_only and not self.run_active:
                self._select_item(obj)
                self._drag_component = obj["component_id"]
                self._drag_start_scene = pos
                self._drag_moved = False
                rect = self._component_rect(obj["component_id"])
                self._drag_origin = (rect.center().x(), -rect.center().y())
                ghost = QGraphicsRectItem(rect)
                pen = QPen(QColor(COLORS["draft"]))
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setWidthF(0.2)
                ghost.setPen(pen)
                ghost.setBrush(QBrush(QColor(134, 190, 232, 40)))
                ghost.setZValue(1000)
                self._scene.addItem(ghost)
                self._drag_ghost = ghost
                self.viewport().grabMouse()
                self.setFocus()
                event.accept()
                return
            if item and obj:
                if not self._select_item(obj):
                    self.select(None, None)
                    self.object_selected.emit("", "")
                self.setFocus()
                event.accept()
                return
            # Empty space: a click clears selection; a drag pans the canvas.
            self._pan_candidate = True
            self._pan_start = event.position()
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(pos.x(), -pos.y())
        if self._panning:
            self._pan_to(event.position())
            return
        if self._pan_candidate:
            delta = event.position() - self._pan_start
            if abs(delta.x()) + abs(delta.y()) >= PAN_THRESHOLD_PX:
                self._begin_pan(self._pan_start)
                self._pan_to(event.position())
            return
        if self._placement_active and self._placement_ghost is not None:
            snap = max(0.001, self.snap_mm)
            world_x = round(pos.x() / snap) * snap
            world_y = round(-pos.y() / snap) * snap
            self._placement_ghost.setVisible(True)
            self._placement_ghost.setPos(world_x, -world_y)
            self.status_message.emit(
                f"{self._placement_label} → X {world_x:.2f} mm · Y {world_y:.2f} mm")
            return
        if self._drag_component and self._drag_start_scene and self._drag_ghost:
            delta = pos - self._drag_start_scene
            if abs(delta.x()) + abs(delta.y()) > 0.05:
                self._drag_moved = True
            self._drag_ghost.setPos(delta)
            self.status_message.emit(
                f"{self._drag_component} → X {pos.x():.2f} mm · Y {-pos.y():.2f} mm")
            return
        item = self._pick(pos)
        obj = self.item_objects.get(item) if item else None
        if obj:
            label = obj.get("ref") or obj.get("text") or obj.get("component_id") or \
                obj.get("net_id") or obj.get("id")
            detail = (obj.get("meta") or {}).get("net_name", "")
            self.status_message.emit(f"{label} {detail}".strip())
        self.view_changed.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._panning:
                self._end_pan()
                event.accept()
                return
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._panning:
                self._end_pan()
                event.accept()
                return
            if self._pan_candidate:
                self._pan_candidate = False
                self.select(None, None)
                self.object_selected.emit("", "")
                event.accept()
                return
            if self._drag_component and self._drag_ghost:
                component_id = self._drag_component
                origin = self._drag_origin
                ghost = self._drag_ghost
                moved = self._drag_moved
                delta = ghost.pos()
                self.viewport().releaseMouse()
                self._scene.removeItem(ghost)
                self._drag_component = None
                self._drag_ghost = None
                self._drag_start_scene = None
                self._drag_origin = None
                self._drag_moved = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                if moved and origin:
                    self.user_interacted = True
                    self._fit_pending = False
                    snap = max(0.001, self.snap_mm)
                    x_mm = round((origin[0] + delta.x()) / snap) * snap
                    y_mm = round((origin[1] - delta.y()) / snap) * snap
                    self.move_requested.emit(component_id, int(round(x_mm * 1_000_000)),
                                             int(round(y_mm * 1_000_000)))
                event.accept()
                return
        super().mouseReleaseEvent(event)

    # --------------------------------------------------------------------- pan
    def _begin_pan(self, press_pos: QPointF) -> None:
        self._panning = True
        self._pan_candidate = False
        self._pan_start = QPointF(press_pos)
        self._pan_center = self.mapToScene(self.viewport().rect().center())
        self.viewport().grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _pan_to(self, view_pos: QPointF) -> None:
        scale = max(1e-6, abs(self.transform().m11()))
        delta = view_pos - self._pan_start
        self.centerOn(self._pan_center -
                      QPointF(delta.x() / scale, delta.y() / scale))
        self.user_interacted = True
        self._fit_pending = False

    def _end_pan(self) -> None:
        self._panning = False
        self._pan_candidate = False
        self.viewport().releaseMouse()
        self.setCursor(Qt.CursorShape.CrossCursor
                       if (self._placement_active or self.tool == "measure")
                       else Qt.CursorShape.ArrowCursor)

    def _cancel_drag(self) -> None:
        if self._drag_ghost is not None:
            self._scene.removeItem(self._drag_ghost)
            self._drag_ghost = None
            self.viewport().releaseMouse()
        if self._drag_component:
            self.status_message.emit("已取消移动")
        self._drag_component = None
        self._drag_start_scene = None
        self._drag_origin = None
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self.mapToScene(event.position().toPoint())
        item = self._pick(pos)
        obj = self.item_objects.get(item) if item else None
        if obj and obj.get("component_id"):
            self._select_item(obj)
            center = self._component_rect(obj["component_id"]).center()
            self.center_on_world(center.x(), -center.y())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self._panning or self._pan_candidate:
                self._end_pan()
                event.accept()
                return
            if self._drag_component:
                self._cancel_drag()
                event.accept()
                return
            if self._placement_active:
                self.cancel_placement()
                event.accept()
                return
            self.clear_measure()
            self.select(None, None)
            self.object_selected.emit("", "")
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ helpers
    def _pick(self, scene_pos: QPointF) -> QGraphicsItem | None:
        # QGraphicsView.items(QPoint) expects viewport pixels, not scene units.
        for item in self.items(self.mapFromScene(scene_pos)):
            if item in self.item_objects:
                return item
        return None

    def _component_rect(self, component_id: str) -> QRectF:
        rect = QRectF()
        for item in self.component_items.get(component_id, []):
            if item in self.item_objects and self.item_objects[item].get("kind") == "text":
                continue
            r = item.mapToScene(item.boundingRect()).boundingRect()
            rect = r if rect.isNull() else rect.united(r)
        if rect.isNull():
            rect = QRectF(0, 0, 4, 4)
        return rect

    def _select_item(self, obj: dict[str, Any]) -> bool:
        if obj.get("component_id"):
            self.select("component", obj["component_id"])
            self.object_selected.emit("component", obj["component_id"])
            return True
        if obj.get("net_id"):
            self.select("net", obj["net_id"])
            self.object_selected.emit("net", obj["net_id"])
            return True
        return False

    def _add_measure_point(self, pos: QPointF) -> None:
        self._measure_points.append(pos)
        if len(self._measure_points) == 1:
            marker = QGraphicsEllipseItem(pos.x() - 0.4, pos.y() - 0.4, 0.8, 0.8)
            marker.setPen(QPen(QColor(COLORS["info"]), 0.15))
            marker.setBrush(QBrush(QColor(COLORS["info"])))
            marker.setZValue(900)
            self._scene.addItem(marker)
            self._measure_items.append(marker)
            self.status_message.emit("Measure: choose the second point (Esc to clear)")
            return
        a, b = self._measure_points[-2], self._measure_points[-1]
        line = QGraphicsLineItem(a.x(), a.y(), b.x(), b.y())
        pen = QPen(QColor(COLORS["selected"]), 0.18)
        pen.setStyle(Qt.PenStyle.DashLine)
        line.setPen(pen)
        line.setZValue(900)
        self._scene.addItem(line)
        self._measure_items.append(line)
        dx = (b.x() - a.x())
        dy = -(b.y() - a.y())
        distance = math.hypot(dx, dy)
        text = f"{distance:.2f} mm" if self.units == "mm" else f"{distance / 25.4:.3f} in"
        label = QGraphicsSimpleTextItem(text)
        font = QFont()
        font.setPixelSize(14)
        label.setFont(font)
        label.setBrush(QBrush(QColor(COLORS["selected"])))
        label.setScale(0.25)
        label.setPos((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 - 1.5)
        label.setZValue(901)
        self._scene.addItem(label)
        self._measure_items.append(label)
        self.status_message.emit(
            f"Measure ΔX {abs(dx):.2f} mm · ΔY {abs(dy):.2f} mm · distance {text}")
        self._measure_points.clear()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        painter.fillRect(rect, QColor(COLORS["background"]))
        if not self.grid_visible:
            return
        pixels_per_mm = max(1e-6, abs(self.transform().m11()))
        step = nice_step(rect.width(), pixels_per_mm, 95.0)
        minor = step / 5.0
        if minor * pixels_per_mm < 4:
            minor = step
        left = math.floor(rect.left() / minor) * minor
        top = math.floor(rect.top() / minor) * minor
        minor_pen = QPen(QColor(COLORS["gridSmall"]), 0.05)
        major_pen = QPen(QColor(COLORS["gridLarge"]), 0.07)
        axis_pen = QPen(QColor("#3C5464"), 0.1)
        x = left
        while x <= rect.right():
            is_major = abs((x / step) - round(x / step)) < 1e-6
            pen = axis_pen if abs(x) < 1e-9 else (major_pen if is_major else minor_pen)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += minor
        y = top
        while y <= rect.bottom():
            is_major = abs((y / step) - round(y / step)) < 1e-6
            pen = axis_pen if abs(y) < 1e-9 else (major_pen if is_major else minor_pen)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += minor

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self.user_interacted and not self._scene.sceneRect().isEmpty() \
                and not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._deferred_fit)
        self._update_chunk_visibility()
        self.request_visible_chunks()

    def _deferred_fit(self) -> None:
        self._fit_pending = False
        if not self.user_interacted:
            self._auto_fit()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        self._update_chunk_visibility()
        self.request_visible_chunks()
        self.view_changed.emit()


class Canvas2D(QWidget):
    object_selected = Signal(str, str)
    move_requested = Signal(str, int, int)
    place_requested = Signal(float, float)
    cursor_moved = Signal(float, float)
    zoom_changed = Signal(float)
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = CanvasView(self)
        self.top_ruler = RulerWidget("top", self.view)
        self.left_ruler = RulerWidget("left", self.view)
        self.corner = QWidget()
        self.corner.setFixedSize(22, 22)
        self.corner.setStyleSheet(f"background: {COLORS['shell']};")
        self.message = QLabel("", self.view.viewport())
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet(
            f"color: {COLORS['textSecondary']}; background: rgba(11,16,21,180);"
            f"padding: 14px 18px; border: 1px solid {COLORS['border']}; border-radius: 8px;")
        self.message.hide()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.corner, 0, 0)
        layout.addWidget(self.top_ruler, 0, 1)
        layout.addWidget(self.left_ruler, 1, 0)
        layout.addWidget(self.view, 1, 1)
        self.view.view_changed.connect(self._refresh_rulers)
        self.view.cursor_moved.connect(self.cursor_moved)
        self.view.zoom_changed.connect(self.zoom_changed)
        self.view.object_selected.connect(self.object_selected)
        self.view.move_requested.connect(self.move_requested)
        self.view.place_requested.connect(self.place_requested)
        self.view.status_message.connect(self.status_message)

    def _refresh_rulers(self) -> None:
        self.top_ruler.update()
        self.left_ruler.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_message()

    def _place_message(self) -> None:
        viewport = self.view.viewport().rect()
        self.message.adjustSize()
        self.message.move(viewport.center().x() - self.message.width() // 2,
                          viewport.center().y() - self.message.height() // 2)

    def load_scene(self, dto: dict[str, Any]) -> None:
        self.view.load_scene(dto)
        self._refresh_rulers()

    def set_chunk_loader(self, loader) -> None:
        self.view.set_chunk_loader(loader)

    def load_manifest(self, manifest: dict[str, Any]) -> None:
        self.view.load_manifest(manifest)
        self._refresh_rulers()

    def load_chunk(self, dto: dict[str, Any]) -> bool:
        loaded = self.view.load_chunk(dto)
        if loaded:
            self._refresh_rulers()
        return loaded

    def set_lod(self, level: int) -> None:
        self.view.set_lod(level)
        self._refresh_rulers()

    def visible_chunk_ids(self) -> list[str]:
        return self.view.visible_chunk_ids()

    def frame_stats(self, frames: int = 20) -> dict[str, float]:
        return self.view.frame_stats(frames)

    def set_message(self, text: str) -> None:
        if text:
            self.message.setText(text)
            self.message.adjustSize()
            self._place_message()
            self.message.show()
            self.message.raise_()
        else:
            self.message.hide()

    def fit(self) -> None:
        self.view.fit()

    def fit_content(self) -> None:
        self.view.fit_content()

    def fit_page(self) -> None:
        self.view.fit_page()

    def fit_selection(self) -> None:
        self.view.fit_selection()

    def fit_board(self) -> None:
        self.view.fit_board()

    def zoom(self, factor: float, *, anchor: QPointF | None = None) -> None:
        self.view.zoom(factor, anchor=anchor)

    def set_tool(self, tool: str) -> None:
        self.view.set_tool(tool)

    def begin_placement(self, label: str, width_mm: float = 4.0,
                        height_mm: float = 3.0) -> None:
        self.view.begin_placement(label, width_mm, height_mm)

    def cancel_placement(self) -> None:
        self.view.cancel_placement()

    def placement_active(self) -> bool:
        return self.view.placement_active()

    def set_units(self, units: str) -> None:
        self.view.set_units(units)

    def set_grid_visible(self, visible: bool) -> None:
        self.view.set_grid_visible(visible)

    def set_read_only(self, read_only: bool) -> None:
        self.view.set_read_only(read_only)

    def set_run_active(self, active: bool) -> None:
        self.view.set_run_active(active)

    def select(self, kind: str | None, object_id: str | None) -> None:
        self.view.select(kind, object_id)

    def center_on_world(self, x_mm: float, y_mm: float) -> None:
        self.view.center_on_world(x_mm, y_mm)

    def reset_view(self) -> None:
        self.view.reset_view()
