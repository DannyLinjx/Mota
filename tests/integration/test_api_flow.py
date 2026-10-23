from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from mota.app.service import ApplicationService
from mota.errors import MotaError
from mota.projects.store import ProjectStore
from mota.app.state import UserScope
from tests.conftest import (
    build_template, login, make_user, new_project, wait_run,
)


def test_register_login_logout_and_isolation(service):
    alice = make_user(service, "alice")
    bob = make_user(service, "bob")
    status = service.dispatch("auth.status", {}, alice)
    assert status["authenticated"] is True
    assert status["profile"]["username"] == "alice"

    a_project, _ = new_project(service, alice, "Alice board")
    b_project, _ = new_project(service, bob, "Bob board")
    assert {p["project_id"] for p in service.dispatch(
        "project.list", {}, alice)["projects"]} == {a_project}
    assert {p["project_id"] for p in service.dispatch(
        "project.list", {}, bob)["projects"]} == {b_project}

    with pytest.raises(MotaError) as exc:
        service.dispatch("project.open", {"project_id": a_project}, bob)
    assert exc.value.code == "PROJECT_NOT_FOUND"

    service.dispatch("auth.logout", {}, alice)
    with pytest.raises(MotaError) as exc:
        service.dispatch("project.list", {}, None)
    assert exc.value.code == "AUTH_REQUIRED"

    again = login(service, "alice")
    assert service.dispatch("project.list", {}, again)["projects"][0]["name"] == "Alice board"


def test_wrong_password_is_rejected(service):
    make_user(service, "carol")
    with pytest.raises(MotaError) as exc:
        service.dispatch("auth.login", {"username": "carol", "password": "wrong-pass"},
                         None)
    assert exc.value.code == "AUTH_FAILED"
    assert "incorrect" in exc.value.message.lower()


def test_full_template_run_and_history(service):
    session = make_user(service, "builder")
    project_id, first_revision = new_project(service, session, "I2C sensor")
    data = build_template(service, session, project_id, "i2c_temperature")
    run = data["run"]
    assert run["state"] == "SUCCEEDED"
    assert run["review_status"] == "READY_FOR_REVIEW"
    assert run["committed_revision_id"]

    revisions = service.dispatch("revision.list", {"project_id": project_id},
                                 session)["revisions"]
    assert len(revisions) == 2
    assert revisions[-1]["is_current"]

    old = service.dispatch("revision.open", {
        "project_id": project_id, "revision_id": first_revision}, session)
    assert old["read_only"] is True
    assert old["revision"]["component_count"] == 0

    restored = service.dispatch("revision.restore", {
        "project_id": project_id, "source_revision_id": first_revision,
        "expected_revision_id": run["committed_revision_id"]}, session)
    assert restored["commit_status"] == "COMMITTED"
    assert restored["revision_id"] not in (first_revision, run["committed_revision_id"])
    revisions = service.dispatch("revision.list", {"project_id": project_id},
                                 session)["revisions"]
    assert len(revisions) == 3
    assert revisions[-1]["restored_from_revision_id"] == first_revision


def test_edit_conflict_undo_redo_and_busy(service):
    session = make_user(service, "editor")
    project_id, first = new_project(service, session, "Edits")
    data = build_template(service, session, project_id, "led_timer")
    current = data["run"]["committed_revision_id"]

    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    component = "component_j1"
    moved = service.dispatch("design.patch", {
        "project_id": project_id, "expected_revision_id": current,
        "operations": [{"op": "move_component", "component_id": component,
                        "x_nm": 7_000_000, "y_nm": 17_500_000}],
        "idempotency_key": "move_1"}, session)
    assert moved["commit_status"] == "COMMITTED"
    assert moved["review_status"] == "READY_FOR_REVIEW"
    snapshot = ProjectStore(session.scope(), service.root).load_revision(
        project_id, moved["revision_id"])
    placed = snapshot.circuit.component(component).placement
    assert placed.x_nm == 7_000_000 and placed.y_nm == 17_500_000
    assert snapshot.circuit.component(component).placement_origin == "user"

    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", {
            "project_id": project_id, "expected_revision_id": current,
            "operations": [{"op": "rotate_component", "component_id": component}],
            "idempotency_key": "stale"}, session)
    assert exc.value.code == "REVISION_CONFLICT"

    undone = service.dispatch("design.undo", {"project_id": project_id}, session)
    assert undone["revision_id"] != moved["revision_id"]
    redone = service.dispatch("design.redo", {"project_id": project_id}, session)
    assert redone["revision_id"] not in (moved["revision_id"], undone["revision_id"])

    # A synthetic active run makes human writes busy without touching the canvas.
    pdir = ProjectStore(session.scope(), service.root).project_dir(project_id)
    fake = pdir / "runs" / "run_fake_busy" / "run.json"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(json.dumps({"state": "ROUTING"}), encoding="utf-8")
    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", {
            "project_id": project_id,
            "expected_revision_id": redone["revision_id"],
            "operations": [{"op": "rotate_component", "component_id": component}],
            "idempotency_key": "busy"}, session)
    assert exc.value.code == "PROJECT_BUSY"
    fake.unlink()


