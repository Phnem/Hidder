"""TICKET-24: extract MCHOSE's keyboard command table and parser schemas.

The find that shapes this tool: MCHOSE's keyboard protocol is **table driven**.
`purify.es-BGo9zI_u.js` carries `hpe`, an array of `[name, {default:{...}}]`
pairs where each entry holds a **frame template as a space-separated hex
string** for the wired and the wireless transport, plus a named parser for each
direction. `oy = new mpe(hpe)` wraps it, and `oy.getCommandConfig(name)` is what
the transport consults before every exchange.

That means two things, and the second is the reason this tool exists:

*   The request half needs no inference at all. A template is the frame, and the
    only unknowns are the fields the app overwrites before sending.
*   The **response reading procedure is data too** -- the parsers are
    declarative field schemas (`{name, type, sub, deep}`), not code. Playbook
    trap S-4 asks for the response reader to be extracted as its own IR node
    rather than folded into the request codec; here the vendor has already
    separated them, and the job is to not re-merge them.

What this tool does NOT do is decide what any command means. A template is
recovered exactly as written; every byte whose purpose is not stated by the
vendor's own parser stays unnamed. Naming them from position is trap S-2/S-3.

Output is normalised facts (templates, field schemas, names), never vendor code:
`data/README.md` forbids committing the bundle, and precedent for recording
extracted protocol facts is `docs/prior-art/aula-bytech-*.md`.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from miner.static.js_extract_fn import balanced_from

_HPE = re.compile(r"\bhpe\s*=\s*\[")
_ENTRY = re.compile(r'\["([A-Za-z0-9_]+)"\s*,\s*\{')
# Templates are written as template literals, often with an embedded
# `${"00 ".repeat(N).trim()}` tail. Capture the literal and the repeat count
# separately so the true frame length is a computed fact, not a guess.
_TEMPLATE = re.compile(
    r'(wiredCommand|wirelessCommand)\s*:\s*[`"]([^`"$]*)'
    r'(?:\$\{"00 "\.repeat\((\d+)\)\.trim\(\)\})?'
)
_PARSER = re.compile(r"(wiredParser|wirelessParser)\s*:\s*([A-Za-z_$][\w$.]*)")


def _frame(literal: str, repeat: str | None) -> dict:
    head = [b for b in literal.strip().split(" ") if b]
    total = len(head) + (int(repeat) if repeat else 0)
    return {
        "template_head_hex": head,
        "zero_tail_count": int(repeat) if repeat else 0,
        # The report id is the first byte of the template; the wire payload is
        # what follows. Both are reported because conflating them is trap I-1,
        # which cost this project a `candidate` fingerprint on three boards.
        "report_id_hex": head[0] if head else None,
        "frame_bytes_including_report_id": total,
        "payload_bytes": total - 1 if total else 0,
    }


def extract(text: str) -> list[dict]:
    m = _HPE.search(text)
    if not m:
        raise SystemExit("hpe table not found -- bundle changed? re-run mchose_version_check")
    body, ok = balanced_from(text, m.end() - 1, cap=400_000)
    if not ok:
        raise SystemExit("hpe table did not close within cap; refusing to emit a truncated table")

    out: list[dict] = []
    for e in _ENTRY.finditer(body):
        chunk, _ = balanced_from(body, e.end() - 1, cap=20_000)
        rec: dict = {"command": e.group(1), "transports": {}, "parsers": {}}
        for t in _TEMPLATE.finditer(chunk):
            kind = "wired" if t.group(1).startswith("wired") else "wireless"
            rec["transports"][kind] = _frame(t.group(2), t.group(3))
        for p in _PARSER.finditer(chunk):
            rec["parsers"]["wired" if p.group(1).startswith("wired") else "wireless"] = p.group(2)
        if "getWirelessYY" in chunk:
            yy = re.search(r"getWirelessYY\s*:\s*(\([^)]*\)\s*=>\s*\{[^}]*\})", chunk)
            rec["get_wireless_yy_verbatim"] = yy.group(1) if yy else "<present, not captured>"
        out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    cmds = extract(text)

    groups: dict[str, list[str]] = {}
    for c in cmds:
        w = c["transports"].get("wired", {})
        head = w.get("template_head_hex") or []
        key = " ".join(head[:2]) if len(head) >= 2 else "<none>"
        groups.setdefault(key, []).append(c["command"])

    doc = {
        "_what": "MCHOSE keyboard command table, extracted verbatim from the vendor's own table",
        "_source_note": (
            "`hpe` in purify.es-BGo9zI_u.js, wrapped as `oy = new mpe(hpe)` and consulted via "
            "`oy.getCommandConfig(name)`. Templates are the vendor's, byte for byte."
        ),
        "_not_claimed": (
            "No byte is named here beyond what the vendor's own field schema names. Field "
            "meaning, GET/SET pairing and risk class are NOT decided by this extraction."
        ),
        "command_count": len(cmds),
        "wired_report_ids": sorted({
            c["transports"].get("wired", {}).get("report_id_hex")
            for c in cmds if c["transports"].get("wired")
        } - {None}),
        "wireless_report_ids": sorted({
            c["transports"].get("wireless", {}).get("report_id_hex")
            for c in cmds if c["transports"].get("wireless")
        } - {None}),
        "wired_frame_sizes": sorted({
            c["transports"]["wired"]["frame_bytes_including_report_id"]
            for c in cmds if c["transports"].get("wired")
        }),
        "wireless_frame_sizes": sorted({
            c["transports"]["wireless"]["frame_bytes_including_report_id"]
            for c in cmds if c["transports"].get("wireless")
        }),
        "wired_leading_pair_groups": groups,
        "commands": cmds,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"commands              : {len(cmds)}")
    print(f"wired report ids      : {doc['wired_report_ids']}")
    print(f"wireless report ids   : {doc['wireless_report_ids']}")
    print(f"wired frame sizes     : {doc['wired_frame_sizes']}")
    print(f"wireless frame sizes  : {doc['wireless_frame_sizes']}")
    print("leading wired pair -> commands:")
    for k, v in sorted(groups.items()):
        print(f"  {k:<10} {', '.join(v)}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
