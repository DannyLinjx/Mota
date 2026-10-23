"""Markdown memory files: parse, serialize, hash, tombstones.

Markdown in the user's workspace is the authority for semantic memory. No hidden
second memory store is created.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mota.domain.models import utcnow
from mota.errors import InvalidRequest
from mota.workspace.atomic import read_json, write_text_atomic

ENTRY_RE = re.compile(r"^## ([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)
BULLET_RE = re.compile(r"^- ([a-z_]+):\s*(.*)$")


@dataclass
class RawEntry:
    entry_id: str
    fields: dict[str, str] = field(default_factory=dict)
    body: str = ""
    path: Path | None = None
    text: str = ""

    @property
    def hash(self) -> str:
        payload = self.text if self.text else self.body
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class MemoryFile:
    frontmatter: dict[str, Any]
    title: str
    entries: list[RawEntry]
    raw: str


def parse_memory_file(path: Path) -> MemoryFile:
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    frontmatter: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise InvalidRequest(f"Invalid frontmatter in {path.name}: {exc}",
                                     code="MEMORY_INVALID")
            body = parts[2].lstrip("\n")
    title = ""
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        lines = lines[1:]
    text = "\n".join(lines)
    entries: list[RawEntry] = []
    matches = [m for m in ENTRY_RE.finditer(text) if _is_entry_heading(text, m)]
    for index, match in enumerate(matches):
        entry_id = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip("\n")
        fields: dict[str, str] = {}
        body_lines: list[str] = []
        in_body = False
        for line in chunk.splitlines():
            bullet = BULLET_RE.match(line.strip())
            if bullet and not in_body:
                fields[bullet.group(1)] = bullet.group(2).strip()
            elif line.strip() == "" and fields and not in_body:
                in_body = True
            elif in_body:
                body_lines.append(line)
        entries.append(RawEntry(entry_id=entry_id, fields=fields,
                                body="\n".join(body_lines).strip(),
                                path=path, text=chunk))
    return MemoryFile(frontmatter=frontmatter, title=title, entries=entries, raw=raw)


def _is_entry_heading(text: str, match: re.Match[str]) -> bool:
    """A heading is an entry only when its section starts with a bullet field.

    Prose sub-headings inside an entry body (for example `## Conditions`) must
    stay part of that entry.
    """
    rest = text[match.end():]
    for line in rest.splitlines():
        if not line.strip():
            continue
        return BULLET_RE.match(line.strip()) is not None
    return False


def new_file_text(frontmatter: dict[str, Any], title: str) -> str:
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n# {title}\n\n"


def entry_text(entry_id: str, fields: dict[str, Any], body: str) -> str:
    lines = [f"## {entry_id}", ""]
    for key, value in fields.items():
        if value is None:
            value = ""
        if isinstance(value, list):
            rendered = "[" + ", ".join(str(v) for v in value) + "]"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    lines.append("")
    if body:
        lines.extend(body.splitlines())
        lines.append("")
    return "\n".join(lines)


def append_entry(path: Path, frontmatter: dict[str, Any], title: str, entry_id: str,
                 fields: dict[str, Any], body: str) -> RawEntry:
    if not path.exists():
        write_text_atomic(path, new_file_text(frontmatter, title))
    existing = parse_memory_file(path)
    if any(e.entry_id == entry_id for e in existing.entries):
        raise InvalidRequest(f"Memory entry {entry_id} already exists.", code="MEMORY_EXISTS")
    text = path.read_text(encoding="utf-8").rstrip() + "\n\n" + entry_text(entry_id, fields, body)
    write_text_atomic(path, text)
    parsed = parse_memory_file(path)
    for entry in parsed.entries:
        if entry.entry_id == entry_id:
            return entry
    raise InvalidRequest("Entry write failed.", code="MEMORY_WRITE_FAILED")


def _entry_spans(raw: str) -> dict[str, tuple[int, int]]:
    """Map entry id to (start, end) offsets in the raw file.

    Only headings whose section starts with a bullet field are entries; prose
    sub-headings stay inside the preceding entry.
    """
    spans: list[tuple[str, int, int]] = []
    for match in ENTRY_RE.finditer(raw):
        rest = raw[match.end():]
        first_line = next((line for line in rest.splitlines() if line.strip()), "")
        if not BULLET_RE.match(first_line.strip()):
            continue
        spans.append((match.group(1), match.start(), match.start()))
    out: dict[str, tuple[int, int]] = {}
    for index, (entry_id, start, _) in enumerate(spans):
        end = spans[index + 1][1] if index + 1 < len(spans) else len(raw)
        out[entry_id] = (start, end)
    return out


def replace_entry(path: Path, entry_id: str, fields: dict[str, Any], body: str) -> RawEntry:
    parsed = parse_memory_file(path)
    target = next((e for e in parsed.entries if e.entry_id == entry_id), None)
    if target is None:
        raise InvalidRequest(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")
    raw = path.read_text(encoding="utf-8")
    span = _entry_spans(raw).get(entry_id)
    if span is None:
        raise InvalidRequest(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")
    start, end = span
    replacement = entry_text(entry_id, fields, body)
    if end < len(raw):
        replacement = replacement.rstrip() + "\n\n"
    raw = raw[:start] + replacement + raw[end:]
    write_text_atomic(path, raw)
    parsed = parse_memory_file(path)
    for entry in parsed.entries:
        if entry.entry_id == entry_id:
            return entry
    raise InvalidRequest("Entry update failed.", code="MEMORY_WRITE_FAILED")


def remove_entry(path: Path, entry_id: str) -> RawEntry:
    parsed = parse_memory_file(path)
    target = next((e for e in parsed.entries if e.entry_id == entry_id), None)
    if target is None:
        raise InvalidRequest(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")
    raw = path.read_text(encoding="utf-8")
    span = _entry_spans(raw).get(entry_id)
    if span is None:
        raise InvalidRequest(f"Memory entry {entry_id} not found.", code="MEMORY_NOT_FOUND")
    start, end = span
    write_text_atomic(path, raw[:start] + raw[end:])
    return target


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