def test_export_formats_and_kicad_gate(service, tmp_path):
    session = make_user(service, "exporter")
    project_id, _ = new_project(service, session, "Export board")
    data = build_template(service, session, project_id, "i2c_temperature")
    revision = data["run"]["committed_revision_id"]
    for fmt in ("mota", "svg", "bom", "report"):
        result = service.dispatch("project.export", {
            "project_id": project_id, "revision_id": revision, "format": fmt,
            "destination_path": str(tmp_path / fmt)}, session)
        assert result["files"]
        for entry in result["files"]:
            assert (tmp_path / fmt / entry["path"]).exists()

    with pytest.raises(MotaError) as exc:
        service.dispatch("project.export", {
            "project_id": project_id, "revision_id": revision, "format": "kicad",
            "destination_path": str(tmp_path / "kicad")}, session)
    assert exc.value.code == "COMPATIBILITY_UNVERIFIED"


def test_bundle_import_into_empty_root_offline(service, tmp_path):
    session = make_user(service, "porter")
    project_id, _ = new_project(service, session, "Portable board")
    data = build_template(service, session, project_id, "power_3v3")
    revision = data["run"]["committed_revision_id"]
    exported = service.dispatch("project.export", {
        "project_id": project_id, "revision_id": revision, "format": "mota",
        "destination_path": str(tmp_path)}, session)
    bundle = tmp_path / exported["files"][0]["path"]
    assert bundle.exists()

    second_root = tmp_path / "second-root"
    service2 = ApplicationService(second_root)
    other = make_user(service2, "importer")
    result = service2.dispatch("project.import_bundle", {
        "file_path": str(bundle), "name": "Imported power",
        "idempotency_key": "import_once"}, other)
    assert result["project_id"].startswith("project_")
    assert (second_root / "users").exists()
    opened = service2.dispatch("project.open", {"project_id": result["project_id"]},
                               other)
    assert opened["component_count"] == 5
    assert opened["checks_summary"]["NOT_CHECKED"] >= 2
    for view in ("schematic", "pcb2d", "pcb3d"):
        scene = service2.dispatch("scene.get", {
            "project_id": result["project_id"],
            "design_version": {"revision_id": result["revision_id"]},
            "view": view}, other)
        assert scene["objects"]
        assert scene["design_version_key"] == f"rev:{result['revision_id']}"

    replay = service2.dispatch("project.import_bundle", {
        "file_path": str(bundle), "name": "Ignored",
        "idempotency_key": "import_once"}, other)
    assert replay["project_id"] == result["project_id"]


def test_bundle_rejects_traversal_and_tampering(service, tmp_path):
    session = make_user(service, "attacker")
    project_id, _ = new_project(service, session, "Attack board")
    data = build_template(service, session, project_id, "power_3v3")
    revision = data["run"]["committed_revision_id"]
    exported = service.dispatch("project.export", {
        "project_id": project_id, "revision_id": revision, "format": "mota",
        "destination_path": str(tmp_path)}, session)
    original = tmp_path / exported["files"][0]["path"]

    evil = tmp_path / "evil.mota"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(evil, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name))
        info = zipfile.ZipInfo("../escaped.txt")
        out.writestr(info, b"nope")
    with pytest.raises(MotaError) as exc:
        service.dispatch("project.import_bundle", {
            "file_path": str(evil), "idempotency_key": "evil"}, session)
    assert exc.value.code == "BUNDLE_INVALID"

    tampered = tmp_path / "tampered.mota"
    with zipfile.ZipFile(original) as src, zipfile.ZipFile(tampered, "w") as out:
        for name in src.namelist():
            data_bytes = src.read(name)
            if name == "circuit.json":
                data_bytes = data_bytes.replace(b'"components"', b'"components" ')
            out.writestr(name, data_bytes)
    with pytest.raises(MotaError) as exc:
        service.dispatch("project.import_bundle", {
            "file_path": str(tampered), "idempotency_key": "tampered"}, session)
    assert exc.value.code == "BUNDLE_INVALID"


