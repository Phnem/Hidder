"""TICKET-25 priority 2: link keyboard `vid:pid` to product names, or admit it cannot.

TICKET-23 left 27 keyboard identities known without names: the config centre's
keyboard sources are id-keyed and carry no marketing name, the storefront lists
names and no ids, and nothing in either bridges them.

This tool bridges only what an artifact actually states, and records WHICH
artifact states it. It deliberately does not:

*   match on name similarity (`"Ace 68 GT"` vs `"Ace 68"` is not evidence),
*   infer from VID (0x3837 spans keyboards and, elsewhere, other classes),
*   infer from family or from adjacency in a table.

Everything it cannot state from a source is emitted as `unresolved`, because an
identity graph is the thing a write path is authorised against, and a wrong edge
there points a command at the wrong device.

Sources, strongest first:

| source | form | strength |
|---|---|---|
| `cardList` | `identities:[{vendorId,productId,usagePage,usage}]` + `desc` | states both halves in one record |
| `keyboardConfig` | key `0x3837_0x3033_MCHOSE G87 V2 2.4G` | states both halves in one key |
| `keyboardPreset` | key `0x3837_0x2020:117` | id only -- contributes population, never a name |
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_ID_ONLY = re.compile(r"^(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)(?::(.+))?$")
_ID_NAMED = re.compile(r"^(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)_(.+)$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configcenter", required=True)
    ap.add_argument("--merged-catalog", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cc = Path(args.configcenter)

    def load(n):
        p = cc / f"{n}.decoded.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    card_list = (load("cardList") or {}).get("data", [])
    kb_config = load("keyboardConfig") or {}
    kb_preset = load("keyboardPreset") or {}

    links: dict[str, dict] = {}

    def add(vid: str, pid: str, name: str, source: str, verbatim: str):
        key = f"{vid.lower()}:{pid.lower()}"
        rec = links.setdefault(key, {"vendor_id": vid.lower(), "product_id": pid.lower(), "names": []})
        rec["names"].append({"name": name, "source": source, "verbatim_key_or_field": verbatim})

    for card in card_list:
        desc = card.get("desc")
        for i in card.get("identities", []):
            add(i["vendorId"], i["productId"], desc, "configCenter/cardList.identities+desc", desc)

    for key in kb_config:
        m = _ID_NAMED.match(key)
        if m:
            add(m.group(1), m.group(2), m.group(3), "configCenter/keyboardConfig key", key)

    # Population: every keyboard id we know of, named or not.
    population: set[str] = set()
    for key in kb_preset:
        m = _ID_ONLY.match(key)
        if m:
            population.add(f"{m.group(1).lower()}:{m.group(2).lower()}")
    for key in kb_config:
        m = _ID_NAMED.match(key) or _ID_ONLY.match(key)
        if m:
            population.add(f"{m.group(1).lower()}:{m.group(2).lower()}")
    population |= set(links)

    # An id with two different stated names is AMBIGUOUS, not "probably the
    # first one". Recorded as such per the ticket's own instruction.
    resolved, ambiguous = {}, {}
    for k, rec in links.items():
        distinct = {n["name"] for n in rec["names"]}
        (resolved if len(distinct) == 1 else ambiguous)[k] = rec

    unresolved = sorted(population - set(links))

    doc = {
        "_what": "MCHOSE keyboard vid:pid <-> product name links, TICKET-25 priority 2",
        "_method": (
            "Only links an artifact states outright, with the stating artifact recorded. "
            "No similarity matching, no VID inference, no family inference. An id with two "
            "stated names is ambiguous, not resolved to the likelier one."
        ),
        "population_size": len(population),
        "resolved_count": len(resolved),
        "ambiguous_count": len(ambiguous),
        "unresolved_count": len(unresolved),
        "resolved": resolved,
        "ambiguous": ambiguous,
        "unresolved_ids_no_name_in_any_artifact": unresolved,
        "_why_unresolved_stays_unresolved": (
            "The app shows a product name for a CONNECTED device. That is an observation "
            "available only with a device (real or emulated) attached, and it is the only "
            "route to these edges that does not involve guessing."
        ),
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"population : {len(population)}")
    print(f"resolved   : {len(resolved)}")
    for k, r in sorted(resolved.items()):
        print(f"   {k}  ->  {r['names'][0]['name']}   [{r['names'][0]['source']}]")
    print(f"ambiguous  : {len(ambiguous)}")
    for k, r in sorted(ambiguous.items()):
        print(f"   {k}  ->  {sorted({n['name'] for n in r['names']})}")
    print(f"unresolved : {len(unresolved)}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
