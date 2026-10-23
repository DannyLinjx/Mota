"""JSON-RPC 2.0 over stdio: newline-delimited UTF-8 frames, 4 MiB limit."""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mota.app.service import ApplicationService
from mota.app.state import SessionContext
from mota.errors import AuthRequired, MotaError
from mota.protocol.schemas import (
    MAX_FRAME_BYTES, encode_frame, make_error, make_event, make_result,
    make_task_event, validate_request,
)


class EngineServer:
    def __init__(self, root: Path | None = None, stdin=None, stdout=None) -> None:
        self.root = root
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.write_lock = threading.Lock()
        self.session: SessionContext | None = None
        self.epoch = 0
        self.service = ApplicationService(root, emit_event=self.send_event,
                                          emit_task=self.send_task_event)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mota-rpc")
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------- frames
    def send(self, payload: dict[str, Any]) -> None:
        try:
            frame = encode_frame(payload)
        except ValueError as exc:
            sys.stderr.write(f"[mota] {exc}\n")
            return
        with self.write_lock:
            try:
                self.stdout.write(frame)
                self.stdout.flush()
            except (BrokenPipeError, ValueError):
                self._shutdown.set()

    def send_event(self, event: dict[str, Any]) -> None:
        if self.session is not None:
            event["session_epoch"] = self.session.epoch
        self.send(make_event(event))

    def send_task_event(self, event: dict[str, Any]) -> None:
        if self.session is not None:
            event["session_epoch"] = self.session.epoch
        self.send(make_task_event(event))

    # ------------------------------------------------------------------- serve
    def serve(self) -> None:
        sys.stderr.write("[mota] engine ready on stdio\n")
        while not self._shutdown.is_set():
            line = self.stdin.readline(MAX_FRAME_BYTES + 2)
            if not line:
                break
            if len(line) > MAX_FRAME_BYTES:
                self.send(make_error(None, "FRAME_TOO_LARGE",
                                     f"Request exceeds {MAX_FRAME_BYTES} bytes."))
                continue
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.send(make_error(None, "PARSE_ERROR", f"Invalid JSON: {exc}"))
                continue
            self.pool.submit(self._handle, raw)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _handle(self, raw: dict[str, Any]) -> None:
        request_id = raw.get("id") if isinstance(raw, dict) else None
        try:
            request = validate_request(raw)
        except ValueError as exc:
            self.send(make_error(request_id, "INVALID_REQUEST", str(exc)))
            return
        method = request.method
        params = dict(request.params)
        try:
            result = self._dispatch(method, params)
            epoch = self.session.epoch if self.session else 0
            if isinstance(result, dict):
                result.setdefault("session_epoch", epoch)
            self.send(make_result(request.id, result))
        except MotaError as exc:
            error = exc.to_error()
            error["session_epoch"] = self.session.epoch if self.session else 0
            self.send(make_error(request.id, exc.code, exc.message, error))
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            self.send(make_error(request.id, "ENGINE_ERROR", str(exc),
                                 {"session_epoch": self.session.epoch
                                  if self.session else 0}))

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method in ("engine.hello", "engine.health"):
            return self.service.dispatch(method, params, None)
        if method in ("auth.register", "auth.login", "auth.resume"):
            result = self.service.dispatch(method, params, None)
            session = result.get("session") or {}
            self.epoch += 1
            self.session = SessionContext(
                session_id=session.get("session_id", "session_" + str(self.epoch)),
                user_id=session["user_id"], username=session["username"],
                epoch=self.epoch, secret_storage=session.get("secret_storage", "none"))
            result["session"] = self.service._session_payload(self.session)
            return result
        if method == "auth.status":
            if self.session is None:
                return {"authenticated": False}
            return self.service.dispatch(method, params, self.session)
        if method == "auth.logout":
            if self.session is None:
                return {"logged_out": True}
            result = self.service.dispatch(method, params, self.session)
            self.epoch += 1
            self.session = None
            return result
        if method == "engine.shutdown":
            self._shutdown.set()
            return {"shutting_down": True}
        if self.session is None:
            raise AuthRequired("Sign in to continue.")
        return self.service.dispatch(method, params, self.session)


def run_stdio_engine(root: Path | None = None) -> int:
    server = EngineServer(root)
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    return 0