def test_artifact_chunking_and_tamper_detection(service):
    session = make_user(service, "artifacts")
    project_id, _ = new_project(service, session, "Artifact board")
    data = build_template(service, session, project_id, "led_timer")
    revision = data["run"]["committed_revision_id"]
    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    artifact_id = opened["project"]["thumbnail_artifact_id"]
    first = service.dispatch("artifact.read", {
        "project_id": project_id,
        "design_version": {"revision_id": revision},
        "artifact_id": artifact_id, "offset": 0, "length": 64}, session)
    assert first["total_bytes"] > 64
    assert first["next_offset"] == 64
    assert len(first["data_base64"]) > 0

    with pytest.raises(MotaError) as exc:
        service.dispatch("artifact.read", {
            "project_id": project_id,
            "design_version": {"revision_id": revision},
            "artifact_id": artifact_id, "offset": 0,
            "length": 2 * 1024 * 1024}, session)
    assert exc.value.code == "ARTIFACT_INVALID"

    store = ProjectStore(session.scope(), service.root)
    path = store.project_dir(project_id) / "revisions" / revision / "thumbnail.svg"
    original = path.read_bytes()
    path.write_bytes(original + b"tamper")
    try:
        with pytest.raises(MotaError) as exc:
            service.dispatch("artifact.read", {
                "project_id": project_id,
                "design_version": {"revision_id": revision},
                "artifact_id": artifact_id, "offset": 0, "length": 64}, session)
        assert exc.value.code == "ARTIFACT_INVALID"
    finally:
        path.write_bytes(original)


def test_commit_fault_keeps_current_and_reports_candidate(service, monkeypatch):
    session = make_user(service, "faults")
    project_id, first = new_project(service, session, "Fault board")
    data = build_template(service, session, project_id, "led_timer")
    current = data["run"]["committed_revision_id"]

    monkeypatch.setenv("MOTA_TEST_FAULT", "after_prepared")
    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", {
            "project_id": project_id, "expected_revision_id": current,
            "operations": [{"op": "rotate_component", "component_id": "component_j1"}],
            "idempotency_key": "fault_edit"}, session)
    assert exc.value.code == "INJECTED_FAULT"
    store = ProjectStore(session.scope(), service.root)
    assert store.current_revision_id(project_id) == current
    index = json.loads((store.project_dir(project_id) / "commit_index.json").read_text())
    statuses = {e["status"] for e in index["entries"]}
    assert "PREPARED" in statuses
    recovery = store.recover_commits(project_id)
    assert store.current_revision_id(project_id) == current
    monkeypatch.delenv("MOTA_TEST_FAULT")
    fixed = service.dispatch("design.patch", {
        "project_id": project_id, "expected_revision_id": current,
        "operations": [{"op": "rotate_component", "component_id": "component_j1"}],
        "idempotency_key": "fault_edit_fixed"}, session)
    assert fixed["commit_status"] == "COMMITTED"


