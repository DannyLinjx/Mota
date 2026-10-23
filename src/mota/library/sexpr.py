"""A small S-expression reader with source offsets.

`sexpdata` (used by the legacy catalog scanner) does not expose offsets, which
makes it impossible to point at a broken token. This reader keeps the byte
offset of every node so diagnostics can report file, line and column.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass


class SexprError(ValueError):
    def __init__(self, message: str, offset: int, line: int = 1,
                 column: int = 1) -> None:
        super().__init__(f"{message} (line {line}, column {column})")
        self.message = message
        self.offset = offset
        self.line = line
        self.column = column


@dataclass(slots=True)
class Node:
    value: str
    offset: int
    is_list: bool
    children: tuple["Node", ...] = ()
    end: int = 0


class TextLocator:
    """Map a character offset back to a 1-based line/column pair."""

    __slots__ = ("text", "_newlines")

    def __init__(self, text: str) -> None:
        self.text = text
        self._newlines = [index for index, char in enumerate(text) if char == "\n"]

    def locate(self, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(self.text)))
        index = bisect_left(self._newlines, offset)
        line = index + 1
        line_start = self._newlines[index - 1] + 1 if index else 0
        return line, offset - line_start + 1


def parse(text: str) -> Node:
    stack: list[tuple[int, list[Node]]] = []
    root: Node | None = None
    index = 0
    length = len(text)
    locator = TextLocator(text)

    def append(node: Node) -> None:
        nonlocal root
        if stack:
            stack[-1][1].append(node)
            return
        if root is not None:
            raise SexprError("trailing expression after root node", node.offset,
                             *locator.locate(node.offset))
        root = node

    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
            continue
        if char == "(":
            stack.append((index, []))
            index += 1
            continue
        if char == ")":
            if not stack:
                raise SexprError("unexpected ')'", index, *locator.locate(index))
            start, children = stack.pop()
            node = Node(value="", offset=start, is_list=True,
                        children=tuple(children), end=index + 1)
            append(node)
            index += 1
            continue
        if char == '"':
            start = index
            value, index = _read_string(text, index, locator)
            append(Node(value=value, offset=start, is_list=False, end=index))
            continue
        start = index
        while index < length and text[index] not in ' \t\r\n()"':
            index += 1
        if start == index:
            raise SexprError(f"unexpected character {char!r}", index,
                             *locator.locate(index))
        append(Node(value=text[start:index], offset=start, is_list=False,
                    end=index))

    if stack:
        start = stack[-1][0]
        raise SexprError("unclosed '('", start, *locator.locate(start))
    if root is None:
        raise SexprError("empty expression", 0, 1, 1)
    return root


def _read_string(text: str, start: int, locator: TextLocator) -> tuple[str, int]:
    index = start + 1
    out: list[str] = []
    length = len(text)
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    while index < length:
        char = text[index]
        if char == "\\":
            if index + 1 >= length:
                break
            escaped = text[index + 1]
            out.append(escapes.get(escaped, escaped))
            index += 2
            continue
        if char == '"':
            return "".join(out), index + 1
        out.append(char)
        index += 1
    raise SexprError("unterminated string", start, *locator.locate(start))


def head(node: Node) -> str:
    if node.is_list and node.children and not node.children[0].is_list:
        return node.children[0].value
    return ""


def children_named(node: Node, name: str) -> list[Node]:
    if not node.is_list:
        return []
    return [child for child in node.children
            if child.is_list and head(child) == name]


def child(node: Node, name: str) -> Node | None:
    rows = children_named(node, name)
    return rows[0] if rows else None


def atoms(node: Node | None) -> list[Node]:
    """Non-list children after the head token.

    `(pin passive line ...)` yields `passive`, `line`; the head `pin` is not a
    value. This mirrors sexpdata's `node[1:]` convention.
    """
    if node is None or not node.is_list:
        return []
    return [item for item in node.children[1:] if not item.is_list]


def atom_value(node: Node | None, default: str = "") -> str:
    if node is None:
        return default
    values = atoms(node)
    return values[0].value if values else default


def atom_floats(node: Node | None) -> list[float]:
    out: list[float] = []
    for item in atoms(node) if node is not None else []:
        try:
            out.append(float(item.value))
        except ValueError:
            continue
    return out
