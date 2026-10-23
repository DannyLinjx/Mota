"""Session, project, run, selection, config and task controllers."""

from __future__ import annotations

import json
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from mota.desktop.bridge import EngineBridge


class SessionController(QObject):
    session_changed = Signal(object)     # profile dict or None
    session_cleared = Signal()
    auth_error = Signal(str)
    busy = Signal(bool)

    REMEMBER_SERVICE = "mota-ui-remember"

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.profile: dict[str, Any] | None = None
        self.epoch = 0
        self.session_id: str | None = None
        self.user_id: str | None = None

    @property
    def logged_in(self) -> bool:
        return self.profile is not None

    def _remember_get(self) -> str | None:
        try:
            import keyring

            return keyring.get_password(self.REMEMBER_SERVICE, "remember")
        except Exception:
            return None

    def _remember_set(self, token: str | None) -> None:
        try:
            import keyring

            if token:
                keyring.set_password(self.REMEMBER_SERVICE, "remember", token)
            else:
                keyring.delete_password(self.REMEMBER_SERVICE, "remember")
        except Exception:
            pass

    def _apply_session(self, payload: dict[str, Any], remember_token: str | None = None,
                       remember: bool = False) -> None:
        self.profile = payload["profile"]
        session = payload.get("session") or {}
        self.session_id = session.get("session_id")
        self.user_id = session.get("user_id")
        self.epoch += 1
        self.bridge.epoch = self.epoch
        if remember and remember_token:
            self._remember_set(remember_token)
        self.session_changed.emit(self.profile)

    def login(self, username: str, password: str, remember: bool) -> None:
        self.busy.emit(True)

        def done(result, error):
            self.busy.emit(False)
            if error:
                self.auth_error.emit(error.get("message", "Sign in failed."))
                return
            self._apply_session(result, result.get("credential_ref"), remember)

        self.bridge.call("auth.login", {"username": username, "password": password,
                                        "remember_me": remember}, done, 15000)

    def register(self, username: str, password: str, display_name: str) -> None:
        self.busy.emit(True)

        def done(result, error):
            self.busy.emit(False)
            if error:
                self.auth_error.emit(error.get("message", "Could not create the account."))
                return
            self._apply_session(result)

        self.bridge.call("auth.register", {"username": username, "password": password,
                                           "display_name": display_name or None},
                         done, 15000)

    def try_resume(self, callback: Callable[[bool], None] | None = None) -> None:
        token = self._remember_get()
        if not token:
            if callback:
                callback(False)
            return

        def done(result, error):
            if error:
                self._remember_set(None)
                if callback:
                    callback(False)
                return
            self._apply_session(result)
            if callback:
                callback(True)

        self.bridge.call("auth.resume", {"credential_ref": token}, done, 15000)

    def logout(self) -> None:
        def done(result, error):
            self.profile = None
            self.epoch += 1
            self.bridge.epoch = self.epoch
            self._remember_set(None)
            self.profile = None
            self.session_cleared.emit()
            self.session_changed.emit(None)

        self.bridge.call("auth.logout", {}, done, 10000)


class SelectionController(QObject):
    selection_changed = Signal(object)       # {"kind": "component"|"net"|None, "id": ...}

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.kind: str | None = None
        self.id: str | None = None

    def select(self, kind: str | None, object_id: str | None) -> None:
        if (kind, object_id) == (self.kind, self.id):
            return
        self.kind = kind
        self.id = object_id
        self.selection_changed.emit({"kind": kind, "id": object_id})

    def clear(self) -> None:
        self.select(None, None)


