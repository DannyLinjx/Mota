"""Import a locked KiCad snapshot and record real coverage and cost.

Usage:
    .venv/bin/python scripts/library_import_scan.py \
        --symbols /tmp/mota-kicad/kicad-symbols-9.0.0 \
        --footprints /tmp/mota-kicad/kicad-footprints-9.0.0 \
        --root /tmp/mota-kicad/mota-data \
        --out docs/engine-v2/library-scan.json

The scan runs every source twice: the first pass parses, the second pass must
reuse the index. Both passes are recorded so incremental behaviour is visible.
Nothing is copied into the repository; only the JSON summary is written there.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from mota.library.catalog import SourceSpec, import_library

KICAD_LICENSE = "CC-BY-SA-4.0 WITH KiCad-Library-Exception"
KICAD_LICENSE_URL = "https://www.kicad.org/libraries/license/"


def scan(source_id: str, path: Path, root: Path, *,
         store_content: bool) -> dict:
    if not path.exists():
        raise SystemExit(f"missing snapshot path: {path}")
    source = SourceSpec(source_id=source_id, path=str(path), version="9.0.0",
                        license=KICAD_LICENSE, license_url=KICAD_LICENSE_URL,
                        format="auto")
    print(f"[{source_id}] first pass: parsing {path}")
    started = time.perf_counter()
    first = import_library(source, root=root, store_content=store_content,
                           trace_memory=False)
    wall_first = time.perf_counter() - started
    print(f"[{source_id}] first pass {first.status} "
          f"{first.counts.indexed} assets in {wall_first:.1f}s")

    print(f"[{source_id}] second pass: incremental")
    started = time.perf_counter()
    second = import_library(source, root=root, store_content=store_content,
                            trace_memory=False)
    wall_second = time.perf_counter() - started
    print(f"[{source_id}] second pass {second.counts.files_reused}/"
          f"{second.counts.files_total} files reused in {wall_second:.1f}s")

    counts = first.counts
    identity = counts.discovered == counts.parsed + counts.unsupported + counts.failed
    payload = {
        "source_id": source_id,
        "path": str(path),
        "version": "9.0.0",
        "license": KICAD_LICENSE,
        "license_url": KICAD_LICENSE_URL,
        "store_content": store_content,
        "snapshot_id": first.snapshot_id,
        "status": first.status,
        "first_pass": {
            "wall_seconds": round(wall_first, 3),
            "duration_ms": first.duration_ms,
            "counts": counts.model_dump(),
            "counts_by_kind": first.counts_by_kind,
            "failure_codes": first.failure_codes,
            "unsupported_codes": first.unsupported_codes,
            "semantic_warning_codes": first.semantic_warning_codes,
            "diagnostic_codes": first.diagnostic_codes,
            "diagnostics_total_capped": len(first.diagnostics),
            "diagnostics_truncated": first.diagnostics_truncated,
            "diagnostics_sample": [diag.model_dump() for diag in first.diagnostics[:20]],
            "process_max_rss_bytes": first.process_max_rss_bytes,
        },
        "second_pass": {
            "wall_seconds": round(wall_second, 3),
            "duration_ms": second.duration_ms,
            "counts": second.counts.model_dump(),
            "snapshot_id": second.snapshot_id,
        },
        "coverage_identity_holds": identity,
        "discovered": counts.discovered,
        "parsed": counts.parsed,
        "unsupported": counts.unsupported,
        "failed": counts.failed,
    }
    if not identity:
        raise SystemExit(f"coverage identity broken for {source_id}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--footprints", required=True)
    parser.add_argument("--root", default="/tmp/mota-kicad/mota-data")
    parser.add_argument("--out", default="docs/engine-v2/library-scan.json")
    parser.add_argument("--store-content", action="store_true",
                        help="Copy source bytes into the content-addressed store")
    parser.add_argument("--keep", action="store_true",
                        help="Keep an existing scan data root")
    args = parser.parse_args()

    root = Path(args.root)
    if not args.keep and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    scans = [
        scan("kicad-symbols-9.0.0", Path(args.symbols), root,
             store_content=args.store_content),
        scan("kicad-footprints-9.0.0", Path(args.footprints), root,
             store_content=args.store_content),
    ]
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "tool": "scripts/library_import_scan.py",
        "library_root": str(root / "catalog" / "library"),
        "sources": scans,
        "totals": {
            "discovered": sum(row["discovered"] for row in scans),
            "parsed": sum(row["parsed"] for row in scans),
            "unsupported": sum(row["unsupported"] for row in scans),
            "failed": sum(row["failed"] for row in scans),
        },
        "notes": [
            "All numbers come from real imports of the public KiCad 9.0.0 "
            "archives; no fixture or estimate is substituted.",
            f"store_content={args.store_content}: "
            + ("source bytes are copied into the content-addressed store."
               if args.store_content else
               "snapshots record per-file SHA-256 and paths but do not copy "
               "library bytes."),
            "process_max_rss_bytes is process-wide max RSS and therefore not "
            "attributable to a single source when both run in one process.",
            "auto_synthesis_ready is not evaluated in C1 (pair synthesis is C2).",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"Wrote {out.resolve()}")
    for row in scans:
        counts = row["first_pass"]["counts"]
        print(f"{row['source_id']}: discovered {counts['discovered']} = "
              f"parsed {counts['parsed']} + unsupported {counts['unsupported']} "
              f"+ failed {counts['failed']} · mapped {counts['mapped']} · "
              f"{row['first_pass']['wall_seconds']}s · "
              f"RSS {row['first_pass']['process_max_rss_bytes'] / (1024 * 1024):.0f} MB")


if __name__ == "__main__":
    main()
