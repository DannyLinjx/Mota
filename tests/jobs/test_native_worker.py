from __future__ import annotations

import json
from pathlib import Path

import pytest

from mota.domain.models import DesignVersion
from mota.engine_v2.contracts import JobBudget, JobRequest
from mota.jobs.contracts import (
    GeometryJobInput, WorkerInputTooLarge, WorkerUnavailable, freeze_input,
    kernel_binary, kernel_info, run_frozen_job, run_native_job, to_job_result,
)

MM = 1_000_000


def _point(x: int, y: int) -> dict:
    return {"kind": "point", "p": {"x": x, "y": y}}


def _segment(ax: int, ay: int, bx: int, by: int) -> dict:
    return {"kind": "segment", "a": {"x": ax, "y": ay}, "b": {"x": bx, "y": by}}


def _document(queries: list[dict] | None = None) -> GeometryJobInput:
    board = {
        "width_nm": 40 * MM,
        "height_nm": 30 * MM,
        "thickness_nm": 1_600_000,
        "stackup": {"layers": [
            {"id": "F.Cu", "order": 1, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
            {"id": "D1", "order": 2, "kind": "dielectric",
             "thickness_nm": 1_530_000, "material_ref": "FR4"},
            {"id": "B.Cu", "order": 3, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
        ]},
        "objects": [
            {"id": "track_f", "layer": "F.Cu",
             "shape": _segment(0, 0, 10 * MM, 0)},
            {"id": "pad_f", "layer": "F.Cu",
             "shape": {"kind": "circle", "center": {"x": 20 * MM, "y": 0},
                       "radius": MM}},
        ],
        "pads": [
            {"id": "U1:1", "plated": True,
             "copper_by_layer": {"F.Cu": {
                 "kind": "circle", "center": {"x": 0, "y": 0}, "radius": MM}},
             "drill": {"kind": "circle", "center": {"x": 0, "y": 0},
                       "radius": 300_000}},
        ],
        "vias": [
            {"id": "v1", "kind": "through", "start_layer": "F.Cu",
             "end_layer": "B.Cu", "drill_nm": 400_000,
             "pad": {"kind": "circle", "center": {"x": 5 * MM, "y": 0},
                     "radius": 400_000}},
        ],
    }
    default_queries = [
        {"id": "q1", "kind": "distance", "a": _point(5 * MM, 0),
         "b": _segment(0, -MM, 0, MM)},
        {"id": "q2", "kind": "stackup"},
        {"id": "q3", "kind": "index_stats"},
    ]
    return GeometryJobInput(input_id="c6-python-fixture", board=board,
                            queries=queries or default_queries)


@pytest.fixture(scope="module")
def kernel_path() -> Path:
    try:
        return kernel_binary()
    except WorkerUnavailable as error:
        pytest.skip(f"native kernel not built: {error}")


def test_kernel_info_and_distance(kernel_path: Path):
    info = kernel_info(kernel_path)
    assert info["name"] == "mota-kernel"
    assert info["version"] == "0.1.0-c6"
    assert info["limits"]["max_frame_bytes"] == 4 * 1024 * 1024

    result = run_native_job(_document(), binary=kernel_path)
    assert result.status == "SUCCEEDED", result.diagnostics
    assert result.complete
    assert result.completed == 3
    assert result.kernel["version"] == "0.1.0-c6"
    first = result.results[0]
    assert first["distance_nm"] == 5 * MM
    assert first["error_nm"] >= 0
    assert result.metrics["objects"] == 2
    assert result.input_sha256


def test_board_query_via_and_padstack(kernel_path: Path):
    queries = [
        {"id": "b1", "kind": "board_query", "layer": "F.Cu",
         "area": {"min_x": -MM, "min_y": -MM, "max_x": 11 * MM, "max_y": MM}},
        {"id": "v1", "kind": "via_span", "id_ref": "v1", "layer": "D1"},
        {"id": "p1", "kind": "padstack", "id_ref": "U1:1", "layer": "F.Cu",
         "transform": {"origin": {"x": 10 * MM, "y": 0},
                       "rotation_deg": 90, "bottom": False}},
        {"id": "s1", "kind": "stackup"},
    ]
    result = run_native_job(_document(queries), binary=kernel_path)
    assert result.status == "SUCCEEDED", result.diagnostics
    by_id = {item["id"]: item for item in result.results}
    assert by_id["b1"]["object_ids"] == ["track_f"]
    assert by_id["v1"]["present"] is True
    assert by_id["v1"]["connected"] is True
    assert by_id["p1"]["present"] is True
    assert by_id["p1"]["bounds"]["min_x"] == 9 * MM
    assert by_id["p1"]["bounds"]["max_x"] == 11 * MM
    assert by_id["s1"]["copper_count"] == 2
    assert by_id["s1"]["thickness_match"] is True


def test_frozen_input_tamper_is_rejected(kernel_path: Path):
    frozen = freeze_input(_document())
    try:
        path = Path(frozen.path)
        path.write_bytes(path.read_bytes() + b" ")
        result = run_frozen_job(frozen, budget=JobBudget(max_expansions=100),
                                binary=kernel_path)
        assert result.status == "FAILED"
        assert any(item["code"] == "STALE_JOB_INPUT"
                   for item in result.diagnostics)
    finally:
        frozen.cleanup()


def test_budget_cancel_and_unknown(kernel_path: Path, tmp_path: Path):
    partial = run_native_job(_document(), budget=JobBudget(max_expansions=1),
                             binary=kernel_path)
    assert partial.status == "PARTIAL"
    assert partial.completed == 1
    assert partial.checkpoint["next_query_index"] == 1

    unknown = run_native_job(_document(), budget=JobBudget(max_expansions=0),
                             binary=kernel_path)
    assert unknown.status == "UNKNOWN"
    assert unknown.status != "INFEASIBLE"
    assert any(item["code"] == "BUDGET_EXHAUSTED"
               for item in unknown.diagnostics)

    cancel_file = tmp_path / "cancel.flag"
    cancel_file.write_text("cancel", encoding="utf-8")
    cancelled = run_native_job(_document(), cancel_file=cancel_file,
                               binary=kernel_path)
    assert cancelled.status == "CANCELLED"
    assert cancelled.results == []


def test_query_error_is_isolated(kernel_path: Path):
    queries = [
        {"id": "good1", "kind": "distance", "a": _point(5 * MM, 0),
         "b": _segment(0, -MM, 0, MM)},
        {"id": "bad", "kind": "distance",
         "a": {"kind": "point", "p": {"x": 1_000_000_000_000_001, "y": 0}},
         "b": _segment(0, -MM, 0, MM)},
        {"id": "good2", "kind": "distance", "a": _point(7 * MM, 0),
         "b": _segment(0, -MM, 0, MM)},
    ]
    result = run_native_job(_document(queries), binary=kernel_path)
    assert result.status == "PARTIAL"
    assert result.completed == 2
    assert result.failed == 1
    failed = result.results[1]
    assert failed["status"] == "FAILED"
    assert failed["error"]["code"] == "COORDINATE_OUT_OF_RANGE"


def test_to_job_result_keeps_frozen_denominator(kernel_path: Path):
    document = _document()
    native = run_native_job(document, binary=kernel_path)
    request = JobRequest(
        job_id="job_c6_geometry", kind="geometry",
        base_version=DesignVersion(revision_id="rev_c6"),
        requested_targets=["q1", "q2", "q3"])
    mapped = to_job_result(native, request)
    assert mapped.status == "SUCCEEDED"
    assert mapped.requested_targets == ["q1", "q2", "q3"]
    assert mapped.completed_targets == ["q1", "q2", "q3"]
    assert mapped.unimplemented_targets == []
    assert mapped.read_set and mapped.read_set[0].sha256 == native.input_sha256


def test_unavailable_or_oversized():
    with pytest.raises(WorkerUnavailable):
        kernel_binary("/nonexistent/mota-kernel")
    oversized = GeometryJobInput(
        input_id="too-big", board={},
        queries=[{"id": "q1", "kind": "x",
                  "blob": "x" * (4 * 1024 * 1024)}])
    with pytest.raises(WorkerInputTooLarge):
        freeze_input(oversized)


def test_protocol_frame_is_one_json_line(kernel_path: Path):
    info = kernel_info(kernel_path)
    encoded = json.dumps(info)
    assert "\n" not in encoded
    assert info["limits"]["max_objects"] == 200_000
