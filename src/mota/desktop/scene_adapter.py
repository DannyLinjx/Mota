"""Version-bound scene manifests, chunking, LOD and stale-reply guards (C13).

Everything here is pure Python so it can be tested without a display. The Qt
widgets consume these structures; they never decide on their own whether a
scene or job result belongs to the current design version.

Rules encoded here:

- a scene manifest is only current for exactly one ``design_version_key``;
- chunks are addressed by ``(version_key, view, chunk_id)``;
- a late reply for an older version is reported as ``STALE`` and is never
  written into the current scene or labelled as saved;
- 3D meshes are deduplicated by geometry key and shared between instances.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

SCENE_APPLIED = "APPLIED"
SCENE_STALE = "STALE"
SCENE_SUPERSEDED = "SUPERSEDED"

JOB_QUEUED = "QUEUED"
JOB_RUNNING = "RUNNING"
JOB_SUCCEEDED = "SUCCEEDED"
JOB_FAILED = "FAILED"
JOB_CANCELLED = "CANCELLED"
JOB_STALE = "STALE"

TEXT_LAYERS = {"Silkscreen", "SilkS", "Courtyard", "Drill", "NPTH"}


def _sha(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def object_bounds(obj: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Axis-aligned bounds in scene millimetres."""

    points = obj.get("points") or []
    if points:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    kind = obj.get("kind")
    if kind == "rect" and obj.get("x") is not None:
        return (float(obj["x"]), float(obj["y"] or 0),
                float(obj.get("w") or 0), float(obj.get("h") or 0))
    if kind == "circle" and obj.get("x") is not None:
        radius = float(obj.get("r") or 0)
        return (float(obj["x"]) - radius, float(obj["y"] or 0) - radius,
                radius * 2, radius * 2)
    if kind == "pad" and obj.get("x") is not None:
        w, h = float(obj.get("w") or 0), float(obj.get("h") or 0)
        return (float(obj["x"]) - w / 2, float(obj["y"] or 0) - h / 2, w, h)
    if kind == "text" and obj.get("x") is not None:
        size = float(obj.get("font_size") or 1.5)
        width = max(size, len(str(obj.get("text") or "")) * size * 0.6)
        return float(obj["x"]), float(obj["y"] or 0), width, size
    matrix = obj.get("world_matrix")
    if matrix and len(matrix) >= 16:
        return float(matrix[3]) - 1.0, float(matrix[7]) - 1.0, 2.0, 2.0
    return None


