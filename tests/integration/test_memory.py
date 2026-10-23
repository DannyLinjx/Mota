from __future__ import annotations

import pytest

from mota.app.state import UserScope
from mota.errors import MotaError
from mota.memory.service import MemoryService
from tests.conftest import make_user, new_project


def test_preferences_are_user_scoped_and_visible_across_projects(data_root, service):
    session = make_user(service, "memuser")
    alice = session.scope()
    project_a, _ = new_project(service, session, "Memory A")
    project_b, _ = new_project(service, session, "Memory B")
    memory = MemoryService(alice, service.root)
    entry = memory.record_preference(
        key="preferred_assembly", value="through_hole",
        summary="Use through-hole parts where possible.",
        source_ids=["session_1/message_12"])
    assert entry.scope == "user"
    assert entry.project_id is None
    listed = memory.list("long_term", project_a)
    assert any(e.entry_id == entry.entry_id for e in listed)
    retrieved = memory.retrieve(project_b, "through hole")
    assert any(row["entry_id"] == entry.entry_id
               for row in retrieved["layers"]["long_term"])


def test_experience_scope_is_project_only_and_isolated_between_users(data_root, service):
    session_a = make_user(service, "alice")
    session_b = make_user(service, "bob")
    project_a, _ = new_project(service, session_a, "Alice A")
    project_a2, _ = new_project(service, session_a, "Alice A2")
    project_b, _ = new_project(service, session_b, "Bob B")
    memory_a = MemoryService(session_a.scope(), service.root)
    lesson = memory_a.record_experience(
        project_a, title="Keep TO-92 mapping", conditions="TO-92 MCP1700",
        observation="Wrong package map failed the pin mapping check.",
        resolution="Resolve the exact package first.",
        evidence="run_1/revision_2", source_ids=["run_1/rev_2"],
        module_id="power_3v3", module_version=1, status="candidate")
    assert lesson.scope == "project"
    assert lesson.project_id == project_a
    assert [e.entry_id for e in memory_a.list("experience", project_a)] == [lesson.entry_id]
    assert memory_a.list("experience", project_a2) == []
    assert memory_a.list("experience", project_b) == []

    memory_b = MemoryService(session_b.scope(), service.root)
    assert memory_b.list("experience", project_b) == []
    with pytest.raises(MotaError):
        memory_b.read(lesson.entry_id)


def test_update_hash_conflict_and_forget_tombstone_survive_restart(data_root, service):
    session = make_user(service, "forgetful")
    alice = session.scope()
    project, _ = new_project(service, session, "Forget board")
    memory = MemoryService(alice, service.root)
    entry = memory.record_preference(
        key="led_color", value="red", summary="Use red LEDs.",
        source_ids=["session/msg_1"])
    with pytest.raises(MotaError) as exc:
        memory.update(entry.entry_id, "Use green LEDs.", "wrong-hash",
                      scope="user", project_id=None)
    assert exc.value.code == "MEMORY_CONFLICT"

    updated = memory.update(entry.entry_id, "Use red LEDs only.", entry.hash,
                            scope="user", project_id=None)
    assert "red LEDs only" in updated.body

    result = memory.forget(updated.entry_id, semantic_key="pref:led_color")
    assert result["status"] == "active"
    assert memory.list("long_term", project) == []
    assert memory.tombstones()

    restarted = MemoryService(alice, service.root)
    assert restarted.list("long_term", project) == []
    assert restarted.retrieve(project, "led colour red")["layers"]["long_term"] == []
    replacement = restarted.record_preference(
        key="led_color", value="green", summary="Use green LEDs.",
        source_ids=["session/msg_99"])
    assert restarted.list("long_term", project) == []
    assert replacement.semantic_key == "pref:led_color"


def test_short_term_conversation_and_working_summary(data_root, service):
    session = make_user(service, "shortterm")
    project, _ = new_project(service, session, "Short board")
    memory = MemoryService(session.scope(), service.root)
    memory.record_conversation("session_x", project, [
        {"message_id": "m1", "role": "user", "run_id": "run_1",
         "text": "做一个 3.3V 电源板"},
        {"message_id": "m2", "role": "assistant", "run_id": "run_1",
         "text": "Selected MCP1700-3302E/TO."},
    ])
    memory.record_working("session_x", project, "Built the supply.",
                          {"current_revision": "rev_000002",
                           "review_status": "REVIEW_BLOCKED"})
    entries = memory.list("short_term", project, "session_x")
    assert any(e.entry_id == "WORKING" for e in entries)
    assert all(e.entry_id != "m1" for e in entries)
    conversation = (memory.base / "short_term" / "session_x" / "conversation.md")
    text = conversation.read_text(encoding="utf-8")
    assert "做一个 3.3V 电源板" in text
    assert "- role: user" in text
