"""QProcess bridge speaking protocol v1 with newline JSON frames."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from mota.protocol.schemas import MAX_FRAME_BYTES

Callback = Callable[[dict[str, Any] | None, dict[str, Any] | None], None]


class EngineBridge(QObject):
    notification = Signal(dict)
    task_event = Signal(dict)
    engine_state = Signal(str, str)          # state: starting|ready|down, detail
    diagnostics = Signal(str)

    def __init__(self, root: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = root
        self.epoch = 0
        self._buffer = bytearray()
        self._stderr = ""
        self._next_id = 0
        self._pending: dict[str, tuple[Callback | None, QTimer]] = {}
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(
            lambda err: self.engine_state.emit("down", f"engine error {err}"))
        self.start()

    # ------------------------------------------------------------------ process
    def start(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            return
        env = QProcessEnvironment.systemEnvironment()
        if self.root:
            env.insert("MOTA_DATA_DIR", str(self.root))
        self._process.setProcessEnvironment(env)
        self._process.setProgram(sys.executable)
        if getattr(sys, "frozen", False):
            self._process.setArguments(["engine", "--stdio"])
        else:
            self._process.setArguments(["-m", "mota.cli.main", "engine", "--stdio"])
        self.engine_state.emit("starting", "Launching the Mota engine")
        self._process.start()
        QTimer.singleShot(800, self.hello)

    def restart(self) -> None:
        self._buffer.clear()
        for request_id, (_, timer) in list(self._pending.items()):
            timer.stop()
            self._pending.pop(request_id, None)
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1500)
        self.start()

    def shutdown(self) -> None:
        for request_id, (_, timer) in list(self._pending.items()):
            timer.stop()
            self._pending.pop(request_id, None)
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.write(
                (json.dumps({"jsonrpc": "2.0", "id": "shutdown",
                             "method": "engine.shutdown", "params": {}}) + "\n").encode())
            self._process.waitForBytesWritten(300)
            self._process.terminate()
            if not self._process.waitForFinished(1500):
                self._process.kill()

    def is_running(self) -> bool:
        return self._process.state() == QProcess.ProcessState.Running

    def _on_finished(self, code: int, status: QProcess.ProcessStatus) -> None:
        self.engine_state.emit("down", f"Engine exited (code {code})")
        for request_id, (callback, timer) in list(self._pending.items()):
            timer.stop()
            self._pending.pop(request_id, None)
            if callback:
                callback(None, {"code": "ENGINE_EXITED",
                                "message": "The engine process exited."})

    # ------------------------------------------------------------------- frames
    def _read_stdout(self) -> None:
        data = bytes(self._process.readAllStandardOutput())
        self._buffer.extend(data)
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                if len(self._buffer) > MAX_FRAME_BYTES:
                    self._buffer.clear()
                    self.diagnostics.emit("Protocol frame exceeded 4 MiB; buffer dropped.")
                return
            frame = bytes(self._buffer[:index])
            del self._buffer[:index + 1]
            if not frame.strip():
                continue
            try:
                message = json.loads(frame.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.diagnostics.emit(f"Invalid protocol frame: {exc}")
                continue
            self._handle_message(message)

    def _read_stderr(self) -> None:
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr = (self._stderr + text)[-8000:]
        for line in text.splitlines():
            if line.strip():
                self.diagnostics.emit(line)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            request_id = str(message.get("id"))
            entry = self._pending.pop(request_id, None)
            if entry:
                callback, timer = entry
                timer.stop()
                if callback:
                    callback(message.get("result"), message.get("error"))
            return
        method = message.get("method")
        params = message.get("params") or {}
        if method == "event":
            self.notification.emit(params)
        elif method == "task.event":
            self.task_event.emit(params)
        elif method == "hello":
            self.engine_state.emit("ready", "Engine ready")

    # --------------------------------------------------------------------- call
    def call(self, method: str, params: dict[str, Any] | None = None,
             callback: Callback | None = None, timeout_ms: int = 60000) -> str:
        self._next_id += 1
        request_id = f"ui_{self._next_id:05d}"
        payload = dict(params or {})
        payload.setdefault("session_epoch", self.epoch)
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": payload}
        try:
            self._process.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            self._process.waitForBytesWritten(200)
        except Exception as exc:  # noqa: BLE001
            if callback:
                QTimer.singleShot(0, lambda: callback(None, {
                    "code": "ENGINE_UNAVAILABLE", "message": str(exc)}))
            return request_id
        if callback:
            timer = QTimer(self)
            timer.setSingleShot(True)

            def on_timeout() -> None:
                if self._pending.pop(request_id, None):
                    callback(None, {"code": "TIMEOUT",
                                    "message": f"{method} timed out after {timeout_ms} ms."})

            timer.timeout.connect(on_timeout)
            timer.start(timeout_ms)
            self._pending[request_id] = (callback, timer)
        return request_id

    def hello(self, callback: Callback | None = None) -> None:
        if not self.is_running():
            return

        def on_hello(result, error):
            if result:
                self.engine_state.emit("ready", "Engine ready")
            if callback:
                callback(result, error)

        self.call("engine.hello", {}, on_hello, timeout_ms=10000)