def _center(bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = bounds
    return x + w / 2, y + h / 2


def object_lod(obj: dict[str, Any]) -> int:
    """0 = always drawn, 1 = structural detail, 2 = labels and tiny drill marks."""

    kind = obj.get("kind")
    layer = str(obj.get("layer") or "")
    if kind == "text":
        return 2
    bounds = object_bounds(obj)
    if bounds is not None and max(bounds[2], bounds[3]) < 0.6 and kind in {
            "circle", "rect", "line", "polyline"}:
        return 2
    if layer in TEXT_LAYERS:
        return 1
    if kind in {"polygon", "mesh", "pad", "polyline"}:
        return 0
    return 1


@dataclass(frozen=True)
class ChunkSpec:
    chunk_id: str
    col: int
    row: int
    x: float
    y: float
    w: float
    h: float
    object_ids: tuple[str, ...]
    counts: dict[str, int]
    lod_counts: dict[str, int]
    sha256: str

    def dto(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id, "col": self.col, "row": self.row,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "object_ids": list(self.object_ids), "counts": dict(self.counts),
            "lod_counts": dict(self.lod_counts), "sha256": self.sha256,
        }


@dataclass(frozen=True)
class UnroutedPoint:
    net: str
    x: float
    y: float
    object_id: str = ""
    reason: str = "unrouted"

    def dto(self) -> dict[str, Any]:
        return {"net": self.net, "x": self.x, "y": self.y,
                "object_id": self.object_id, "reason": self.reason}


@dataclass(frozen=True)
class SceneManifest:
    project_id: str
    view: str
    design_version_key: str
    bounds: dict[str, float]
    coordinate_system: dict[str, Any]
    cols: int
    rows: int
    cell_mm: float
    chunks: tuple[ChunkSpec, ...]
    layers: tuple[str, ...]
    net_classes: dict[str, str]
    unrouted: tuple[UnroutedPoint, ...]
    totals: dict[str, int]
    mesh_table: dict[str, dict[str, Any]]
    component_refs: dict[str, str]
    net_counts: dict[str, int]
    component_counts: dict[str, int]
    net_chunks: dict[str, list[str]]
    net_bounds: dict[str, list[float]]
    component_chunks: dict[str, list[str]]
    component_bounds: dict[str, list[float]]
    content_hash: str

    def chunk(self, chunk_id: str) -> ChunkSpec | None:
        for item in self.chunks:
            if item.chunk_id == chunk_id:
                return item
        return None

    def dto(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id, "view": self.view,
            "design_version_key": self.design_version_key,
            "bounds": dict(self.bounds),
            "coordinate_system": dict(self.coordinate_system),
            "grid": {"cols": self.cols, "rows": self.rows,
                     "cell_mm": self.cell_mm},
            "chunks": [item.dto() for item in self.chunks],
            "layers": list(self.layers),
            "net_classes": dict(self.net_classes),
            "unrouted": [item.dto() for item in self.unrouted],
            "totals": dict(self.totals),
            "mesh_table": dict(self.mesh_table),
            "component_refs": dict(self.component_refs),
            "net_counts": dict(self.net_counts),
            "component_counts": dict(self.component_counts),
            "net_chunks": {key: list(value)
                           for key, value in self.net_chunks.items()},
            "net_bounds": {key: list(value)
                           for key, value in self.net_bounds.items()},
            "component_chunks": {key: list(value)
                                 for key, value in self.component_chunks.items()},
            "component_bounds": {key: list(value)
                                 for key, value in self.component_bounds.items()},
            "content_hash": self.content_hash,
        }


@dataclass
class AdapterScene:
    manifest: SceneManifest
    chunks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    project_id: str = ""
    view: str = ""
    design_version_key: str = ""

    def chunk_payload(self, chunk_id: str) -> dict[str, Any] | None:
        spec = self.manifest.chunk(chunk_id)
        if spec is None:
            return None
        return {
            "chunk_id": chunk_id, "view": self.view,
            "design_version_key": self.design_version_key,
            "sha256": spec.sha256,
            "objects": self.chunks.get(chunk_id, []),
            "mesh_table": self.manifest.mesh_table,
            "bounds": {"x": spec.x, "y": spec.y, "w": spec.w, "h": spec.h},
        }


@dataclass(frozen=True)
class NetLocation:
    view: str
    net: str
    chunk_ids: tuple[str, ...]
    bounds: tuple[float, float, float, float]
    objects: int
    unrouted: bool = False

    def dto(self) -> dict[str, Any]:
        return {"view": self.view, "net": self.net,
                "chunk_ids": list(self.chunk_ids),
                "bounds": {"x": self.bounds[0], "y": self.bounds[1],
                           "width": self.bounds[2], "height": self.bounds[3]},
                "objects": self.objects, "unrouted": self.unrouted}


def _sha(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MeshLibrary:
    """Geometry-keyed 3D mesh table shared by every instance."""

    def __init__(self) -> None:
        self._meshes: dict[str, dict[str, Any]] = {}
        self._instances: dict[str, int] = {}

    def intern(self, obj: dict[str, Any]) -> str | None:
        vertices = obj.get("vertices")
        faces = obj.get("faces")
        if not vertices or not faces:
            return None
        key = "mesh_" + _sha({"v": vertices, "f": faces})[:16]
        if key not in self._meshes:
            self._meshes[key] = {"vertices": vertices, "faces": faces,
                                 "bytes": len(json.dumps(vertices)) +
                                          len(json.dumps(faces))}
        self._instances[key] = self._instances.get(key, 0) + 1
        return key

    @property
    def table(self) -> dict[str, dict[str, Any]]:
        return {
            key: {"vertices": value["vertices"], "faces": value["faces"],
                  "bytes": value["bytes"], "instances": self._instances.get(key, 0)}
            for key, value in self._meshes.items()
        }

    def stats(self) -> dict[str, int]:
        unique = len(self._meshes)
        instances = sum(self._instances.values())
        bytes_per_mesh = sum(value["bytes"] for value in self._meshes.values())
        return {"unique_meshes": unique, "instances": instances,
                "table_bytes": bytes_per_mesh,
                "naive_bytes": sum(value["bytes"] * self._instances.get(key, 0)
                                   for key, value in self._meshes.items())}


class SceneAdapter:
    def __init__(self, *, cell_mm: float = 25.0,
                 max_chunks: int = 256) -> None:
        if cell_mm <= 0:
            raise ValueError("cell_mm must be positive")
        self.cell_mm = float(cell_mm)
        self.max_chunks = max(1, int(max_chunks))

    def build(self, scene: Any, *, design_version_key: str | None = None,
              net_classes: dict[str, str] | None = None,
              unrouted: Iterable[dict[str, Any] | UnroutedPoint] | None = None
              ) -> AdapterScene:
        dto = scene if isinstance(scene, dict) else scene.model_dump()
        view = str(dto.get("scene_kind") or dto.get("view") or "")
        key = design_version_key or str(dto.get("design_version_key") or "")
        bounds = dto.get("bounds") or {}
        width = float(bounds.get("width") or 0) or 1.0
        height = float(bounds.get("height") or 0) or 1.0
        origin_x = float(bounds.get("x") or 0)
        origin_y = float(bounds.get("y") or 0)
        cell = self.cell_mm
        cols = max(1, int(math.ceil(width / cell)))
        rows = max(1, int(math.ceil(height / cell)))
        while cols * rows > self.max_chunks:
            cell *= 1.25
            cols = max(1, int(math.ceil(width / cell)))
            rows = max(1, int(math.ceil(height / cell)))

        library = MeshLibrary()
        buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
        totals: dict[str, int] = {}
        layers: set[str] = set()
        component_refs: dict[str, str] = {}
        net_counts: dict[str, int] = {}
        component_counts: dict[str, int] = {}
        grouped_net_classes = dict(net_classes or {})
        for raw in dto.get("objects") or []:
            obj = dict(raw)
            mesh_key = library.intern(obj)
            if mesh_key is not None:
                obj.pop("vertices", None)
                obj.pop("faces", None)
                obj["mesh_ref"] = mesh_key
            obj["lod"] = object_lod(obj)
            if obj.get("component_id") and obj.get("ref"):
                component_refs.setdefault(str(obj["component_id"]),
                                          str(obj["ref"]))
            if obj.get("component_id"):
                component = str(obj["component_id"])
                component_counts[component] = component_counts.get(component, 0) + 1
            if obj.get("net_id"):
                net = str(obj["net_id"])
                net_counts[net] = net_counts.get(net, 0) + 1
            position = object_bounds(obj)
            if position is None:
                col, row = 0, 0
            else:
                center_x, center_y = _center(position)
                col = min(cols - 1, max(0, int((center_x - origin_x) // cell)))
                row = min(rows - 1, max(0, int((center_y - origin_y) // cell)))
            buckets.setdefault((col, row), []).append(obj)
            totals[obj.get("kind", "unknown")] = totals.get(obj.get("kind", "unknown"), 0) + 1
            if obj.get("layer"):
                layers.add(str(obj["layer"]))
            net_id = obj.get("net_id")
            if net_id and str(net_id) not in grouped_net_classes:
                meta = obj.get("meta") or {}
                if meta.get("net_class"):
                    grouped_net_classes[str(net_id)] = str(meta["net_class"])

        chunks: list[ChunkSpec] = []
        payloads: dict[str, list[dict[str, Any]]] = {}
        for (col, row), objects in sorted(buckets.items()):
            chunk_id = f"{view}:{col}:{row}"
            counts: dict[str, int] = {}
            lod_counts = {"0": 0, "1": 0, "2": 0}
            for obj in objects:
                kind = str(obj.get("kind") or "unknown")
                counts[kind] = counts.get(kind, 0) + 1
                lod_counts[str(obj.get("lod", 0))] += 1
            digest = _sha(objects)
            chunks.append(ChunkSpec(
                chunk_id=chunk_id, col=col, row=row,
                x=origin_x + col * cell, y=origin_y + row * cell,
                w=cell, h=cell,
                object_ids=tuple(str(obj.get("id") or "") for obj in objects),
                counts=counts, lod_counts=lod_counts, sha256=digest))
            payloads[chunk_id] = objects

        net_chunks: dict[str, list[str]] = {}
        net_bounds: dict[str, list[float]] = {}
        component_chunks: dict[str, list[str]] = {}
        component_bounds: dict[str, list[float]] = {}

        def index_location(chunks: dict[str, list[str]],
                           bounds: dict[str, list[float]], key: str,
                           chunk_id: str,
                           box: tuple[float, float, float, float] | None) -> None:
            if not key:
                return
            entry = chunks.setdefault(key, [])
            if chunk_id not in entry:
                entry.append(chunk_id)
            if box is None:
                return
            if key not in bounds:
                bounds[key] = list(box)
            else:
                current = bounds[key]
                min_x = min(current[0], box[0])
                min_y = min(current[1], box[1])
                max_x = max(current[0] + current[2], box[0] + box[2])
                max_y = max(current[1] + current[3], box[1] + box[3])
                bounds[key] = [min_x, min_y, max_x - min_x, max_y - min_y]

        for chunk_id, objects in payloads.items():
            for obj in objects:
                box = object_bounds(obj)
                index_location(net_chunks, net_bounds,
                               str(obj.get("net_id") or ""), chunk_id, box)
                index_location(component_chunks, component_bounds,
                               str(obj.get("component_id") or ""), chunk_id, box)

        unrouted_points: list[UnroutedPoint] = []
        for item in unrouted or []:
            if isinstance(item, UnroutedPoint):
                unrouted_points.append(item)
            else:
                unrouted_points.append(UnroutedPoint(
                    net=str(item.get("net") or ""),
                    x=float(item.get("x") or 0), y=float(item.get("y") or 0),
                    object_id=str(item.get("object_id") or ""),
                    reason=str(item.get("reason") or "unrouted")))
        if not unrouted_points:
            for obj in dto.get("objects") or []:
                meta = obj.get("meta") or {}
                if meta.get("unrouted"):
                    bounds_obj = object_bounds(obj)
                    if bounds_obj is not None:
                        cx, cy = _center(bounds_obj)
                        unrouted_points.append(UnroutedPoint(
                            net=str(obj.get("net_id") or ""), x=cx, y=cy,
                            object_id=str(obj.get("id") or ""),
                            reason=str(meta.get("reason") or "unrouted")))

        manifest = SceneManifest(
            project_id=str(dto.get("project_id") or ""), view=view,
            design_version_key=key, bounds={"x": origin_x, "y": origin_y,
                                            "width": width, "height": height},
            coordinate_system=dict(dto.get("coordinate_system") or {}),
            cols=cols, rows=rows, cell_mm=cell, chunks=tuple(chunks),
            layers=tuple(sorted(layers)), net_classes=grouped_net_classes,
            unrouted=tuple(unrouted_points), totals=totals,
            mesh_table=library.table,
            component_refs=component_refs, net_counts=net_counts,
            component_counts=component_counts,
            net_chunks=net_chunks, net_bounds=net_bounds,
            component_chunks=component_chunks,
            component_bounds=component_bounds,
            content_hash=_sha({"key": key, "view": view,
                               "chunks": [item.sha256 for item in chunks],
                               "meshes": sorted(library.table)}))
        return AdapterScene(manifest=manifest, chunks=payloads,
                            project_id=manifest.project_id, view=view,
                            design_version_key=key)

    # ------------------------------------------------------------------ filters
    def filter_objects(self, objects: Iterable[dict[str, Any]], *,
                       layers: Iterable[str] | None = None,
                       net_classes: Iterable[str] | None = None,
                       nets: Iterable[str] | None = None,
                       only_unrouted: bool = False,
                       unrouted_nets: Iterable[str] | None = None,
                       net_map: dict[str, str] | None = None,
                       lod: int = 2) -> list[dict[str, Any]]:
        layers_set = set(layers) if layers is not None else None
        classes_set = set(net_classes) if net_classes is not None else None
        nets_set = set(nets) if nets is not None else None
        unrouted_set = set(unrouted_nets or ())
        net_map = net_map or {}
        out: list[dict[str, Any]] = []
        for obj in objects:
            if int(obj.get("lod", 0)) > lod:
                continue
            if layers_set is not None and str(obj.get("layer") or "") not in layers_set:
                continue
            net_id = str(obj.get("net_id") or "")
            if nets_set is not None and net_id not in nets_set:
                continue
            if classes_set is not None:
                if net_map.get(net_id, "") not in classes_set:
                    continue
            if only_unrouted and net_id not in unrouted_set:
                continue
            out.append(obj)
        return out

    # ------------------------------------------------------------------- locate
    def locate_net(self, scenes: dict[str, Any], net: str,
                   *, unrouted_nets: Iterable[str] | None = None) -> list[NetLocation]:
        locations: list[NetLocation] = []
        unrouted_set = set(unrouted_nets or ())
        for view, scene in sorted(scenes.items()):
            manifest = scene.manifest if isinstance(scene, AdapterScene) else scene
            chunk_ids = list(manifest.net_chunks.get(str(net), []))
            if not chunk_ids:
                continue
            box = manifest.net_bounds.get(str(net), [0.0, 0.0, 0.0, 0.0])
            locations.append(NetLocation(
                view=view, net=str(net), chunk_ids=tuple(sorted(chunk_ids)),
                bounds=(box[0], box[1], box[2], box[3]),
                objects=manifest.net_counts.get(str(net), 0),
                unrouted=str(net) in unrouted_set))
        return locations

    def locate_component(self, scenes: dict[str, Any],
                         component_id: str) -> list[NetLocation]:
        locations: list[NetLocation] = []
        for view, scene in sorted(scenes.items()):
            manifest = scene.manifest if isinstance(scene, AdapterScene) else scene
            chunk_ids = list(manifest.component_chunks.get(component_id, []))
            if not chunk_ids:
                continue
            box = manifest.component_bounds.get(component_id,
                                                [0.0, 0.0, 0.0, 0.0])
            locations.append(NetLocation(
                view=view, net=component_id,
                chunk_ids=tuple(sorted(chunk_ids)),
                bounds=(box[0], box[1], box[2], box[3]),
                objects=manifest.component_counts.get(component_id, 0)))
        return locations

    def visible_chunks(self, scene: AdapterScene, viewport: dict[str, float],
                       *, only_loaded: bool = True) -> list[str]:
        x = float(viewport.get("x") or 0)
        y = float(viewport.get("y") or 0)
        w = float(viewport.get("width") or 0)
        h = float(viewport.get("height") or 0)
        out: list[str] = []
        for spec in scene.manifest.chunks:
            if spec.x >= x + w or spec.x + spec.w <= x:
                continue
            if spec.y >= y + h or spec.y + spec.h <= y:
                continue
            if only_loaded and spec.chunk_id not in scene.chunks:
                continue
            out.append(spec.chunk_id)
        return out


# --------------------------------------------------------------------------- #
# Stale-reply guards
# --------------------------------------------------------------------------- #

class VersionedSceneCache:
    """Only the manifest for the current design version can become current."""

    def __init__(self) -> None:
        self.current_key: str | None = None
        self.current: AdapterScene | None = None
        self._sequence = 0
        self._latest: dict[str, int] = {}
        self.applied = 0
        self.stale = 0
        self.superseded = 0

    def set_current(self, design_version_key: str | None) -> None:
        self.current_key = design_version_key

    def begin(self, design_version_key: str) -> tuple[str, int]:
        self._sequence += 1
        self._latest[design_version_key] = self._sequence
        return design_version_key, self._sequence

    def accept(self, token: tuple[str, int],
               scene: AdapterScene) -> str:
        key, sequence = token
        if self.current_key is None or key != self.current_key:
            self.stale += 1
            return SCENE_STALE
        if scene.design_version_key != key:
            self.stale += 1
            return SCENE_STALE
        if self._latest.get(key, 0) != sequence:
            self.superseded += 1
            return SCENE_SUPERSEDED
        self.current = scene
        self.applied += 1
        return SCENE_APPLIED

    def stats(self) -> dict[str, int]:
        return {"applied": self.applied, "stale": self.stale,
                "superseded": self.superseded}


@dataclass
class JobSnapshot:
    job_id: str
    kind: str
    design_version_key: str
    state: str = JOB_QUEUED
    progress: float = 0.0
    message: str = ""
    stale: bool = False
    saved: bool = False

    def dto(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "kind": self.kind,
                "design_version_key": self.design_version_key,
                "state": self.state, "progress": self.progress,
                "message": self.message, "stale": self.stale,
                "saved": self.saved}


class VersionedJobBoard:
    """Job rows whose results are bound to the design version that started them."""

    def __init__(self) -> None:
        self.current_key: str | None = None
        self._jobs: dict[str, JobSnapshot] = {}
        self._order: list[str] = []
        self.stale_results = 0

    def set_current(self, design_version_key: str | None) -> None:
        self.current_key = design_version_key

    def submit(self, job_id: str, kind: str, design_version_key: str,
               message: str = "") -> JobSnapshot:
        job = JobSnapshot(job_id=job_id, kind=kind,
                          design_version_key=design_version_key,
                          state=JOB_QUEUED, message=message)
        self._jobs[job_id] = job
        if job_id not in self._order:
            self._order.append(job_id)
        return job

    def start(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job and not job.stale:
            job.state = JOB_RUNNING

    def accept(self, job_id: str, design_version_key: str, state: str, *,
               message: str = "", progress: float = 1.0) -> str:
        job = self._jobs.get(job_id)
        if job is None:
            return "UNKNOWN_JOB"
        if self.current_key is None or design_version_key != self.current_key:
            job.stale = True
            job.saved = False
            job.state = JOB_STALE
            job.message = ("Result ignored: it belongs to "
                           f"{design_version_key}, not the current "
                           f"{self.current_key}.")
            self.stale_results += 1
            return SCENE_STALE
        job.design_version_key = design_version_key
        job.state = state
        job.message = message
        job.progress = progress
        job.stale = False
        job.saved = state == JOB_SUCCEEDED
        return SCENE_APPLIED

    def saved_jobs(self) -> list[str]:
        return [job_id for job_id in self._order
                if self._jobs[job_id].saved and not self._jobs[job_id].stale]

    def rows(self) -> list[JobSnapshot]:
        return [self._jobs[job_id] for job_id in self._order]

    def summary(self) -> dict[str, int]:
        rows = self.rows()
        return {"total": len(rows),
                "saved": sum(1 for row in rows if row.saved),
                "stale": sum(1 for row in rows if row.stale),
                "running": sum(1 for row in rows if row.state == JOB_RUNNING)}


def manifest_from_dto(dto: dict[str, Any]) -> SceneManifest:
    """Rebuild a manifest from its wire DTO (used by the UI and tests)."""

    grid = dto.get("grid") or {}
    chunks = tuple(ChunkSpec(
        chunk_id=str(item["chunk_id"]), col=int(item["col"]),
        row=int(item["row"]), x=float(item["x"]), y=float(item["y"]),
        w=float(item["w"]), h=float(item["h"]),
        object_ids=tuple(item.get("object_ids") or ()),
        counts=dict(item.get("counts") or {}),
        lod_counts=dict(item.get("lod_counts") or {}),
        sha256=str(item.get("sha256") or ""))
        for item in dto.get("chunks") or [])
    unrouted = tuple(UnroutedPoint(
        net=str(item.get("net") or ""), x=float(item.get("x") or 0),
        y=float(item.get("y") or 0), object_id=str(item.get("object_id") or ""),
        reason=str(item.get("reason") or "unrouted"))
        for item in dto.get("unrouted") or [])
    return SceneManifest(
        project_id=str(dto.get("project_id") or ""),
        view=str(dto.get("view") or ""),
        design_version_key=str(dto.get("design_version_key") or ""),
        bounds=dict(dto.get("bounds") or {}),
        coordinate_system=dict(dto.get("coordinate_system") or {}),
        cols=int(grid.get("cols") or 1), rows=int(grid.get("rows") or 1),
        cell_mm=float(grid.get("cell_mm") or 0),
        chunks=chunks, layers=tuple(dto.get("layers") or ()),
        net_classes=dict(dto.get("net_classes") or {}),
        unrouted=unrouted, totals=dict(dto.get("totals") or {}),
        mesh_table=dict(dto.get("mesh_table") or {}),
        component_refs=dict(dto.get("component_refs") or {}),
        net_counts=dict(dto.get("net_counts") or {}),
        component_counts=dict(dto.get("component_counts") or {}),
        net_chunks={key: list(value) for key, value in
                    (dto.get("net_chunks") or {}).items()},
        net_bounds={key: list(value) for key, value in
                    (dto.get("net_bounds") or {}).items()},
        component_chunks={key: list(value) for key, value in
                          (dto.get("component_chunks") or {}).items()},
        component_bounds={key: list(value) for key, value in
                          (dto.get("component_bounds") or {}).items()},
        content_hash=str(dto.get("content_hash") or ""))
