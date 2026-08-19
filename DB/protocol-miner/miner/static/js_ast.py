"""Small structural JavaScript parser for function bodies used by static data-flow.

It intentionally parses only function/arrow-function boundaries, but does so
with quote/comment-aware brace matching rather than a regex crossing arbitrary
nested code. Statement/expression values remain source text evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FunctionNode:
    name: str
    parameters: str
    body: str
    start: int


def _skip_quoted(source: str, index: int) -> int:
    quote = source[index]
    index += 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    return index


def _match(source: str, index: int, opening: str, closing: str) -> int | None:
    depth = 0
    while index < len(source):
        char = source[index]
        if char in "'\"`":
            index = _skip_quoted(source, index)
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline == -1 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end == -1 else end + 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _identifier(source: str, index: int) -> tuple[str, int]:
    while index < len(source) and source[index].isspace():
        index += 1
    start = index
    while index < len(source) and (source[index].isalnum() or source[index] in "_$"):
        index += 1
    return source[start:index], index


def functions(source: str) -> list[FunctionNode]:
    result: list[FunctionNode] = []
    index = 0
    while index < len(source):
        if source[index] in "'\"`":
            index = _skip_quoted(source, index)
            continue
        if source.startswith("function", index) and (index == 0 or not (source[index - 1].isalnum() or source[index - 1] in "_$")):
            name, cursor = _identifier(source, index + len("function"))
            if not name:
                index += len("function")
                continue
            cursor = next((position for position in range(cursor, len(source)) if not source[position].isspace()), len(source))
            if cursor >= len(source) or source[cursor] != "(":
                index = cursor
                continue
            params_end = _match(source, cursor, "(", ")")
            if params_end is None:
                break
            brace = params_end + 1
            while brace < len(source) and source[brace].isspace():
                brace += 1
            body_end = _match(source, brace, "{", "}") if brace < len(source) and source[brace] == "{" else None
            if body_end is not None:
                result.append(FunctionNode(name, source[cursor + 1:params_end], source[brace + 1:body_end], index))
                index = body_end + 1
                continue
        index += 1
    return result
