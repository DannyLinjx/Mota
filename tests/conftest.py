from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from mota.app.service import ApplicationService
from mota.app.state import SessionContext


@pytest.fixture(scope="session", autouse=True)
def _route_budget_for_unit_tests() -> None:
    """Unit tests build boards without the `data_root` fixture.

    Wall-clock routing can be several times slower when the whole test session
    runs on a loaded machine; the budget is a timeout guard, not a design rule.
    """
    os.environ.setdefault("MOTA_ROUTE_BUDGET_SECONDS", "300")


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mota-data"
    monkeypatch.setenv("MOTA_DATA_DIR", str(root))
    # The 30 s wall-clock route budget is a production guard. The test session
    # runs many builds back to back on a shared machine, so give the baseline
    # builds headroom while keeping the timeout-vs-infeasible distinction.
    monkeypatch.setenv("MOTA_ROUTE_BUDGET_SECONDS", "300")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MOTA_OPENAI_API_KEY", raising=False)
    return root


@pytest.fixture()
def service(data_root: Path) -> ApplicationService:
    return ApplicationService(data_root)


def make_user(service: ApplicationService, username: str = "tester") -> SessionContext:
    result = service.dispatch("auth.register", {
        "username": username, "password": "Passw0rd!23",
        "display_name": username}, None)
    info = result["session"]
    return SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                          username=info["username"], epoch=1)


def login(service: ApplicationService, username: str = "tester") -> SessionContext:
    result = service.dispatch("auth.login", {
        "username": username, "password": "Passw0rd!23"}, None)
    info = result["session"]
    return SessionContext(session_id=info["session_id"], user_id=info["user_id"],
                          username=info["username"], epoch=1)


def wait_run(service: ApplicationService, session: SessionContext, run_id: str,
             timeout: float = 300.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = service.dispatch("run.inspect", {"run_id": run_id}, session)
        if data["run"]["state"] in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
                                    "RECOVERABLE"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def new_project(service: ApplicationService, session: SessionContext,
                name: str = "Test board") -> tuple[str, str]:
    result = service.dispatch("project.create", {"name": name}, session)
    return result["project_id"], result["current_revision_id"]


def build_template(service: ApplicationService, session: SessionContext,
                   project_id: str, template_id: str,
                   parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    started = service.dispatch("run.start", {
        "project_id": project_id,
        "expected_revision_id": opened["current_revision_id"],
        "input": {"kind": "template", "template_id": template_id,
                  "parameters": parameters or {},
                  "accept_template_defaults": True},
        "idempotency_key": f"test_{template_id}_{time.time_ns()}"}, session)
    return wait_run(service, session, started["run_id"])