def test_engine_restart_marks_active_run_recoverable(service):
    from mota.domain.models import utcnow
    from mota.projects.store import ProjectStore

    session = make_user(service, "restart")
    project, first = new_project(service, session, "Restart board")
    store = ProjectStore(session.scope(), service.root)
    draft = store.begin_draft(project, first, "run_orphan")
    run_dir = store.run_dir(project, "run_orphan")
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "run_orphan", "project_id": project,
        "owner_user_id": session.user_id, "parent_run_id": None,
        "base_revision_id": first, "draft_id": draft.draft_id, "draft_seq": 0,
        "state": "ROUTING", "mode": "template", "input_kind": "template",
        "template_id": "led_timer", "parameters": {},
        "checkpoint_available": True, "last_event_seq": 5,
        "created_at": utcnow(), "updated_at": utcnow(),
    }), encoding="utf-8")
    listing = service.dispatch("project.list", {}, session)
    assert listing["projects"]
    inspected = service.dispatch("run.inspect", {"run_id": "run_orphan"}, session)
    assert inspected["run"]["state"] == "RECOVERABLE"
    assert inspected["run"]["checkpoint_available"] is True
    assert inspected["events"][-1]["payload"]["recovered_after_restart"] is True
    opened = service.dispatch("project.open", {"project_id": project}, session)
    assert opened["active_run"]["run_id"] == "run_orphan"

    resumed = service.dispatch("run.resume", {
        "run_id": "run_orphan", "expected_revision_id": first}, session)
    assert resumed["parent_run_id"] == "run_orphan"
    final = wait_run(service, session, resumed["run_id"])
    assert final["run"]["state"] in ("SUCCEEDED", "PARTIAL")
    old = service.dispatch("run.inspect", {"run_id": "run_orphan"}, session)
    assert old["run"]["state"] == "RECOVERABLE"


def test_route_failure_commits_partial_without_green_checks(service, monkeypatch):
    import mota.app.runs as runs_module
    from mota.domain.models import RatsnestLine

    def fake_route(board, constraints=None, net_classes=None, cancel=None,
                   time_budget=None):
        lines = []
        pads = [p for p in board.pads if p.net_id == "net_gnd"]
        for a, b in zip(pads, pads[1:]):
            lines.append(RatsnestLine(net_id="net_gnd", x1_nm=a.x_nm, y1_nm=a.y_nm,
                                      x2_nm=b.x_nm, y2_nm=b.y_nm))
        return board.model_copy(update={"unrouted_nets": ["net_gnd"],
                                        "ratsnest": lines}), ["net_gnd"]

    monkeypatch.setattr(runs_module, "route_board", fake_route)
    session = make_user(service, "routefail")
    project_id, first = new_project(service, session, "Route fail")
    data = build_template(service, session, project_id, "led_timer")
    run = data["run"]
    assert run["state"] == "PARTIAL"
    assert run["review_status"] == "REVIEW_BLOCKED"
    revision = run["committed_revision_id"]
    checks = service.dispatch("validation.list", {
        "project_id": project_id,
        "design_version": {"revision_id": revision}}, session)
    by_id = {c["check_id"]: c for c in checks["report"]["checks"]}
    assert by_id["ROUTING_COMPLETE"]["status"] == "FAIL"
    assert by_id["COPPER_CONNECTIVITY"]["status"] == "FAIL"
    assert "net_gnd" in by_id["ROUTING_COMPLETE"]["evidence"]["unrouted_nets"]


def test_duplicate_reference_and_invalid_asset_are_rejected(service):
    session = make_user(service, "badparts")
    project_id, first = new_project(service, session, "Bad parts")
    data = build_template(service, session, project_id, "led_timer")
    current = data["run"]["committed_revision_id"]
    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", {
            "project_id": project_id, "expected_revision_id": current,
            "operations": [{"op": "add_component", "part_id": "conn_1x02",
                            "reference": "R1", "x_mm": 5, "y_mm": 5}],
            "idempotency_key": "dup_ref"}, session)
    assert exc.value.code == "DUPLICATE_REFERENCE"
    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", {
            "project_id": project_id, "expected_revision_id": current,
            "operations": [{"op": "add_component", "part_id": "no_such_part"}],
            "idempotency_key": "bad_asset"}, session)
    assert exc.value.code == "PART_NOT_FOUND"


def test_draft_is_initialised_once_and_stale_seq_is_rejected(service):
    from mota.agent.tools import ToolExecutor
    from mota.domain.models import RunRecord, utcnow

    session = make_user(service, "draftseq")
    project_id, first = new_project(service, session, "Draft seq")
    store = ProjectStore(session.scope(), service.root)
    draft_one = store.begin_draft(project_id, first, "run_seq")
    draft_two = store.begin_draft(project_id, first, "run_seq")
    assert draft_one.draft_id == draft_two.draft_id
    assert draft_two.draft_seq == 0

    run = RunRecord(run_id="run_seq", project_id=project_id,
                    owner_user_id=session.user_id, base_revision_id=first,
                    draft_id=draft_one.draft_id, state="SYNTHESIZING",
                    created_at=utcnow(), updated_at=utcnow())
    service.runs._save_run(store, run)
    executor = ToolExecutor(session.scope(), store, run, service.runs, service.root)
    result = executor.execute("apply_design_patch", {
        "draft_id": draft_one.draft_id, "expected_draft_seq": 7,
        "operations": []})
    assert result["ok"] is False
    diagnostic = result["diagnostics"][0]
    assert diagnostic["code"] == "DRAFT_CONFLICT"
    assert diagnostic.get("current_draft_seq") == 0


