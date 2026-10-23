from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import mota.agent.provider as provider_module
from mota.agent.provider import Completion
from mota.agent.tools import TOOL_SCHEMAS
from mota.errors import MotaError
from tests.conftest import make_user, new_project, wait_run


class FakeConversation:
    def __init__(self, adapter: "FakeProvider") -> None:
        self.adapter = adapter

    def complete(self, tools, on_text=None, *, max_output_tokens=None) -> Completion:
        return self.adapter.complete(tools=tools, on_text=on_text)

    def add_tool_results(self, results) -> None:
        self.adapter.results.extend(results)


class FakeProvider:
    """Deterministic adapter for unit/integration tests only (never used by the product)."""

    instances: list["FakeProvider"] = []

    def __init__(self, binding, api_key: str = "") -> None:
        self.binding = binding
        self.api_key = api_key
        self.calls = 0
        self.results: list = []
        self.draft_id: str | None = None
        FakeProvider.instances.append(self)

    def start_conversation(self, instructions, user_text, image_data_url=None):
        return FakeConversation(self)

    def _wait_for_draft(self) -> str:
        deadline = time.time() + 10
        while self.draft_id is None and time.time() < deadline:
            time.sleep(0.02)
        assert self.draft_id, "draft id was not supplied by the test"
        return self.draft_id

    @staticmethod
    def _call(name: str, arguments: dict, call_id: str) -> dict:
        return {"call_id": call_id, "name": name, "arguments": json.dumps(arguments)}

    def complete(self, *, tools, on_text=None) -> Completion:
        assert len(tools) == 13
        self.calls += 1
        if on_text:
            on_text("Working on it.")
        if self.calls == 1:
            return Completion(
                text="I need the supply voltage before building.",
                tool_calls=[self._call("ask_user", {
                    "question_id": "supply_question",
                    "prompt": "What will power this board?",
                    "choices": [{"label": "Regulated 5 V",
                                 "description": "Matches this template."},
                                {"label": "Another supply", "description": "Enter details."}],
                    "fields": ["supply_voltage_v"]}, "call_1")],
                usage={"input_tokens": 10, "output_tokens": 5},
                returned_model="fake-model", request_id="req_1",
                finish_reason="tool_calls")
        if self.calls == 2:
            draft = self._wait_for_draft()
            return Completion(
                text="Building the LED timer.",
                tool_calls=[
                    self._call("list_templates", {"scenario": "led timer"}, "call_2"),
                    self._call("prepare_design", {
                        "draft_id": draft, "expected_draft_seq": 0,
                        "template_id": "led_timer",
                        "parameters": {"input_voltage_v": None, "max_load_ma": None,
                                       "supply_voltage_v": 5.0, "target_frequency_hz": 1.0,
                                       "bus_frequency_khz": None, "board_width_mm": 50,
                                       "board_height_mm": 35}}, "call_3"),
                    self._call("validate_design", {"draft_id": draft, "draft_seq": 1},
                               "call_4"),
                    self._call("build_board", {"draft_id": draft, "expected_draft_seq": 1},
                               "call_5"),
                    self._call("validate_design", {"draft_id": draft, "draft_seq": 2},
                               "call_6"),
                    self._call("render_preview", {"draft_id": draft, "draft_seq": 2,
                                                  "view": "pcb2d"}, "call_7"),
                    self._call("summarize_build", {}, "call_8"),
                ],
                usage={}, returned_model="fake-model", request_id="req_2",
                finish_reason="tool_calls")
        return Completion(
            text="Done. The revision is ready for review.",
            usage={"input_tokens": 20, "output_tokens": 8},
            returned_model="fake-model", request_id="req_3",
            finish_reason="stop")


@pytest.fixture()
def patched_provider(monkeypatch):
    FakeProvider.instances = []
    monkeypatch.setattr(provider_module, "build_adapter",
                        lambda binding, api_key: FakeProvider(binding, api_key))
    return FakeProvider


def _configure_key(service, session):
    service.secrets.set_profile_key(session.scope(), "openai-main", "sk-test-session",
                                    session_only=True)


