"""TICKET-23 step 3 (final): merge every MCHOSE device source into one catalogue.

Four independent sources, none of them complete on its own, and the point of
merging them is precisely that their disagreements are visible afterwards:

| source | what it uniquely gives | what it lacks |
|---|---|---|
| bundle identity triples (`NAME$$$vid$$$pid`) | marketing names, audio deviceType | no usagePage/usage; only products needing per-product special-casing |
| config centre `cardList` | full HID identity tuples incl. usagePage/usage, and `webdriverEnum` | only the handful of "carded" products |
| config centre `keyboardPreset` | the real keyboard population, keyed vid_pid:firmware | no names, no usage |
| config centre `keyboardConfig` / `newMouseConfig` / `mouseConfig` | firmware manifests per model | mixed key shapes |
| storefront page | the marketing claim of what is supported | no ids at all |

The output deliberately does NOT collapse these into a single "device" record
with one authoritative name. Two of the sources key by `vid_pid`, two key by
marketing name, and the mapping between the two is not total -- pretending
otherwise would manufacture an identity graph that the vendor's own data does
not support. What is produced instead is: the union, per-source provenance for
every fact, and an explicit discrepancy section.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

_KP_KEY = re.compile(r"^(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+):(.+)$")
_KC_KEY = re.compile(r"^(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)(?:_(.*))?$")

# Read off the storefront's "Currently Supported Devices" paragraph
# (https://www.mchose.store/pages/mchose-hub, retrieved 2026-08-24). This is a
# marketing claim, not a protocol fact; it is here so the two can be compared.
STOREFRONT = {
    "mice": [
        "K7 V2 Series", "A7X Ultra", "K7 Ultra", "A7 Series", "M7 Series", "L7 Series",
        "G7 Series", "G3 A", "G3 Ultra", "G3 V2", "G3 V2 Pro", "A5 V2 Ultra", "A5 Ultra",
        "A5 V3", "AX5 V2",
    ],
    "hall_effect_keyboards": [
        "Ace 60 Series", "Ace 68 Series", "Ace 68 Air", "Ace 68 Turbo", "Ace 68 GT",
        "Jet 75 Series", "ZERO 75X", "Mix 87 Series",
    ],
    "mechanical_keyboards": [
        "UT98", "K99 Series", "G98 Series", "G87 Series", "K87S", "X75 Series",
        "GX87S", "KX75", "K87", "Z75S", "G75 Pro",
    ],
}


def _norm(name: str) -> str:
    """Loose key for comparing a marketing string with a catalogue string."""
    s = name.upper()
    s = s.replace("MCHOSE", "").replace("SERIES", "")
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle-catalog", required=True)
    ap.add_argument("--configcenter", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cc = Path(args.configcenter)
    bundle = json.loads(Path(args.bundle_catalog).read_text(encoding="utf-8"))

    def load(name):
        p = cc / f"{name}.decoded.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    card_list = (load("cardList") or {}).get("data", [])
    kb_preset = load("keyboardPreset") or {}
    kb_config = load("keyboardConfig") or {}
    new_mouse = load("newMouseConfig") or {}
    old_mouse = load("mouseConfig") or {}

    # --- identity axis: everything that names a (vid, pid) -------------------
    ids: dict[tuple[str, str], dict] = {}

    def touch(vid: str, pid: str) -> dict:
        k = (vid.lower(), pid.lower())
        return ids.setdefault(k, {"vendor_id": k[0], "product_id": k[1], "sources": {}})

    for card in card_list:
        for i in card.get("identities", []):
            rec = touch(i["vendorId"], i["productId"])
            rec["sources"].setdefault("cardList", []).append(
                {
                    "desc": card.get("desc"),
                    "usage_page": i.get("usagePage"),
                    "usage": i.get("usage"),
                    "priority": i.get("priority"),
                    "webdriver_enum": card.get("webdriverEnum"),
                }
            )
    for key in kb_preset:
        m = _KP_KEY.match(key)
        if m:
            rec = touch(m.group(1), m.group(2))
            rec["sources"].setdefault("keyboardPreset", []).append({"firmware": m.group(3)})
    for key in kb_config:
        m = _KC_KEY.match(key)
        if m:
            rec = touch(m.group(1), m.group(2))
            rec["sources"].setdefault("keyboardConfig", []).append({"suffix": m.group(3)})
    for e in bundle.get("entries", []):
        rec = touch(f"0x{e['product_key_vid']:04x}", f"0x{e['product_key_pid']:04x}")
        rec["sources"].setdefault("bundle", []).append(
            {"name": e["product_key_name"], "device_type": e.get("deviceType")}
        )

    # --- what the vendor's own driver-type discriminator says ----------------
    webdriver = collections.Counter(
        json.dumps(s["webdriver_enum"], sort_keys=True)
        for r in ids.values()
        for s in r["sources"].get("cardList", [])
        if s.get("webdriver_enum")
    )

    # A vendor config collection is the channel a configurator actually opens;
    # recording which (usagePage, usage) pairs exist is the first identity fact
    # any engine needs, and MCHOSE's differs from aula-bytech's.
    usages = collections.Counter(
        (s.get("usage_page"), s.get("usage"))
        for r in ids.values()
        for s in r["sources"].get("cardList", [])
    )

    # --- name axis: sources that key by marketing name, not by id ------------
    named_only = {
        "keyboardConfig": [k for k in kb_config if not _KC_KEY.match(k)],
        "newMouseConfig": list(new_mouse),
        "mouseConfig": [k for k in old_mouse if not k.isdigit()],
        "mouseConfig_numeric_pid_keys": [k for k in old_mouse if k.isdigit()],
        "bundle": sorted({e["product_key_name"] for e in bundle.get("entries", [])}),
    }

    # --- discrepancy: storefront claim vs anything we actually found ---------
    found_names = {_norm(n) for group in named_only.values() for n in group}
    discrepancies = {}
    for group, items in STOREFRONT.items():
        missing = [n for n in items if _norm(n) not in found_names]
        discrepancies[group] = {
            "claimed": len(items),
            "not_matched_in_any_artifact": missing,
            # An unmatched name is NOT evidence the storefront overstates support.
            # For keyboards it is mostly evidence of something else, and saying
            # so here stops the number being read as an accusation: every
            # keyboard source in the config centre (`keyboardPreset`,
            # `keyboardConfig`) is keyed by vid_pid and carries no marketing
            # name at all, so a keyboard name has almost nothing to match
            # against. The real finding is the missing edge, recorded below as
            # `name_to_id_mapping`.
            "why_unmatched_may_be_misleading": (
                "keyboard sources are id-keyed and nameless; a name has nothing to match"
                if "keyboard" in group
                else "mouse sources are partly name-keyed, so a miss here is more meaningful"
            ),
        }
    extra = sorted(
        {
            n
            for group in ("keyboardConfig", "newMouseConfig", "mouseConfig", "bundle")
            for n in named_only[group]
            if _norm(n)
            not in {_norm(x) for items in STOREFRONT.values() for x in items}
        }
    )

    doc = {
        "_what": "MCHOSE merged device catalogue, TICKET-23",
        "_caveat": (
            "A union with provenance, not a resolved identity graph. Sources key by "
            "(vid,pid) or by marketing name and the mapping between them is not total; "
            "collapsing them would invent an identity graph the vendor's data does not "
            "support. Absence here is never evidence a device is unsupported."
        ),
        "distinct_vid_pid": len(ids),
        "vendor_ids": dict(collections.Counter(r["vendor_id"] for r in ids.values())),
        "hid_usage_pairs_from_cardList": {f"{u[0]}:{u[1]}": n for u, n in usages.items() if u[0]},
        "webdriver_enum_histogram": dict(webdriver),
        "storefront_vs_artifacts": discrepancies,
        # The single most consequential gap this merge exposes. For keyboards
        # the software artifacts give ids without names and the storefront gives
        # names without ids, and nothing in either bridges them. Until it is
        # bridged, no keyboard vid:pid can be stated to BE a named product --
        # which is an identity-lane problem (the app resolves it at connect
        # time), not something to close by matching strings that look similar.
        "name_to_id_mapping": {
            "state": "OPEN",
            "keyboard_ids_without_names": sorted(
                f"{r['vendor_id']}:{r['product_id']}"
                for r in ids.values()
                if ("keyboardPreset" in r["sources"] or "keyboardConfig" in r["sources"])
                and "bundle" not in r["sources"]
                and "cardList" not in r["sources"]
            ),
            "closed_by": "observing the name the app shows for a connected device (TICKET-25)",
        },
        "named_in_artifacts_but_not_on_storefront": extra,
        "name_keyed_sources": named_only,
        "identities": sorted(ids.values(), key=lambda r: (r["vendor_id"], r["product_id"])),
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"distinct vid:pid          : {len(ids)}")
    print(f"vendor ids                : {doc['vendor_ids']}")
    print(f"hid usage pairs (cardList): {doc['hid_usage_pairs_from_cardList']}")
    print(f"webdriverEnum             : {doc['webdriver_enum_histogram']}")
    for g, v in discrepancies.items():
        print(f"storefront {g:<24} claimed={v['claimed']:<3} unmatched={len(v['not_matched_in_any_artifact'])}")
    print(f"in artifacts, not on storefront: {len(extra)}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
