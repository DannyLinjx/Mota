#!/usr/bin/env python
"""Real template regression: runs each Golden template 10 times and writes
verification sidecars with actual run/revision ids.

Usage:
    MOTA_DATA_DIR=/tmp/mota-regression python scripts/regression.py
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mota.app.service import ApplicationService  # noqa: E402
from mota.auth.service import username_key  # noqa: E402
from mota.catalog.templates import get_template  # noqa: E402

TEMPLATES = {
    "power_3v3": [
        {"input_voltage_v": 5.0, "max_load_ma": 50, "board_width_mm": 40, "board_height_mm": 30},
        {"input_voltage_v": 4.9, "max_load_ma": 30, "board_width_mm": 44, "board_height_mm": 30},
        {"input_voltage_v": 5.1, "max_load_ma": 50, "board_width_mm": 40, "board_height_mm": 34},
    ],
    "led_timer": [
        {"supply_voltage_v": 5.0, "target_frequency_hz": 1.0, "board_width_mm": 50, "board_height_mm": 35},
        {"supply_voltage_v": 5.0, "target_frequency_hz": 1.0, "board_width_mm": 55, "board_height_mm": 35},
        {"supply_voltage_v": 4.9, "target_frequency_hz": 1.0, "board_width_mm": 50, "board_height_mm": 40},
    ],
    "i2c_temperature": [
        {"supply_voltage_v": 3.3, "bus_frequency_khz": 100, "board_width_mm": 40, "board_height_mm": 30},
        {"supply_voltage_v": 3.3, "bus_frequency_khz": 50, "board_width_mm": 44, "board_height_mm": 30},
        {"supply_voltage_v": 3.3, "bus_frequency_khz": 10, "board_width_mm": 40, "board_height_mm": 34},
    ],
}

PASSWORD = "MotaRegression!2026"
USERNAME = "regression"


def login_or_register(service: ApplicationService):
    try:
        result = service.dispatch("auth.login", {"username": USERNAME,
                                                 "password": PASSWORD}, None)
    except Exception:
        result = service.dispatch("auth.register", {
            "username": USERNAME, "password": PASSWORD,
            "display_name": "Regression"}, None)
    info = result["session"]
    from mota.app.state import SessionContext

    return SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                          username=info["username"], epoch=1)


def wait(service, session, run_id: str) -> dict:
    deadline = time.time() + 120
    while time.time() < deadline:
        data = service.dispatch("run.inspect", {"run_id": run_id}, session)
        if data["run"]["state"] in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
                                    "RECOVERABLE"):
            return data
        time.sleep(0.1)
    raise TimeoutError(run_id)


def main() -> int:
    data_root = Path(os.environ.get("MOTA_DATA_DIR", "/tmp/mota-regression"))
    service = ApplicationService(data_root)
    session = login_or_register(service)
    report: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "platform": platform.platform(), "python": platform.python_version(),
                    "data_root": str(data_root), "templates": {}}
    for template_id, variants in TEMPLATES.items():
        recipe = get_template(template_id)
        runs = []
        hashes: dict[str, set[str]] = {}
        wall_times = []
        for index in range(10):
            params = variants[index % len(variants)]
            created = service.dispatch("project.create", {
                "name": f"Regression {template_id} {index + 1}",
                "template_id": template_id}, session)
            project_id = created["project_id"]
            started = service.dispatch("run.start", {
                "project_id": project_id,
                "expected_revision_id": created["current_revision_id"],
                "input": {"kind": "template", "template_id": template_id,
                          "parameters": params, "accept_template_defaults": True},
                "idempotency_key": f"regression_{template_id}_{index}_{time.time_ns()}"},
                session)
            final = wait(service, session, started["run_id"])
            run = final["run"]
            revision_id = run.get("committed_revision_id")
            opened = service.dispatch("project.open", {"project_id": project_id}, session)
            variant_key = json.dumps({k: params[k] for k in sorted(params)
                                      if k.startswith("board")}, sort_keys=True)
            hashes.setdefault(variant_key, set()).add(opened["revision"]["design_hash"])
            wall = final["run"].get("metrics", {}).get("wall_ms", 0)
            wall_times.append(wall)
            runs.append({
                "index": index + 1,
                "run_id": run["run_id"],
                "revision_id": revision_id,
                "state": run["state"],
                "review_status": run.get("review_status"),
                "counts": opened["checks_summary"],
                "parameters": params,
                "design_hash": opened["revision"]["design_hash"],
                "wall_ms": wall,
            })
            print(f"{template_id} run {index + 1}/10: {run['state']} "
                  f"{run.get('review_status')} {opened['revision']['design_hash'][:10]}")
        states = Counter(run["state"] for run in runs)
        reviews = Counter(run["review_status"] for run in runs)
        deterministic = all(len(values) == 1 for values in hashes.values())
        all_cached = all(run["revision_id"] for run in runs)
        if template_id == "power_3v3":
            verified = False
            note = ("Required output-capacitor effective-capacitance and ESR evidence is "
                    "not recorded, so every run is REVIEW_BLOCKED/PARTIAL by design.")
        else:
            verified = (deterministic and all_cached
                        and states.get("SUCCEEDED", 0) == len(runs)
                        and reviews.get("READY_FOR_REVIEW", 0) == len(runs))
            note = "All runs READY_FOR_REVIEW with deterministic canonical hashes."
        sidecar = {
            "schema_version": 1,
            "template_id": template_id,
            "template_version": recipe.version,
            "status": "verified" if verified else "candidate",
            "validated_run_ids": [r["run_id"] for r in runs] if verified else [],
            "validated_revision_ids": [r["revision_id"] for r in runs],
            "runs": len(runs),
            "sample_runs": runs[:3],
            "states": dict(states),
            "review_statuses": dict(reviews),
            "deterministic_per_variant": deterministic,
            "design_hashes_by_board_variant": {k: sorted(v) for k, v in hashes.items()},
            "wall_ms_median": statistics.median(wall_times) if wall_times else 0,
            "wall_ms_max": max(wall_times) if wall_times else 0,
            "generated_at": report["generated_at"],
            "platform": report["platform"],
            "python": report["python"],
            "notes": note,
        }
        sidecar_path = ROOT / "src" / "mota" / "resources" / "templates" / \
            f"{template_id}.verification.json"
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        report["templates"][template_id] = sidecar
    out = ROOT / "docs" / "reports" / "regression.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
