"""Three-layer Markdown memory service with scope filtering and tombstones."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Iterable

from mota.app.state import UserScope
from mota.domain.models import MemoryEntry, utcnow
from mota.errors import InvalidRequest, NotFound
from mota.memory import markdown as md
from mota.workspace import paths

LAYERS = ("short_term", "long_term", "experience")
LONG_TERM_FILES = {"profile": "profile.md", "preferences": "preferences.md",
                   "constraints": "constraints.md"}


class MemoryService:
    def __init__(self, owner: UserScope, root: Path | None = None) -> None:
        self.owner = owner
        self.root = root or paths.data_root()
        self.base = paths.ensure_dir(paths.memory_dir(owner.user_id, self.root))
        self._ensure_layout()

    # ------------------------------------------------------------------ paths
    def _ensure_layout(self) -> None:
        for name in ("long_term", "experience"):
            (self.base / name).mkdir(parents=True, exist_ok=True)
        (self.base / "short_term").mkdir(parents=True, exist_ok=True)
        for name, filename in LONG_TERM_FILES.items():
            path = self.base / "long_term" / filename
            if not path.exists():
                path.write_text(md.new_file_text({
                    "schema_version": 1, "owner_user_id": self.owner.user_id,
                    "scope": "user", "project_id": None, "updated_at": utcnow(),
                }, name.title()), encoding="utf-8")

    def session_dir(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_") or "session"
        folder = self.base / "short_term" / safe
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _layer_files(self, layer: str, session_id: str | None = None) -> list[Path]:
        if layer == "long_term":
            return sorted((self.base / "long_term").glob("*.md"))
        if layer == "experience":
            return sorted((self.base / "experience").glob("*.md"))
        if layer == "short_term":
            folders = [self.session_dir(session_id)] if session_id else \
                sorted(p for p in (self.base / "short_term").iterdir() if p.is_dir())
            files: list[Path] = []
            for folder in folders:
                files.extend(sorted(folder.glob("*.md")))
            return files
        raise InvalidRequest(f"Unknown memory layer {layer}.", code="MEMORY_LAYER")

    # ------------------------------------------------------------------ parse
    def _entries(self, layer: str, session_id: str | None = None) -> list[MemoryEntry]:
        out: list[MemoryEntry] = []
        for path in self._layer_files(layer, session_id):
            parsed = md.parse_memory_file(path)
            for raw in parsed.entries:
                fields = raw.fields
                entry_id = fields.get("entry_id") or raw.entry_id
                if entry_id.startswith("TOMB-") or fields.get("tombstone_id"):
                    continue
                if fields.get("role"):
                    # conversation messages are transcript records, not memories
                    continue
                scope = fields.get("scope") or parsed.frontmatter.get("scope")
                project_id = fields.get("project_id") or parsed.frontmatter.get("project_id")
                if scope not in ("project", "user"):
                    scope = "project"
                if scope == "project" and not project_id:
                    continue
                out.append(MemoryEntry(
                    entry_id=entry_id,
                    layer=layer,  # type: ignore[arg-type]
                    owner_user_id=fields.get("owner_user_id") or self.owner.user_id,
                    scope=scope,  # type: ignore[arg-type]
                    project_id=None if project_id in (None, "null", "") else project_id,
                    status=fields.get("status", "candidate"),
                    summary=fields.get("summary", raw.body.splitlines()[0] if raw.body else ""),
                    body=raw.body,
                    source_ids=_parse_list(fields.get("source_ids", "")),
                    updated_at=fields.get("updated_at", ""),
                    relative_path=str(path.relative_to(self.base.parent.parent)),
                    hash=raw.hash,
                    semantic_key=fields.get("semantic_key"),
                    module_id=fields.get("module_id"),
                    module_version=int(fields["module_version"]) if fields.get("module_version") else None,
                    library_snapshot_id=fields.get("library_snapshot_id"),
                    evidence_run_id=fields.get("evidence_run_id"),
                    evidence_revision_id=fields.get("evidence_revision_id"),
                    verified_evidence=fields.get("status") == "verified",
                ))
        return out

    def tombstones(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for layer in LAYERS:
            files = []
            if layer == "short_term":
                files = sorted((self.base / "short_term").glob("*/forgotten.md"))
            else:
                files = [self.base / layer / "forgotten.md"]
            for path in files:
                if not path.exists():
                    continue
                parsed = md.parse_memory_file(path)
                for raw in parsed.entries:
                    if raw.fields.get("status", "active") != "active":
                        continue
                    rows.append({"entry_id": raw.fields.get("target_entry_id", ""),
                                 "semantic_key": raw.fields.get("semantic_key", ""),
                                 "scope": raw.fields.get("scope", "project"),
                                 "project_id": raw.fields.get("project_id", ""),
                                 "layer": layer})
        return rows

    # -------------------------------------------------------------------- API
    def list(self, layer: str, project_id: str | None = None,
             session_id: str | None = None) -> list[MemoryEntry]:
        if layer not in LAYERS:
            raise InvalidRequest(f"Unknown memory layer {layer}.", code="MEMORY_LAYER")
        tombstones = self.tombstones()
        blocked_ids = {t["entry_id"] for t in tombstones}
        blocked_keys = {t["semantic_key"] for t in tombstones if t["semantic_key"]}
        rows = []
        for entry in self._entries(layer, session_id):
            if entry.entry_id in blocked_ids or (entry.semantic_key and
                                                 entry.semantic_key in blocked_keys):
                continue
            if project_id is not None and entry.scope == "project" and \
                    entry.project_id != project_id:
                continue
            rows.append(entry)
        return rows

    def read(self, entry_id: str) -> MemoryEntry:
        for layer in LAYERS:
            for entry in self._entries(layer):
                if entry.entry_id == entry_id:
                    return entry
        raise NotFound(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")

    def retrieve(self, project_id: str | None, query: str = "", *,
                 limit_tokens: dict[str, int] | None = None) -> dict[str, Any]:
        """Owner/scope/tombstone filters first, then keyword relevance."""
        tombstones = self.tombstones()
        blocked_ids = {t["entry_id"] for t in tombstones}
        blocked_keys = {t["semantic_key"] for t in tombstones if t["semantic_key"]}
        budgets = limit_tokens or {"short_term": 1200, "long_term": 800, "experience": 1500}
        tokens = [t for t in query.casefold().split() if t]
        out: dict[str, list[dict[str, Any]]] = {}
        for layer in LAYERS:
            candidates = []
            for entry in self._entries(layer):
                if entry.owner_user_id != self.owner.user_id:
                    continue
                if entry.entry_id in blocked_ids:
                    continue
                if entry.semantic_key and entry.semantic_key in blocked_keys:
                    continue
                if entry.scope == "project" and entry.project_id != project_id:
                    continue
                if entry.scope == "user" and project_id is None and layer != "long_term":
                    continue
                haystack = f"{entry.summary} {entry.body} {entry.semantic_key or ''}".casefold()
                score = sum(1 for token in tokens if token in haystack) if tokens else 1
                if score == 0 and tokens:
                    continue
                candidates.append((score, entry))
            candidates.sort(key=lambda pair: (-pair[0], pair[1].updated_at))
            rows: list[dict[str, Any]] = []
            used = 0
            for _, entry in candidates:
                cost = max(1, len(entry.body) // 4)
                if used + cost > budgets[layer]:
                    continue
                used += cost
                rows.append({"entry_id": entry.entry_id, "summary": entry.summary,
                             "body": entry.body, "scope": entry.scope,
                             "project_id": entry.project_id, "status": entry.status,
                             "source_ids": entry.source_ids,
                             "updated_at": entry.updated_at,
                             "module_id": entry.module_id,
                             "verified_evidence": entry.verified_evidence})
                if layer == "experience" and len(rows) >= 5:
                    break
            out[layer] = rows
        return {"layers": out, "project_id": project_id, "query": query}

    def update(self, entry_id: str, content: str, expected_hash: str, *,
               scope: str | None = None, project_id: str | None = None) -> MemoryEntry:
        for layer in LAYERS:
            for path in self._layer_files(layer):
                parsed = md.parse_memory_file(path)
                raw = next((e for e in parsed.entries if e.entry_id == entry_id), None)
                if raw is None:
                    continue
                if expected_hash and raw.hash != expected_hash:
                    raise InvalidRequest(
                        "This memory changed outside Mota. Reload before saving.",
                        code="MEMORY_CONFLICT",
                        data={"current_hash": raw.hash, "expected_hash": expected_hash})
                fields = dict(raw.fields)
                fields["updated_at"] = utcnow()
                if scope:
                    fields["scope"] = scope
                if project_id is not None:
                    fields["project_id"] = project_id
                if scope == "project" and not fields.get("project_id"):
                    raise InvalidRequest("Project-scoped memory requires a project id.",
                                         code="MEMORY_SCOPE_INVALID")
                md.replace_entry(path, entry_id, fields, content)
                return self.read(entry_id)
        raise NotFound(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")

    def forget(self, entry_id: str, *, semantic_key: str | None = None) -> dict[str, Any]:
        for layer in LAYERS:
            for path in self._layer_files(layer):
                parsed = md.parse_memory_file(path)
                raw = next((e for e in parsed.entries if e.entry_id == entry_id), None)
                if raw is None:
                    continue
                tombstone_id = "TOMB-" + secrets.token_hex(4)
                key = semantic_key or raw.fields.get("semantic_key") or entry_id
                md.remove_entry(path, entry_id)
                forgotten_path = path.parent / "forgotten.md"
                md.append_entry(forgotten_path, {
                    "schema_version": 1, "owner_user_id": self.owner.user_id,
                    "scope": raw.fields.get("scope", "project"),
                    "project_id": raw.fields.get("project_id", "null"),
                    "updated_at": utcnow(),
                }, "Forgotten memory", tombstone_id, {
                    "tombstone_id": tombstone_id,
                    "target_entry_id": entry_id,
                    "semantic_key": key,
                    "scope": raw.fields.get("scope", "project"),
                    "project_id": raw.fields.get("project_id", "null"),
                    "source_ids": _parse_list(raw.fields.get("source_ids", "")),
                    "forgotten_at": utcnow(),
                    "status": "active",
                }, "The fact above is suppressed. It must not be re-extracted from "
                   "old conversations without explicit user confirmation.")
                return {"tombstone_id": tombstone_id, "entry_id": entry_id,
                        "semantic_key": key, "status": "active",
                        "relative_path": str(forgotten_path.relative_to(self.root))}
        raise NotFound(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")

    # ------------------------------------------------------------ write paths
    def record_preference(self, *, key: str, value: str, summary: str,
                          source_ids: list[str], scope: str = "user",
                          project_id: str | None = None,
                          category: str = "preferences") -> MemoryEntry:
        if scope == "project" and not project_id:
            raise InvalidRequest("Project-scoped memory requires a project id.",
                                 code="MEMORY_SCOPE_INVALID")
        entry_id = "PREF-" + secrets.token_hex(3).upper()
        path = self.base / "long_term" / LONG_TERM_FILES.get(category,
                                                              "preferences.md")
        fields = {
            "entry_id": entry_id, "scope": scope,
            "project_id": project_id if scope == "project" else "null",
            "status": "user_confirmed", "source_ids": source_ids,
            "updated_at": utcnow(), "summary": summary, "key": key, "value": value,
            "semantic_key": f"pref:{key}",
        }
        md.append_entry(path, {"schema_version": 1,
                               "owner_user_id": self.owner.user_id,
                               "scope": "user", "project_id": None,
                               "updated_at": utcnow()},
                        category.title(), entry_id, fields, summary)
        return self.read(entry_id)

    def record_conversation(self, session_id: str, project_id: str,
                            messages: Iterable[dict[str, Any]]) -> None:
        folder = self.session_dir(session_id)
        path = folder / "conversation.md"
        if not path.exists():
            md.write_text_atomic(path, md.new_file_text({
                "schema_version": 1, "owner_user_id": self.owner.user_id,
                "scope": "project", "project_id": project_id,
                "updated_at": utcnow(),
            }, "Conversation"))
        for message in messages:
            message_id = message.get("message_id") or "message_" + secrets.token_hex(4)
            md.append_entry(path, {
                "schema_version": 1, "owner_user_id": self.owner.user_id,
                "scope": "project", "project_id": project_id, "updated_at": utcnow(),
            }, "Conversation", message_id, {
                "entry_id": message_id, "role": message.get("role", "user"),
                "run_id": message.get("run_id", ""), "at": message.get("at", utcnow()),
                "scope": "project", "project_id": project_id,
            }, message.get("text", ""))

    def record_working(self, session_id: str, project_id: str, summary: str,
                       bullets: dict[str, str]) -> None:
        folder = self.session_dir(session_id)
        path = folder / "working.md"
        fields = {"entry_id": "WORKING", "scope": "project", "project_id": project_id,
                  "status": "active", "updated_at": utcnow(),
                  "source_ids": ["current_session"]}
        fields.update(bullets)
        if not path.exists():
            md.write_text_atomic(path, md.new_file_text({
                "schema_version": 1, "owner_user_id": self.owner.user_id,
                "scope": "project", "project_id": project_id, "updated_at": utcnow(),
            }, "Working summary"))
            md.append_entry(path, {}, "Working summary", "WORKING", fields, summary)
        else:
            md.replace_entry(path, "WORKING", fields, summary)

    def record_experience(self, project_id: str, *, title: str, conditions: str,
                          observation: str, resolution: str, evidence: str,
                          source_ids: list[str], module_id: str | None = None,
                          module_version: int | None = None,
                          status: str = "candidate",
                          evidence_run_id: str | None = None,
                          evidence_revision_id: str | None = None) -> MemoryEntry:
        lesson_id = "LESSON-" + secrets.token_hex(3).upper()
        path = self.base / "experience" / f"{lesson_id}.md"
        fields = {
            "schema_version": 1, "lesson_id": lesson_id, "entry_id": lesson_id,
            "owner_user_id": self.owner.user_id, "scope": "project",
            "project_id": project_id, "status": status,
            "source_ids": source_ids, "updated_at": utcnow(),
            "module_id": module_id or "", "module_version": module_version or 1,
            "library_snapshot_id": "mota_builtin_v1",
            "evidence_run_id": evidence_run_id or "",
            "evidence_revision_id": evidence_revision_id or "",
            "summary": title,
            "semantic_key": f"lesson:{module_id or 'general'}:{title.casefold()[:40]}",
        }
        body = (f"## Conditions\n{conditions}\n\n## Observation\n{observation}\n\n"
                f"## Resolution\n{resolution}\n\n## Evidence\n{evidence}\n")
        md.append_entry(path, {"schema_version": 1, "owner_user_id": self.owner.user_id,
                               "scope": "project", "project_id": project_id,
                               "updated_at": utcnow()},
                        title, lesson_id, fields, body)
        index = self.base / "experience" / "index.md"
        if not index.exists():
            index.write_text("# Experience index\n\n", encoding="utf-8")
        with index.open("a", encoding="utf-8") as fh:
            fh.write(f"- {lesson_id} — {title} (status: {status})\n")
        return self.read(lesson_id)


def _parse_list(text: str) -> list[str]:
    text = (text or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip() for part in text.split(",") if part.strip()]
