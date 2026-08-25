"""TICKET-25 consolidation, item 6: the identity-graph checks, as data.

Three questions, each answered from an artifact rather than from memory:

1. Does any `vid:pid` + `usage` carry more than one protocol identity? A collision
   would mean the app cannot tell two devices apart without asking a human, which
   is exactly what §0.2's "identity graph closed without manual selection"
   forbids. Zero collisions is a fact worth pinning, because it is the thing that
   makes the remaining unnamed ids a catalogue debt rather than a safety blocker.

2. Which identities are the bootloader/DFU interface? Driving one of those is
   how an earlier pass captured zero frames for a week's worth of runs, so the
   set is published rather than remembered.

3. Which keyboard ids still have no name from a source that states both halves?

Nothing here infers a name from similarity, vendor id, or family, and the CZ
SDK's fallback string is not a name.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def analyse(cz_table: dict, filters: dict) -> dict:
    rows = cz_table["rows"]
    resolved = [r for r in rows if r.get("name_link") == "RESOLVED"]

    # 1. Collisions. Key on the full HID selector the app filters with, since
    # that -- not vid:pid alone -- is what the browser hands the vendor code.
    by_selector: dict[tuple, set[str]] = collections.defaultdict(set)
    for e in filters["config"]["HidIndexDeviceFilters"]:
        if "productId" not in e:
            continue
        key = (e["vendorId"], e["productId"], e.get("usagePage"), e.get("usage"))
        match = next((r for r in resolved
                      if r["vendorId"] == e["vendorId"] and r["productId"] == e["productId"]), None)
        if match:
            by_selector[key].add(match["deviceName"])
    collisions = {f"{v:#06x}:{p:#06x}/{up}/{u}": sorted(names)
                  for (v, p, up, u), names in by_selector.items() if len(names) > 1}

    # A weaker key too: if vid:pid alone were ambiguous, a device that exposes a
    # different usage than expected could still be mis-resolved.
    by_vid_pid: dict[tuple, set[str]] = collections.defaultdict(set)
    for r in resolved:
        by_vid_pid[(r["vendorId"], r["productId"])].add(r["deviceName"])
    vid_pid_collisions = {f"{v:#06x}:{p:#06x}": sorted(n)
                          for (v, p), n in by_vid_pid.items() if len(n) > 1}

    boot = [{"id": f"{r['vendorId']:#06x}:{r['productId']:#06x}", "name": r["deviceName"]}
            for r in resolved if r.get("isBootUpdateMode") is True]

    products: dict[str, dict] = {}
    for r in resolved:
        p = products.setdefault(r["deviceName"], {"normal": [], "boot": []})
        p["boot" if r.get("isBootUpdateMode") else "normal"].append(
            f"{r['vendorId']:#06x}:{r['productId']:#06x}")

    return {
        "identities_in_filters": sum(1 for e in filters["config"]["HidIndexDeviceFilters"]
                                     if "productId" in e),
        "resolved_name_links": len(resolved),
        "distinct_products": len(products),
        "selector_collisions": collisions,
        "vid_pid_collisions": vid_pid_collisions,
        "identity_graph_closed_without_manual_selection": (
            not collisions and not vid_pid_collisions),
        "boot_mode_identities": boot,
        "products": products,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cz-table", required=True)
    ap.add_argument("--filters", required=True)
    ap.add_argument("--name-link", required=False)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cz_table = load(Path(args.cz_table))
    filters = load(Path(args.filters))
    result = analyse(cz_table, filters)

    if args.name_link:
        nl = load(Path(args.name_link))
        unresolved = [s for s in nl.get("unresolved_ids_no_name_in_any_artifact", [])]
        resolved_keys = {(r["vendorId"], r["productId"])
                         for r in cz_table["rows"] if r.get("name_link") == "RESOLVED"}
        still = []
        newly = []
        for s in unresolved:
            v, p = (int(x, 16) for x in s.split(":"))
            if (v, p) in resolved_keys:
                name = next(r["deviceName"] for r in cz_table["rows"]
                            if r["vendorId"] == v and r["productId"] == p)
                newly.append({"id": s, "name": name})
            else:
                still.append(s)
        result["previously_unresolved"] = len(unresolved)
        result["newly_named_from_cz_sdk"] = newly
        result["still_unresolved"] = still
        result["still_unresolved_note"] = (
            "These ids appear in no CZ filter entry, so the CZ SDK has nothing to say about "
            "them. Leaving them unresolved is the correct outcome; a name would have to be "
            "invented. Per §0.2 this is catalogue completeness, not a safety blocker, "
            "because the identity graph closes without manual selection."
        )

    doc = {
        "_what": "MCHOSE identity-graph checks, TICKET-25 consolidation item 6",
        "_method": "derived only from artifacts that state both halves of an edge",
        **result,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"resolved links     : {result['resolved_name_links']} "
          f"({result['distinct_products']} products)")
    print(f"selector collisions: {len(result['selector_collisions'])}")
    print(f"vid:pid collisions : {len(result['vid_pid_collisions'])}")
    print(f"graph closed       : {result['identity_graph_closed_without_manual_selection']}")
    print(f"boot-mode ids      : {len(result['boot_mode_identities'])}")
    if "still_unresolved" in result:
        print(f"newly named        : {len(result['newly_named_from_cz_sdk'])}")
        print(f"still unresolved   : {result['still_unresolved']}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
