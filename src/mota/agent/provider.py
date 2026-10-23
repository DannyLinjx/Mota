"""Provider adapters for Responses and Chat Completions.

Provider (vendor preset) and API format are independent. The adapter owns protocol
differences, streaming assembly, thinking/reasoning mapping and error normalization.
Raw reasoning is kept only in adapter-private state and never returned in public results.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from mota.domain.models import ProviderBinding
from mota.errors import ModelAuthFailed, ModelResponseInvalid, ModelUnavailable

ReasoningState = dict[str, str]


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    returned_model: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    latency_ms: int = 0
    usage_missing: bool = False


class ProviderAdapter:
    def __init__(self, binding: ProviderBinding, api_key: str) -> None:
        self.binding = binding
        self.api_key = api_key
        self._reasoning_state: ReasoningState = {}
        self._client = None

    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key, base_url=self.binding.base_url,
                timeout=self.binding.request_timeout_seconds, max_retries=0)
        return self._client

    def start_conversation(self, instructions: str, user_text: str,
                           image_data_url: str | None = None) -> "Conversation":
        raise NotImplementedError

    # -------------------------------------------------------------- tool schema
    def request_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError

    # ----------------------------------------------------------------- thinking
    def thinking_kwargs(self, *, chat: bool) -> dict[str, Any]:
        mode = self.binding.thinking_mode
        effort = self.binding.thinking_effort
        if self.binding.provider == "deepseek":
            # DeepSeek reads the thinking control from the provider extension object
            # in both Chat Completions and Responses. Effort only exists when enabled.
            thinking: dict[str, Any] = {
                "type": "enabled" if mode == "enabled"
                else "auto" if mode == "auto" else "disabled"}
            if mode == "enabled" and effort:
                thinking["effort"] = effort
            return {"extra_body": {"thinking": thinking}}
        if self.binding.provider == "openai":
            if mode == "enabled" and effort:
                return ({"reasoning_effort": effort} if chat
                        else {"reasoning": {"effort": effort}})
            return {}
        return {}


class Conversation:
    def complete(self, tools: list[dict[str, Any]],
                 on_text: Callable[[str], None] | None = None,
                 *, max_output_tokens: int | None = None) -> Completion:
        raise NotImplementedError

    def add_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Responses API
# --------------------------------------------------------------------------- #

class ResponsesConversation(Conversation):
    def __init__(self, adapter: ProviderAdapter, instructions: str, user_text: str,
                 image_data_url: str | None) -> None:
        self.adapter = adapter
        content: list[dict[str, Any]] = [{"type": "input_text", "text": user_text}]
        if image_data_url:
            content.append({"type": "input_image", "image_url": image_data_url})
        self.instructions = instructions
        self.input_items: list[dict[str, Any]] = [{"role": "user", "content": content}]

    def complete(self, tools, on_text=None, *, max_output_tokens=None) -> Completion:
        started = time.monotonic()
        binding = self.adapter.binding
        kwargs: dict[str, Any] = {
            "model": binding.model,
            "instructions": self.instructions,
            "input": self.input_items,
            "store": False,
            "max_output_tokens": max_output_tokens or binding.max_output_tokens,
        }
        request_tools = self.adapter.request_tools(tools)
        if request_tools:
            kwargs["tools"] = request_tools
            kwargs["parallel_tool_calls"] = False
        kwargs.update(self.adapter.thinking_kwargs(chat=False))
        text_parts: list[str] = []
        final = None
        try:
            if binding.stream:
                stream = self.adapter.client().responses.create(
                    **kwargs, stream=True)
                for event in stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            text_parts.append(delta)
                            if on_text:
                                on_text(delta)
                    elif event_type == "response.completed":
                        final = getattr(event, "response", None)
                    elif event_type in ("response.failed", "response.incomplete"):
                        response = getattr(event, "response", None)
                        if response is not None:
                            final = response
                        else:
                            raise ModelResponseInvalid(
                                f"Model response ended with {event_type}.",
                                code="MODEL_RESPONSE_INVALID")
                    elif event_type == "error":
                        raise ModelResponseInvalid(
                            str(getattr(event, "message", "stream error")),
                            code="MODEL_RESPONSE_INVALID")
            else:
                final = self.adapter.client().responses.create(**kwargs)
        except (ModelAuthFailed, ModelUnavailable, ModelResponseInvalid):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc
        if final is None:
            raise ModelResponseInvalid("The model returned no completed response.",
                                       code="MODEL_RESPONSE_INVALID")
        data = final.model_dump() if hasattr(final, "model_dump") else dict(final)
        output_items = data.get("output", []) or []
        calls: list[dict[str, str]] = []
        for item in output_items:
            if item.get("type") == "function_call":
                call_id = item.get("call_id") or item.get("id") or ""
                calls.append({"call_id": call_id, "name": item.get("name", ""),
                              "arguments": item.get("arguments") or "{}"})
            elif item.get("type") == "message":
                for part in item.get("content", []) or []:
                    if part.get("type") in ("output_text", "text"):
                        if not text_parts:
                            text_parts.append(part.get("text", ""))
        self.input_items.extend(_clean_responses_items(output_items))
        usage = _normalize_usage(data.get("usage"))
        status = data.get("status") or ""
        incomplete = data.get("incomplete_details") or {}
        finish = ("length" if incomplete.get("reason") == "max_output_tokens"
                  else status or None)
        if status == "failed":
            error = data.get("error") or {}
            raise ModelResponseInvalid(
                error.get("message") or "The model response failed.",
                code="MODEL_RESPONSE_INVALID")
        return Completion(
            text="".join(text_parts) or data.get("output_text", "") or "",
            tool_calls=calls, usage=usage,
            returned_model=data.get("model") or binding.model,
            request_id=data.get("id"), finish_reason=finish,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage_missing=not bool(usage))

    def add_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> None:
        for call_id, payload in results:
            self.input_items.append({
                "type": "function_call_output", "call_id": call_id,
                "output": json.dumps(payload, ensure_ascii=False)})


class ResponsesAdapter(ProviderAdapter):
    def start_conversation(self, instructions: str, user_text: str,
                           image_data_url: str | None = None) -> Conversation:
        return ResponsesConversation(self, instructions, user_text, image_data_url)

    def request_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for schema in tools:
            row = dict(schema)
            if not self.binding.strict_tool_schema:
                row.pop("strict", None)
            out.append(row)
        return out


# --------------------------------------------------------------------------- #
# Chat Completions
# --------------------------------------------------------------------------- #

class ChatConversation(Conversation):
    def __init__(self, adapter: ProviderAdapter, instructions: str, user_text: str,
                 image_data_url: str | None) -> None:
        self.adapter = adapter
        content: str | list[dict[str, Any]]
        if image_data_url:
            content = [{"type": "text", "text": user_text},
                       {"type": "image_url", "image_url": {"url": image_data_url}}]
        else:
            content = user_text
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ]

    def complete(self, tools, on_text=None, *, max_output_tokens=None) -> Completion:
        started = time.monotonic()
        binding = self.adapter.binding
        kwargs: dict[str, Any] = {
            "model": binding.model,
            "messages": self.messages,
            "max_tokens": max_output_tokens or binding.max_output_tokens,
        }
        request_tools = self.adapter.request_tools(tools)
        if request_tools:
            kwargs["tools"] = request_tools
            kwargs["tool_choice"] = "auto"
        kwargs.update(self.adapter.thinking_kwargs(chat=True))
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict[str, Any] = {}
        returned_model = binding.model
        request_id = None
        finish_reason = None
        calls_by_index: dict[int, dict[str, str]] = {}
        try:
            response = self.adapter.client().chat.completions.create(
                **kwargs, stream=binding.stream,
                **({"stream_options": {"include_usage": True}} if binding.stream else {}))
            if binding.stream:
                for chunk in response:
                    request_id = request_id or getattr(chunk, "id", None)
                    returned_model = getattr(chunk, "model", None) or returned_model
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage = _normalize_usage(chunk_usage)
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        text_parts.append(content)
                        if on_text:
                            on_text(content)
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                    for call in getattr(delta, "tool_calls", None) or []:
                        index = int(getattr(call, "index", 0) or 0)
                        slot = calls_by_index.setdefault(index, {
                            "call_id": "", "name": "", "arguments": ""})
                        if getattr(call, "id", None):
                            slot["call_id"] = call.id
                        function = getattr(call, "function", None)
                        if function is not None:
                            if getattr(function, "name", None):
                                slot["name"] += function.name
                            if getattr(function, "arguments", None):
                                slot["arguments"] += function.arguments
                    if finish_reason == "length" and not calls_by_index:
                        break
            else:
                data = response.model_dump() if hasattr(response, "model_dump") else {}
                request_id = data.get("id")
                returned_model = data.get("model") or returned_model
                usage = _normalize_usage(data.get("usage"))
                choices = data.get("choices", []) or []
                if choices:
                    message = choices[0].get("message", {})
                    text_parts.append(message.get("content") or "")
                    finish_reason = choices[0].get("finish_reason")
                    for index, call in enumerate(message.get("tool_calls") or []):
                        function = call.get("function") or {}
                        calls_by_index[index] = {
                            "call_id": call.get("id") or f"call_{index}",
                            "name": function.get("name", ""),
                            "arguments": function.get("arguments") or "{}"}
        except (ModelAuthFailed, ModelUnavailable, ModelResponseInvalid):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc
        if reasoning_parts:
            # Adapter-private only. Never exposed in UI, events, memory or exports.
            self.adapter._reasoning_state["last"] = "".join(reasoning_parts)
        calls = [calls_by_index[i] for i in sorted(calls_by_index) if calls_by_index[i]["name"]]
        for index, call in enumerate(calls):
            if not call["call_id"]:
                call["call_id"] = f"call_{index}"
        text = "".join(text_parts)
        if calls:
            self.messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [{
                    "id": call["call_id"],
                    "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": call["arguments"] or "{}"}}
                    for call in calls]})
        elif text:
            self.messages.append({"role": "assistant", "content": text})
        return Completion(
            text=text, tool_calls=calls, usage=usage,
            returned_model=returned_model, request_id=request_id,
            finish_reason=finish_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage_missing=not bool(usage))

    def add_tool_results(self, results: list[tuple[str, dict[str, Any]]]) -> None:
        for call_id, payload in results:
            self.messages.append({
                "role": "tool", "tool_call_id": call_id,
                "content": json.dumps(payload, ensure_ascii=False)})


class ChatCompletionsAdapter(ProviderAdapter):
    def start_conversation(self, instructions: str, user_text: str,
                           image_data_url: str | None = None) -> Conversation:
        return ChatConversation(self, instructions, user_text, image_data_url)

    def request_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for schema in tools:
            function = {
                "name": schema["name"], "description": schema.get("description", ""),
                "parameters": schema.get("parameters", {}),
            }
            if self.binding.strict_tool_schema:
                function["strict"] = True
            out.append({"type": "function", "function": function})
        return out


def build_adapter(binding: ProviderBinding, api_key: str) -> ProviderAdapter:
    if binding.api_format == "chat_completions":
        return ChatCompletionsAdapter(binding, api_key)
    return ResponsesAdapter(binding, api_key)


# --------------------------------------------------------------------------- #
# Compatibility wrapper used by older tests/tools (Responses only)
# --------------------------------------------------------------------------- #

@dataclass
class ModelConfig:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0
    max_rounds: int = 6
    max_tool_calls: int = 24
    provider: str = "openai"
    api_format: str = "responses"
    strict_tool_schema: bool = True


class OpenAIProvider(ResponsesAdapter):
    """Legacy thin wrapper retained for callers that only need Responses."""

    def __init__(self, config: ModelConfig) -> None:
        from mota.domain.models import ProviderBinding, utcnow

        binding = ProviderBinding(
            binding_id="binding_legacy", profile_id="legacy", profile_name="Legacy",
            provider=config.provider, api_format="responses",
            base_url=config.base_url, model=config.model,
            request_timeout_seconds=config.timeout_seconds,
            strict_tool_schema=config.strict_tool_schema)
        super().__init__(binding, config.api_key)
        self.config = config

    def probe(self) -> dict[str, Any]:
        conversation = self.start_conversation(
            "You are a connection probe.", "Reply with the single word OK.")
        completion = conversation.complete([], max_output_tokens=16)
        return {"ok": "OK" in completion.text.upper(),
                "model": completion.returned_model,
                "response_text": completion.text[:200],
                "latency_ms": completion.latency_ms,
                "usage": completion.usage,
                "billing_note": "This probe performs one paid API request."}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _normalize_usage(usage: Any) -> dict[str, Any]:
    if not usage:
        return {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        try:
            usage = vars(usage)
        except TypeError:
            return {}
    return {
        "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
        "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get(
            "reasoning_tokens") if isinstance(usage.get("output_tokens_details"), dict)
        else usage.get("reasoning_tokens"),
    }


def _clean_responses_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        item_type = item.get("type")
        if item_type == "function_call":
            cleaned.append({"type": "function_call", "call_id": item.get("call_id"),
                            "name": item.get("name"),
                            "arguments": item.get("arguments", "{}")})
        elif item_type == "message":
            cleaned.append({"role": "assistant", "content": item.get("content", [])})
        # reasoning items are intentionally not carried into later turns
    return cleaned


def _map_error(exc: Exception) -> Exception:
    name = exc.__class__.__name__
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 403):
        return ModelAuthFailed(
            "The model service rejected the API key. Update the key in AI connections.",
            code="MODEL_AUTH_FAILED")
    if status == 404 or ("model" in message.lower() and "not found" in message.lower()):
        return ModelUnavailable(
            f"The configured model is not available to this account: {message}",
            code="MODEL_UNAVAILABLE")
    if status == 429:
        return ModelUnavailable("The model service is rate limiting requests. Retry later.",
                                code="MODEL_UNAVAILABLE")
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout"):
        return ModelUnavailable(
            "Could not reach the model service. Check the network and try again.",
            code="MODEL_UNAVAILABLE")
    return ModelResponseInvalid(f"Model request failed: {message}",
                                code="MODEL_RESPONSE_INVALID")


def tool_result(ok: bool, data: dict[str, Any] | None = None,
                diagnostics: list[dict[str, Any]] | None = None,
                affected: list[str] | None = None,
                artifacts: list[str] | None = None,
                retryable: bool = False) -> dict[str, Any]:
    return {"ok": ok, "data": data or {}, "diagnostics": diagnostics or [],
            "affected_object_ids": affected or [], "artifact_ids": artifacts or [],
            "retryable": retryable}


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
