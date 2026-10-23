"""Local accounts: Argon2id password hashing, locked atomic account file.

A local account is not an OS security boundary; directory isolation still
depends on file permissions of the OS user. Usernames are never used as paths.
"""

from __future__ import annotations

import secrets
import unicodedata
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from mota.app.state import SessionContext
from mota.domain.models import utcnow
from mota.errors import AuthRequired, Conflict, InvalidRequest, NotFound
from mota.workspace import paths
from mota.workspace.atomic import read_json, write_json_atomic
from mota.workspace.locks import file_lock

_hasher = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

MIN_USERNAME = 3
MAX_USERNAME = 32


def username_key(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def _validate_username(username: str) -> str:
    name = unicodedata.normalize("NFKC", username).strip()
    if not (MIN_USERNAME <= len(name) <= MAX_USERNAME):
        raise InvalidRequest(
            f"Username must be between {MIN_USERNAME} and {MAX_USERNAME} characters.",
            code="INVALID_USERNAME")
    if any(ch in name for ch in ("/", "\\", "\x00")) or name in (".", ".."):
        raise InvalidRequest("Username contains unsupported characters.", code="INVALID_USERNAME")
    if not any(ch.isalnum() for ch in name):
        raise InvalidRequest("Username must contain at least one letter or digit.",
                             code="INVALID_USERNAME")
    return name


class AuthService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or paths.data_root()
        self.app_dir = paths.ensure_dir(paths.app_dir(self.root))
        self.accounts_path = self.app_dir / "accounts.json"
        self.lock_path = self.app_dir / ".accounts.lock"

    # ---------------------------------------------------------------- storage
    def _load(self) -> dict[str, Any]:
        data = read_json(self.accounts_path, default=None)
        if not data:
            return {"schema_version": 1, "accounts": []}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        write_json_atomic(self.accounts_path, data)

    def _find(self, data: dict[str, Any], key: str) -> dict[str, Any] | None:
        for account in data.get("accounts", []):
            if account.get("username_key") == key:
                return account
        return None

    def _public(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": account["user_id"],
            "username": account["username"],
            "display_name": account.get("display_name") or account["username"],
            "created_at": account.get("created_at"),
        }

    # ------------------------------------------------------------------ API
    def register(self, username: str, password: str, display_name: str | None = None
                 ) -> tuple[dict[str, Any], SessionContext]:
        name = _validate_username(username)
        if len(password) < 8:
            raise InvalidRequest("Password must be at least 8 characters.", code="WEAK_PASSWORD")
        key = username_key(name)
        with file_lock(self.lock_path):
            data = self._load()
            if self._find(data, key) is not None:
                raise Conflict("That username is already in use.", code="USERNAME_TAKEN")
            user_id = "user_" + secrets.token_hex(8)
            account = {
                "user_id": user_id,
                "username": name,
                "username_key": key,
                "display_name": (display_name or name).strip() or name,
                "password_hash": _hasher.hash(password),
                "created_at": utcnow(),
            }
            data.setdefault("accounts", []).append(account)
            self._save(data)
        self._provision(user_id, account["display_name"])
        session = SessionContext(
            session_id="session_" + secrets.token_hex(8),
            user_id=user_id,
            username=account["username"],
            epoch=1,
        )
        return {"account_created": True, "profile": self._public(account)}, session

    def login(self, username: str, password: str) -> tuple[dict[str, Any], SessionContext]:
        key = username_key(username)
        data = self._load()
        account = self._find(data, key)
        if account is None:
            raise AuthRequired("The username or password is incorrect.", code="AUTH_FAILED")
        try:
            _hasher.verify(account["password_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise AuthRequired("The username or password is incorrect.", code="AUTH_FAILED")
        if _hasher.check_needs_rehash(account["password_hash"]):
            with file_lock(self.lock_path):
                data = self._load()
                target = self._find(data, key)
                if target:
                    target["password_hash"] = _hasher.hash(password)
                    self._save(data)
        self._provision(account["user_id"], account.get("display_name") or account["username"])
        session = SessionContext(
            session_id="session_" + secrets.token_hex(8),
            user_id=account["user_id"],
            username=account["username"],
            epoch=1,
        )
        return {"profile": self._public(account)}, session

    def get_public(self, user_id: str) -> dict[str, Any]:
        data = self._load()
        for account in data.get("accounts", []):
            if account["user_id"] == user_id:
                return self._public(account)
        raise NotFound("Account not found.", code="ACCOUNT_NOT_FOUND")

    def _provision(self, user_id: str, display_name: str) -> None:
        user_root = paths.ensure_dir(paths.user_dir(user_id, self.root))
        workspace = paths.ensure_dir(paths.workspace_dir(user_id, self.root))
        for sub in ("projects", "memory/short_term", "memory/long_term",
                    "memory/experience", "tasks", "exports", "trash", "cache"):
            (workspace / sub).mkdir(parents=True, exist_ok=True)
        profile_path = user_root / "profile.json"
        if not profile_path.exists():
            write_json_atomic(profile_path, {
                "schema_version": 1,
                "user_id": user_id,
                "display_name": display_name,
                "created_at": utcnow(),
            })