class ProjectController(QObject):
    projects_loaded = Signal(list)
    project_opened = Signal(dict)
    project_changed = Signal(dict)
    revision_opened = Signal(dict)
    checks_updated = Signal(dict)
    scene_ready = Signal(str, dict)
    error = Signal(dict)
    busy = Signal(bool)

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.card: dict[str, Any] | None = None
        self.revision: dict[str, Any] | None = None
        self.checks: dict[str, Any] | None = None
        self.requirement: dict[str, Any] | None = None
        self.current_revision_id: str | None = None
        self.viewing_revision_id: str | None = None
        self.read_only = False
        self._scene_callbacks: dict[str, list[Callable[[dict[str, Any]], None]]] = {}

    @property
    def project_id(self) -> str | None:
        return self.card.get("project_id") if self.card else None

    def list(self, archived: bool = False) -> None:
        def done(result, error):
            if error:
                self.error.emit(error)
                return
            self.projects_loaded.emit(result.get("projects", []))

        self.bridge.call("project.list", {"archived": archived}, done)

    def create(self, name: str, template_id: str | None = None,
               callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        def done(result, error):
            if error:
                self.error.emit(error)
                return
            self.project_changed.emit(result)
            if callback:
                callback(result)

        self.bridge.call("project.create",
                         {"name": name, "template_id": template_id}, done, 30000)

    def open(self, project_id: str) -> None:
        self.busy.emit(True)

        def done(result, error):
            self.busy.emit(False)
            if error:
                self.error.emit(error)
                return
            self.card = result["project"]
            self.revision = result["revision"]
            self.checks = result["checks"]
            self.requirement = result["requirement"]
            self.current_revision_id = result["current_revision_id"]
            self.viewing_revision_id = self.current_revision_id
            self.read_only = False
            self.project_opened.emit(result)

        self.bridge.call("project.open", {"project_id": project_id}, done, 30000)

    def open_revision(self, revision_id: str) -> None:
        if not self.project_id:
            return

        def done(result, error):
            if error:
                self.error.emit(error)
                return
            self.revision = result["revision"]
            self.checks = result["checks"]
            self.requirement = result["requirement"]
            self.viewing_revision_id = revision_id
            self.read_only = bool(result.get("read_only"))
            self.revision_opened.emit(result)

        self.bridge.call("revision.open", {"project_id": self.project_id,
                                           "revision_id": revision_id}, done, 30000)

    def scene(self, view: str, callback: Callable[[dict[str, Any]], None],
              revision_id: str | None = None) -> None:
        if not self.project_id:
            return
        target = revision_id or self.viewing_revision_id or self.current_revision_id
        if not target:
            return

        def done(result, error):
            if error:
                callback({"__error__": error})
                return
            callback(result)

        self.bridge.call("scene.get", {"project_id": self.project_id,
                                       "design_version": {"revision_id": target},
                                       "view": view}, done, 45000)

    def scene_for_draft(self, view: str, draft_id: str, draft_seq: int,
                        callback: Callable[[dict[str, Any]], None]) -> None:
        if not self.project_id:
            return

        def done(result, error):
            callback({"__error__": error} if error else result)

        self.bridge.call("scene.get", {"project_id": self.project_id,
                                       "design_version": {"draft_id": draft_id,
                                                          "draft_seq": draft_seq},
                                       "view": view}, done, 45000)

    def validation(self, callback: Callable[[dict[str, Any]], None],
                   revision_id: str | None = None) -> None:
        if not self.project_id:
            return
        target = revision_id or self.viewing_revision_id or self.current_revision_id

        def done(result, error):
            if error:
                self.error.emit(error)
                return
            self.checks = result.get("report")
            self.checks_updated.emit(result)

        self.bridge.call("validation.list", {
            "project_id": self.project_id,
            "design_version": {"revision_id": target}}, done)

    def patch(self, operations: list[dict[str, Any]],
              expected_revision_id: str | None,
              callback: Callable[[dict[str, Any] | None, dict[str, Any] | None], None],
              *, operation_id: str | None = None) -> None:
        if not self.project_id:
            return
        self.bridge.call("design.patch", {
            "project_id": self.project_id,
            "expected_revision_id": expected_revision_id or self.current_revision_id,
            "operations": operations,
            "idempotency_key": operation_id or f"ui_edit_{id(operations)}",
        }, callback, 90000)

    def undo(self, callback) -> None:
        self._history("design.undo", callback)

    def redo(self, callback) -> None:
        self._history("design.redo", callback)

    def _history(self, method: str, callback) -> None:
        if not self.project_id:
            return

        def done(result, error):
            if not error and result:
                self.current_revision_id = result.get("revision_id")
                self.viewing_revision_id = self.current_revision_id
                self.read_only = False
            callback(result, error)

        self.bridge.call(method, {"project_id": self.project_id}, done, 90000)

    def export(self, fmt: str, destination: str,
               callback: Callable[[dict[str, Any] | None, dict[str, Any] | None], None]) -> None:
        if not self.project_id or not self.viewing_revision_id:
            return
        self.bridge.call("project.export", {
            "project_id": self.project_id,
            "revision_id": self.viewing_revision_id,
            "format": fmt, "destination_path": destination}, callback, 120000)

    def import_bundle(self, file_path: str, callback) -> None:
        def done(result, error):
            if not error:
                self.project_changed.emit(result)
            callback(result, error)

        self.bridge.call("project.import_bundle", {
            "file_path": file_path,
            "idempotency_key": f"ui_import_{file_path}"}, done, 120000)

    def rename(self, project_id: str, name: str, callback) -> None:
        self.bridge.call("project.rename", {"project_id": project_id, "name": name},
                         callback)

    def archive(self, project_id: str, callback) -> None:
        self.bridge.call("project.archive", {"project_id": project_id}, callback)

    def restore_archive(self, project_id: str, callback) -> None:
        self.bridge.call("project.restore_from_archive", {"project_id": project_id},
                         callback)

    def delete(self, project_id: str, callback) -> None:
        self.bridge.call("project.delete", {"project_id": project_id,
                                            "confirm_project_id": project_id}, callback)

    def duplicate(self, project_id: str, callback) -> None:
        self.bridge.call("project.duplicate", {
            "source_project_id": project_id,
            "idempotency_key": f"ui_dup_{project_id}"}, callback, 90000)

    def revisions(self, callback) -> None:
        if not self.project_id:
            return
        self.bridge.call("revision.list", {"project_id": self.project_id},
                         lambda result, error: callback(result, error))

    def restore_revision(self, revision_id: str, callback) -> None:
        if not self.project_id:
            return

        def done(result, error):
            if not error and result:
                self.current_revision_id = result.get("revision_id")
                self.viewing_revision_id = self.current_revision_id
                self.read_only = False
            callback(result, error)

        self.bridge.call("revision.restore", {
            "project_id": self.project_id,
            "source_revision_id": revision_id,
            "expected_revision_id": self.current_revision_id}, done, 90000)


class RunController(QObject):
    run_event = Signal(dict)
    state_changed = Signal(dict)
    run_started = Signal(dict)
    run_finished = Signal(dict)
    question = Signal(dict)
    committed = Signal(dict)
    error = Signal(dict)

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.run_id: str | None = None
        self.project_id: str | None = None
        self.last_seq = 0
        self.draft_id: str | None = None
        self.draft_seq = 0
        self.state = "CREATED"
        self.follow_build = True
        self._seen: set[int] = set()

    def reset(self) -> None:
        self.run_id = None
        self.project_id = None
        self.last_seq = 0
        self.draft_id = None
        self.draft_seq = 0
        self.state = "CREATED"
        self._seen.clear()

    def start(self, project_id: str, expected_revision_id: str | None,
              payload: dict[str, Any], callback: Callable[[dict[str, Any] | None,
                                                          dict[str, Any] | None], None]
              ) -> None:
        def done(result, error):
            if error:
                self.error.emit(error)
                callback(None, error)
                return
            self.run_id = result["run_id"]
            self.project_id = project_id
            self.draft_id = result.get("draft_id")
            self.draft_seq = result.get("draft_seq", 0)
            self.last_seq = 0
            self._seen.clear()
            self.run_started.emit(result)
            callback(result, None)

        self.bridge.call("run.start", {
            "project_id": project_id,
            "expected_revision_id": expected_revision_id,
            "input": payload,
            "idempotency_key": f"ui_build_{project_id}_{payload.get('template_id') or 'prompt'}_{id(payload)}",
        }, done, 60000)

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("session_epoch") not in (0, self.bridge.epoch):
            return
        run_id = event.get("run_id")
        if self.run_id is None:
            self.run_id = run_id
        if run_id != self.run_id:
            return
        seq = int(event.get("seq", 0))
        if seq in self._seen:
            return
        self._seen.add(seq)
        self.last_seq = max(self.last_seq, seq)
        if event.get("draft_id"):
            self.draft_id = event["draft_id"]
        if event.get("draft_seq") is not None:
            self.draft_seq = event["draft_seq"]
        event_type = event.get("type")
        if event_type == "run.state_changed":
            self.state = event["payload"].get("state", self.state)
            self.state_changed.emit(event["payload"])
        elif event_type == "user_input.required":
            payload = dict(event["payload"])
            payload["run_id"] = self.run_id
            self.question.emit(payload)
        elif event_type == "project.revision_committed":
            payload = dict(event["payload"])
            payload.setdefault("project_id", self.project_id)
            self.committed.emit(payload)
        elif event_type == "run.completed":
            self.state = event["payload"].get("state", self.state)
            payload = dict(event["payload"])
            payload.setdefault("project_id", self.project_id)
            self.run_finished.emit(payload)
        self.run_event.emit(event)

    def answer(self, question_id: str, answer: dict[str, Any], callback) -> None:
        if not self.run_id:
            return

        def done(result, error):
            callback(result, error)

        self.bridge.call("run.answer", {"run_id": self.run_id,
                                        "question_id": question_id,
                                        "answer": answer}, done, 20000)

    def cancel(self, callback) -> None:
        if not self.run_id:
            return
        self.bridge.call("run.cancel", {"run_id": self.run_id}, callback, 15000)

    def resume(self, expected_revision_id: str | None, callback) -> None:
        if not self.run_id:
            return

        def done(result, error):
            if not error:
                self.run_id = result["run_id"]
                self.last_seq = 0
                self._seen.clear()
            callback(result, error)

        self.bridge.call("run.resume", {
            "run_id": self.run_id,
            "expected_revision_id": expected_revision_id}, done, 30000)

    def inspect(self) -> None:
        if not self.run_id:
            return

        def done(result, error):
            if error or not result:
                return
            for event in result.get("events", []):
                self.handle_event(event)
            run = result.get("run") or {}
            if run.get("state") in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
                                    "RECOVERABLE"):
                self.state = run["state"]

        self.bridge.call("run.inspect", {"run_id": self.run_id,
                                         "after_seq": self.last_seq}, done, 30000)


