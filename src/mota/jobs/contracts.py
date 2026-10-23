"""Native job contracts and the C6 worker bridge.

`engine_v2.contracts` already owns the versioned JobRequest/JobResult DTOs.
This module re-exports them and adds the frozen geometry payload, the
subprocess transport and a JobResult mapping. The native kernel is the only
component that evaluates geometry; Python does not reimplement shape maths.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from mota.domain.models import DTO
from mota.engine_v2.contracts import (
    DependencyRead, JobBudget, JobRequest, JobResult,
)
from mota.errors import MotaError

KERNEL_NAME = "mota-kernel"
KERNEL_VERSION = "0.1.0-c6"
MAX_FRAME_BYTES = 4 * 1024 * 1024

GeometryStatus = Literal[
    "SUCCEEDED", "PARTIAL", "INFEASIBLE", "UNKNOWN", "CANCELLED", "FAILED",
]


class WorkerUnavailable(MotaError):
    code = "WORKER_UNAVAILABLE"


class WorkerProtocolError(MotaError):
    code = "WORKER_PROTOCOL_ERROR"


class WorkerInputTooLarge(MotaError):
    code = "WORKER_INPUT_TOO_LARGE"


@dataclass(frozen=True)
class FrozenInput:
    path: Path
    sha256: str
    size: int

    def cleanup(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class GeometryJobInput(DTO):
    schema_version: Literal[2] = 2
    input_id: str
    board: dict[str, Any]
    queries: list[dict[str, Any]]
    rules: list[dict[str, Any]] = Field(default_factory=list)


class NativeJobResult(DTO):
    schema_version: int = 2
    input_id: str = ""
    status: GeometryStatus
    completed: int = 0
    total: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    input_sha256: str = ""
    kernel: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    checkpoint: dict[str, Any] = Field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.status == "SUCCEEDED" and self.completed == self.total


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def kernel_binary(explicit: str | Path | None = None) -> Path:
    """Resolve the native binary. Never downloads or builds automatically."""

    def check(candidate: Path) -> Path | None:
        return candidate.resolve() if candidate.is_file() and os.access(
            candidate, os.X_OK) else None

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        found = check(candidate)
        if found is not None:
            return found
        raise WorkerUnavailable(
            f"Native kernel binary {candidate} is missing or not executable.",
            data={"candidates": [str(candidate)]})

    configured = os.environ.get("MOTA_KERNEL_BINARY")
    if configured:
        candidate = Path(configured).expanduser()
        found = check(candidate)
        if found is not None:
            return found
        raise WorkerUnavailable(
            f"MOTA_KERNEL_BINARY points at {candidate}, which is missing or "
            f"not executable.", data={"candidates": [str(candidate)]})

    root = repository_root()
    candidates = [
        root / "native" / "build" / KERNEL_NAME,
        root / "build" / KERNEL_NAME,
    ]
    for candidate in candidates:
        found = check(candidate)
        if found is not None:
            return found
    raise WorkerUnavailable(
        "Native kernel binary was not found. Build it with "
        "`cmake -S native -B native/build && cmake --build native/build` "
        "or set MOTA_KERNEL_BINARY.",
        data={"candidates": [str(item) for item in candidates]})


def canonical_json(document: GeometryJobInput | dict[str, Any]) -> str:
    payload = (document.model_dump(mode="json")
               if isinstance(document, GeometryJobInput) else document)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def freeze_input(document: GeometryJobInput | dict[str, Any]) -> FrozenInput:
    """Write the canonical payload to a temp artifact and hash the exact bytes."""

    text = canonical_json(document)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise WorkerInputTooLarge(
            f"frozen geometry input is {len(encoded)} bytes; the frame limit "
            f"is {MAX_FRAME_BYTES}.")
    handle, path = tempfile.mkstemp(prefix="mota_geometry_", suffix=".json")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(encoded)
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    return FrozenInput(path=Path(path), sha256=hashlib.sha256(encoded).hexdigest(),
                       size=len(encoded))


def kernel_info(binary: str | Path | None = None) -> dict[str, Any]:
    executable = kernel_binary(binary)
    request = {"jsonrpc": "2.0", "id": "kernel-info", "method": "kernel.info"}
    response = _invoke(executable, request, timeout=10.0)
    if "error" in response:
        raise WorkerProtocolError(str(response["error"]))
    result = response.get("result")
    if not isinstance(result, dict):
        raise WorkerProtocolError("kernel.info returned no object")
    return result


def run_frozen_job(frozen: FrozenInput, *,
                   budget: JobBudget | None = None,
                   cancel_file: str | Path | None = None,
                   binary: str | Path | None = None,
                   timeout: float | None = None) -> NativeJobResult:
    executable = kernel_binary(binary)
    effective = budget or JobBudget()
    wall_seconds = max(0.0, float(effective.wall_seconds))
    max_operations = (effective.max_expansions
                      if effective.max_expansions is not None else 100_000)
    request = {
        "jsonrpc": "2.0",
        "id": f"geometry-{frozen.sha256[:12]}",
        "method": "job.run",
        "params": {
            "input_path": str(frozen.path),
            "input_sha256": frozen.sha256,
            "budget": {
                "wall_seconds": wall_seconds,
                "max_operations": int(max_operations),
                "max_memory_mb": float(effective.max_memory_mb)
                if effective.max_memory_mb is not None else 0.0,
                "cancel_file": str(cancel_file) if cancel_file else "",
            },
        },
    }
    response = _invoke(executable, request,
                       timeout=timeout if timeout is not None else wall_seconds + 10.0)
    if "error" in response:
        error = response["error"]
        data = error.get("data") if isinstance(error, dict) else {}
        raise WorkerProtocolError(
            str(error.get("message", error)) if isinstance(error, dict) else str(error),
            data=data if isinstance(data, dict) else {})
    result = response.get("result")
    if not isinstance(result, dict):
        raise WorkerProtocolError("job.run returned no result object")
    return NativeJobResult.model_validate(result)


def run_native_job(document: GeometryJobInput | dict[str, Any], *,
                   budget: JobBudget | None = None,
                   cancel_file: str | Path | None = None,
                   binary: str | Path | None = None,
                   timeout: float | None = None) -> NativeJobResult:
    frozen = freeze_input(document)
    try:
        return run_frozen_job(frozen, budget=budget, cancel_file=cancel_file,
                              binary=binary, timeout=timeout)
    finally:
        frozen.cleanup()


def to_job_result(native: NativeJobResult, request: JobRequest) -> JobResult:
    """Map a native geometry result onto the frozen-scheduler contract."""

    completed = [str(item.get("id")) for item in native.results
                 if item.get("status") == "OK" and item.get("id")]
    failed = [str(item.get("id")) for item in native.results
              if item.get("status") == "FAILED" and item.get("id")]
    requested = list(request.requested_targets)
    if not requested:
        requested = list(dict.fromkeys(completed + failed))
    unimplemented = [target for target in requested
                     if target not in completed and target not in failed]
    diagnostics = list(native.diagnostics)
    for item in native.results:
        if item.get("status") == "FAILED":
            error = item.get("error", {})
            diagnostics.append({
                "code": error.get("code", "QUERY_FAILED"),
                "message": error.get("message", ""),
                "target": item.get("id", ""),
            })
    error_code = None
    error_message = None
    if native.status == "FAILED" and diagnostics:
        error_code = str(diagnostics[0].get("code", "FAILED"))
        error_message = str(diagnostics[0].get("message", ""))
    read_set = []
    if native.input_sha256:
        read_set.append(DependencyRead(
            artifact_id=native.input_id or native.input_sha256,
            sha256=native.input_sha256, purpose="frozen geometry input"))
    return JobResult(
        job_id=request.job_id, base_version=request.base_version,
        status=native.status, read_set=read_set,
        requested_targets=requested, completed_targets=completed,
        unimplemented_targets=unimplemented,
        unimplemented_checks=[item.get("code", "") for item in diagnostics
                              if item.get("code")],
        diagnostics=diagnostics,
        error_code=error_code, error_message=error_message)


def _invoke(executable: Path, request: dict[str, Any], *,
            timeout: float) -> dict[str, Any]:
    line = json.dumps(request, ensure_ascii=False) + "\n"
    if len(line.encode("utf-8")) > MAX_FRAME_BYTES:
        raise WorkerInputTooLarge("JSON-RPC frame exceeds 4 MiB")
    try:
        completed = subprocess.run(
            [str(executable)], input=line, text=True, capture_output=True,
            timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise WorkerProtocolError(
            "native kernel exceeded the caller timeout; this is an execution "
            "timeout, not a proof of infeasibility.",
            data={"code": "WORKER_TIMEOUT", "timeout_s": timeout}) from exc
    except OSError as exc:
        raise WorkerUnavailable(f"cannot execute native kernel: {exc}",
                                data={"binary": str(executable)}) from exc
    if completed.returncode != 0:
        raise WorkerProtocolError(
            f"native kernel exited with {completed.returncode}",
            data={"stderr": completed.stderr[-4096:],
                  "returncode": completed.returncode})
    stdout = completed.stdout.strip()
    if not stdout:
        raise WorkerProtocolError("native kernel produced no response",
                                  data={"stderr": completed.stderr[-4096:]})
    if len(stdout.encode("utf-8")) > MAX_FRAME_BYTES:
        raise WorkerProtocolError("native response exceeds 4 MiB")
    first, _, rest = stdout.partition("\n")
    if rest.strip():
        raise WorkerProtocolError("native kernel returned more than one frame")
    try:
        response = json.loads(first)
    except json.JSONDecodeError as exc:
        raise WorkerProtocolError(f"native response is not JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise WorkerProtocolError("native response is not an object")
    return response
