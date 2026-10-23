"""Credential storage: OS keychain first, explicit session-only fallback.

API keys and remember-me tokens never enter settings.json, logs or events.
"""

from __future__ import annotations

import secrets
from typing import Any

from mota.app.state import UserScope

SERVICE = "mota"


class SecretStore:
    def __init__(self, *, allow_keychain: bool = True) -> None:
        self._session: dict[str, str] = {}
        self._session_only: set[str] = set()
        self._keyring = None
        self.storage = "none"
        if allow_keychain:
            try:
                import keyring

                backend = keyring.get_keyring()
                name = backend.__class__.__name__.lower()
                if "fail" not in name and "null" not in name:
                    self._keyring = keyring
                    self.storage = "keychain"
            except Exception:
                self._keyring = None

    def _key(self, user_id: str, name: str) -> str:
        return f"{name}:{user_id}"

    def set(self, user_id: str, name: str, value: str, *, session_only: bool = False) -> str:
        if session_only or self._keyring is None:
            self._session[self._key(user_id, name)] = value
            self._session_only.add(self._key(user_id, name))
            return "session_only"
        try:
            self._keyring.set_password(SERVICE, self._key(user_id, name), value)
            self._session_only.discard(self._key(user_id, name))
            return "keychain"
        except Exception:
            self._session[self._key(user_id, name)] = value
            self._session_only.add(self._key(user_id, name))
            return "session_only"

    def get(self, user_id: str, name: str) -> str | None:
        value = self._session.get(self._key(user_id, name))
        if value is not None:
            return value
        if self._keyring is None:
            return None
        try:
            return self._keyring.get_password(SERVICE, self._key(user_id, name))
        except Exception:
            return None

    def delete(self, user_id: str, name: str) -> None:
        self._session.pop(self._key(user_id, name), None)
        self._session_only.discard(self._key(user_id, name))
        if self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE, self._key(user_id, name))
            except Exception:
                pass

    def clear_session(self) -> None:
        self._session.clear()
        self._session_only.clear()

    def storage_for(self, user_id: str, name: str) -> str:
        key = self._key(user_id, name)
        if key in self._session_only:
            return "session_only"
        if self.get(user_id, name):
            return "keychain" if self._keyring is not None else "session_only"
        return "none"

    # Convenience -----------------------------------------------------------
    def get_api_key(self, owner: UserScope) -> str | None:
        import os

        env = os.environ.get("MOTA_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        return self.get(owner.user_id, "openai_api_key") or env

    def profile_key_name(self, profile_id: str) -> str:
        return f"api_key:{profile_id}"

    def get_profile_key(self, owner: UserScope, profile_id: str,
                        provider: str = "openai") -> str | None:
        import os

        stored = self.get(owner.user_id, self.profile_key_name(profile_id))
        if stored:
            return stored
        env_names = {
            "openai": ("MOTA_OPENAI_API_KEY", "OPENAI_API_KEY"),
            "deepseek": ("MOTA_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
            "custom_openai_compatible": ("MOTA_CUSTOM_API_KEY", "CUSTOM_API_KEY"),
        }.get(provider, ())
        for name in env_names:
            value = os.environ.get(name)
            if value:
                return value
        if profile_id == "openai-main":
            return self.get(owner.user_id, "openai_api_key")
        return None

    def set_profile_key(self, owner: UserScope, profile_id: str, value: str,
                        *, session_only: bool = False) -> str:
        return self.set(owner.user_id, self.profile_key_name(profile_id), value,
                        session_only=session_only)

    def delete_profile_key(self, owner: UserScope, profile_id: str) -> None:
        self.delete(owner.user_id, self.profile_key_name(profile_id))

    def profile_storage(self, owner: UserScope, profile_id: str,
                        provider: str = "openai") -> str:
        name = self.profile_key_name(profile_id)
        if self._key(owner.user_id, name) in self._session_only:
            return "session_only"
        if self.get(owner.user_id, name):
            return "keychain" if self._keyring is not None else "session_only"
        import os

        for env_name in {
            "openai": ("MOTA_OPENAI_API_KEY", "OPENAI_API_KEY"),
            "deepseek": ("MOTA_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
            "custom_openai_compatible": ("MOTA_CUSTOM_API_KEY", "CUSTOM_API_KEY"),
        }.get(provider, ()):
            if os.environ.get(env_name):
                return "environment"
        return "none"
