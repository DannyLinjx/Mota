"""Versioned library sources, snapshots, incremental index and asset lookup.

A `SourceSpec` points at a local KiCad symbols library, a footprints tree or a
mixed directory. Import produces a content-addressed snapshot plus an index of
`AssetRecord`s; the active snapshot is selected by an atomically written
`current.json`. Re-importing unchanged files reuses their parsed records, and a
failed import never replaces the active snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import resource
import shutil
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from mota.domain.models import DTO, utcnow
from mota.errors import InvalidRequest, NotFound
from mota.library import kicad_footprints, kicad_symbols
from mota.library.records import (
    AssetRecord, AssetSummary, Diagnostic, make_asset_id, parse_asset_id,
)
from mota.workspace import paths
from mota.workspace.atomic import (
    read_json, sha256_bytes, write_bytes_atomic, write_json_atomic,
)

LIBRARY_SCHEMA_VERSION = 1
PARSER_VERSIONS = {
    "symbol": kicad_symbols.PARSER_VERSION,
    "footprint": kicad_footprints.PARSER_VERSION,
}
MAX_REPORT_DIAGNOSTICS = 200


class SourceSpec(DTO):
    source_id: str
    path: str
    version: str = "unknown"
    license: str = "unknown"
    license_url: str = ""
    format: Literal["auto", "packed", "unpacked"] = "auto"
    library_name: str | None = None


class SourceInfo(DTO):
    source_id: str
    path: str
    version: str
    license: str
    license_url: str
    format_requested: str
    format_detected: str
    file_count: int


class ImportCounts(DTO):
    discovered: int = 0
    parsed: int = 0
    unsupported: int = 0
    failed: int = 0
    indexed: int = 0
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    mapped: int = 0
    rendered: int = 0
    auto_synthesis_ready: int | None = None
    files_total: int = 0
    files_processed: int = 0
    files_reused: int = 0
    bytes_parsed: int = 0

    def check_identity(self) -> None:
        if self.discovered != self.parsed + self.unsupported + self.failed:
            raise RuntimeError(
                "library coverage identity broken: "
                f"discovered={self.discovered} parsed={self.parsed} "
                f"unsupported={self.unsupported} failed={self.failed}")


class LibraryImportReport(DTO):
    schema_version: int = LIBRARY_SCHEMA_VERSION
    report_id: str
    status: Literal["IMPORTED", "PARTIAL", "FAILED"]
    source: SourceInfo
    snapshot_id: str
    snapshot_dir: str
    index_path: str
    counts: ImportCounts
    counts_by_kind: dict[str, dict[str, int]] = {}
    failure_codes: dict[str, int] = {}
    unsupported_codes: dict[str, int] = {}
    semantic_warning_codes: dict[str, int] = {}
    diagnostic_codes: dict[str, int] = {}
    diagnostics: list[Diagnostic] = []
    diagnostics_truncated: int = 0
    parser_versions: dict[str, str] = {}
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    peak_memory_bytes: int = 0
    process_max_rss_bytes: int = 0
    notes: list[str] = []


@dataclass
class _SourceFile:
    path: Path
    relpath: str
    kind: Literal["symbol", "footprint", "legacy"]
    library: str


def import_library(source: SourceSpec, *, root: Path | None = None,
                   store_content: bool = True, trace_memory: bool = True,
                   progress: Callable[[dict[str, Any]], None] | None = None
                   ) -> LibraryImportReport:
    if not source.source_id or any(ch not in
                                   "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                                   for ch in source.source_id):
        raise InvalidRequest("source_id may only contain letters, digits, '.', '_' and '-'.",
                             code="LIBRARY_SOURCE_ID_INVALID")
    source_path = Path(source.path).expanduser().resolve()
    if not source_path.exists():
        raise InvalidRequest(f"library source does not exist: {source_path}",
                             code="LIBRARY_SOURCE_MISSING")

    started_at = utcnow()
    started = time.perf_counter()
    if trace_memory:
        tracemalloc.start()
    base = _library_base(root)
    files = _enumerate(source_path, source)
    if not files:
        raise InvalidRequest(
            f"no .kicad_sym/.kicad_mod/.lib files under {source_path}",
            code="LIBRARY_SOURCE_EMPTY")

    previous = _load_current(base, source.source_id)
    prev_files = {row["relpath"]: row for row in
                  (previous["manifest"]["files"] if previous else [])}
    prev_index = {row["asset_id"]: row for row in
                  (previous["index"] if previous else [])}

    summaries: list[AssetSummary] = []
    diagnostics: list[Diagnostic] = []
    manifest_files: list[dict[str, Any]] = []
    counts = ImportCounts(files_total=len(files))
    counts_by_kind: dict[str, dict[str, int]] = {
        "symbol": {"discovered": 0, "parsed": 0, "unsupported": 0, "failed": 0},
        "footprint": {"discovered": 0, "parsed": 0, "unsupported": 0, "failed": 0},
        "legacy": {"discovered": 0, "parsed": 0, "unsupported": 0, "failed": 0},
    }
    seen_ids: dict[str, AssetSummary] = {}
    duplicate_index: dict[str, int] = {}

    for index, item in enumerate(files):
        if progress and index % 25 == 0:
            progress({"stage": "library_import", "file": item.relpath,
                      "done": index, "total": len(files)})
        data = item.path.read_bytes()
        sha = sha256_bytes(data)
        parser_version = PARSER_VERSIONS.get(item.kind, "none")
        reused = _reuse_file(prev_files.get(item.relpath), prev_index,
                             sha=sha, kind=item.kind,
                             library=item.library,
                             parser_version=parser_version)
        if reused is not None:
            old_summaries = [_register(seen_ids, duplicate_index, summary)
                             for summary in reused]
            summaries.extend(old_summaries)
            counts.files_reused += 1
            manifest_files.append({
                "relpath": item.relpath, "kind": item.kind,
                "library": item.library, "sha256": sha,
                "size": len(data), "parser_version": parser_version,
                "asset_ids": [row.asset_id for row in old_summaries]})
            _count_records(counts, counts_by_kind, item.kind, old_summaries)
            _collect_diagnostics(old_summaries, diagnostics)
            if store_content:
                _store_object(base, source.source_id, sha, data)
            continue

        if item.kind == "legacy":
            name = item.path.stem
            counts.files_processed += 1
            asset_id = make_asset_id("symbol", source.source_id, item.library, name)
            record = AssetRecord(
                asset_id=asset_id, kind="symbol", source_id=source.source_id,
                library=item.library, name=name, status="unsupported",
                content_hash=sha256_bytes(b"legacy:" + sha.encode()),
                source_file=item.relpath, source_sha256=sha,
                unsupported_features=["LEGACY_LIB_FORMAT"],
                diagnostics=[Diagnostic(
                    severity="error", code="LEGACY_LIB_FORMAT",
                    message=("KiCad 5 .lib libraries are not parsed; export the "
                             "library as .kicad_sym first"),
                    file=item.relpath, line=1, column=1, asset_id=asset_id)])
            file_records = [record]
        else:
            text = data.decode("utf-8-sig", errors="replace")
            counts.bytes_parsed += len(data)
            if item.kind == "symbol":
                parsed = kicad_symbols.parse_symbol_file(
                    text, library=item.library, source_id=source.source_id,
                    source_file=item.relpath, source_sha256=sha)
                file_records = kicad_symbols.build_symbol_assets(parsed)
                diagnostics.extend(parsed.diagnostics)
            else:
                parsed_fp = kicad_footprints.parse_footprint_file(
                    text, library=item.library, source_id=source.source_id,
                    source_file=item.relpath, source_sha256=sha)
                file_records = parsed_fp.records
            counts.files_processed += 1

        file_summaries = [record.to_summary() for record in file_records]
        _collect_record_diagnostics(file_records, diagnostics)
        deduped: list[AssetSummary] = []
        for summary in file_summaries:
            summary = _register(seen_ids, duplicate_index, summary)
            deduped.append(summary)
        summaries.extend(deduped)
        manifest_files.append({
            "relpath": item.relpath, "kind": item.kind,
            "library": item.library, "sha256": sha, "size": len(data),
            "parser_version": parser_version,
            "asset_ids": [row.asset_id for row in deduped]})
        _count_records(counts, counts_by_kind, item.kind, deduped)
        if store_content:
            _store_object(base, source.source_id, sha, data)

    counts.indexed = len(summaries)
    counts.discovered = counts.parsed + counts.unsupported + counts.failed
    counts.check_identity()

    current_ids = {row.asset_id for row in summaries}
    for asset_id, old in prev_index.items():
        if asset_id not in current_ids:
            counts.removed += 1
        elif old.get("content_hash") != seen_ids[asset_id].content_hash or \
                old.get("source_sha256") != seen_ids[asset_id].source_sha256:
            counts.changed += 1
        else:
            counts.unchanged += 1
    counts.added = len(current_ids - set(prev_index))

    manifest = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "source": {
            "source_id": source.source_id, "path": str(source_path),
            "version": source.version, "license": source.license,
            "license_url": source.license_url,
            "format_requested": source.format,
            "format_detected": "unpacked" if source_path.is_dir() else "packed",
        },
        "parser_versions": dict(PARSER_VERSIONS),
        "files": sorted(manifest_files, key=lambda row: row["relpath"]),
        "created_at": started_at,
    }
    manifest["snapshot_id"] = _snapshot_id(manifest)

    failure_codes = _code_counts(summaries, "failed")
    unsupported_codes = _code_counts(summaries, "unsupported")
    warning_codes = _semantic_warning_counts(summaries)
    diagnostic_codes = _diagnostic_code_counts(summaries)
    for diag in diagnostics:
        diagnostic_codes[diag.code] = diagnostic_codes.get(diag.code, 0) + 1
    total_diagnostics = len(diagnostics)
    collected = list(diagnostics[:MAX_REPORT_DIAGNOSTICS])
    for summary in summaries:
        total_diagnostics += len(summary.diagnostics)
        if len(collected) >= MAX_REPORT_DIAGNOSTICS:
            continue
        for diag in summary.diagnostics:
            if len(collected) >= MAX_REPORT_DIAGNOSTICS:
                break
            collected.append(diag)
    capped = collected
    truncated = max(0, total_diagnostics - len(capped))
    if trace_memory:
        peak_traced, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    else:
        peak_traced = 0
    duration_ms = int((time.perf_counter() - started) * 1000)

    report = LibraryImportReport(
        report_id=uuid.uuid4().hex, status=_status(counts),
        source=SourceInfo(
            source_id=source.source_id, path=str(source_path),
            version=source.version, license=source.license,
            license_url=source.license_url,
            format_requested=source.format,
            format_detected=manifest["source"]["format_detected"],
            file_count=len(files)),
        snapshot_id=manifest["snapshot_id"],
        snapshot_dir="", index_path="",
        counts=counts, counts_by_kind=counts_by_kind,
        failure_codes=failure_codes, unsupported_codes=unsupported_codes,
        semantic_warning_codes=warning_codes,
        diagnostic_codes=diagnostic_codes,
        diagnostics=capped, diagnostics_truncated=truncated,
        parser_versions=dict(PARSER_VERSIONS),
        started_at=started_at, finished_at=utcnow(),
        duration_ms=duration_ms, peak_memory_bytes=peak_traced,
        process_max_rss_bytes=_max_rss_bytes(),
        notes=[
            "auto_synthesis_ready is not evaluated by C1; symbol+footprint "
            "pair synthesis belongs to C2.",
            "rendered counts previews generated during import; previews are "
            "generated on demand by preview_asset().",
            ("peak_memory_bytes is Python heap traced by tracemalloc; "
             "process_max_rss_bytes is process-wide max RSS." if trace_memory
             else "peak_memory_bytes was not traced for this run "
                  "(trace_memory=False); process_max_rss_bytes is the "
                  "process-wide max RSS."),
            "files_processed counts files handed to a parser, including files "
            "that failed with SYNTAX_ERROR.",
        ])

    snapshot_dir = _commit_snapshot(base, source.source_id,
                                    manifest["snapshot_id"], manifest,
                                    summaries)
    report = report.model_copy(update={
        "snapshot_dir": str(snapshot_dir),
        "index_path": str(snapshot_dir / "index.json")})
    write_json_atomic(snapshot_dir / "report.json", report.model_dump())
    _write_current(base, source.source_id, manifest["snapshot_id"],
                   previous, counts)
    if progress:
        progress({"stage": "library_import_done",
                  "snapshot_id": manifest["snapshot_id"],
                  "counts": counts.model_dump()})
    return report


def _library_base(root: Path | None) -> Path:
    return (root or paths.data_root()) / "catalog" / "library"


def _enumerate(source_path: Path, source: SourceSpec) -> list[_SourceFile]:
    if source_path.is_file():
        kind = _kind_of(source_path)
        if kind is None:
            raise InvalidRequest(
                f"unsupported library file: {source_path.name}",
                code="LIBRARY_FORMAT_UNSUPPORTED")
        if source.format == "unpacked":
            raise InvalidRequest("a single file cannot be an unpacked library.",
                                 code="LIBRARY_FORMAT_MISMATCH")
        library = source.library_name or source_path.stem
        return [_SourceFile(source_path, source_path.name, kind, library)]

    if source.format == "packed":
        raise InvalidRequest("packed format requires a single library file.",
                             code="LIBRARY_FORMAT_MISMATCH")
    out: list[_SourceFile] = []
    for path in sorted(source_path.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(source_path).parts):
            continue
        if not path.is_file():
            continue
        kind = _kind_of(path)
        if kind is None:
            continue
        rel = path.relative_to(source_path).as_posix()
        if kind == "footprint":
            library = source.library_name or path.parent.name.removesuffix(".pretty")
        else:
            library = source.library_name or path.stem
        out.append(_SourceFile(path, rel, kind, library))
    return out


def _kind_of(path: Path) -> Literal["symbol", "footprint", "legacy"] | None:
    suffix = path.suffix.lower()
    if suffix == ".kicad_sym":
        return "symbol"
    if suffix == ".kicad_mod":
        return "footprint"
    if suffix == ".lib":
        return "legacy"
    return None


def _reuse_file(entry: dict[str, Any] | None,
                prev_index: dict[str, dict[str, Any]], *, sha: str,
                kind: str, library: str,
                parser_version: str) -> list[AssetSummary] | None:
    if not entry:
        return None
    if entry.get("sha256") != sha:
        return None
    if entry.get("kind") != kind or entry.get("library") != library:
        return None
    if entry.get("parser_version") != parser_version:
        return None
    asset_ids = list(entry.get("asset_ids", []))
    if not asset_ids:
        return None
    try:
        return [AssetSummary.model_validate(prev_index[asset_id])
                for asset_id in asset_ids]
    except (KeyError, ValueError):
        return None


def _register(seen: dict[str, AssetSummary],
              duplicate_index: dict[str, int],
              summary: AssetSummary) -> AssetSummary:
    existing = seen.get(summary.asset_id)
    if existing is None:
        seen[summary.asset_id] = summary
        return summary
    if existing.content_hash == summary.content_hash and \
            existing.source_sha256 == summary.source_sha256:
        return existing
    next_index = duplicate_index.get(summary.asset_id, 1)
    duplicate_index[summary.asset_id] = next_index + 1
    clone = summary.model_copy(deep=True)
    kind, source_id, library, name = parse_asset_id(summary.asset_id)
    clone.asset_id = make_asset_id(kind, source_id, library, name,
                                   duplicate_index=next_index)
    clone.status = "failed"
    clone.mapped = False
    clone.placeable = False
    clone.unsupported_features = ["DUPLICATE_ASSET_ID"]
    clone.diagnostic_codes = {
        **clone.diagnostic_codes, "DUPLICATE_ASSET_ID": 1}
    clone.diagnostics = list(clone.diagnostics) + [Diagnostic(
        severity="error", code="DUPLICATE_ASSET_ID",
        message=(f"{summary.asset_id} was found in more than one source file "
                 "with different content"),
        file=summary.source_file, line=summary.line, column=1,
        asset_id=clone.asset_id)]
    seen[clone.asset_id] = clone
    return clone


def _collect_record_diagnostics(items: list[AssetRecord],
                                sink: list[Diagnostic]) -> None:
    """Keep a bounded sample of error/warning diagnostics for the report."""
    for record in items:
        for diag in record.diagnostics:
            if diag.severity == "info":
                continue
            sink.append(diag)
            if len(sink) >= MAX_REPORT_DIAGNOSTICS * 5:
                return


def _collect_diagnostics(items: list[AssetSummary],
                         sink: list[Diagnostic]) -> None:
    """Same as `_collect_record_diagnostics` for reused summaries."""
    for summary in items:
        for diag in summary.diagnostics:
            if diag.severity == "info":
                continue
            sink.append(diag)
            if len(sink) >= MAX_REPORT_DIAGNOSTICS * 5:
                return


def _count_records(counts: ImportCounts, by_kind: dict[str, dict[str, int]],
                   kind: str, items: list[AssetSummary]) -> None:
    bucket = by_kind.setdefault(kind, {"discovered": 0, "parsed": 0,
                                       "unsupported": 0, "failed": 0})
    for summary in items:
        counts.discovered += 1
        bucket["discovered"] += 1
        if summary.status == "parsed":
            counts.parsed += 1
            bucket["parsed"] += 1
            if summary.mapped:
                counts.mapped += 1
        elif summary.status == "unsupported":
            counts.unsupported += 1
            bucket["unsupported"] += 1
        else:
            counts.failed += 1
            bucket["failed"] += 1


def _status(counts: ImportCounts) -> Literal["IMPORTED", "PARTIAL", "FAILED"]:
    if counts.failed and counts.parsed == 0:
        return "FAILED"
    if counts.failed or counts.unsupported:
        return "PARTIAL"
    return "IMPORTED"


def _code_counts(items: list[AssetSummary], status: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for summary in items:
        if summary.status != status:
            continue
        code = (summary.unsupported_features[0] if summary.unsupported_features
                else "UNSPECIFIED")
        out[code] = out.get(code, 0) + 1
    return out


def _semantic_warning_counts(items: list[AssetSummary]) -> dict[str, int]:
    out: dict[str, int] = {}
    for summary in items:
        if summary.status != "parsed":
            continue
        for feature in summary.unsupported_features:
            out[feature] = out.get(feature, 0) + 1
    return out


def _diagnostic_code_counts(items: list[AssetSummary]) -> dict[str, int]:
    out: dict[str, int] = {}
    for summary in items:
        for code, count in summary.diagnostic_codes.items():
            out[code] = out.get(code, 0) + count
    return out


def _snapshot_id(manifest: dict[str, Any]) -> str:
    payload = {
        "source_id": manifest["source"]["source_id"],
        "parser_versions": manifest["parser_versions"],
        "files": [[row["relpath"], row["sha256"], row["kind"], row["library"]]
                  for row in manifest["files"]],
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _store_object(base: Path, source_id: str, sha: str, data: bytes) -> None:
    target = base / source_id / "objects" / sha[:2] / sha
    if target.exists():
        return
    write_bytes_atomic(target, data)


def _commit_snapshot(base: Path, source_id: str, snapshot_id: str,
                     manifest: dict[str, Any],
                     summaries: list[AssetSummary]) -> Path:
    snapshots = base / source_id / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    final = snapshots / snapshot_id
    staging = snapshots / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        write_json_atomic(staging / "manifest.json", manifest)
        write_json_atomic(staging / "index.json",
                          [summary.model_dump() for summary in summaries])
        if final.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _write_current(base: Path, source_id: str, snapshot_id: str,
                   previous: dict[str, Any] | None,
                   counts: ImportCounts) -> None:
    previous_snapshot_id = (previous or {}).get("snapshot_id")
    if previous_snapshot_id == snapshot_id:
        # Re-importing identical content must not erase the real rollback point.
        previous_snapshot_id = (previous or {}).get("previous_snapshot_id")
    write_json_atomic(base / source_id / "current.json", {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "imported_at": utcnow(),
        "counts": counts.model_dump(),
    })


def _load_current(base: Path, source_id: str) -> dict[str, Any] | None:
    current = read_json(base / source_id / "current.json", default=None)
    if not current:
        return None
    snapshot_dir = base / source_id / "snapshots" / current["snapshot_id"]
    manifest = read_json(snapshot_dir / "manifest.json", default=None)
    index = read_json(snapshot_dir / "index.json", default=None)
    if manifest is None or index is None:
        return None
    return {**current, "manifest": manifest, "index": index}


def _max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return int(value if value > 10_000_000 else value * 1024)


# --------------------------------------------------------------------------- #
# Query surface
# --------------------------------------------------------------------------- #

def library_status(root: Path | None = None) -> dict[str, Any]:
    base = _library_base(root)
    sources: list[dict[str, Any]] = []
    if base.exists():
        for folder in sorted(base.iterdir()):
            current = read_json(folder / "current.json", default=None)
            if not current or not folder.is_dir():
                continue
            manifest = read_json(
                folder / "snapshots" / current["snapshot_id"] / "manifest.json",
                default=None) or {}
            sources.append({
                "source_id": current.get("source_id", folder.name),
                "snapshot_id": current["snapshot_id"],
                "previous_snapshot_id": current.get("previous_snapshot_id"),
                "imported_at": current.get("imported_at"),
                "counts": current.get("counts", {}),
                "source": manifest.get("source", {}),
                "file_count": len(manifest.get("files", [])),
            })
    return {
        "library_root": str(base),
        "sources": sources,
        "total_assets": sum(int(source["counts"].get("indexed", 0))
                            for source in sources),
    }


def load_summaries(source_id: str, root: Path | None = None) -> list[AssetSummary]:
    return [AssetSummary.model_validate(row)
            for row in _index_rows(source_id, root)]


def _index_rows(source_id: str, root: Path | None) -> list[dict[str, Any]]:
    base = _library_base(root)
    current = read_json(base / source_id / "current.json", default=None)
    if not current:
        raise NotFound(f"library source {source_id!r} has no active snapshot.",
                       code="LIBRARY_SOURCE_NOT_FOUND")
    payload = read_json(
        base / source_id / "snapshots" / current["snapshot_id"] / "index.json",
        default=None)
    if payload is None:
        raise NotFound(f"library snapshot {current['snapshot_id']} is missing.",
                       code="LIBRARY_SNAPSHOT_MISSING")
    return payload


def _all_index_rows(root: Path | None,
                    source_id: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in library_status(root)["sources"]:
        if source_id and source["source_id"] != source_id:
            continue
        rows.extend(_index_rows(source["source_id"], root))
    return rows


def search_assets(query: str = "", *, root: Path | None = None,
                  source_id: str | None = None, kind: str | None = None,
                  status: str | None = None, limit: int = 20,
                  offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    text = (query or "").strip().casefold()
    tokens = [token for token in text.split() if token]
    matches: list[dict[str, Any]] = []
    for row in _all_index_rows(root, source_id):
        if kind and row.get("kind") != kind:
            continue
        if status and row.get("status") != status:
            continue
        haystack = " ".join([
            str(row.get("name", "")), str(row.get("library", "")),
            str(row.get("description", "")), str(row.get("keywords", "")),
            str(row.get("asset_id", "")),
        ]).casefold()
        if tokens and not all(token in haystack for token in tokens):
            continue
        matches.append(row)
    matches.sort(key=lambda row: (row.get("status") != "parsed",
                                  str(row.get("kind")), str(row.get("library")),
                                  str(row.get("name"))))
    total = len(matches)
    page = matches[offset:offset + limit]
    return {
        "query": query, "total": total, "offset": offset, "limit": limit,
        "candidates": [AssetSummary.model_validate(row).public() for row in page],
        "state": "ok" if page else ("no_match" if total == 0 else "ok"),
    }


def inspect_asset(asset_id: str, root: Path | None = None) -> dict[str, Any]:
    summary, manifest, base = _find_summary(asset_id, root)
    record = _resolve_record(summary, manifest, base)
    if record is not None:
        payload = record.model_dump()
        payload["preview_view"] = ("schematic" if record.kind == "symbol"
                                   else "pcb2d")
        payload["preview_supported"] = True
        payload["content_available"] = True
        return payload
    payload = summary.model_dump()
    payload.update({
        "pins": [], "pads": [], "properties": {}, "models": [],
        "attributes": [], "preview_view": None, "preview_supported": False,
        "content_available": False,
        "content_note": ("Source bytes for this snapshot are not stored and the "
                         "source file changed or disappeared; only the index "
                         "entry is available."),
    })
    return payload


def _find_summary(asset_id: str, root: Path | None
                  ) -> tuple[AssetSummary, dict[str, Any], Path]:
    try:
        kind, source_id, library, name = parse_asset_id(asset_id)
    except ValueError as exc:
        raise NotFound(str(exc), code="LIBRARY_ASSET_NOT_FOUND") from exc
    base = _library_base(root)
    current = read_json(base / source_id / "current.json", default=None)
    if not current:
        raise NotFound(f"library source {source_id!r} has no active snapshot.",
                       code="LIBRARY_SOURCE_NOT_FOUND")
    snapshot_dir = base / source_id / "snapshots" / current["snapshot_id"]
    manifest = read_json(snapshot_dir / "manifest.json", default=None)
    rows = read_json(snapshot_dir / "index.json", default=None)
    if manifest is None or rows is None:
        raise NotFound(f"library snapshot {current['snapshot_id']} is missing.",
                       code="LIBRARY_SNAPSHOT_MISSING")
    for row in rows:
        if row.get("asset_id") == asset_id:
            return AssetSummary.model_validate(row), manifest, base
    raise NotFound(
        f"{kind} {name!r} from library {library!r} is not in the active snapshot "
        f"of source {source_id!r}.", code="LIBRARY_ASSET_NOT_FOUND")


def _resolve_record(summary: AssetSummary, manifest: dict[str, Any],
                    base: Path) -> AssetRecord | None:
    """Re-read the snapshot source on demand; returns None when unavailable."""
    data = _source_bytes(summary, manifest, base)
    if data is None:
        return None
    text = data.decode("utf-8-sig", errors="replace")
    if summary.kind == "symbol":
        parsed = kicad_symbols.parse_symbol_file(
            text, library=summary.library, source_id=summary.source_id,
            source_file=summary.source_file,
            source_sha256=summary.source_sha256)
        records = kicad_symbols.build_symbol_assets(parsed)
    else:
        records = kicad_footprints.parse_footprint_file(
            text, library=summary.library, source_id=summary.source_id,
            source_file=summary.source_file,
            source_sha256=summary.source_sha256).records
    for record in records:
        if record.asset_id == summary.asset_id:
            return record
    return None


def _source_bytes(summary: AssetSummary, manifest: dict[str, Any],
                  base: Path) -> bytes | None:
    object_path = base / summary.source_id / "objects" / \
        summary.source_sha256[:2] / summary.source_sha256
    if object_path.exists():
        data = object_path.read_bytes()
        return data if sha256_bytes(data) == summary.source_sha256 else None
    raw_source = manifest.get("source", {}).get("path", "")
    if not raw_source:
        return None
    source_root = Path(raw_source)
    if not source_root.exists():
        return None
    file_path = source_root if source_root.is_file() else source_root / summary.source_file
    if not file_path.exists():
        return None
    data = file_path.read_bytes()
    if sha256_bytes(data) != summary.source_sha256:
        return None
    return data


def preview_asset(asset_id: str, root: Path | None = None) -> str:
    summary, manifest, base = _find_summary(asset_id, root)
    record = _resolve_record(summary, manifest, base)
    if record is None:
        raise NotFound(
            f"source content for {asset_id} is unavailable; preview cannot be "
            "rendered from the index alone.",
            code="LIBRARY_CONTENT_UNAVAILABLE")
    if record.kind == "symbol":
        return _symbol_svg(record)
    return _footprint_svg(record)


def rollback_library(source_id: str, root: Path | None = None) -> dict[str, Any]:
    base = _library_base(root)
    current = read_json(base / source_id / "current.json", default=None)
    if not current:
        raise NotFound(f"library source {source_id!r} has no active snapshot.",
                       code="LIBRARY_SOURCE_NOT_FOUND")
    previous = current.get("previous_snapshot_id")
    if not previous:
        raise InvalidRequest("no previous snapshot to roll back to.",
                             code="LIBRARY_ROLLBACK_UNAVAILABLE")
    snapshot_dir = base / source_id / "snapshots" / previous
    if not (snapshot_dir / "manifest.json").exists():
        raise NotFound(f"previous snapshot {previous} is missing.",
                       code="LIBRARY_SNAPSHOT_MISSING")
    previous_report = read_json(snapshot_dir / "report.json", default=None) or {}
    write_json_atomic(base / source_id / "current.json", {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "source_id": source_id,
        "snapshot_id": previous,
        "previous_snapshot_id": current["snapshot_id"],
        "imported_at": utcnow(),
        "counts": previous_report.get("counts", current.get("counts", {})),
    })
    return {"source_id": source_id, "snapshot_id": previous,
            "replaced_snapshot_id": current["snapshot_id"],
            "status": "ROLLED_BACK"}


# --------------------------------------------------------------------------- #
# Preview rendering (generated on demand, never during import)
# --------------------------------------------------------------------------- #

def _symbol_svg(record: AssetRecord) -> str:
    points: list[tuple[float, float, float, float]] = []
    for pin in record.pins:
        angle = pin.angle_deg * 3.141592653589793 / 180.0
        x2 = pin.x_mm + pin.length_mm * _cos(angle)
        y2 = pin.y_mm + pin.length_mm * _sin(angle)
        points.append((pin.x_mm, pin.y_mm, x2, y2))
    xs = [value for point in points for value in (point[0], point[2])]
    ys = [value for point in points for value in (point[1], point[3])]
    if not xs:
        xs, ys = [0.0, 10.0], [0.0, 10.0]
    return _wrap_svg(xs, ys, "".join(
        f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}"/>'
        f'<circle cx="{x2:.3f}" cy="{y2:.3f}" r="0.25"/>'
        for x1, y1, x2, y2 in points), f"symbol {record.name}")


def _footprint_svg(record: AssetRecord) -> str:
    shapes: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    for pad in record.pads:
        half_w, half_h = pad.width_mm / 2, pad.height_mm / 2
        xs.extend([pad.x_mm - half_w, pad.x_mm + half_w])
        ys.extend([pad.y_mm - half_h, pad.y_mm + half_h])
        if pad.shape == "circle":
            shapes.append(
                f'<circle cx="{pad.x_mm:.3f}" cy="{pad.y_mm:.3f}" '
                f'r="{max(half_w, half_h):.3f}"/>')
        else:
            shapes.append(
                f'<rect x="{pad.x_mm - half_w:.3f}" y="{pad.y_mm - half_h:.3f}" '
                f'width="{pad.width_mm:.3f}" height="{pad.height_mm:.3f}" '
                f'transform="rotate({pad.rotation_deg:.3f} {pad.x_mm:.3f} '
                f'{pad.y_mm:.3f})"/>')
    if not xs:
        xs, ys = [0.0, 10.0], [0.0, 10.0]
    return _wrap_svg(xs, ys, "".join(shapes), f"footprint {record.name}")


def _wrap_svg(xs: list[float], ys: list[float], body: str, title: str) -> str:
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = max(1.0, 0.05 * max(max_x - min_x, max_y - min_y))
    min_x, min_y = min_x - pad, min_y - pad
    width = max(1.0, (max_x - min_x) + pad)
    height = max(1.0, (max_y - min_y) + pad)
    flip = min_y + max_y
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.3f} {height:.3f}" '
        f'width="360" height="240" role="img" aria-label="{title}">'
        f'<rect x="0" y="0" width="{width:.3f}" height="{height:.3f}" fill="#0d1117"/>'
        f'<g transform="translate({-min_x:.3f} 0) translate(0 {flip:.3f}) scale(1 -1)" '
        f'stroke="#5ad48a" fill="none" stroke-width="{max(0.03, pad / 20):.3f}">'
        f'{body}</g></svg>')


def _cos(angle: float) -> float:
    return math.cos(angle)


def _sin(angle: float) -> float:
    return math.sin(angle)
