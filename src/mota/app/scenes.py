"""Scene/artifact persistence for drafts and revisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mota.domain.models import ArtifactRef, DesignVersion
from mota.projects.store import DesignDraft, ProjectStore, Snapshot
from mota.render.scene import make_scene
from mota.render.svg import scene_to_svg
from mota.workspace.atomic import read_json, sha256_bytes, write_json_atomic


def save_draft_scene(store: ProjectStore, draft: DesignDraft, view: str) -> tuple[dict[str, Any], str]:
    assert draft.schematic is not None and draft.board is not None
    scene = make_scene(draft.project_id, draft.version, view, draft.circuit,
                       draft.schematic, draft.board)
    draft_dir = store.project_dir(draft.project_id) / "drafts" / (draft.run_id or draft.draft_id)
    scene_dir = draft_dir / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{view}_{draft.draft_seq}.json"
    path = scene_dir / filename
    data = json.dumps(scene.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(data)
    artifact_id = f"artifact_scene_{draft.draft_id}_{draft.draft_seq}_{view}"
    manifest_path = draft_dir / "artifacts.json"
    manifest = read_json(manifest_path, default={"schema_version": 1, "entries": {}}) or {}
    manifest.setdefault("entries", {})[artifact_id] = {
        "artifact_id": artifact_id, "kind": f"scene_{view}",
        "relative_path": str(path.relative_to(store.project_dir(draft.project_id))),
        "sha256": sha256_bytes(data), "bytes": len(data),
        "design_version_key": draft.version.key, "view": view,
    }
    write_json_atomic(manifest_path, manifest)
    return scene.model_dump(), artifact_id


def draft_scene_json(store: ProjectStore, draft: DesignDraft, view: str) -> dict[str, Any]:
    scene, artifact_id = save_draft_scene(store, draft, view)
    scene["artifact_id"] = artifact_id
    return scene


def write_revision_scenes(snapshot: Snapshot, store: ProjectStore) -> list[ArtifactRef]:
    """Generate and register scene artifacts for an immutable revision."""
    pdir = store.project_dir(snapshot.project_id)
    rdir = pdir / "revisions" / snapshot.revision.revision_id
    scene_dir = rdir / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    rows = read_json(rdir / "artifacts.json", default=[]) or []
    refs: list[ArtifactRef] = []
    version_key = snapshot.version.key
    refs_by_id = {row["artifact_id"]: row for row in rows}
    for view in ("schematic", "pcb2d", "pcb3d"):
        scene = make_scene(snapshot.project_id, snapshot.version, view, snapshot.circuit,
                           snapshot.schematic, snapshot.board)
        data = json.dumps(scene.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
        path = scene_dir / f"{view}.json"
        path.write_bytes(data)
        artifact_id = f"artifact_scene_{snapshot.revision.revision_id}_{view}"
        ref = ArtifactRef(
            artifact_id=artifact_id, kind=f"scene_{view}",
            relative_path=str(path.relative_to(pdir)), sha256=sha256_bytes(data),
            bytes=len(data), design_version_key=version_key)
        refs.append(ref)
        refs_by_id[artifact_id] = ref.model_dump()
    svg = scene_to_svg(make_scene(snapshot.project_id, snapshot.version, "pcb2d",
                                  snapshot.circuit, snapshot.schematic, snapshot.board))
    thumb_bytes = svg.encode("utf-8")
    thumb_path = rdir / "thumbnail.svg"
    thumb_path.write_bytes(thumb_bytes)
    thumb = ArtifactRef(artifact_id=f"artifact_thumb_{snapshot.revision.revision_id}",
                        kind="thumbnail", relative_path=str(thumb_path.relative_to(pdir)),
                        sha256=sha256_bytes(thumb_bytes), bytes=len(thumb_bytes),
                        design_version_key=version_key, mime_type="image/svg+xml")
    refs.append(thumb)
    refs_by_id[thumb.artifact_id] = thumb.model_dump()
    write_json_atomic(rdir / "artifacts.json", list(refs_by_id.values()))
    meta_path = rdir / "revision.json"
    meta = read_json(meta_path, default={}) or {}
    existing = {a["artifact_id"] for a in meta.get("artifacts", [])}
    for ref in refs:
        if ref.artifact_id not in existing:
            meta.setdefault("artifacts", []).append(ref.model_dump())
    write_json_atomic(meta_path, meta)
    project_path = pdir / "project.json"
    project = read_json(project_path, default=None)
    if project:
        from mota.domain.models import utcnow

        project["thumbnail_artifact_id"] = thumb.artifact_id
        project["updated_at"] = utcnow()
        write_json_atomic(project_path, project)
    return refs


def find_artifact(store: ProjectStore, project_id: str, version: DesignVersion,
                  artifact_id: str) -> tuple[Path, dict[str, Any]]:
    pdir = store.project_dir(project_id)
    if version.revision_id:
        rdir = pdir / "revisions" / version.revision_id
        rows = read_json(rdir / "artifacts.json", default=[]) or []
        for row in rows:
            if row.get("artifact_id") == artifact_id:
                return pdir / row["relative_path"], row
    else:
        for folder in (pdir / "drafts").iterdir():
            manifest = read_json(folder / "artifacts.json", default={}) or {}
            entry = manifest.get("entries", {}).get(artifact_id)
            if entry:
                return pdir / entry["relative_path"], entry
    from mota.errors import ArtifactInvalid

    raise ArtifactInvalid("Artifact not found for this design version.",
                          code="ARTIFACT_INVALID")
