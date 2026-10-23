"""Config schema v2: migration, profiles, default switching and secret isolation."""

from __future__ import annotations

import json

from mota.app.config import ConfigService
from mota.workspace import paths
from tests.conftest import make_user


def _settings_path(data_root, user_id):
    return paths.ensure_dir(paths.user_dir(user_id, data_root)) / "settings.json"


def test_v1_settings_migrate_to_a_profile_and_keep_preferences(service, data_root):
    session = make_user(service, "cfgv1")
    path = _settings_path(data_root, session.user_id)
    path.write_text(json.dumps({
        "schema_version": 1,
        "config_version": 4,
        "settings": {
            "provider": "openai", "api_mode": "responses", "model": "gpt-5.4",
            "base_url": "https://api.openai.com/v1", "units": "in",
            "reduced_motion": True, "snap_mm": 0.5,
        },
    }), encoding="utf-8")
    service.secrets.set(session.user_id, "openai_api_key", "sk-legacy-key",
                        session_only=True)

    cfg = service.dispatch("config.get", {}, session)["config"]
    assert cfg["schema_version"] == 2
    assert cfg["config_version"] == 5
    assert cfg["units"] == "in"
    assert cfg["reduced_motion"] is True
    assert cfg["snap_mm"] == 0.5
    profile = cfg["profiles"][0]
    assert profile["profile_id"] == "openai-main"
    assert profile["api_format"] == "responses"
    assert profile["model"] == "gpt-5.4"
    assert cfg["default_profile_id"] == "openai-main"
    assert profile["api_key_configured"] is True
    assert profile["secret_storage"] == "session_only"
    assert "sk-legacy-key" not in json.dumps(cfg)


def test_deepseek_profile_and_default_switch_do_not_rewrite_a_frozen_binding(service):
    session = make_user(service, "cfgv2")
    cfg = service.dispatch("config.get", {}, session)["config"]
    updated = service.dispatch("config.update", {
        "expected_config_version": cfg["config_version"],
        "changes": {"profiles": [{
            "profile_id": "deepseek-main",
            "name": "DeepSeek",
            "provider": "deepseek",
            "api_format": "chat_completions",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "stream": True,
            "thinking_mode": "disabled",
            "thinking_effort": None,
            "max_output_tokens": 8192,
            "request_timeout_seconds": 120.0,
            "strict_tool_schema": False,
            "supports_vision": True,
        }]},
    }, session)["config"]
    assert {p["profile_id"] for p in updated["profiles"]} == {
        "openai-main", "deepseek-main"}
    deepseek = next(p for p in updated["profiles"] if p["profile_id"] == "deepseek-main")
    assert deepseek["model"] == "deepseek-flash"
    assert deepseek["api_format"] == "chat_completions"
    assert deepseek["thinking_effort"] is None

    config_service = ConfigService(session.scope(), service.secrets)
    frozen, _ = config_service.get_profile("openai-main")
    binding = config_service.build_binding(frozen)
    assert binding.provider == "openai" and binding.model == "gpt-5.4"

    switched = service.dispatch("config.update", {
        "expected_config_version": updated["config_version"],
        "changes": {"set_default_profile_id": "deepseek-main"},
    }, session)["config"]
    assert switched["default_profile_id"] == "deepseek-main"
    # The binding captured before the switch keeps the request-time configuration.
    assert binding.provider == "openai"
    assert binding.model == "gpt-5.4"
    assert binding.profile_id == "openai-main"
    assert len(switched["profiles"]) == 2


def test_profile_key_is_written_through_the_secret_channel(service, data_root):
    session = make_user(service, "cfgkey")
    cfg = service.dispatch("config.get", {}, session)["config"]
    saved = service.dispatch("config.update", {
        "expected_config_version": cfg["config_version"],
        "changes": {
            "profiles": [{"profile_id": "deepseek-main", "provider": "deepseek",
                          "api_format": "chat_completions",
                          "base_url": "https://api.deepseek.com",
                          "model": "deepseek-flash"}],
            "api_key_action": "set",
            "api_key": "sk-deepseek-secret",
            "api_key_profile_id": "deepseek-main",
            "api_key_session_only": True,
        },
    }, session)["config"]
    deepseek = next(p for p in saved["profiles"] if p["profile_id"] == "deepseek-main")
    assert deepseek["api_key_configured"] is True
    assert deepseek["secret_storage"] == "session_only"
    assert "sk-deepseek-secret" not in json.dumps(saved)
    stored = _settings_path(data_root, session.user_id).read_text(encoding="utf-8")
    assert "sk-deepseek-secret" not in stored
