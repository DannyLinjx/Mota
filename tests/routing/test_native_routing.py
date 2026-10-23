from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.run import run_suite
from mota.engine_v2.contracts import JobBudget
from mota.jobs.contracts import WorkerUnavailable, kernel_binary, run_native_job

MM = 1_000_000
CASE = (Path(__file__).resolve().parents[2] / "benchmarks" /
        "routing_single_ended" / "two_layer_two_nets.json")


@pytest.fixture(scope="module")
def kernel_path() -> Path:
    try:
        return kernel_binary()
    except WorkerUnavailable as error:
        pytest.skip(f"native kernel not built: {error}")


def _run(document: dict, kernel_path: Path) -> dict:
    native = run_native_job(document, binary=kernel_path,
                            budget=JobBudget(wall_seconds=30.0,
                                             max_expansions=1000))
    assert native.status == "SUCCEEDED", native.diagnostics
    assert native.results and native.results[0]["status"] == "OK"
    return native.results[0]["routing"]


def test_route_job_through_worker(kernel_path: Path):
    document = json.loads(CASE.read_text(encoding="utf-8"))
    routing = _run(document, kernel_path)
    assert routing["status"] == "SUCCEEDED"
    assert routing["u_total"] == 0
    assert routing["requested_nets"] == ["A", "B"]
    assert routing["completed_nets"] == ["A", "B"]
    assert routing["unrouted_nets"] == []
    assert routing["tracks"]
    assert routing["via_count"] >= 1
    assert routing["grid_layers"] == 2
    assert len(routing["semantic_hash"]) == 64


def test_blocked_net_stays_in_denominator(kernel_path: Path):
    document = json.loads(CASE.read_text(encoding="utf-8"))
    document["routing"]["nets"] = [
        {"net": "N", "terminals": ["P1", "P2"], "locked": False}]
    document["board"]["keepouts"] = [
        {"id": "WALL", "layer": "F.Cu",
         "outline": [{"x": 20 * MM, "y": 0}, {"x": 21 * MM, "y": 0},
                     {"x": 21 * MM, "y": 30 * MM}, {"x": 20 * MM, "y": 30 * MM}]},
        {"id": "WALL_B", "layer": "B.Cu",
         "outline": [{"x": 20 * MM, "y": 0}, {"x": 21 * MM, "y": 0},
                     {"x": 21 * MM, "y": 30 * MM}, {"x": 20 * MM, "y": 30 * MM}]},
    ]
    document["board"]["pads"] = [
        {"id": "P1", "net": "N", "component_id": "U1", "pin": "1",
         "plated": True,
         "copper_by_layer": {
             "F.Cu": {"kind": "circle", "center": {"x": 5 * MM, "y": 15 * MM},
                      "radius": 800_000},
             "B.Cu": {"kind": "circle", "center": {"x": 5 * MM, "y": 15 * MM},
                      "radius": 800_000}}},
        {"id": "P2", "net": "N", "component_id": "U1", "pin": "2",
         "plated": True,
         "copper_by_layer": {
             "F.Cu": {"kind": "circle", "center": {"x": 35 * MM, "y": 15 * MM},
                      "radius": 800_000},
             "B.Cu": {"kind": "circle", "center": {"x": 35 * MM, "y": 15 * MM},
                      "radius": 800_000}}},
    ]
    routing = _run(document, kernel_path)
    assert routing["status"] == "INFEASIBLE"
    assert routing["requested_nets"] == ["N"]
    assert routing["unrouted_nets"] == ["N"]
    assert routing["completed_nets"] == []
    assert routing["u_total"] >= 1


def test_frozen_suite_keeps_denominator(tmp_path: Path):
    report = run_suite("single-ended-v2", tmp_path)
    assert report["frozen_hashes_ok"]
    aggregate = report["aggregate"]
    assert aggregate["cases"] == 6
    assert aggregate["requested_targets"] == 8
    assert aggregate["completed_targets"] == 8
    assert aggregate["unrouted_targets"] == 0
    assert aggregate["u_total"] == 0
    for case in report["cases"]:
        assert len(case["requested_nets"]) == (
            len(case["completed_nets"]) + len(case["unrouted_nets"]))
    assert (tmp_path / "single-ended-v2.json").is_file()
    assert (tmp_path / "single-ended-v2.md").is_file()
