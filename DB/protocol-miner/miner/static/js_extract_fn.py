"""Pull a named function / binding out of a minified bundle, verbatim.

Playbook v4 trap S-1: "в комментарии к декодеру цитируй строку исходника
дословно. Если в комментарии проза, а не код — это не evidence." That rule is
unenforceable without a way to lift the exact bytes of a definition out of a
7 MB single-line file, which is what this does.

Brace/paren/bracket balanced, string- and regex-aware enough for minified
output: it tracks quotes and escapes so a `}` inside a string literal does not
end a body early. It does NOT parse JavaScript -- when balance cannot be
resolved inside the cap it says so rather than returning a plausible-looking
truncation, because a half-quoted "verbatim" citation is worse than none.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_OPEN = {"{": "}", "(": ")", "[": "]"}
_CLOSE = {v: k for k, v in _OPEN.items()}


def balanced_from(text: str, start: int, cap: int = 200_000) -> tuple[str, bool]:
    """Text from `start` through the close of the first bracket opened at/after it."""
    i = start
    depth = 0
    began = False
    quote: str | None = None
    while i < min(len(text), start + cap):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            i += 1
            continue
        if c in _OPEN:
            depth += 1
            began = True
        elif c in _CLOSE:
            depth -= 1
            if began and depth == 0:
                return text[start : i + 1], True
        i += 1
    return text[start : start + cap], False


def find_definitions(text: str, name: str) -> list[tuple[int, str]]:
    """Every plausible definition site for `name`, minified-friendly."""
    pats = [
        rf"\bfunction\s+{re.escape(name)}\s*\(",          # function f(...)
        rf"\b{re.escape(name)}\s*=\s*function\b",          # f=function
        rf"\b{re.escape(name)}\s*=\s*async\s+function\b",
        rf"\bconst\s+{re.escape(name)}\s*=",               # const f=
        rf"\b{re.escape(name)}\s*=\s*\(",                  # f=(a,b)=>
        rf"\b{re.escape(name)}\s*=\s*async\s*\(",
        rf"\basync\s+function\s+{re.escape(name)}\s*\(",
        rf"\b{re.escape(name)}\s*\([^)]*\)\s*\{{",         # method shorthand
    ]
    hits: list[tuple[int, str]] = []
    for p in pats:
        for m in re.finditer(p, text):
            hits.append((m.start(), p))
    hits.sort()
    deduped: list[tuple[int, str]] = []
    for pos, p in hits:
        if not deduped or pos - deduped[-1][0] > 4:
            deduped.append((pos, p))
    return deduped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--name", action="append", required=True)
    ap.add_argument("--cap", type=int, default=6000)
    ap.add_argument("--max-hits", type=int, default=3)
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    for name in args.name:
        defs = find_definitions(text, name)
        print(f"\n{'='*78}\n### {name}  ({len(defs)} definition site(s))\n{'='*78}")
        for pos, pat in defs[: args.max_hits]:
            body, ok = balanced_from(text, pos, cap=args.cap)
            # An arrow function's FIRST balanced group is its parameter list --
            # and with destructured params that group is `({a,b})`, so stopping
            # there yields a signature and no body. Continue through `=>` into
            # the body, which is the half that carries the evidence.
            tail = text[pos + len(body) :]
            m = re.match(r"\s*=>\s*", tail)
            if m:
                nxt, ok2 = balanced_from(text, pos + len(body) + m.end(), cap=args.cap)
                body = body + m.group(0) + nxt
                ok = ok and ok2
            flag = "" if ok else "   [UNBALANCED WITHIN CAP - TRUNCATED, NOT A CITATION]"
            print(f"\n-- at offset {pos} via {pat}{flag}")
            print(body[: args.cap * 2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
