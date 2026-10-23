"""protocol v1 JSON-RPC framing and envelopes (single definition)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from mota import PROTOCOL_VERSION

MAX_FRAME_BYTES = 4 * 1024 * 1024

METHODS: list[str] = [
    "engine.hello", "engine.health", "engine.shutdown",
    "task.inspect",
    "auth.register", "auth.login", "auth.resume", "auth.logout", "auth.status",
    "config.get", "config.update", "config.probe_model",
    "catalog.sync", "catalog.status", "catalog.search", "catalog.inspect",
    "template.list", "template.inspect",
    "source.import", "source.list",
    "project.create", "project.open", "project.list", "project.rename",
    "project.archive", "project.restore_from_archive", "project.duplicate",
    "project.delete", "project.import_bundle", "project.export",
    "run.start", "run.answer", "run.cancel", "run.resume", "run.inspect",
    "design.patch", "design.undo", "design.redo",
    "scene.get", "artifact.read", "validation.list",
    "revision.list", "revision.open", "revision.restore",
    "memory.list", "memory.read", "memory.update", "memory.forget",
    "debug.conflict_after_commit",
]


class RpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class RpcErrorBody(BaseModel):
    code: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class RpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: dict[str, Any] | None = None
    error: RpcErrorBody | None = None


def encode_frame(message: dict[str, Any]) -> bytes:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError(f"protocol frame exceeds {MAX_FRAME_BYTES} bytes")
    return data + b"\n"


def make_result(request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(request_id: str | int | None, code: str, message: str,
               data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message, "data": data or {}},
    }


def make_event(event: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "event", "params": event}


def make_task_event(event: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "task.event", "params": event}


def hello_payload() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": 1,
        "methods": METHODS,
        "event_types": [
            "run.state_changed", "assistant.text_delta", "assistant.summary",
            "tool.started", "tool.completed", "tool.failed", "user_input.required",
            "design.patch_applied", "scene.updated", "validation.completed",
            "artifact.created", "project.revision_committed", "run.completed",
        ],
        "limits": {"max_frame_bytes": MAX_FRAME_BYTES, "artifact_chunk_bytes": 1024 * 1024},
    }


def validate_request(raw: dict[str, Any]) -> RpcRequest:
    try:
        return RpcRequest.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