def test_prompt_build_with_clarification_and_tool_timeline(service, patched_provider):
    session = make_user(service, "agentuser")
    _configure_key(service, session)
    project, first = new_project(service, session, "Agent LED")
    started = service.dispatch("run.start", {
        "project_id": project, "expected_revision_id": first,
        "input": {"kind": "prompt", "text": "Make a blinking LED board.", "source_ids": []},
        "idempotency_key": "agent_build"}, session)
    assert started["run_id"].startswith("run_")
    deadline = time.time() + 5
    while not FakeProvider.instances and time.time() < deadline:
        time.sleep(0.02)
    FakeProvider.instances[0].draft_id = started["draft_id"]

    data = None
    deadline = time.time() + 15
    while time.time() < deadline:
        data = service.dispatch("run.inspect", {"run_id": started["run_id"]}, session)
        if data["run"]["state"] == "WAITING_FOR_USER":
            break
        time.sleep(0.05)
    assert data and data["run"]["state"] == "WAITING_FOR_USER"
    pending = [q for q in data["questions"] if not q.get("consumed_at")]
    assert pending and pending[0]["question_id"] == "supply_question"

    answer = {"value": "Regulated 5 V", "choice": "Regulated 5 V"}
    accepted = service.dispatch("run.answer", {
        "run_id": started["run_id"], "question_id": "supply_question",
        "answer": answer}, session)
    assert accepted["accepted"] is True
    replay = service.dispatch("run.answer", {
        "run_id": started["run_id"], "question_id": "supply_question",
        "answer": answer}, session)
    assert replay.get("idempotent_replay") is True
    with pytest.raises(MotaError) as exc:
        service.dispatch("run.answer", {
            "run_id": started["run_id"], "question_id": "supply_question",
            "answer": {"value": "12 V"}}, session)
    assert exc.value.code == "QUESTION_CONSUMED"

    final = wait_run(service, session, started["run_id"], timeout=40)
    assert final["run"]["state"] in ("SUCCEEDED", "PARTIAL")
    assert final["run"]["committed_revision_id"]
    types = [event["type"] for event in final["events"]]
    assert "tool.started" in types and "tool.completed" in types
    assert "design.patch_applied" in types
    assert "validation.completed" in types
    assert "project.revision_committed" in types
    assert "run.completed" in types
    assert "model.turn" in types
    turn = next(e for e in final["events"] if e["type"] == "model.turn")["payload"]
    assert turn["returned_model"] == "fake-model"
    assert turn["request_id"] == "req_1"
    assert turn["provider"] == "openai"
    assert turn["api_format"] == "responses"
    binding = final["run"]["provider_binding"]
    assert binding["profile_id"] == "openai-main"
    assert binding["model"] == "gpt-5.4"
    assert binding["api_format"] == "responses"
    assert len(final["run"]["model_turns"]) == 3
    assert final["run"]["model_turns"][0]["binding_id"] == binding["binding_id"]
    tool_events = [e for e in final["events"] if e["type"] == "tool.completed"]
    tools = {e["payload"]["tool"] for e in tool_events}
    assert {"prepare_design", "build_board", "validate_design", "render_preview"} <= tools
    questions = service.dispatch("run.inspect", {"run_id": started["run_id"]},
                                 session)["questions"]
    assert questions[0]["consumed_at"]
    assert questions[0]["answer"]["value"] == "Regulated 5 V"


def test_prompt_without_key_fails_before_creating_a_run(service):
    session = make_user(service, "nokey")
    project, first = new_project(service, session, "No key")
    with pytest.raises(MotaError) as exc:
        service.dispatch("run.start", {
            "project_id": project, "expected_revision_id": first,
            "input": {"kind": "prompt", "text": "Build something."},
            "idempotency_key": "no_key"}, session)
    assert exc.value.code == "MODEL_UNAVAILABLE"
    run_index = service.root / "users" / session.user_id / "workspace" / "projects" / project / "runs"
    assert not list(run_index.glob("run_*/run.json"))


def test_tool_schemas_are_strict_and_complete():
    assert len(TOOL_SCHEMAS) == 13
    names = {schema["name"] for schema in TOOL_SCHEMAS}
    assert names == {"read_requirement", "search_catalog", "inspect_part",
                     "list_templates", "read_rules", "ask_user", "begin_design",
                     "prepare_design", "apply_design_patch", "validate_design",
                     "build_board", "render_preview", "summarize_build"}
    for schema in TOOL_SCHEMAS:
        assert schema["strict"] is True
        parameters = schema["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
