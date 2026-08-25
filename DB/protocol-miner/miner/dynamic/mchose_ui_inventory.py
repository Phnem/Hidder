"""TICKET-25 point 2: the UI action inventory.

Builds, for every UI action driven through the fake-WebHID oracle:

    UI action -> dispatcher -> command name -> full encoded frame
              -> transport family -> read/write -> destructive classification

and, just as importantly, the list of actions **found but not performed**.
Playbook trap O-4: `NO_DESTRUCTIVE_PATH_FOUND` issued while that list is
non-empty is an artifact of coverage, not a fact. This tool therefore refuses to
emit that verdict itself; it emits the coverage numbers a person needs to see
before considering it.

Classification vocabulary, and the one rule that matters:

    SAFE_READ               a read whose frame matches a known read template
    REVERSIBLE_WRITE        a write with an established inverse
    POTENTIALLY_DESTRUCTIVE a write that cannot be distinguished from a
                            destructive one by frame shape
    DESTRUCTIVE_CONFIRMED   traced to a destructive action
    UNKNOWN                 anything else

**UNKNOWN never becomes SAFE.** A frame nobody has explained is not a frame
nobody needs to explain, and the default has to be the one that costs a person
time rather than a user their keyboard.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

# From TICKET-24's extraction (kb_command_table.json). Read templates lead with
# report id 0x06 and a command byte with bit 7 set; writes lead with a bare
# report id. Kept as data here rather than re-derived.
WIRED_READ_LEAD = "06"
KNOWN_WIRED_WRITE_LEADS = {
    "03": ["setKeySetting"],
    "04": ["setPerformance", "setReset"],   # <- the ambiguous pair
    "05": ["setMacro"],
    "06": ["setDiyLight"],
    "10": ["setLightColor"],
}
AMBIGUOUS_WIRED_LEADS = {"04"}


def classify_frame(report_id, payload_hex: str) -> tuple[str, str, list[str]]:
    """(classification, transport_family_guess, candidate command names)."""
    rid = f"{int(report_id):02x}" if report_id is not None else "??"
    body = (payload_hex or "").lower()

    if rid == WIRED_READ_LEAD and len(body) >= 2:
        second = body[0:2]
        if second and int(second, 16) & 0x80:
            return "SAFE_READ", "keyboard/wired", [f"read group 0x{second}"]

    if rid in KNOWN_WIRED_WRITE_LEADS:
        names = KNOWN_WIRED_WRITE_LEADS[rid]
        if rid in AMBIGUOUS_WIRED_LEADS:
            # setPerformance and setReset share BOTH the leading byte and the
            # parser (TICKET-25 priority 1). Nothing in the frame's shape tells
            # them apart, so every frame here inherits the worse class.
            return "POTENTIALLY_DESTRUCTIVE", "keyboard/wired", names
        return "UNKNOWN", "keyboard/wired", names

    return "UNKNOWN", "unclassified", []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--actions-found-not-performed", default="")
    args = ap.parse_args()

    rows = []
    p = Path(args.frames)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))

    per_action: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        cls, fam, names = classify_frame(r.get("report_id"), r.get("payload_hex"))
        per_action[r.get("ui_action", "<none>")].append(
            {
                "seq": r["seq"],
                "method": r.get("method"),
                "report_id": r.get("report_id"),
                "payload_hex": r.get("payload_hex"),
                "payload_len": r.get("payload_len"),
                "transport_family": fam,
                "candidate_commands": names,
                "classification": cls,
                "read_or_write": "read" if cls == "SAFE_READ" else ("write" if names else "unknown"),
            }
        )

    counts = collections.Counter(
        f["classification"] for frames in per_action.values() for f in frames
    )
    not_performed = [a for a in args.actions_found_not_performed.split(",") if a.strip()]

    doc = {
        "_what": "MCHOSE UI action inventory, TICKET-25 point 2",
        "_coverage_warning": (
            "NO_DESTRUCTIVE_PATH_FOUND is NOT emitted by this tool and must not be inferred "
            "from it. Trap O-4: that verdict over an incomplete action inventory is a "
            "statement about coverage, not about the device."
        ),
        "_unknown_is_not_safe": (
            "UNKNOWN frames are unexplained, not benign. They are counted separately and "
            "never folded into SAFE_READ."
        ),
        "actions_performed": sorted(per_action),
        "actions_found_but_not_performed": not_performed,
        "frames_total": len(rows),
        "classification_counts": dict(counts),
        "by_action": per_action,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"frames               : {len(rows)}")
    print(f"actions performed    : {len(per_action)}")
    print(f"classification counts: {dict(counts)}")
    print(f"found-not-performed  : {not_performed or '(none recorded)'}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
