"""Frozen single-ended routing benchmark runner.

The suite manifest pins every case by SHA-256. Requested nets are the frozen
denominator: an unrouted or failed case stays in the denominator and is never
silently removed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mota.engine_v2.contracts import JobBudget
from mota.jobs.contracts import run_native_job

SUITE_DIR = Path(__file__).resolve().parent / "routing_single_ended"


def load_manifest(suite: str) -> dict[str, Any]:
    path = SUITE_DIR / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("suite") != suite:
        raise ValueError(
            f"suite {suite!r} does not match manifest {manifest.get('suite')!r}")
    return manifest


def run_suite(suite: str, report_dir: str | Path, *,
              binary: str | None = None) -> dict[str, Any]:
    manifest = load_manifest(suite)
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        records.append(_run_case(case, binary))

    requested = sum(len(record["requested_nets"]) for record in records)
    completed = sum(len(record["completed_nets"]) for record in records)
    unrouted = sum(len(record["unrouted_nets"]) for record in records)
    aggregate = {
        "cases": len(records),
        "requested_targets": requested,
        "completed_targets": completed,
        "unrouted_targets": unrouted,
        "u_total": sum(int(record.get("u_total", 0)) for record in records),
        "routed_ratio": (completed / requested) if requested else 0.0,
        "frozen_hashes_ok": all(record["frozen_hash_ok"] for record in records),
        "status_counts": _status_counts(records),
        "total_runtime_ms": sum(float(record.get("runtime_ms", 0.0))
                                for record in records),
    }
    report = {
        "suite": suite,
        "suite_version": manifest.get("version"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frozen_hashes_ok": aggregate["frozen_hashes_ok"],
        "aggregate": aggregate,
        "cases": records,
    }
    (directory / f"{suite}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (directory / f"{suite}.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _run_case(case: dict[str, Any], binary: str | None) -> dict[str, Any]:
    requested = list(case.get("requested_nets", []))
    path = SUITE_DIR / case["file"]
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    record: dict[str, Any] = {
        "id": case["id"],
        "file": case["file"],
        "sha256": case["sha256"],
        "actual_sha256": actual,
        "frozen_hash_ok": actual == case["sha256"],
        "requested_nets": requested,
        "completed_nets": [],
        "unrouted_nets": list(requested),
        "status": "INVALID",
        "u_total": 0,
        "wirelength_nm": 0.0,
        "via_count": 0,
        "track_count": 0,
        "expansions": 0,
        "rounds": 0,
        "semantic_hash": "",
        "runtime_ms": 0.0,
        "diagnostics": [],
    }
    if not record["frozen_hash_ok"]:
        record["diagnostics"] = ["FROZEN_HASH_MISMATCH"]
        return record
    try:
        document = json.loads(payload)
        native = run_native_job(
            document, binary=binary,
            budget=JobBudget(wall_seconds=60.0, max_expansions=1000))
    except Exception as error:  # noqa: BLE001 - benchmark records failures
        record["status"] = "ERROR"
        record["diagnostics"] = [f"{type(error).__name__}: {error}"]
        return record
    if native.status != "SUCCEEDED" or not native.results:
        record["status"] = "ERROR"
        record["diagnostics"] = [native.status] + [
            str(item.get("code", item)) for item in native.diagnostics]
        return record
    result = native.results[0]
    if result.get("status") != "OK":
        record["status"] = "ERROR"
        record["diagnostics"] = [str(result.get("error", {}))]
        return record
    routing = dict(result.get("routing", {}))
    completed = [net for net in requested
                 if net in set(routing.get("completed_nets", []))]
    record.update({
        "status": routing.get("status", "UNKNOWN"),
        "completed_nets": completed,
        "unrouted_nets": [net for net in requested if net not in completed],
        "u_total": int(routing.get("u_total", 0)),
        "short_count": int(routing.get("short_count", 0)),
        "wirelength_nm": float(routing.get("wirelength_nm", 0.0)),
        "via_count": int(routing.get("via_count", 0)),
        "track_count": len(routing.get("tracks", [])),
        "expansions": int(routing.get("expansions", 0)),
        "rounds": int(routing.get("rounds", 0)),
        "grid": {
            "columns": int(routing.get("grid_columns", 0)),
            "rows": int(routing.get("grid_rows", 0)),
            "layers": int(routing.get("grid_layers", 0)),
        },
        "semantic_hash": str(routing.get("semantic_hash", "")),
        "runtime_ms": float(result.get("metrics", {}).get("elapsed_ms", 0.0)),
        "diagnostics": list(routing.get("diagnostics", [])),
    })
    return record


def _status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        f"# Routing benchmark — {report['suite']}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"- cases: **{aggregate['cases']}**",
        f"- requested nets (frozen denominator): **{aggregate['requested_targets']}**",
        f"- completed: **{aggregate['completed_targets']}**",
        f"- unrouted (kept in denominator): **{aggregate['unrouted_targets']}**",
        f"- U total: **{aggregate['u_total']}**",
        f"- frozen hashes: **{'ok' if aggregate['frozen_hashes_ok'] else 'MISMATCH'}**",
        f"- status counts: {aggregate['status_counts']}",
        "",
        "| case | status | requested | completed | unrouted | U | wirelength mm | vias | runtime ms | frozen |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in report["cases"]:
        lines.append(
            f"| {record['id']} | {record['status']} | "
            f"{len(record['requested_nets'])} | {len(record['completed_nets'])} | "
            f"{len(record['unrouted_nets'])} | {record.get('u_total', 0)} | "
            f"{float(record.get('wirelength_nm', 0.0)) / 1e6:.2f} | "
            f"{record.get('via_count', 0)} | "
            f"{float(record.get('runtime_ms', 0.0)):.1f} | "
            f"{'ok' if record['frozen_hash_ok'] else 'MISMATCH'} |")
    lines += [
        "",
        "Unrouted nets are never removed from the denominator. "
        "INFEASIBLE is a finite-grid proof; UNKNOWN/PARTIAL means the budget was exhausted.",
        "",
    ]
    return "\n".join(lines)
