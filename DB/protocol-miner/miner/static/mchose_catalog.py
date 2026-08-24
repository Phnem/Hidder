"""TICKET-23 step 3: extract the MCHOSE device catalogue from the M HUB Web bundle.

The vendor's catalogue entries are object literals anchored on a `productKey`
of the shape `"<PRODUCT NAME>$$$<vendorId>$$$<productId>"`. Every entry seen so
far also carries `deviceType`, `vendorId`, `productId` and `productName`, and
audio entries additionally carry `audioRuntimeType`.

Two deliberate choices:

*   **Anchor on `productKey`, then read the enclosing braces**, rather than
    matching a fixed field order. A first pass used one rigid regex with the
    fields in the order they happened to appear in the audio table; it found 19
    entries in `main-BNV9-yBD.js` where `productKey` occurs 80 times, i.e. it
    silently dropped three quarters of that file's catalogue. A completeness
    criterion cannot be checked by a matcher whose failure mode is silence, so
    this one reports its own misses.

*   **`productKey` is parsed as the source of truth for vid/pid and cross-checked
    against the sibling fields**, because it encodes them redundantly. Any
    disagreement is a finding about the vendor's own data, not something to
    normalise away -- `aula-bytech`'s catalogue turned out to contain two rows
    sharing a display name and two sharing a uuid, and quietly de-duplicating
    them would have hidden a real ambiguity.

Nothing here decides which entries are in scope; that is ADR-0003's job.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

# The identity anchor is the triple-string itself, WHEREVER it occurs -- not the
# `productKey:` field.
#
# This was the second attempt. The first anchored on `productKey\s*:\s*"..."`,
# which is how the audio table in `main-BNV9-yBD.js` is written, and it found
# 19 entries there and *zero* in `purify.es-BGo9zI_u.js` despite that chunk
# containing 210 occurrences of the word `productKey` and 136 of the `$$$`
# separator. The reason is structural: in `purify.es` the same strings appear as
# KEYS of configuration maps (`{"MCHOSE G20$$$3690$$$273": {...}}`) and as
# comparison literals (`n.productKey === "MCHOSE K7 Ultra$$$14391$$$4097"`),
# while `productKey` itself is a computed runtime property
# (`e.productKey || [e.vendorId, e.productId, e.productName].join("-")`).
#
# Assuming the audio table's shape generalised to the keyboard/mouse tables
# would have under-reported the catalogue by construction while looking like a
# clean result -- the exact failure the playbook calls trap S-1.
_ID_TRIPLE = re.compile(r'"([^"$]*?)\$\$\$(\d+)\$\$\$(\d+)"')

# Kept as a secondary signal: where an entry IS a full object literal, it also
# carries deviceType and friends, which the bare string cannot give us.
_PRODUCT_KEY_FIELD = re.compile(r'productKey\s*:\s*"([^"$]*?)\$\$\$(\d+)\$\$\$(\d+)"')

_FIELD = {
    "deviceType": re.compile(r'deviceType\s*:\s*([A-Za-z_$][\w$]*\.[A-Z_0-9]+|"[^"]*")'),
    "vendorId": re.compile(r"vendorId\s*:\s*(\d+)"),
    "productId": re.compile(r"productId\s*:\s*(\d+)"),
    "productName": re.compile(r'productName\s*:\s*"([^"]*)"'),
    "audioRuntimeType": re.compile(r'audioRuntimeType\s*:\s*([A-Za-z_$][\w$]*\.[A-Z_0-9]+|"[^"]*")'),
}


def _enclosing_object(text: str, at: int, span: int = 900) -> str:
    """Text of the object literal containing `at`, found by brace balance.

    Falls back to a fixed window if the braces do not balance inside `span`
    (minified code with nested literals); the field regexes are anchored on
    their own names, so a slightly wide window costs precision, not silence.
    """
    start = text.rfind("{", max(0, at - span), at)
    if start == -1:
        return text[max(0, at - span) : at + span]
    depth = 0
    for i in range(start, min(len(text), start + 2 * span)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start : start + span]


def extract(path: Path) -> tuple[list[dict], int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    anchors = list(_ID_TRIPLE.finditer(text))
    # Compare on the NAME group, not on the whole match: the two patterns start
    # at different places (`"` vs `productKey`), so comparing `.span()` silently
    # never matched and every entry lost its deviceType.
    field_anchored = {m.span(1) for m in _PRODUCT_KEY_FIELD.finditer(text)}
    out: list[dict] = []
    for m in anchors:
        is_field = m.span(1) in field_anchored
        rec: dict = {
            "source_chunk": path.name,
            "product_key_name": m.group(1),
            "product_key_vid": int(m.group(2)),
            "product_key_pid": int(m.group(3)),
            # How the string was written tells us how much to expect from it:
            # a full `productKey:` field sits in an object with deviceType and
            # friends; a bare literal is a map key or a comparison and carries
            # identity only.
            "occurrence": "productKey_field" if is_field else "bare_literal",
        }
        if is_field:
            obj = _enclosing_object(text, m.start())
            for field, pat in _FIELD.items():
                f = pat.search(obj)
                if f:
                    v = f.group(1)
                    rec[field] = int(v) if v.isdigit() else v.strip('"')
        out.append(rec)
    return out, len(anchors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blobs", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    blobs = Path(args.blobs)
    occurrences: list[dict] = []
    per_chunk: dict[str, dict] = {}
    for js in sorted(blobs.glob("*.js")):
        recs, anchors = extract(js)
        if anchors:
            per_chunk[js.name] = {"id_triple_occurrences": anchors, "parsed": len(recs)}
            occurrences.extend(recs)

    # One identity may be written many times (a map key per colour, a
    # comparison literal, and a catalogue field all name the same product).
    # Collapse to unique identities, keeping the richest record for each and
    # recording where it was seen -- the occurrence count is itself a hint at
    # how much per-product special-casing the vendor does.
    merged: dict[tuple[str, int, int], dict] = {}
    for rec in occurrences:
        key = (rec["product_key_name"], rec["product_key_vid"], rec["product_key_pid"])
        cur = merged.get(key)
        if cur is None:
            cur = {k: v for k, v in rec.items() if k != "source_chunk"}
            cur["seen_in_chunks"] = {}
            cur["occurrence_kinds"] = set()
            merged[key] = cur
        cur["seen_in_chunks"][rec["source_chunk"]] = cur["seen_in_chunks"].get(rec["source_chunk"], 0) + 1
        cur["occurrence_kinds"].add(rec["occurrence"])
        # A field-anchored sighting carries deviceType etc.; let it win.
        for k, v in rec.items():
            if k not in ("source_chunk", "occurrence") and k not in cur:
                cur[k] = v
    for cur in merged.values():
        cur.pop("occurrence", None)
        cur["occurrence_kinds"] = sorted(cur["occurrence_kinds"])
    entries = list(merged.values())

    # Disagreements between productKey's embedded ids and the sibling fields.
    conflicts = [
        e for e in entries
        if ("vendorId" in e and e["vendorId"] != e["product_key_vid"])
        or ("productId" in e and e["productId"] != e["product_key_pid"])
    ]

    by_type = collections.Counter(str(e.get("deviceType", "<unparsed>")) for e in entries)
    vids = collections.Counter(e["product_key_vid"] for e in entries)

    # Same (vid,pid) reachable under more than one product name: the vendor
    # cannot tell those apart by identity either. This is the fact that makes
    # a product id an index rather than an answer.
    by_vidpid: dict[tuple[int, int], set[str]] = collections.defaultdict(set)
    for e in entries:
        by_vidpid[(e["product_key_vid"], e["product_key_pid"])].add(e["product_key_name"])
    ambiguous = {f"{v:#06x}:{p:#06x}": sorted(n) for (v, p), n in by_vidpid.items() if len(n) > 1}

    doc = {
        "_what": "MCHOSE device catalogue extracted from M HUB Web, TICKET-23",
        "_caveat": (
            "This is the catalogue the shipped bundle carries on the crawl date. It is a "
            "subset of what the vendor supports over time -- the storefront says 'More "
            "supported devices coming soon'. Absence from this table is never evidence "
            "that a device is unsupported."
        ),
        "entry_count": len(entries),
        "per_chunk": per_chunk,
        "device_type_histogram": dict(by_type.most_common()),
        "vendor_ids": {f"{v:#06x}": n for v, n in sorted(vids.items())},
        "product_key_vs_fields_conflicts": conflicts,
        "vid_pid_shared_by_multiple_names": ambiguous,
        "entries": sorted(entries, key=lambda e: (str(e.get("deviceType")), e["product_key_name"])),
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"entries        : {len(entries)}")
    for chunk, s in per_chunk.items():
        flag = "" if s["id_triple_occurrences"] == s["parsed"] else "  <-- MISSED SOME"
        print(f"  {chunk:<32} anchors={s["id_triple_occurrences"]:<5} parsed={s["parsed"]}{flag}")
    print(f"device types   : {dict(by_type.most_common())}")
    print(f"vendor ids     : {[f'{v:#06x}' for v in sorted(vids)]}")
    print(f"key/field conflicts : {len(conflicts)}")
    print(f"ambiguous vid:pid   : {len(ambiguous)}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
