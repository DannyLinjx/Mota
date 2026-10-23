from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mota.desktop.job_panel import JobPanel  # noqa: E402
from mota.desktop.project_tree import ProjectTree  # noqa: E402
from mota.desktop.scene_adapter import (  # noqa: E402
    JOB_SUCCEEDED, SCENE_APPLIED, SCENE_STALE, SCENE_SUPERSEDED, SceneAdapter,
    VersionedSceneCache, manifest_from_dto,
)
from mota.desktop.widgets.canvas_2d import CanvasView  # noqa: E402
from mota.domain.models import (  # noqa: E402
    BoardIR, BoardPad, CircuitIR, Component, DesignVersion, FootprintInstance,
    Net, Placement, RatsnestLine, Rect, SchematicIR, SchematicWire,
    SymbolInstance, Track,
)
from mota.render.scene import pcb2d_scene, pcb3d_scene, schematic_scene  # noqa: E402

MM = 1_000_000
VERSION_A = DesignVersion.of_revision("rev_000001")
VERSION_B = DesignVersion.of_revision("rev_000002")


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    for widget in list(instance.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    instance.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    instance.processEvents()


def build_design(count: int = 60, columns: int = 10, spacing_mm: float = 6.0,
                 *, with_schematic: bool = True):
    """Synthetic design with ``count`` components spread over a grid."""

    pitch = int(spacing_mm * MM)
    rows = (count + columns - 1) // columns
    width = int((columns + 1) * spacing_mm * MM)
    height = int((rows + 1) * spacing_mm * MM)
    components: list[Component] = []
    footprints: list[FootprintInstance] = []
    pads: list[BoardPad] = []
    tracks: list[Track] = []
    nets: list[Net] = []
    ratsnest: list[RatsnestLine] = []
    symbols: list[SymbolInstance] = []
    wires: list[SchematicWire] = []
    for index in range(count):
        col = index % columns
        row = index // columns
        x = (col + 1) * 6_000_000
        y = (row + 1) * 6_000_000
        component_id = f"c{index}"
        reference = f"U{index + 1}"
        components.append(Component(
            id=component_id, reference=reference, part_id="demo",
            symbol_id="sym:demo", footprint_id="fp:demo",
            pin_pad_map={"1": ["1"], "2": ["2"]},
            placement=Placement(x_nm=x, y_nm=y)))
        footprints.append(FootprintInstance(
            component_id=component_id, footprint_id="fp:demo",
            reference=reference, x_nm=x, y_nm=y, rotation_deg=0,
            courtyard=Rect(x_nm=x - 1_500_000, y_nm=y - 1_000_000,
                           w_nm=3_000_000, h_nm=2_000_000)))
        nets.append(Net(id=f"n{index}", name=f"N{index}",
                        pins=[f"{component_id}:1", f"{component_id}:2"]))
        for pin, dx in (("1", -1_000_000), ("2", 1_000_000)):
            pads.append(BoardPad(
                id=f"{component_id}-{pin}", component_id=component_id,
                number=pin, net_id=f"n{index}", x_nm=x + dx, y_nm=y,
                w_nm=800_000, h_nm=800_000, shape="rect", drill_nm=400_000))
        tracks.append(Track(id=f"t{index}", net_id=f"n{index}", layer="F.Cu",
                            width_nm=250_000,
                            points=[[x - 1_000_000, y], [x + 1_000_000, y]]))
        if index % 2 == 0:
            ratsnest.append(RatsnestLine(net_id=f"n{index}",
                                         x1_nm=x, y1_nm=y,
                                         x2_nm=x, y2_nm=y + 2_000_000))
        symbols.append(SymbolInstance(
            component_id=component_id, symbol_id="sym:demo",
            reference=reference, value="demo", x_nm=x, y_nm=y,
            body_w_nm=2_000_000, body_h_nm=1_500_000))
        wires.append(SchematicWire(
            id=f"w{index}", net_id=f"n{index}",
            points=[[x - 2_000_000, y], [x + 2_000_000, y]]))
    board = BoardIR(
        outline=[[0, 0], [width, 0], [width, height], [0, height], [0, 0]],
        width_nm=width, height_nm=height, layer_count=2,
        footprints=footprints, pads=pads, tracks=tracks, ratsnest=ratsnest)
    circuit = CircuitIR(components=components, nets=nets)
    schematic = SchematicIR(page_w_nm=width, page_h_nm=height,
                            symbols=symbols, wires=wires)
    return circuit, schematic, board


def test_manifest_chunks_cover_every_object_and_roundtrip():
    circuit, _, board = build_design(36, columns=6)
    scene = pcb2d_scene("p1", VERSION_A, circuit, board)
    adapter = SceneAdapter(cell_mm=20.0)
    adapted = adapter.build(scene, design_version_key=VERSION_A.key)

    assert adapted.design_version_key == VERSION_A.key
    assert adapted.manifest.view == "pcb2d"
    assert adapted.manifest.cols >= 2 and adapted.manifest.rows >= 2
    assert 0 < len(adapted.manifest.chunks) <= adapted.manifest.cols * adapted.manifest.rows
    total_ids = sum(len(chunk.object_ids) for chunk in adapted.manifest.chunks)
    assert total_ids == len(scene.objects)
    assert sum(adapted.manifest.totals.values()) == len(scene.objects)
    assert "F.Cu" in adapted.manifest.layers
    assert adapted.manifest.net_counts["n0"] >= 1
    assert adapted.manifest.component_refs["c0"] == "U1"

    restored = manifest_from_dto(adapted.manifest.dto())
    assert restored.content_hash == adapted.manifest.content_hash
    assert [chunk.chunk_id for chunk in restored.chunks] == [
        chunk.chunk_id for chunk in adapted.manifest.chunks]
    payload = adapted.chunk_payload(adapted.manifest.chunks[0].chunk_id)
    assert payload["design_version_key"] == VERSION_A.key
    assert payload["objects"]


def test_cross_view_net_and_component_location():
    circuit, schematic, board = build_design(36, columns=6)
    adapter = SceneAdapter(cell_mm=20.0)
    scenes = {
        "pcb2d": adapter.build(pcb2d_scene("p1", VERSION_A, circuit, board),
                               design_version_key=VERSION_A.key),
        "schematic": adapter.build(
            schematic_scene("p1", VERSION_A, circuit, schematic),
            design_version_key=VERSION_A.key),
    }
    locations = adapter.locate_net(scenes, "n17")
    assert {item.view for item in locations} == {"pcb2d", "schematic"}
    for item in locations:
        assert item.objects > 0
        assert item.chunk_ids
        assert item.bounds[2] > 0

    component = adapter.locate_component(scenes, "c17")
    assert {item.view for item in component} == {"pcb2d", "schematic"}
    assert all(item.objects >= 2 for item in component)


def test_filters_use_layers_net_classes_and_unrouted():
    circuit, _, board = build_design(24, columns=6)
    adapter = SceneAdapter(cell_mm=20.0)
    adapted = adapter.build(
        pcb2d_scene("p1", VERSION_A, circuit, board),
        design_version_key=VERSION_A.key,
        net_classes={"n1": "POWER", "n2": "POWER"},
        unrouted=[{"net": "n3", "x": 12.0, "y": 12.0}])
    objects = [obj for chunk in adapted.chunks.values() for obj in chunk]
    copper = adapter.filter_objects(objects, layers=["F.Cu"])
    assert copper and all(obj["layer"] == "F.Cu" for obj in copper)
    power = adapter.filter_objects(objects, net_classes=["POWER"],
                                   net_map=adapted.manifest.net_classes)
    assert {obj.get("net_id") for obj in power} == {"n1", "n2"}
    unrouted = adapter.filter_objects(objects, only_unrouted=True,
                                      unrouted_nets=["n3"])
    assert {obj.get("net_id") for obj in unrouted} == {"n3"}
    high_lod = adapter.filter_objects(objects, lod=0)
    assert len(high_lod) < len(objects)
    assert adapted.manifest.unrouted[0].net == "n3"


def test_versioned_scene_cache_rejects_old_replies():
    circuit, _, board = build_design(12, columns=4)
    adapter = SceneAdapter(cell_mm=20.0)
    scene_a = adapter.build(pcb2d_scene("p1", VERSION_A, circuit, board),
                            design_version_key=VERSION_A.key)
    scene_b = adapter.build(pcb2d_scene("p1", VERSION_B, circuit, board),
                            design_version_key=VERSION_B.key)
    cache = VersionedSceneCache()
    token_a = cache.begin(VERSION_A.key)
    cache.set_current(VERSION_B.key)
    assert cache.accept(token_a, scene_a) == SCENE_STALE
    assert cache.current is None
    token_b = cache.begin(VERSION_B.key)
    assert cache.accept(token_b, scene_b) == SCENE_APPLIED
    assert cache.current is scene_b
    late = cache.begin(VERSION_B.key)
    newer = cache.begin(VERSION_B.key)
    assert cache.accept(late, scene_b) == SCENE_SUPERSEDED
    assert cache.accept(newer, scene_b) == SCENE_APPLIED
    assert cache.stats() == {"applied": 2, "stale": 1, "superseded": 1}


def test_job_panel_never_labels_a_stale_result_saved(app):
    panel = JobPanel()
    panel.set_current_version(VERSION_B.key)
    panel.submit("job_1", "build", VERSION_A.key, "old run")
    status = panel.accept("job_1", VERSION_A.key, JOB_SUCCEEDED,
                          message="finished on rev1")
    assert status == SCENE_STALE
    assert panel.saved_count() == 0
    assert panel.row_states()["job_1"] == "STALE"
    assert "STALE" in panel.table.item(0, 3).text()
    seen: list[str] = []
    panel.resolve_requested.connect(seen.append)
    button = panel.table.cellWidget(0, 4)
    assert button.text() == "Re-solve"
    button.click()
    assert seen == ["job_1"]

    panel.submit("job_2", "build", VERSION_B.key, "current run")
    assert panel.accept("job_2", VERSION_B.key, JOB_SUCCEEDED,
                        message="saved") == SCENE_APPLIED
    assert panel.saved_count() == 1
    assert panel.table.item(1, 3).text() == "Saved"


def test_canvas_ignores_stale_chunks_and_hides_lod(app):
    circuit, _, board = build_design(24, columns=6)
    adapter = SceneAdapter(cell_mm=20.0)
    adapted = adapter.build(pcb2d_scene("p1", VERSION_B, circuit, board),
                            design_version_key=VERSION_B.key)
    view = CanvasView()
    view.resize(900, 620)
    view.show()
    view.load_manifest(adapted.manifest.dto())
    first = adapted.manifest.chunks[0].chunk_id

    stale = adapted.chunk_payload(first)
    stale["design_version_key"] = VERSION_A.key
    assert view.load_chunk(stale) is False
    assert view.chunk_items == {}

    assert view.load_chunk(adapted.chunk_payload(first)) is True
    loaded = len(view.chunk_items[first])
    assert loaded > 0
    view.set_lod(0)
    hidden = [item for item in view.chunk_items[first] if not item.isVisible()]
    assert hidden, "text and detail items must disappear at LOD 0"
    view.set_lod(2)
    assert all(item.isVisible() for item in view.chunk_items[first]
               if int(view.item_objects[item].get("lod", 0)) <= 2)


def test_project_tree_navigates_nets_layers_and_unrouted(app):
    circuit, schematic, board = build_design(24, columns=6)
    adapter = SceneAdapter(cell_mm=20.0)
    manifests = {
        "pcb2d": adapter.build(
            pcb2d_scene("p1", VERSION_A, circuit, board),
            design_version_key=VERSION_A.key,
            net_classes={"n1": "POWER"},
            unrouted=[{"net": "n3", "x": 12.0, "y": 12.0}]).manifest,
        "schematic": adapter.build(
            schematic_scene("p1", VERSION_A, circuit, schematic),
            design_version_key=VERSION_A.key).manifest,
    }
    tree = ProjectTree()
    tree.resize(360, 640)
    tree.show()
    tree.set_checks({"counts": {"PASS": 7, "WARN": 1}})
    tree.set_manifests(manifests)

    assert "n3" in tree.nets()
    assert tree.layers()
    assert tree.unrouted_points()[0]["net"] == "n3"
    assert "POWER" in [tree.class_filter.itemText(index)
                       for index in range(tree.class_filter.count())]

    filters: list[dict] = []
    tree.filters_changed.connect(filters.append)
    tree.search.setText("unrouted")
    assert filters[-1]["only_unrouted"] is True

    located: list[dict] = []
    tree.locate_requested.connect(located.append)
    tree.locate("net", "n17")
    assert located and located[0]["id"] == "n17"
    assert located[0]["chunks"]
    assert {item["view"] for item in tree.locate("net", "n17")} == {
        "pcb2d", "schematic"}

    located.clear()
    point = tree.unrouted_points()[0]
    tree.locate("unrouted", "n3", point)
    assert located[0]["x"] == pytest.approx(12.0)
    assert located[0]["y"] == pytest.approx(12.0)


def test_cli_and_ui_share_the_same_edit_operations(app):
    from mota.cli.main import design_app
    from mota.desktop.controllers import ProjectController

    names = {command.name for command in design_app.registered_commands}
    assert {"patch", "move", "lock", "undo", "redo"} <= names

    class RecordingBridge:
        epoch = 1

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def call(self, method, params, callback=None, timeout_ms=0):
            self.calls.append((method, params))
            return "req"

    bridge = RecordingBridge()
    controller = ProjectController(bridge)
    controller.card = {"project_id": "p1"}
    controller.current_revision_id = "rev_000001"
    controller.patch([{"op": "lock_component", "component_id": "c1",
                       "locked": True}], "rev_000001", lambda result, error: None)
    method, params = bridge.calls[-1]
    assert method == "design.patch"
    assert params["operations"][0]["op"] == "lock_component"
    assert params["expected_revision_id"] == "rev_000001"


def test_thousand_component_stress_reports_real_metrics(app, capsys):
    started = time.perf_counter()
    circuit, schematic, board = build_design(1000, columns=40, spacing_mm=5.0)
    build_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    scene = pcb2d_scene("stress", VERSION_A, circuit, board)
    scene_ms = (time.perf_counter() - started) * 1000

    adapter = SceneAdapter(cell_mm=25.0)
    started = time.perf_counter()
    adapted = adapter.build(scene, design_version_key=VERSION_A.key,
                            net_classes={f"n{i}": ("POWER" if i % 3 == 0 else "signal")
                                         for i in range(1000)})
    adapt_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    locations = adapter.locate_net({"pcb2d": adapted}, "n512")
    locate_ms = (time.perf_counter() - started) * 1000
    assert locations and locations[0].chunk_ids

    view = CanvasView()
    view.resize(1280, 800)
    view.show()
    view.load_manifest(adapted.manifest.dto())
    loaded: list[str] = []

    def loader(chunk_id: str) -> None:
        payload = adapted.chunk_payload(chunk_id)
        if payload is not None and view.load_chunk(payload):
            loaded.append(chunk_id)

    view.set_chunk_loader(loader)
    view.request_visible_chunks()
    assert loaded
    frames = view.frame_stats(12)
    overview_chunks = len(view.visible_chunk_ids())
    view.zoom(6.0)
    zoomed_chunks = len(view.visible_chunk_ids())
    view.fit()
    view.set_lod(0)

    mesh_circuit, _, mesh_board = build_design(200, columns=20, spacing_mm=5.0)
    mesh_scene = pcb3d_scene("stress", VERSION_A, mesh_circuit, mesh_board)
    mesh_adapted = SceneAdapter(cell_mm=25.0).build(
        mesh_scene, design_version_key=VERSION_A.key)
    mesh_objects = sum(len(items) for items in mesh_adapted.chunks.values())
    mesh_table = mesh_adapted.manifest.mesh_table
    instances = sum(item["instances"] for item in mesh_table.values())
    naive_bytes = sum(item["bytes"] * item["instances"]
                      for item in mesh_table.values())
    table_bytes = sum(item["bytes"] for item in mesh_table.values())

    report = {
        "components": 1000,
        "scene_objects": len(scene.objects),
        "chunks": len(adapted.manifest.chunks),
        "build_ms": round(build_ms, 1),
        "scene_ms": round(scene_ms, 1),
        "adapter_ms": round(adapt_ms, 1),
        "locate_ms": round(locate_ms, 2),
        "visible_chunks": len(view.visible_chunk_ids()),
        "overview_chunks": overview_chunks,
        "zoomed_chunks": zoomed_chunks,
        "loaded_chunks": len(loaded),
        "fps": round(frames["fps"], 1),
        "p50_ms": round(frames["p50_ms"], 2),
        "p95_ms": round(frames["p95_ms"], 2),
        "mesh_objects": mesh_objects,
        "unique_meshes": len(mesh_table),
        "mesh_instances": instances,
        "mesh_table_bytes": table_bytes,
        "mesh_naive_bytes": naive_bytes,
    }
    print("C13_STRESS", report)
    with capsys.disabled():
        print(f"C13 stress: {report}")

    assert len(scene.objects) > 6000
    assert len(adapted.manifest.chunks) >= 4
    assert build_ms < 30000
    assert scene_ms < 30000
    assert adapt_ms < 30000
    assert locate_ms < 250
    assert frames["fps"] > 3, frames
    assert frames["p95_ms"] < 1000, frames
    assert zoomed_chunks < overview_chunks, (zoomed_chunks, overview_chunks)
    assert len(mesh_table) < mesh_objects / 10
    assert instances >= mesh_objects * 0.5
    assert table_bytes < naive_bytes
