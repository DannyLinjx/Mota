"""Versioned, redacted model configuration with multiple provider profiles.

Secrets never enter settings.json. Profile-level keys live in the OS keychain (or an
explicit session-only store) under `api_key:<profile_id>`.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from mota.app.state import UserScope
from mota.auth.credentials import SecretStore
from mota.domain.models import (
    ProviderBinding, ProviderProfile, RedactedConfig, utcnow,
)
from mota.errors import ConfigConflict, InvalidRequest
from mota.workspace import paths
from mota.workspace.atomic import read_json, write_json_atomic
from mota.workspace.locks import file_lock

PROFILE_FIELDS = {
    "profile_id", "name", "provider", "api_format", "base_url", "model", "stream",
    "thinking_mode", "thinking_effort", "max_output_tokens",
    "request_timeout_seconds", "strict_tool_schema", "supports_vision",
}
PREFERENCE_FIELDS = {
    "units", "default_view", "show_grid", "snap_mm", "reduced_motion", "follow_build",
    "send_with_ctrl_enter", "restore_last_project", "remember_me", "left_panel_width",
    "right_panel_width", "font_scale", "request_budget",
}
PROVIDERS = {"openai", "deepseek", "custom_openai_compatible"}
API_FORMATS = {"responses", "chat_completions"}
THINKING_MODES = {"enabled", "auto", "disabled"}
THINKING_EFFORTS = {"low", "high", "max"}

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI", "provider": "openai", "api_format": "responses",
        "base_url": "https://api.openai.com/v1", "model": "gpt-5.4",
        "stream": True, "thinking_mode": "disabled", "thinking_effort": None,
        "max_output_tokens": 8192, "request_timeout_seconds": 120.0,
        "strict_tool_schema": True, "supports_vision": True,
    },
    "deepseek": {
        "name": "DeepSeek", "provider": "deepseek", "api_format": "chat_completions",
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
        "stream": True, "thinking_mode": "disabled", "thinking_effort": None,
        "max_output_tokens": 8192, "request_timeout_seconds": 120.0,
        "strict_tool_schema": False, "supports_vision": True,
    },
    "custom_openai_compatible": {
        "name": "Custom OpenAI-compatible", "provider": "custom_openai_compatible",
        "api_format": "chat_completions", "base_url": "", "model": "",
        "stream": True, "thinking_mode": "disabled", "thinking_effort": None,
        "max_output_tokens": 8192, "request_timeout_seconds": 120.0,
        "strict_tool_schema": False, "supports_vision": False,
    },
}


def default_profile(provider: str = "openai", profile_id: str | None = None) -> dict[str, Any]:
    preset = dict(PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["openai"]))
    preset["profile_id"] = profile_id or f"{provider}-main"
    preset["name"] = preset["name"]
    return preset


class ConfigService:
    def __init__(self, owner: UserScope, secrets: SecretStore, root: Path | None = None) -> None:
        self.owner = owner
        self.secrets = secrets
        self.root = root or paths.data_root()
        self.path = paths.ensure_dir(paths.user_dir(owner.user_id, self.root)) / "settings.json"
        self.lock_path = paths.user_dir(owner.user_id, self.root) / ".settings.lock"

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, Any]:
        data = read_json(self.path, default=None)
        if not data:
            profile = default_profile()
            data = {
                "schema_version": 2,
                "config_version": 2,
                "profiles": [profile],
                "default_profile_id": profile["profile_id"],
                "preferences": {},
                "updated_at": utcnow(),
            }
            write_json_atomic(self.path, data)
            return data
        if int(data.get("schema_version", 1)) < 2:
            data = self._migrate_v1(data)
        if not data.get("profiles"):
            profile = default_profile()
            data["profiles"] = [profile]
            data["default_profile_id"] = profile["profile_id"]
            write_json_atomic(self.path, data)
        return data

    def _migrate_v1(self, data: dict[str, Any]) -> dict[str, Any]:
        settings = dict(data.get("settings", {}))
        provider = settings.get("provider", "openai")
        api_mode = settings.get("api_mode", "responses")
        api_format = "responses" if api_mode == "responses" else "chat_completions"
        profile = default_profile(provider)
        profile.update({
            "api_format": api_format,
            "base_url": settings.get("base_url") or profile["base_url"],
            "model": settings.get("model") or profile["model"],
            "supports_vision": provider == "openai",
        })
        preferences = {k: v for k, v in settings.items()
                       if k in PREFERENCE_FIELDS}
        migrated = {
            "schema_version": 2,
            "config_version": int(data.get("config_version", 1)) + 1,
            "profiles": [profile],
            "default_profile_id": profile["profile_id"],
            "preferences": preferences,
            "updated_at": utcnow(),
            "migrated_from_schema": 1,
        }
        old_key = self.secrets.get(self.owner.user_id, "openai_api_key")
        if old_key and not self.secrets.get(self.owner.user_id,
                                             self.secrets.profile_key_name(
                                                 profile["profile_id"])):
            session_only = self.secrets.storage_for(
                self.owner.user_id, "openai_api_key") == "session_only"
            self.secrets.set_profile_key(self.owner, profile["profile_id"], old_key,
                                         session_only=session_only)
        write_json_atomic(self.path, migrated)
        return migrated

    def _decorate(self, profile: dict[str, Any]) -> dict[str, Any]:
        data = dict(profile)
        provider = data.get("provider", "openai")
        pid = data["profile_id"]
        key = self.secrets.get_profile_key(self.owner, pid, provider)
        data["api_key_configured"] = bool(key)
        data["secret_storage"] = self.secrets.profile_storage(self.owner, pid, provider)
        data.setdefault("test_results", {})
        data.setdefault("model_verified", False)
        data.setdefault("verified_config_version", None)
        return data

    def _projections(self, profiles: list[dict[str, Any]], default_id: str | None
                     ) -> dict[str, Any]:
        chosen = next((p for p in profiles if p["profile_id"] == default_id),
                      profiles[0] if profiles else {})
        return {
            "provider": chosen.get("provider", "openai"),
            "api_mode": chosen.get("api_format", "responses"),
            "base_url": chosen.get("base_url", ""),
            "model": chosen.get("model", ""),
            "api_key_configured": bool(chosen.get("api_key_configured")),
            "secret_storage": chosen.get("secret_storage", "none"),
            "model_verified": bool(chosen.get("model_verified")),
            "model_verified_config_version": chosen.get("verified_config_version"),
        }

    def get(self) -> RedactedConfig:
        data = self._load()
        profiles = [self._decorate(p) for p in data.get("profiles", [])]
        default_id = data.get("default_profile_id") or (
            profiles[0]["profile_id"] if profiles else None)
        preferences = dict(data.get("preferences", {}))
        preferences["remember_me"] = bool(read_json(
            paths.app_dir(self.root) / "remember.json", default={}).get(
                self.owner.user_id, {}).get("enabled", False))
        payload = {
            "schema_version": 2,
            "config_version": data.get("config_version", 2),
            "profiles": profiles,
            "default_profile_id": default_id,
            **preferences,
            **self._projections(profiles, default_id),
        }
        return RedactedConfig.model_validate(payload)

    # ----------------------------------------------------------------- profile
    def get_profile(self, profile_id: str | None = None) -> tuple[ProviderProfile, str | None]:
        cfg = self.get()
        pid = profile_id or cfg.default_profile_id
        profile = next((p for p in cfg.profiles if p.profile_id == pid), None)
        if profile is None:
            raise InvalidRequest("The selected AI connection does not exist.",
                                 code="PROFILE_NOT_FOUND")
        key = self.secrets.get_profile_key(self.owner, profile.profile_id, profile.provider)
        return profile, key

    def build_binding(self, profile: ProviderProfile) -> ProviderBinding:
        return ProviderBinding(
            binding_id="binding_" + secrets.token_hex(6),
            profile_id=profile.profile_id, profile_name=profile.name,
            provider=profile.provider, api_format=profile.api_format,
            base_url=profile.base_url, model=profile.model, stream=profile.stream,
            thinking_mode=profile.thinking_mode,
            thinking_effort=profile.thinking_effort,
            max_output_tokens=profile.max_output_tokens,
            request_timeout_seconds=profile.request_timeout_seconds,
            strict_tool_schema=profile.strict_tool_schema,
            supports_vision=profile.supports_vision,
            config_version=self.get().config_version)

    def record_probe_result(self, profile_id: str, suites: list[dict[str, Any]],
                            config_version: int) -> None:
        with file_lock(self.lock_path):
            data = self._load()
            for profile in data.get("profiles", []):
                if profile.get("profile_id") != profile_id:
                    continue
                results: dict[str, Any] = dict(profile.get("test_results") or {})
                for suite in suites:
                    results[suite.get("suite", "")] = {
                        "status": suite.get("status"),
                        "requests": suite.get("requests"),
                        "latency_ms": suite.get("latency_ms"),
                        "detail": suite.get("detail", ""),
                        "tested_at": utcnow(),
                        "config_version": config_version,
                    }
                profile["test_results"] = results
                if any(s.get("suite") == "text" and s.get("status") == "PASS"
                       for s in suites):
                    profile["model_verified"] = True
                    profile["verified_config_version"] = config_version
                write_json_atomic(self.path, data)
                return

    # ------------------------------------------------------------------ update
    def update(self, expected_config_version: int, changes: dict[str, Any],
               idempotency_key: str = "") -> tuple[RedactedConfig, dict[str, Any]]:
        with file_lock(self.lock_path):
            data = self._load()
            current = int(data.get("config_version", 2))
            if int(expected_config_version) != current:
                raise ConfigConflict("Settings changed. Reload before saving.",
                                     data={"current_config_version": current})
            changes = dict(changes or {})
            profiles = {p["profile_id"]: dict(p) for p in data.get("profiles", [])}
            default_id = data.get("default_profile_id") or next(iter(profiles), None)

            incoming = changes.pop("profiles", None)
            if incoming is not None:
                if not isinstance(incoming, list):
                    raise InvalidRequest("profiles must be a list.")
                for raw in incoming:
                    if not isinstance(raw, dict):
                        continue
                    profile_id = str(raw.get("profile_id") or "").strip()
                    if not profile_id:
                        raise InvalidRequest("Each AI connection needs a profile_id.")
                    base = profiles.get(profile_id) or default_profile(
                        raw.get("provider", "openai"), profile_id)
                    merged = dict(base)
                    for key, value in raw.items():
                        if key not in PROFILE_FIELDS:
                            continue
                        if value is None and key != "thinking_effort":
                            continue
                        merged[key] = value
                    if merged.get("provider") not in PROVIDERS:
                        raise InvalidRequest(
                            f"Unknown provider “{merged.get('provider')}”.",
                            code="PROVIDER_UNSUPPORTED")
                    if merged.get("api_format") not in API_FORMATS:
                        raise InvalidRequest(
                            f"Unknown API format “{merged.get('api_format')}”.",
                            code="API_FORMAT_UNSUPPORTED")
                    if merged.get("thinking_mode") not in THINKING_MODES:
                        raise InvalidRequest("Unknown thinking mode.")
                    effort = merged.get("thinking_effort")
                    if effort is not None and effort not in THINKING_EFFORTS:
                        raise InvalidRequest("Unknown thinking effort.")
                    profiles[profile_id] = merged
            delete_id = changes.pop("delete_profile_id", None)
            if delete_id:
                profiles.pop(delete_id, None)
                self.secrets.delete_profile_key(self.owner, delete_id)
                if default_id == delete_id:
                    default_id = next(iter(profiles), None)
            legacy_provider = changes.get("provider")
            legacy_model = changes.get("model")
            legacy_base = changes.get("base_url")
            legacy_mode = changes.get("api_mode")
            if legacy_provider or legacy_model or legacy_base or legacy_mode:
                pid = changes.pop("profile_id", None) or default_id
                if pid and pid in profiles:
                    target = profiles[pid]
                    target["provider"] = legacy_provider or target.get("provider", "openai")
                    target["model"] = legacy_model or target.get("model", "")
                    target["base_url"] = legacy_base or target.get("base_url", "")
                    if legacy_mode:
                        target["api_format"] = ("responses" if legacy_mode == "responses"
                                                else "chat_completions")
                    profiles[pid] = target
            if not profiles:
                fallback = default_profile()
                profiles[fallback["profile_id"]] = fallback
                default_id = fallback["profile_id"]

            api_profile = changes.pop("api_key_profile_id", None)
            api_action = changes.pop("api_key_action", "unchanged")
            api_key = changes.pop("api_key", None)
            api_session = bool(changes.pop("api_key_session_only", False))
            if api_action in ("set", "clear"):
                pid = api_profile or default_id
                if not pid or pid not in profiles:
                    raise InvalidRequest("Choose an AI connection for the key.",
                                         code="PROFILE_NOT_FOUND")
                if api_action == "set" and api_key:
                    self.secrets.set_profile_key(self.owner, pid, api_key,
                                                 session_only=api_session)
                    profiles[pid]["model_verified"] = False
                    profiles[pid]["verified_config_version"] = None
                elif api_action == "clear":
                    self.secrets.delete_profile_key(self.owner, pid)
                    profiles[pid]["model_verified"] = False
                    profiles[pid]["verified_config_version"] = None

            requested_default = changes.pop("set_default_profile_id", None) or \
                changes.pop("default_profile_id", None)
            if requested_default:
                if requested_default not in profiles:
                    raise InvalidRequest("The default AI connection does not exist.",
                                         code="PROFILE_NOT_FOUND")
                default_id = requested_default
            if default_id not in profiles:
                default_id = next(iter(profiles), None)

            preferences = dict(data.get("preferences", {}))
            for key in list(changes.keys()):
                if key in PREFERENCE_FIELDS:
                    preferences[key] = changes.pop(key)
            remember_me = preferences.pop("remember_me", None)

            data["profiles"] = list(profiles.values())
            data["default_profile_id"] = default_id
            data["preferences"] = preferences
            data["schema_version"] = 2
            data["config_version"] = current + 1
            data["updated_at"] = utcnow()
            write_json_atomic(self.path, data)

        if remember_me is not None:
            remember_path = paths.app_dir(self.root) / "remember.json"
            remember = read_json(remember_path, default={}) or {}
            remember.setdefault(self.owner.user_id, {})["enabled"] = bool(remember_me)
            write_json_atomic(remember_path, remember)
        cfg = self.get()
        return cfg, {"config_version": data["config_version"],
                     "default_profile_id": default_id,
                     "secret_storage": cfg.secret_storage}

    def mark_model_verified(self, config_version: int,
                            profile_id: str | None = None) -> None:
        pid = profile_id or self.get().default_profile_id
        self.record_probe_result(pid or "", [
            {"suite": "text", "status": "PASS", "requests": 1,
             "detail": "Marked verified by legacy call"}], config_version)
