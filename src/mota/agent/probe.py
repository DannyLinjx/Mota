"""Side-effect-free AI connection probes.

Only synthetic text and a generated 8x8 image are sent. Project data, requirement
documents and memory never enter a probe. Each suite has a hard request cap.
"""

from __future__ import annotations

import base64
import json
import struct
import time
import zlib
from typing import Any, Callable

from mota.agent.provider import build_adapter
from mota.domain.models import ProviderBinding
from mota.errors import MotaError

SUITE_LIMITS = {"text": 1, "tool_roundtrip": 2, "vision": 1}

PROBE_TOOL = {
    "type": "function", "name": "probe_echo",
    "description": "Echo a token. This tool only runs in a connection probe and has no "
                   "side effects on any project.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"token": {"type": "string"}},
        "required": ["token"],
        "additionalProperties": False,
    },
}


def _png_data_url() -> str:
    width = height = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + \
        chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def vision_supported(binding: ProviderBinding) -> bool:
    if not binding.supports_vision:
        return False
    if binding.provider == "deepseek":
        model = binding.model.lower()
        if "pro" in model or "reasoner" in model:
            return False
    return True


class _ProbeSession:
    def __init__(self, binding: ProviderBinding, api_key: str,
                 progress: Callable[[dict[str, Any]], None] | None) -> None:
        self.binding = binding
        self.adapter = build_adapter(binding, api_key)
        self.progress = progress
        self.counts: dict[str, int] = {k: 0 for k in SUITE_LIMITS}
        self.evidence: list[dict[str, Any]] = []

    def complete(self, suite: str, conversation, tools, *, max_tokens: int):
        if self.counts[suite] >= SUITE_LIMITS[suite]:
            raise MotaError(f"Probe request cap reached for {suite}.",
                            code="PROBE_BUDGET_EXCEEDED")
        self.counts[suite] += 1
        if self.progress:
            self.progress({"stage": "probe_request", "suite": suite,
                           "request": self.counts[suite],
                           "model": self.binding.model})
        completion = conversation.complete(tools, max_output_tokens=max_tokens)
        self.evidence.append({
            "suite": suite,
            "request": self.counts[suite],
            "request_id": completion.request_id,
            "returned_model": completion.returned_model,
            "usage": completion.usage or None,
            "usage_missing": completion.usage_missing,
            "latency_ms": completion.latency_ms,
            "finish_reason": completion.finish_reason,
        })
        return completion

    def suite_result(self, suite: str, status: str, started: float,
                     detail: str) -> dict[str, Any]:
        return {
            "suite": suite, "status": status, "requests": self.counts[suite],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": detail,
            "evidence": [e for e in self.evidence if e["suite"] == suite],
        }


def run_probe(binding: ProviderBinding, api_key: str,
              suites: list[str], progress=None) -> dict[str, Any]:
    requested = []
    for suite in suites:
        if suite in SUITE_LIMITS and suite not in requested:
            requested.append(suite)
    if not requested:
        requested = ["text"]
    session = _ProbeSession(binding, api_key, progress)
    results: list[dict[str, Any]] = []
    for suite in requested:
        started = time.monotonic()
        if progress:
            progress({"stage": "probe_suite", "suite": suite})
        try:
            if suite == "text":
                results.append(_probe_text(session, started))
            elif suite == "tool_roundtrip":
                results.append(_probe_tools(session, started))
            elif suite == "vision":
                results.append(_probe_vision(session, started))
        except MotaError as exc:
            results.append(session.suite_result(suite, "FAILED", started,
                                               f"{exc.code}: {exc.message}"))
        except Exception as exc:  # noqa: BLE001 - probe boundary
            results.append(session.suite_result(suite, "FAILED", started, str(exc)))
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for entry in session.evidence:
        usage = entry.get("usage") or {}
        for key in total_usage:
            total_usage[key] += int(usage.get(key) or 0)
    return {
        "profile_id": binding.profile_id,
        "binding": binding.model_dump(),
        "suites": results,
        "usage_total": total_usage,
        "ok": all(r["status"] == "PASS" for r in results),
        "vision_supported": vision_supported(binding),
        "billing_note": "Probes send synthetic content only and may incur API charges.",
    }


def _probe_text(session: _ProbeSession, started: float) -> dict[str, Any]:
    conversation = session.adapter.start_conversation(
        "You are a connection probe. Follow the instruction exactly.",
        "Reply with exactly MOTA-OK and nothing else.")
    completion = session.complete("text", conversation, [], max_tokens=32)
    passed = "MOTA-OK" in (completion.text or "").upper()
    return session.suite_result(
        "text", "PASS" if passed else "FAILED", started,
        f"response={completion.text[:120]!r}; expected MOTA-OK")


def _probe_tools(session: _ProbeSession, started: float) -> dict[str, Any]:
    conversation = session.adapter.start_conversation(
        "You are a connection probe. Use the probe_echo tool exactly once and then "
        "summarize the echo. The tool has no side effects.",
        "Call probe_echo with token MOTA-TOOL-7.")
    first = session.complete("tool_roundtrip", conversation, [PROBE_TOOL],
                            max_tokens=256)
    calls = first.tool_calls
    if len(calls) != 1 or calls[0].get("name") != "probe_echo":
        return session.suite_result(
            "tool_roundtrip", "FAILED", started,
            f"expected one probe_echo call, received {[c.get('name') for c in calls]}")
    try:
        arguments = json.loads(calls[0].get("arguments") or "{}")
    except json.JSONDecodeError:
        return session.suite_result(
            "tool_roundtrip", "FAILED", started,
            "probe_echo arguments were not complete JSON")
    token = str(arguments.get("token") or "")
    if token != "MOTA-TOOL-7":
        return session.suite_result(
            "tool_roundtrip", "FAILED", started,
            f"unexpected token {token!r}")
    conversation.add_tool_results([(calls[0]["call_id"],
                                    {"ok": True, "data": {"echo": token}})])
    second = session.complete("tool_roundtrip", conversation, [PROBE_TOOL],
                             max_tokens=128)
    if second.tool_calls:
        return session.suite_result(
            "tool_roundtrip", "FAILED", started,
            "the model requested another tool call instead of closing the loop")
    lowered = (second.text or "").lower()
    if token.lower() not in lowered and "echo" not in lowered and "ok" not in lowered:
        return session.suite_result(
            "tool_roundtrip", "FAILED", started,
            f"tool result was not acknowledged: {second.text[:120]!r}")
    return session.suite_result(
        "tool_roundtrip", "PASS", started,
        "probe_echo called once, result returned and acknowledged")


def _probe_vision(session: _ProbeSession, started: float) -> dict[str, Any]:
    if not vision_supported(session.binding):
        return session.suite_result(
            "vision", "UNSUPPORTED", started,
            "this provider/model is marked as not supporting image input")
    conversation = session.adapter.start_conversation(
        "You are a vision connection probe. Answer in one word.",
        "The image is a solid color. Reply with the color name only: red, green, or blue.",
        image_data_url=_png_data_url())
    completion = session.complete("vision", conversation, [], max_tokens=32)
    text = (completion.text or "").lower()
    passed = any(color in text for color in ("red", "green", "blue"))
    return session.suite_result(
        "vision", "PASS" if passed else "FAILED", started,
        f"response={completion.text[:80]!r}; expected red")
