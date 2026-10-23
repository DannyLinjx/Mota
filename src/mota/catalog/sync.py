"""Catalog sync: real KiCad library discovery/parsing with honest coverage.

Two sources:
  * a user-selected local KiCad library tree (read-only), and
  * an official repository archive download pinned by an explicit ref.

Coverage is reported as separate counters, never as one "supported" flag.
Only the curated Golden parts are recipe-ready.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from mota.catalog.kicad_parser import ParseError, parse_footprint, parse_symbol_library
from mota.workspace import paths
from mota.workspace.atomic import read_json, sha256_file, write_json_atomic

OFFICIAL_REPOS = {
    "kicad-symbols": "https://gitlab.com/kicad/libraries/kicad-symbols/-/archive/{ref}/kicad-symbols-{ref}.tar.gz",
    "kicad-footprints": "https://gitlab.com/kicad/libraries/kicad-footprints/-/archive/{ref}/kicad-footprints-{ref}.tar.gz",
    "kicad-templates": "https://gitlab.com/kicad/libraries/kicad-templates/-/archive/{ref}/kicad-templates-{ref}.tar.gz",
}


@dataclass
class Coverage:
    discovered: int = 0
    indexed: int = 0
    parsed: int = 0
    placeable: int = 0
    mapped: int = 0
    recipe_ready: int = 0
    unsupported: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    parser_version: str = "mota-kicad-parser-1"
    source_ref: str = ""
    snapshot_id: str = ""
    files: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "discovered": self.discovered, "indexed": self.indexed,
            "parsed": self.parsed, "placeable": self.placeable,
            "mapped": self.mapped, "recipe_ready": self.recipe_ready,
            "unsupported": self.unsupported, "failures": self.failures,
            "parser_version": self.parser_version, "source_ref": self.source_ref,
            "snapshot_id": self.snapshot_id, "files": self.files,
        }


ProgressCb = Callable[[dict[str, object]], None]


def sync_local_tree(source: Path, *, snapshot_id: str | None = None,
                    progress: ProgressCb | None = None) -> Coverage:
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(str(source))
    snapshot_id = snapshot_id or f"snapshot_local_{int(time.time())}"
    dest = paths.ensure_dir(paths.catalog_dir() / "snapshots" / snapshot_id)
    coverage = Coverage(snapshot_id=snapshot_id, source_ref=str(source))
    symbol_files = sorted(source.rglob("*.kicad_sym"))
    footprint_files = sorted(source.rglob("*.kicad_mod"))
    template_files = sorted(source.rglob("meta/info.html")) + sorted(source.rglob("*.kicad_pro"))
    total = len(symbol_files) + len(footprint_files) + len(template_files)
    coverage.discovered = total
    done = 0
    for path in symbol_files:
        try:
            symbols = parse_symbol_library(path)
        except ParseError as exc:
            coverage.unsupported += 1
            coverage.failures.append({"file": str(path), "reason": str(exc)})
        else:
            coverage.indexed += len(symbols)
            coverage.parsed += sum(1 for s in symbols if s.pins)
            coverage.placeable += sum(1 for s in symbols if s.pins and not s.diagnostics)
            for sym in symbols:
                if sym.diagnostics:
                    coverage.unsupported += 1
                    coverage.failures.append({"file": str(path), "object": sym.lib_id,
                                              "reason": ";".join(sym.diagnostics)})
            coverage.files.append({"kind": "symbol", "path": str(path),
                                   "count": str(len(symbols)),
                                   "sha256": sha256_file(path)})
        done += 1
        if progress and (done % 10 == 0 or done == total):
            progress({"stage": "parsing_symbols", "completed": done, "total": total})
    for path in footprint_files:
        try:
            fp = parse_footprint(path)
        except ParseError as exc:
            coverage.unsupported += 1
            coverage.failures.append({"file": str(path), "reason": str(exc)})
        else:
            coverage.indexed += 1
            coverage.parsed += 1
            if fp.diagnostics:
                coverage.unsupported += 1
                coverage.failures.append({"file": str(path), "object": fp.lib_id,
                                          "reason": ";".join(fp.diagnostics)})
            else:
                coverage.placeable += 1
            coverage.files.append({"kind": "footprint", "path": str(path),
                                   "pads": str(len(fp.pads)),
                                   "sha256": sha256_file(path)})
        done += 1
        if progress and (done % 200 == 0 or done == total):
            progress({"stage": "parsing_footprints", "completed": done, "total": total})
    for path in template_files:
        coverage.indexed += 1
        coverage.files.append({"kind": "template", "path": str(path), "sha256": sha256_file(path)})
        done += 1
    # Only curated Golden parts are mapped/recipe-ready in this build.
    coverage.mapped = 0
    coverage.recipe_ready = 0
    (dest / "inventory.json").write_text(
        json.dumps(coverage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "coverage.json").write_text(
        json.dumps(coverage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    write_json_atomic(paths.catalog_dir() / "current_snapshot.json", {
        "snapshot_id": snapshot_id, "kind": "local", "source_ref": str(source),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": coverage.to_dict()})
    return coverage


def sync_official(ref: str = "master", *, max_bytes: int = 400 * 1024 * 1024,
                  progress: ProgressCb | None = None) -> Coverage:
    """Download official KiCad library archives at an explicit ref."""
    snapshot_id = f"snapshot_kicad_{ref.replace('/', '_')}_{int(time.time())}"
    dest = paths.ensure_dir(paths.catalog_dir() / "snapshots" / snapshot_id)
    coverage = Coverage(snapshot_id=snapshot_id, source_ref=ref)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        downloaded = 0
        for repo, url_template in OFFICIAL_REPOS.items():
            url = url_template.format(ref=ref)
            archive = tmp_path / f"{repo}.tar.gz"
            if progress:
                progress({"stage": "downloading", "repo": repo, "url": url})
            try:
                _download(url, archive, max_bytes)
            except Exception as exc:
                coverage.failures.append({"file": repo, "reason": f"DOWNLOAD_FAILED: {exc}"})
                continue
            downloaded += archive.stat().st_size
            repo_dir = tmp_path / repo
            repo_dir.mkdir()
            try:
                with tarfile.open(archive, "r:gz") as tar:
                    _safe_extract(tar, repo_dir)
            except Exception as exc:
                coverage.failures.append({"file": repo, "reason": f"EXTRACT_FAILED: {exc}"})
                continue
            for path in sorted(repo_dir.rglob("*.kicad_sym")):
                try:
                    symbols = parse_symbol_library(path)
                except ParseError as exc:
                    coverage.unsupported += 1
                    coverage.failures.append({"file": path.name, "reason": str(exc)})
                else:
                    coverage.indexed += len(symbols)
                    coverage.parsed += sum(1 for s in symbols if s.pins)
                    coverage.placeable += sum(1 for s in symbols if s.pins and not s.diagnostics)
                    for sym in symbols:
                        if sym.diagnostics:
                            coverage.unsupported += 1
                            coverage.failures.append({"file": path.name, "object": sym.lib_id,
                                                      "reason": ";".join(sym.diagnostics)})
            for path in sorted(repo_dir.rglob("*.kicad_mod")):
                try:
                    fp = parse_footprint(path)
                except ParseError as exc:
                    coverage.unsupported += 1
                    coverage.failures.append({"file": path.name, "reason": str(exc)})
                else:
                    coverage.indexed += 1
                    coverage.parsed += 1
                    if fp.diagnostics:
                        coverage.unsupported += 1
                        coverage.failures.append({"file": path.name, "object": fp.lib_id,
                                                  "reason": ";".join(fp.diagnostics)})
                    else:
                        coverage.placeable += 1
            if progress:
                progress({"stage": "parsed_repo", "repo": repo})
            # Keep a manifest, not the whole library in this demo snapshot.
            (dest / f"{repo}_manifest.txt").write_text(
                "\n".join(str(p.relative_to(repo_dir)) for p in repo_dir.rglob("*") if p.is_file()),
                encoding="utf-8")
    coverage.discovered = coverage.indexed
    coverage.mapped = 0
    coverage.recipe_ready = 0
    (dest / "coverage.json").write_text(
        json.dumps(coverage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "licenses").mkdir(exist_ok=True)
    (dest / "licenses" / "README.md").write_text(
        "Official KiCad libraries are distributed under CC-BY-SA 4.0 with the "
        "design-output exception. See https://www.kicad.org/libraries/license/ .\n"
        "This snapshot stores a manifest and parse report only.\n", encoding="utf-8")
    write_json_atomic(paths.catalog_dir() / "current_snapshot.json", {
        "snapshot_id": snapshot_id, "kind": "official", "source_ref": ref,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": coverage.to_dict()})
    return coverage


def _download(url: str, target: Path, max_bytes: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mota/1.0 catalog-sync"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as fh:
        total = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"archive exceeds {max_bytes} bytes")
            fh.write(chunk)


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if dest != target and dest not in target.parents:
            raise RuntimeError("archive path traversal rejected")
        if member.issym() or member.islnk():
            raise RuntimeError("archive links rejected")
    tar.extractall(dest)


def catalog_status(snapshot_id: str | None = None) -> dict[str, object]:
    from mota.catalog import builtin

    base = paths.catalog_dir()
    current = read_json(base / "current_snapshot.json", default=None)
    builtin_info = {
        "snapshot_id": "mota_builtin_v1",
        "kind": "builtin",
        "parts": len(builtin.PARTS),
        "footprints": len(builtin.FOOTPRINTS),
        "symbols": len(builtin.SYMBOLS),
        "recipe_ready": sum(1 for t in ("power_3v3", "led_timer", "i2c_temperature")),
        "note": "Curated Golden-template snapshot shipped with Mota.",
    }
    snapshots = [builtin_info]
    for folder in sorted((base / "snapshots").glob("*")) if (base / "snapshots").exists() else []:
        coverage = read_json(folder / "coverage.json", default=None)
        if coverage:
            snapshots.append({"snapshot_id": folder.name, "kind": "imported",
                              "coverage": coverage})
    target = None
    if snapshot_id:
        target = next((s for s in snapshots if s.get("snapshot_id") == snapshot_id), None)
    return {"current": target or current or builtin_info, "snapshots": snapshots,
            "builtin": builtin_info,
            "active_snapshot_id": (current or {}).get("snapshot_id")
            if current else "mota_builtin_v1"}
