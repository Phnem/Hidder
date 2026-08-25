"""TICKET-25 item 7: the destructive-path inventory, regenerated from artifacts.

The CZ section is DERIVED from a UI-walk capture rather than typed, so it cannot
drift from the frames it describes. The BY section is static facts with their
own provenance and is carried unchanged: no CZ observation may alter a BY
classification, and vice versa. Five families, no shared rules.

`NO_DESTRUCTIVE_PATH_FOUND` is never emitted. Over an incomplete walk it
describes coverage, not the device.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

# Facts established statically in TICKET-24/25 for the BY keyboard family
# (`hpe` table, `navigator.deviceHandler`). Nothing in the CZ lane touches these.
BY_FAMILY = {
    "transport": "navigator.deviceHandler.sendCommand, hpe command table (M HUB purify.es)",
    "coverage": ("driven live through the native Vue configurator against a fake K99: wired "
                 "reads 0x87/0x83/0x84/0x8a observed, setPerformance and setReset captured in "
                 "full; no hardware involved"),
    "paths": [
        {
            "name": "setReset",
            "classification": "DESTRUCTIVE_CONFIRMED",
            "why": ("traced from the restoreFactorySetting UI action through the dispatcher to "
                    "sendCommand('set','setReset',...), and its full 519-byte wired frame is "
                    "captured -- produced by the vendor's own confirmation dialog against a "
                    "fake device, so nobody had to send one to a keyboard"),
            "frame_captured": True,
            "evidence": "analysis/by_0x04_identity_k99.json",
            "sequence": ("the confirm handler emits 0x04 (setReset) and then 0x0a, 0x06 and "
                         "three 0x03 frames; only the first is setReset"),
        },
        {
            "name": "setPerformance",
            "classification": "POTENTIALLY_DESTRUCTIVE",
            "classification_is_final": True,
            "why": ("same wired lead byte 0x04 as setReset and built by literally the same "
                    "parser function j_(!0); nothing in the frame shape separates them"),
            "wire_level_discriminator": "PROVEN IMPOSSIBLE",
            "proof": (
                "A routine setPerformance frame and the factory-reset frame were captured in "
                "one session through the vendor's own UI and are BYTE-IDENTICAL across all 519 "
                "bytes. The reset's data is the constant x8[model].otherObj -- the factory "
                "default performance record -- and that value is reachable from the settings "
                "UI, because it is simply what a factory-fresh keyboard reports. Toggling one "
                "switch off and back on reproduced it exactly."
            ),
            "evidence": "analysis/by_0x04_identity_k99.json; regression in "
                        "tests/test_mchose_by_0x04_indistinguishable.py",
            "consequence": (
                "No classifier reading a wired 0x04 frame can separate a settings write from a "
                "factory reset. Safety for this path CANNOT be enforced by frame inspection; it "
                "has to be enforced at the point of intent -- which command is being issued -- "
                "and a captured 0x04 frame must never be replayed."
            ),
            "requires_hardware": False,
        },
        {
            "name": "setKeySetting / setMacro / setDiyLight / setLightColor",
            "classification": "UNKNOWN",
            "why": "distinct lead bytes (0x03/0x05/0x06/0x10), no captured frame, no known inverse",
        },
    ],
}

OTA_FAMILY = {
    "transport": "report id 77, preamble [170,85,170,85,170,85], HEADER_LENGTH 7",
    "coverage": "static only",
    "paths": [{
        "name": "firmware transfer",
        "classification": "DESTRUCTIVE_CONFIRMED",
        "why": "TYPE_SYNC_INFO / TYPE_TRANSFORM / TYPE_DONE flash sequence in the CZ SDK",
        "note": "never exercised; out of scope by standing instruction",
    }],
}

# Command names the vendor's own channel methods give, for the CZ 0x55 family.
# Source: CZ_SHARED_DATA/main.<hash>.js, `Channel:Keyboard`.
CZ_COMMAND_NAMES = {
    0x03: "getInfo",
    0x04: "getBase",
    0x05: "getFuncConfig",
    0x07: "getKeyMatrix(default)",
    0x08: "getKeyMatrix(user)",
    0x0E: "setBase",
}


def build_cz(walk: dict, sweeps: dict | None) -> dict:
    per_cmd: dict[int, dict] = {}
    for action, rows in (walk.get("by_action") or {}).items():
        for r in rows:
            d = r.get("decoded")
            if not d or d.get("command") is None:
                continue
            e = per_cmd.setdefault(d["command"], {
                "frames": 0, "offsets": set(), "classes": collections.Counter(),
                "actions": set(), "any_nonzero_trailing": False,
            })
            e["frames"] += 1
            e["offsets"].add(d["offset"])
            e["classes"][r["safety_class"]] += 1
            e["actions"].add(action)
            if not d.get("trailing_is_all_zero"):
                e["any_nonzero_trailing"] = True

    swept: dict[int, list] = collections.defaultdict(list)
    for sw in (sweeps or {}).get("sweeps", []):
        for g in sw.get("groups", []):
            if g.get("verdict") != "FIELDS_OBSERVED":
                continue
            cmd = int(g["command"], 16)
            for f in g.get("fields", []):
                swept[cmd].append({
                    "tab": sw.get("tab"), "record_offset": f.get("record_offset"),
                    "encoding": f.get("encoding"),
                })

    paths = []
    for cmd in sorted(per_cmd):
        e = per_cmd[cmd]
        if e["any_nonzero_trailing"]:
            cls = "POTENTIALLY_DESTRUCTIVE"
            why = ("non-zero bytes after the 8-byte header; the vendor's read builder never "
                   "emits those, so this is a write. What it writes is unknown, and an "
                   "unexplained write is not a safe one.")
            direction = "WRITE"
        else:
            cls = "UNKNOWN"
            why = ("every observed frame has an all-zero trailing region, which the envelope "
                   "cannot distinguish from a write of zeros")
            direction = "UNKNOWN"
        paths.append({
            "command": f"{cmd:#04x}",
            "vendor_method": CZ_COMMAND_NAMES.get(cmd),
            "frames": e["frames"],
            "distinct_offsets": len(e["offsets"]),
            "direction": direction,
            "classification": cls,
            "why": why,
            "ui_actions": sorted(e["actions"])[:6],
            "fields_located_by_sweep": swept.get(cmd) or None,
        })

    counts = collections.Counter(p["classification"] for p in paths)
    return {
        "transport": "CZ SDK envelope, report id 0, 64-byte packet, leading 0x55",
        "coverage": walk.get("ui_inventory_coverage"),
        "frames_captured": walk.get("frames_total"),
        "structural_finding": {
            "claim": "a CZ read and a CZ write of an all-zero payload are BYTE-IDENTICAL",
            "why": ("byte 4 carries 'bytes requested' in _simpleGetCommand and 'bytes supplied' "
                    "in _simpleFullSendCommand; the frame does not distinguish them"),
            "asymmetry": ("The inference runs one way only. The read builder never places bytes "
                          "after the 8-byte header, so a non-zero trailing region proves a "
                          "WRITE. An all-zero trailing region proves nothing."),
            "same_shape_as": ("the BY wired-0x04 ambiguity -- two independent families, two "
                              "independent instances of the same trap, neither informing the other"),
        },
        "command_number_collision_warning": (
            "CZ command 0x04 is getBase. BY's wired lead byte 0x04 is setPerformance/setReset. "
            "The number is a coincidence across two unrelated families and carries no connection."
        ),
        "counts": dict(counts),
        "paths": paths,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--walk", required=True)
    ap.add_argument("--sweeps", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    walk = json.loads(Path(args.walk).read_text(encoding="utf-8"))
    sweeps = json.loads(Path(args.sweeps).read_text(encoding="utf-8")) if args.sweeps else None
    cz = build_cz(walk, sweeps)

    doc = {
        "_what": "MCHOSE destructive-path inventory, TICKET-25",
        "_rule": ("UNKNOWN is never promoted to SAFE. A path nobody has explained is not a path "
                  "nobody needs to explain."),
        "_families_are_not_merged": (
            "keyboard/BY, keyboard/CZ, mouse receiver, AA-55 OTA on report id 77 are tracked "
            "separately and no rule crosses between them. AULA contributes nothing to any."),
        "_no_destructive_path_found": (
            "NOT ISSUED for any family. Coverage is incomplete and that verdict over incomplete "
            "coverage describes the walk, not the device (trap O-4)."),
        "_derivation": {
            "keyboard/CZ": f"derived from {Path(args.walk).name}"
                           + (f" and {Path(args.sweeps).name}" if args.sweeps else ""),
            "keyboard/BY": "static facts, carried unchanged",
        },
        "families": {
            "keyboard/BY": BY_FAMILY,
            "keyboard/CZ": cz,
            "ota/aa55": OTA_FAMILY,
            "mouse_receiver": {
                "transport": "64-byte buffer, XOR checksum, 16-bit LE command id",
                "coverage": "static only; out of scope for the keyboard track",
                "paths": [],
            },
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"CZ commands   : {len(cz['paths'])}")
    print(f"CZ classes    : {cz['counts']}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
