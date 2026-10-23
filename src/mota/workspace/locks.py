"""Per-project and per-user file locks.

The engine is one process, but CLI invocations may run concurrently with the
desktop. flock keeps writers serialised across processes; the in-process
registry keeps threads honest on platforms where flock is per-process.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

_registry_guard = threading.Lock()
_registry: dict[str, threading.RLock] = {}


def _thread_lock(key: str) -> threading.RLock:
    with _registry_guard:
        if key not in _registry:
            _registry[key] = threading.RLock()
        return _registry[key]


@contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Acquire an exclusive advisory lock; yields whether acquisition succeeded."""
    lock = _thread_lock(str(path))
    acquired = lock.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        if fcntl is not None:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle.fileno(), flags)
            except OSError:
                if not blocking:
                    yield False
                    return
                raise
        yield True
    finally:
        if handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        lock.release()
