from __future__ import annotations

import json
import time

import pytest

import mota.agent.provider as provider_module
from mota.agent.provider import Completion
from mota.projects.store import ProjectStore
from tests.conftest import make_user, new_project, wait_run


class BlankBoardProvider:
    """One tool round that edits a blank project, then finishes."""

    instances: list["BlankBoardProvider"] = []

    def __init__(self, binding, api_key: str = "") -> None:
        self.binding = binding
        self.api_key = api_key
        self.calls = 0
        self.draft_id: str | None = None
        BlankBoardProvider.instances.append(self)

    def start_conversation(self, instructions, user_text, image_data_url=None):
        return self

    def add_tool_results(self, results) -> None:
        return None

    def _draft(self) -> str:
        deadline = time.time() + 10
        while self.draft_id is None and time.time() < deadline:
            time.sleep(0.02)
        assert self.draft_id, "run.start did not create a draft"
        return self.draft_id

    @staticmethod
    def _call(name: str, arguments: dict, call_id: str) -> dict:
        return {"call_id": call_id, "name": name, "arguments": json.dumps(arguments)}

    def complete(self, tools, on_text=None) -> Completion:
        self.calls += 1
        if self.calls == 1:
            return Completion(
                text="Adding one resistor to the blank board.",
                tool_calls=[self._call("apply_design_patch", {
                    "draft_id": self._draft(), "expected_draft_seq": 0,
                    "operations": [{"op": "add_component", "part_id": "r_10k",
                                    "x_mm": 20.0, "y_mm": 15.0}]}, "call_1")],
                usage={}, returned_model="fake-model", request_id="req_1",
                finish_reason="tool_calls")
        return Completion(text="Added one component.", usage={},
                          returned_model="fake-model", request_id="req_2",
                          finish_reason="stop")


@pytest.fixture()
def blank_provider(monkeypatch):
    BlankBoardProvider.instances = []
    monkeypatch.setattr(provider_module, "build_adapter",
                        lambda binding, api_key: BlankBoardProvider(binding, api_key))
    return BlankBoardProvider


def test_agent_edit_commits_real_component_on_blank_project(service, blank_provider):
    session = make_user(service, "blank-agent")
    service.secrets.set_profile_key(session.scope(), "openai-main", "sk-test-session",
                                    session_only=True)
    project, first = new_project(service, session, "Blank agent board")
    before = ProjectStore(session.scope(), service.root).open_current(project)
    assert before.circuit.components == []
    assert before.board.outline == []

    started = service.dispatch("run.start", {
        "project_id": project, "expected_revision_id": first,
        "input": {"kind": "prompt", "text": "Put one resistor on the blank board.",
                  "source_ids": []},
        "idempotency_key": "blank_agent_edit"}, session)
    deadline = time.time() + 5
    while not blank_provider.instances and time.time() < deadline:
        time.sleep(0.02)
    blank_provider.instances[0].draft_id = started["draft_id"]

    final = wait_run(service, session, started["run_id"], timeout=60)
    assert final["run"]["state"] in ("SUCCEEDED", "PARTIAL"), (
        final["run"].get("error"),
        [(e["type"], e["payload"].get("error") or e["payload"].get("diagnostics"))
         for e in final["events"]][-8:])
    committed = final["run"]["committed_revision_id"]
    assert committed and committed != first

    snapshot = ProjectStore(session.scope(), service.root).open_current(project)
    assert snapshot.revision.revision_id == committed
    assert [c.reference for c in snapshot.circuit.components] == ["R1"]
    assert len(snapshot.board.footprints) == 1
    applied = [e for e in final["events"] if e["type"] == "design.patch_applied"]
    assert applied and applied[-1]["payload"]["added"]