class ConfigController(QObject):
    config_loaded = Signal(dict)
    config_saved = Signal(dict)
    probe_started = Signal(str, dict)
    error = Signal(dict)

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.config: dict[str, Any] | None = None

    def load(self) -> None:
        def done(result, error):
            if error:
                self.error.emit(error)
                return
            self.config = result["config"]
            self.config["secret_storage_capabilities"] = result.get(
                "secret_storage_capabilities", {})
            self.config_loaded.emit(self.config)

        self.bridge.call("config.get", {}, done)

    def update(self, changes: dict[str, Any], callback) -> None:
        if not self.config:
            self.load()
            return

        def done(result, error):
            if error:
                self.error.emit(error)
                callback(None, error)
                return
            self.config = result["config"]
            self.config_saved.emit(self.config)
            callback(result, None)

        self.bridge.call("config.update", {
            "expected_config_version": self.config.get("config_version", 1),
            "changes": changes,
            "idempotency_key": f"ui_cfg_{id(changes)}"}, done, 30000)

    def probe(self, callback, profile_id: str | None = None,
              suites: list[str] | None = None) -> None:
        if not self.config:
            return

        def done(result, error):
            if error:
                self.error.emit(error)
                callback(None, error)
                return
            self.probe_started.emit(result["task_id"], result)
            callback(result, None)

        params: dict[str, Any] = {
            "expected_config_version": self.config.get("config_version", 1)}
        if profile_id:
            params["profile_id"] = profile_id
        if suites:
            params["test_suite"] = list(suites)
        self.bridge.call("config.probe_model", params, done, 30000)


class TaskController(QObject):
    event = Signal(dict)
    finished = Signal(dict)
    error = Signal(dict)

    def __init__(self, bridge: EngineBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.task_id: str | None = None
        self.last_seq = 0

    def inspect(self, task_id: str) -> None:
        self.task_id = task_id

        def poll():
            if not self.task_id:
                return

            def done(result, error):
                if error or not result:
                    return
                for event in result.get("events", []):
                    self.last_seq = max(self.last_seq, event.get("seq", 0))
                    self.event.emit(event)
                task = result.get("task") or {}
                if task.get("state") in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    self.finished.emit(task)
                    self.task_id = None
                    return
                QTimer.singleShot(500, poll)

            self.bridge.call("task.inspect", {"task_id": self.task_id,
                                              "after_seq": self.last_seq}, done, 20000)

        poll()

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("session_epoch") not in (0, self.bridge.epoch):
            return
        if self.task_id and event.get("task_id") != self.task_id:
            return
        self.event.emit(event)
        if event.get("type") in ("task.completed", "task.failed"):
            self.finished.emit(event.get("payload", {}))
