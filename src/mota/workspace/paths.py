"""Filesystem paths and data root resolution.

The data root is resolved once from MOTA_DATA_DIR or platformdirs. All user
files stay under it; nothing is written into the source tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs


def data_root() -> Path:
    override = os.environ.get("MOTA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(platformdirs.user_data_dir("Mota", "Mota")).resolve()


def app_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "app"


def users_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "users"


def catalog_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "catalog"


def user_dir(user_id: str, root: Path | None = None) -> Path:
    return users_dir(root) / user_id


def workspace_dir(user_id: str, root: Path | None = None) -> Path:
    return user_dir(user_id, root) / "workspace"


def projects_dir(user_id: str, root: Path | None = None) -> Path:
    return workspace_dir(user_id, root) / "projects"


def memory_dir(user_id: str, root: Path | None = None) -> Path:
    return workspace_dir(user_id, root) / "memory"


def tasks_dir(user_id: str, root: Path | None = None) -> Path:
    return workspace_dir(user_id, root) / "tasks"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_child(base: Path, candidate: str | Path) -> Path:
    """Resolve candidate under base and reject traversal / symlink escapes."""
    base = base.resolve()
    target = (base / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    if target != base and base not in target.parents:
        raise PermissionError(f"path escapes allowed root: {candidate}")
    return target
