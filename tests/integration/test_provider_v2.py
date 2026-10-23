"""Protocol adapters: Chat Completions streaming and side-effect-free probes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import mota.agent.probe as probe_module
from mota.agent.probe import SUITE_LIMITS, run_probe
from mota.agent.provider import Completion, ChatCompletionsAdapter, build_adapter
from mota.domain.models import ProviderBinding


def _binding(**overrides) -> ProviderBinding:
    data = dict(
        binding_id="binding_test", profile_id="deepseek-main", profile_name="DeepSeek",
        provider="deepseek", api_format="chat_completions",
        base_url="https://api.deepseek.com", model="deepseek-flash", stream=True,
        thinking_mode="disabled", thinking_effort=None, max_output_tokens=8192,
        request_timeout_seconds=120.0, strict_tool_schema=False, supports_vision=True)
    data.update(overrides)
    return ProviderBinding(**data)


def _chunk(*, delta=None, finish=None, usage=None, model="deepseek-flash",
           chunk_id="chatcmpl-1") -> SimpleNamespace:
    choices = []
    if delta is not None or finish is not None:
        choices = [SimpleNamespace(index=0, delta=delta or SimpleNamespace(),
                                   finish_reason=finish)]
    return SimpleNamespace(id=chunk_id, model=model, usage=usage, choices=choices)


class _FakeCompletions:
    def __init__(self, chunks, record) -> None:
        self.chunks = chunks
        self.record = record

    def create(self, **kwargs):
        self.record.update(kwargs)
        return iter(self.chunks)


class _FakeClient:
    def __init__(self, chunks, record) -> None:
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(chunks, record))


def test_chat_stream_aggregates_incremental_tool_arguments_and_final_usage():
    record: dict = {}
    adapter = ChatCompletionsAdapter(_binding(), "sk-test")
    adapter._client = _FakeClient([
        _chunk(delta=SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(
                index=0, id="call_a",
                function=SimpleNamespace(name="prepare_design",
                                         arguments='{"draft_id": "draft_1", '))])),
        _chunk(delta=SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(
                index=0, id=None,
                function=SimpleNamespace(name=None,
                                         arguments='"expected_draft_seq": 0}'))])),
        _chunk(delta=SimpleNamespace(content=None, tool_calls=[]),
               finish="tool_calls"),
        _chunk(delta=None, usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15)),
    ], record)
    conversation = adapter.start_conversation("system", "build it")
    completion = conversation.complete([{"name": "prepare_design", "description": "",
                                         "parameters": {}, "strict": True}])

    assert record["stream"] is True
    assert record["stream_options"] == {"include_usage": True}
    assert record["tools"][0]["type"] == "function"
    assert "strict" not in record["tools"][0]["function"]  # DeepSeek preset is not strict
    assert completion.tool_calls == [{
        "call_id": "call_a", "name": "prepare_design",
        "arguments": '{"draft_id": "draft_1", "expected_draft_seq": 0}'}]
    assert completion.usage == {"input_tokens": 10, "output_tokens": 5,
                                "total_tokens": 15, "reasoning_tokens": None}
    assert completion.usage_missing is False
    assert completion.finish_reason == "tool_calls"
    assert completion.request_id == "chatcmpl-1"
    assert completion.returned_model == "deepseek-flash"
    # Tool results are appended as role=tool messages tied to the assembled call id.
    conversation.add_tool_results([("call_a", {"ok": True, "data": {}})])
    assert conversation.messages[-1] == {
        "role": "tool", "tool_call_id": "call_a",
        "content": json.dumps({"ok": True, "data": {}}, ensure_ascii=False)}


def test_reasoning_content_stays_in_adapter_private_state():
    adapter = ChatCompletionsAdapter(_binding(), "sk-test")
    adapter._client = _FakeClient([
        _chunk(delta=SimpleNamespace(content="answer", tool_calls=[],
                                     reasoning_content="private chain")),
        _chunk(delta=SimpleNamespace(content=None, tool_calls=[]), finish="stop"),
    ], {})
    conversation = adapter.start_conversation("system", "question")
    completion = conversation.complete([])
    assert completion.text == "answer"
    assert "private chain" not in json.dumps(completion.__dict__)
    assert adapter._reasoning_state["last"] == "private chain"


class _ProbeAdapter:
    def __init__(self, binding, text="MOTA-OK", tool_token="MOTA-TOOL-7",
                 second_text="echo MOTA-TOOL-7", tool_twice=False) -> None:
        self.binding = binding
        self.text = text
        self.tool_token = tool_token
        self.second_text = second_text
        self.tool_twice = tool_twice
        self.calls = 0
        self.tool_rounds = 0

    def start_conversation(self, instructions, user_text, image_data_url=None):
        return _ProbeConversation(self)

    def request_tools(self, tools):
        return tools

    def complete(self, tools, on_text=None, *, max_output_tokens=None):
        self.calls += 1
        if not tools:
            return Completion(text=self.text, usage={"input_tokens": 1,
                                                     "output_tokens": 1},
                              returned_model=self.binding.model, request_id="r_text",
                              finish_reason="stop")
        self.tool_rounds += 1
        if self.tool_rounds == 1:
            return Completion(
                text="", usage={"input_tokens": 2, "output_tokens": 1},
                returned_model=self.binding.model, request_id="r_tool1",
                finish_reason="tool_calls",
                tool_calls=[{"call_id": "call_probe", "name": "probe_echo",
                             "arguments": json.dumps({"token": self.tool_token})}])
        if self.tool_twice:
            return Completion(
                text="", usage={}, returned_model=self.binding.model,
                request_id="r_tool2", finish_reason="tool_calls",
                tool_calls=[{"call_id": "call_again", "name": "probe_echo",
                             "arguments": json.dumps({"token": self.tool_token})}])
        return Completion(text=self.second_text, usage={"input_tokens": 1,
                                                        "output_tokens": 1},
                          returned_model=self.binding.model, request_id="r_tool2",
                          finish_reason="stop")


class _ProbeConversation:
    def __init__(self, adapter: _ProbeAdapter) -> None:
        self.adapter = adapter
        self.results = []

    def complete(self, tools, on_text=None, *, max_output_tokens=None):
        return self.adapter.complete(tools, on_text=on_text,
                                     max_output_tokens=max_output_tokens)

    def add_tool_results(self, results):
        self.results.extend(results)


def _run(monkeypatch, adapter, suites, binding=None):
    monkeypatch.setattr(probe_module, "build_adapter",
                        lambda b, key: adapter)
    return run_probe(binding or _binding(), "sk-test", suites)


def test_probe_caps_and_evidence_are_per_suite(monkeypatch):
    adapter = _ProbeAdapter(_binding())
    result = _run(monkeypatch, adapter, ["text", "tool_roundtrip"])
    suites = {row["suite"]: row for row in result["suites"]}
    assert suites["text"]["status"] == "PASS"
    assert suites["tool_roundtrip"]["status"] == "PASS"
    assert suites["text"]["requests"] == 1
    assert suites["tool_roundtrip"]["requests"] == 2
    assert len(suites["tool_roundtrip"]["evidence"]) == 2
    assert SUITE_LIMITS == {"text": 1, "tool_roundtrip": 2, "vision": 1}
    assert result["ok"] is True


def test_text_success_does_not_imply_a_tool_roundtrip(monkeypatch):
    adapter = _ProbeAdapter(_binding(), tool_twice=True)
    result = _run(monkeypatch, adapter, ["text", "tool_roundtrip"])
    suites = {row["suite"]: row for row in result["suites"]}
    assert suites["text"]["status"] == "PASS"
    assert suites["tool_roundtrip"]["status"] == "FAILED"
    assert "another tool call" in suites["tool_roundtrip"]["detail"]
    assert result["ok"] is False


def test_vision_is_unsupported_for_deepseek_pro_without_a_request(monkeypatch):
    binding = _binding(model="deepseek-pro")
    adapter = _ProbeAdapter(binding)
    result = _run(monkeypatch, adapter, ["vision"], binding=binding)
    assert result["suites"][0]["status"] == "UNSUPPORTED"
    assert result["suites"][0]["requests"] == 0
    assert adapter.calls == 0
    assert result["vision_supported"] is False


def test_chat_adapter_is_selected_from_api_format_not_provider():
    from mota.agent.provider import ResponsesAdapter

    assert isinstance(build_adapter(_binding(), "k"), ChatCompletionsAdapter)
    assert isinstance(build_adapter(
        _binding(api_format="responses", provider="deepseek"), "k"), ResponsesAdapter)


def test_thinking_effort_mapping_follows_the_mode():
    disabled = ChatCompletionsAdapter(_binding(), "k").thinking_kwargs(chat=True)
    assert disabled == {"extra_body": {"thinking": {"type": "disabled"}}}
    auto = ChatCompletionsAdapter(
        _binding(thinking_mode="auto"), "k").thinking_kwargs(chat=True)
    assert auto == {"extra_body": {"thinking": {"type": "auto"}}}
    enabled = ChatCompletionsAdapter(
        _binding(thinking_mode="enabled", thinking_effort="max"), "k"
    ).thinking_kwargs(chat=True)
    assert enabled == {"extra_body": {"thinking": {"type": "enabled", "effort": "max"}}}
    assert "reasoning_effort" not in enabled

    openai_chat = ChatCompletionsAdapter(
        _binding(provider="openai", api_format="responses",
                 thinking_mode="enabled", thinking_effort="high"), "k"
    ).thinking_kwargs(chat=True)
    assert openai_chat == {"reasoning_effort": "high"}
    openai_responses = ChatCompletionsAdapter(
        _binding(provider="openai", api_format="responses",
                 thinking_mode="enabled", thinking_effort="high"), "k"
    ).thinking_kwargs(chat=False)
    assert openai_responses == {"reasoning": {"effort": "high"}}
