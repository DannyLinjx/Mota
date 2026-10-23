"""Project tree: layers, nets, components, unrouted points and constraints.

The tree owns filtering and cross-view location; it never invents geometry.
Clicking a net or component emits the exact chunk ids and world coordinate that
contain it in each view, so the canvas can jump without reloading everything.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from mota.desktop.scene_adapter import SceneAdapter, SceneManifest

ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_LAYER = Qt.ItemDataRole.UserRole + 2
ROLE_POINT = Qt.ItemDataRole.UserRole + 3


class ProjectTree(QWidget):
    selection_changed = Signal(str, str)
    filters_changed = Signal(dict)
    locate_requested = Signal(dict)     # {"kind","id","view","x","y","chunks"}
    view_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.adapter = SceneAdapter()
        self.manifests: dict[str, SceneManifest] = {}
        self.checks: dict[str, Any] | None = None
        self._layer_filter: list[str] = []
        self._ignore_changes = False

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter nets, components, layers…")
        self.search.setClearButtonEnabled(True)
        self.class_filter = QComboBox(self)
        self.class_filter.addItem("All net classes", "")
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Design", "Detail"])
        self.tree.setColumnWidth(0, 190)
        self.status = QLabel("Open a project to navigate.", self)
        self.status.setWordWrap(True)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.search, 1)
        top.addWidget(self.class_filter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)

        self.search.textChanged.connect(self._apply_search)
        self.class_filter.currentIndexChanged.connect(self._emit_filters)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)

    # ------------------------------------------------------------------ inputs
    def set_manifests(self, manifests: dict[str, SceneManifest]) -> None:
        self.manifests = dict(manifests)
        classes = sorted({name for manifest in manifests.values()
                          for name in manifest.net_classes.values() if name})
        current = self.class_filter.currentData()
        self.class_filter.blockSignals(True)
        self.class_filter.clear()
        self.class_filter.addItem("All net classes", "")
        for name in classes:
            self.class_filter.addItem(name, name)
        index = self.class_filter.findData(current)
        self.class_filter.setCurrentIndex(max(0, index))
        self.class_filter.blockSignals(False)
        self.rebuild()

    def set_checks(self, report: dict[str, Any] | None) -> None:
        self.checks = report
        self.rebuild()

    def layers(self) -> list[str]:
        layers: set[str] = set()
        for manifest in self.manifests.values():
            layers.update(manifest.layers)
        return sorted(layers)

    def nets(self) -> list[str]:
        nets: dict[str, int] = {}
        for manifest in self.manifests.values():
            for net, count in manifest.net_counts.items():
                nets[net] = nets.get(net, 0) + count
        return sorted(nets)

    def unrouted_points(self) -> list[dict[str, Any]]:
        seen: set[tuple[str, float, float]] = set()
        out: list[dict[str, Any]] = []
        for view, manifest in sorted(self.manifests.items()):
            for point in manifest.unrouted:
                key = (point.net, round(point.x, 3), round(point.y, 3))
                if key in seen:
                    continue
                seen.add(key)
                record = point.dto()
                record["view"] = view
                out.append(record)
        return out

    # ------------------------------------------------------------------- build
    def rebuild(self) -> None:
        checked = set(self._layer_filter) or set(self.layers())
        expanded = {self.tree.topLevelItem(index).text(0)
                    for index in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(index).isExpanded()}
        self._ignore_changes = True
        self.tree.clear()

        views = self._section("Views")
        for view, label in (("schematic", "Schematic"),
                            ("pcb2d", "PCB · 2D"), ("pcb3d", "PCB · 3D")):
            item = QTreeWidgetItem(views, [label, view])
            item.setData(0, ROLE_KIND, "view")
            item.setData(0, ROLE_ID, view)
            item.setExpanded(True)

        layers = self._section("Layers")
        for layer in self.layers():
            item = QTreeWidgetItem(layers, [layer, ""])
            item.setData(0, ROLE_KIND, "layer")
            item.setData(0, ROLE_LAYER, layer)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if layer in checked
                               else Qt.CheckState.Unchecked)
        layers.setExpanded(True)

        unrouted_nets = {point["net"] for point in self.unrouted_points()}
        classes: dict[str, str] = {}
        for manifest in self.manifests.values():
            classes.update(manifest.net_classes)
        nets = self._section("Nets")
        for net in self.nets():
            routed = "unrouted" if net in unrouted_nets else "routed"
            detail = f"{classes.get(net) or 'signal'} · {routed}"
            item = QTreeWidgetItem(nets, [net or "(no net)", detail])
            item.setData(0, ROLE_KIND, "net")
            item.setData(0, ROLE_ID, net)
            if net in unrouted_nets:
                item.setForeground(0, Qt.GlobalColor.yellow)
        nets.setExpanded(bool(unrouted_nets))

        components = self._section("Components")
        refs: dict[str, str] = {}
        for manifest in self.manifests.values():
            for component_id, ref in manifest.component_refs.items():
                refs.setdefault(component_id, ref)
        for component_id, ref in sorted(refs.items(), key=lambda row: row[1]):
            item = QTreeWidgetItem(components, [ref, component_id])
            item.setData(0, ROLE_KIND, "component")
            item.setData(0, ROLE_ID, component_id)

        points = self._section("Unrouted")
        for point in self.unrouted_points():
            item = QTreeWidgetItem(points, [
                point["net"] or "(no net)",
                f"X {point['x']:.2f} · Y {point['y']:.2f} mm · {point['view']}"])
            item.setData(0, ROLE_KIND, "unrouted")
            item.setData(0, ROLE_ID, point["net"])
            item.setData(0, ROLE_POINT, point)
        points.setExpanded(bool(self.unrouted_points()))

        constraints = self._section("Constraints")
        counts = (self.checks or {}).get("counts") or {}
        if counts:
            for key in sorted(counts):
                item = QTreeWidgetItem(constraints, [str(key), str(counts[key])])
                item.setData(0, ROLE_KIND, "constraint")
                item.setData(0, ROLE_ID, key)
        else:
            QTreeWidgetItem(constraints, ["No validation report", ""])
        constraints.setExpanded(True)

        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            item.setExpanded(item.text(0) in expanded or item.text(0) in {
                "Layers", "Constraints"})
        self._ignore_changes = False
        self._apply_search(self.search.text())
        self.status.setText(
            f"{len(self.nets())} nets · {len(self.layers())} layers · "
            f"{len(self.unrouted_points())} unrouted point(s) · "
            f"{len(self.manifests)} view(s)")

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [title, ""])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setFirstColumnSpanned(True)
        return item

    # ----------------------------------------------------------------- filters
    def filters(self) -> dict[str, Any]:
        layers = [layer for layer in self.layers()
                  if layer in self._layer_filter] if self._layer_filter \
            else self.layers()
        net_class = self.class_filter.currentData() or ""
        return {
            "layers": layers,
            "net_classes": [net_class] if net_class else [],
            "only_unrouted": self.search.text().strip().lower() == "unrouted",
        }

    def _emit_filters(self) -> None:
        self.filters_changed.emit(self.filters())

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._ignore_changes or not item.data(0, ROLE_LAYER):
            return
        layers: list[str] = []
        for index in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(index)
            for child_index in range(section.childCount()):
                child = section.child(child_index)
                if child.data(0, ROLE_LAYER) and \
                        child.checkState(0) == Qt.CheckState.Checked:
                    layers.append(str(child.data(0, ROLE_LAYER)))
        self._layer_filter = sorted(set(layers))
        self._emit_filters()

    def _apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(index)
            visible = 0
            for child_index in range(section.childCount()):
                child = section.child(child_index)
                haystack = f"{child.text(0)} {child.text(1)}".lower()
                hidden = bool(needle) and needle not in haystack \
                    and needle != "unrouted"
                child.setHidden(hidden)
                visible += 0 if hidden else 1
            section.setHidden(visible == 0 and bool(needle)
                              and needle != "unrouted")
        self._emit_filters()

    # ------------------------------------------------------------------ locate
    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        kind = item.data(0, ROLE_KIND)
        object_id = item.data(0, ROLE_ID)
        if kind == "view":
            self.view_requested.emit(str(object_id))
            return
        if kind not in {"net", "component", "unrouted"}:
            return
        self.selection_changed.emit(str(kind), str(object_id))
        self.locate(str(kind), str(object_id), item.data(0, ROLE_POINT))

    def locate(self, kind: str, object_id: str,
               point: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if kind == "unrouted" and point is not None:
            payload = {"kind": kind, "id": object_id,
                       "view": point.get("view", ""),
                       "x": float(point.get("x") or 0),
                       "y": float(point.get("y") or 0),
                       "chunks": [], "unrouted": True}
            self.locate_requested.emit(payload)
            return [payload]
        if kind == "net":
            locations = self.adapter.locate_net(self.manifests, object_id)
        elif kind == "component":
            locations = self.adapter.locate_component(self.manifests, object_id)
        else:
            return []
        payloads: list[dict[str, Any]] = []
        for location in locations:
            dto = location.dto()
            x, y, width, height = location.bounds
            payloads.append({"kind": kind, "id": object_id,
                             "view": dto["view"],
                             "x": x + width / 2, "y": y + height / 2,
                             "chunks": dto["chunk_ids"],
                             "unrouted": dto.get("unrouted", False)})
        if payloads:
            self.locate_requested.emit(payloads[0])
        return payloads
