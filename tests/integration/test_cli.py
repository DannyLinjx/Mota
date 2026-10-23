from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mota.cli.main import app


def _json(result) -> dict:
    return json.loads(result.stdout)


def test_cli_register_create_and_question_exit(data_root, monkeypatch):
    runner = CliRunner()
    result = runner.invoke(app, ["auth", "register", "--username", "cliuser",
                                 "--password", "Passw0rd!23",
                                 "--confirm", "Passw0rd!23", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    created = runner.invoke(app, ["project", "create", "--name", "CLI board", "--json"])
    assert created.exit_code == 0
    project_id = _json(created)["project_id"]

    question = runner.invoke(app, ["build", "--project", project_id,
                                   "--template", "led_timer", "--json",
                                   "--non-interactive"])
    assert question.exit_code == 2
    payload = _json(question)
    assert payload["state"] == "WAITING_FOR_USER"
    assert payload["questions"][0]["question_id"] == "template_defaults"
    assert payload["questions"][0]["defaults"]["supply_voltage_v"] == 5.0

    prompt = runner.invoke(app, ["build", "--project", project_id,
                                 "--prompt", "Build a timer", "--json",
                                 "--non-interactive"])
    assert prompt.exit_code == 4
    assert _json(prompt)["error"]["code"] == "MODEL_UNAVAILABLE"

    templates = runner.invoke(app, ["templates", "list", "--json"])
    assert templates.exit_code == 0
    assert {t["template_id"] for t in _json(templates)["templates"]} == {
        "power_3v3", "led_timer", "i2c_temperature"}