def test_design_patch_idempotency_replay_and_conflict(service):
    session = make_user(service, "editidem")
    project_id, first = new_project(service, session, "Idempotent edit")
    data = build_template(service, session, project_id, "i2c_temperature")
    current = data["run"]["committed_revision_id"]
    payload = {
        "project_id": project_id, "expected_revision_id": current,
        "operations": [{"op": "rotate_component", "component_id": "component_j1"}],
        "idempotency_key": "edit_once"}
    first_result = service.dispatch("design.patch", payload, session)
    assert first_result["commit_status"] == "COMMITTED"
    replay = service.dispatch("design.patch", payload, session)
    assert replay.get("idempotent_replay") is True
    assert replay["revision_id"] == first_result["revision_id"]
    different = dict(payload)
    different["operations"] = [{"op": "rotate_component",
                                "component_id": "component_u1"}]
    with pytest.raises(MotaError) as exc:
        service.dispatch("design.patch", different, session)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_duplicate_project_gets_new_ids_and_reopens_views(service):
    session = make_user(service, "dupe")
    project_id, _ = new_project(service, session, "Original")
    data = build_template(service, session, project_id, "i2c_temperature")
    result = service.dispatch("project.duplicate", {
        "source_project_id": project_id,
        "source_revision_id": data["run"]["committed_revision_id"],
        "name": "Copy board", "idempotency_key": "dup_once"}, session)
    assert result["project_id"] != project_id
    opened = service.dispatch("project.open", {"project_id": result["project_id"]},
                              session)
    assert opened["component_count"] == 5
    for view in ("schematic", "pcb2d", "pcb3d"):
        scene = service.dispatch("scene.get", {
            "project_id": result["project_id"],
            "design_version": {"revision_id": result["revision_id"]},
            "view": view}, session)
        assert scene["objects"]
        ids = {o["component_id"] for o in scene["objects"] if o.get("component_id")}
        assert len(ids) == 5


def test_run_idempotency_and_cancel_resume(service):
    session = make_user(service, "runner")
    project_id, first = new_project(service, session, "Run lifecycle")
    payload = {"kind": "template", "template_id": "led_timer",
               "parameters": {}, "accept_template_defaults": True}
    started = service.dispatch("run.start", {
        "project_id": project_id, "expected_revision_id": first, "input": payload,
        "idempotency_key": "same_key"}, session)
    replay = service.dispatch("run.start", {
        "project_id": project_id, "expected_revision_id": first, "input": payload,
        "idempotency_key": "same_key"}, session)
    assert replay["run_id"] == started["run_id"]

    cancelled = service.dispatch("run.cancel", {"run_id": started["run_id"]}, session)
    data = wait_run(service, session, started["run_id"])
    if data["run"]["state"] == "CANCELLED":
        assert data["run"]["checkpoint_available"] is True
        resumed = service.dispatch("run.resume", {
            "run_id": started["run_id"], "expected_revision_id": first}, session)
        assert resumed["parent_run_id"] == started["run_id"]
        assert resumed["run_id"] != started["run_id"]
        final = wait_run(service, session, resumed["run_id"])
        assert final["run"]["state"] in ("SUCCEEDED", "PARTIAL")
        old = service.dispatch("run.inspect", {"run_id": started["run_id"]}, session)
        assert old["run"]["state"] == "CANCELLED"
    else:
        assert cancelled["outcome"] in ("cancel_requested", "already_committed")
        assert data["run"]["state"] in ("SUCCEEDED", "PARTIAL")

    with pytest.raises(MotaError) as exc:
        service.dispatch("run.start", {
            "project_id": project_id,
            "expected_revision_id": service.dispatch(
                "project.open", {"project_id": project_id}, session)["current_revision_id"],
            "input": {"kind": "template", "template_id": "i2c_temperature",
                      "parameters": {}, "accept_template_defaults": True},
            "idempotency_key": "same_key"}, session)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
