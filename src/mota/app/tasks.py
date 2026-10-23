"""Background tasks (catalog sync, model probe) with a separate event stream."""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from mota.app.state import UserScope
from mota.domain.models import TaskEvent, TaskRecord, utcnow
from mota.errors import NotFound
from mota.workspace import paths
from mota.workspace.atomic import append_jsonl, read_json, read_jsonl, write_json_atomic


class TaskService:
    def __init__(self, root: Path | None = None,
                 emit: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.root = root
        self.emit = emit or (lambda event: None)
        self._threads: dict[str, threading.Thread] = {}

    def _dir(self, owner: UserScope, task_id: str) -> Path:
        return paths.ensure_dir(paths.tasks_dir(owner.user_id, self.root) / task_id)

    def _save(self, owner: UserScope, task: TaskRecord) -> None:
        task.updated_at = utcnow()
        write_json_atomic(self._dir(owner, task.task_id) / "task.json", task.model_dump())

    def _event(self, owner: UserScope, task: TaskRecord, event_type: str,
               payload: dict[str, Any], session_epoch: int) -> None:
        task.last_seq += 1
        event = TaskEvent(task_id=task.task_id, task_kind=task.task_kind,
                          seq=task.last_seq, type=event_type, payload=payload,
                          session_epoch=session_epoch)
        append_jsonl(self._dir(owner, task.task_id) / "events.jsonl", event.model_dump())
        self._save(owner, task)
        self.emit(event.model_dump())

    def start(self, owner: UserScope, task_kind: str, runner: Callable[[Callable], Any],
              session_epoch: int = 0) -> dict[str, Any]:
        task = TaskRecord(task_id="task_" + secrets.token_hex(6),
                          owner_user_id=owner.user_id, task_kind=task_kind)  # type: ignore[arg-type]
        self._save(owner, task)
        self._event(owner, task, "task.started", {"status": "RUNNING",
                                                  "task_kind": task_kind}, session_epoch)

        def progress(payload: dict[str, Any]) -> None:
            self._event(owner, task, "task.progress",
                        {"status": "RUNNING", **payload}, session_epoch)

        def work() -> None:
            try:
                result = runner(progress)
                task.state = "SUCCEEDED"
                task.result = result if isinstance(result, dict) else {"result": result}
                self._save(owner, task)
                self._event(owner, task, "task.completed",
                            {"status": "SUCCEEDED", "result": task.result}, session_epoch)
            except Exception as exc:  # noqa: BLE001 - task boundary
                task.state = "FAILED"
                task.error = {"code": getattr(exc, "code", "TASK_FAILED"),
                              "message": str(exc)}
                self._save(owner, task)
                self._event(owner, task, "task.failed",
                            {"status": "FAILED", "error": task.error,
                             "retryable": True}, session_epoch)

        thread = threading.Thread(target=work, name=f"mota-{task.task_id}", daemon=True)
        self._threads[task.task_id] = thread
        thread.start()
        return {"task_id": task.task_id, "task_kind": task_kind,
                "state": "QUEUED"}

    def inspect(self, owner: UserScope, task_id: str, after_seq: int | None = None,
                session_epoch: int = 0) -> dict[str, Any]:
        data = read_json(self._dir(owner, task_id) / "task.json")
        if not data:
            raise NotFound(f"Task {task_id} not found.", code="TASK_NOT_FOUND")
        task = TaskRecord.model_validate(data)
        events = read_jsonl(self._dir(owner, task_id) / "events.jsonl")
        if after_seq is not None:
            events = [e for e in events if e.get("seq", 0) > after_seq]
        for event in events:
            event["session_epoch"] = session_epoch
        return {"task": task.model_dump(), "events": events, "last_seq": task.last_seq}
