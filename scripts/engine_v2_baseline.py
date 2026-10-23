#!/usr/bin/env python
"""Record the three real v1 circuits as engine-v2 migration baselines."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mota.app.service import ApplicationService  # noqa: E402
from mota.app.state import SessionContext  # noqa: E402
from mota.engine_v2 import migrate_snapshot, semantic_hash, semantic_signature  # noqa: E402
from mota.projects.store import ProjectStore  # noqa: E402

TEMPLATES = ["power_3v3", "led_timer", "i2c_temperature"]
PASSWORD = "Passw0rd!23"
USERNAME = "enginev2baseline"
OUT = ROOT / "docs" / "engine-v2" / "baseline"


def _wait(service, session, run_id: str) -> dict:
    deadline = time.time() + 120
    while time.time() < deadline:
        data = service.dispatch("run.inspect", {"run_id": run_id}, session)
        if data["run"]["state"] in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
                                    "RECOVERABLE"):
            return data
        time.sleep(0.1)
    raise TimeoutError(run_id)


def main() -> int:
    data_root = Path("/tmp/mota-engine-v2-baseline")
    if data_root.exists():
        shutil.rmtree(data_root)
    os.environ["MOTA_DATA_DIR"] = str(data_root)
    service = ApplicationService(data_root)
    registered = service.dispatch("auth.register", {
        "username": USERNAME, "password": PASSWORD, "display_name": "Engine v2"}, None)
    info = registered["session"]
    session = SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                             username=USERNAME, epoch=1)
    store = ProjectStore(session.scope(), data_root)
    OUT.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    for template_id in TEMPLATES:
        project = service.dispatch("project.create", {
            "name": f"Baseline {template_id}"}, session)
        opened = service.dispatch("project.open", {
            "project_id": project["project_id"]}, session)
        started = service.dispatch("run.start", {
            "project_id": project["project_id"],
            "expected_revision_id": opened["current_revision_id"],
            "input": {"kind": "template", "template_id": template_id,
                      "parameters": {}, "accept_template_defaults": True},
            "idempotency_key": f"engine_v2_{template_id}"}, session)
        final = _wait(service, session, started["run_id"])
        revision_id = final["run"]["committed_revision_id"]
        snapshot = store.load_revision(project["project_id"], revision_id)
        doc = migrate_snapshot({
            "requirement": snapshot.requirement, "circuit": snapshot.circuit,
            "schematic": snapshot.schematic, "board": snapshot.board,
            "checks": snapshot.checks,
        }, design_version=snapshot.version,
            design_hash=snapshot.revision.design_hash)
        signature = semantic_signature(snapshot)
        record = {
            "schema_version": 2,
            "template_id": template_id,
            "template_version": snapshot.revision.build_manifest.template_version,
            "parameters": {k: v.value
                           for k, v in snapshot.requirement.parameters.items()},
            "requirement": {
                "board_size": snapshot.requirement.board_size,
                "supply": snapshot.requirement.supply,
                "parameters": {k: v.model_dump()
                               for k, v in snapshot.requirement.parameters.items()},
            },
            "state": final["run"]["state"],
            "review_status": snapshot.checks.review_status,
            "counts": snapshot.checks.counts,
            "design_hash": snapshot.revision.design_hash,
            "semantic_hash_v1": semantic_hash(snapshot),
            "semantic_hash_v2": semantic_hash(doc),
            "components": len(snapshot.circuit.components),
            "nets": len(snapshot.circuit.nets),
            "pads": len(snapshot.board.pads),
            "tracks": len(snapshot.board.tracks),
            "vias": len(snapshot.board.vias),
            "through_hole_pads": sum(1 for p in snapshot.board.pads if p.pad_type == "tht"),
            "component_ids": sorted(c.id for c in snapshot.circuit.components),
            "net_ids": sorted(n.id for n in snapshot.circuit.nets),
            "check_statuses": {c.check_id: c.status for c in snapshot.checks.checks},
            "run_id": final["run"]["run_id"],
            "revision_id": revision_id,
        }
        assert record["semantic_hash_v1"] == record["semantic_hash_v2"], template_id
        (OUT / f"{template_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        index.append({k: record[k] for k in (
            "template_id", "state", "review_status", "counts", "design_hash",
            "semantic_hash_v1", "components", "nets", "pads", "tracks", "vias")})
        print(f"{template_id}: {record['state']} {record['review_status']} "
              f"{record['counts']} tracks={record['tracks']} vias={record['vias']}")
    (OUT / "index.json").write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(), "python": platform.python_version(),
        "engine": "mota-engine-v1 (migrated to v2)", "templates": index,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote baselines to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
