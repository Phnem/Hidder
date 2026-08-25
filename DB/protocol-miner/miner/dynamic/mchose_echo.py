"""TICKET-25 point 3: tell a real device answer apart from everything that
merely looks like one.

Four things can put bytes in front of a decoder, and only one of them is
evidence about a device:

| class | what it is | is it evidence? |
|---|---|---|
| `DEVICE_RESPONSE` | an inputreport whose content is not derivable from the request | yes |
| `ECHO` | the request handed back | **no** -- carries zero bits about the device |
| `UI_LOCAL_STATE` | a value the page computed and never asked the device for | no |
| `SYNTHESIZED` | a reply this harness (or an emulator) invented | no, and dangerous |

`EVIDENCE_VOID` is the verdict for a (command, source) pair whose replies are
all non-evidence. Per playbook §1.1 it does not mean "discard the frames" -- the
TX side stays valid -- it means **any hypothesis about reply semantics resting
only on those frames is `UNTESTABLE`, never `SUPPORTED`.**

## Why the comparison is over the whole body

The predecessor of this detector, on `aula-bytech`, compared the *data section
of the reply bounded by the reply's declared length* against the request
payload. For a command whose request payload is empty that compares nothing with
nothing and returns true for any frame content, so two of three commands
promoted on 2026-08-24 were falsely called echoes
(playbook §1.2a). This one compares the whole body and never consults a declared
length -- which is doubly right for MCHOSE, whose vendor reader does not consult
one either (TICKET-24).

## Why an empty-payload echo is classified `UNKNOWN`, not `ECHO`

§1.2b: a reply that is an echo of an empty request is indistinguishable from
"nothing to report". Calling it `ECHO` invents a protocol fact; calling it
`SAFE` invents a safety fact. It is `UNKNOWN`, and blockers are not built on it.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def zero_pad(hexstr: str, width_bytes: int) -> str:
    return (hexstr or "").ljust(width_bytes * 2, "0")


def classify(request_hex: str | None, reply_hex: str | None, reply_origin: str) -> tuple[str, str]:
    """(classification, reason). `reply_origin` is where the bytes came from."""
    if reply_origin == "vendor_schema":
        # Built by us from the vendor's own envelope schema so the client would
        # accept it. It is the strongest non-evidence there is, and that is
        # exactly why it needs its own class: it is the one most likely to be
        # mistaken later for an observation, because the client believed it.
        return ("SYNTHETIC_FROM_VENDOR_SCHEMA",
                "built by this harness from the vendor's schema; the client accepting it "
                "says nothing about any device")
    if reply_origin == "harness":
        return "SYNTHESIZED", "this harness produced the bytes; nothing about a device"
    if reply_origin == "ui_state":
        return "UI_LOCAL_STATE", "value read from page state, no device exchange observed"
    if not reply_hex:
        return "NO_REPLY", "device produced nothing"

    req = (request_hex or "").lower()
    rep = reply_hex.lower()

    if not req:
        # §1.2b -- an echo of nothing is indistinguishable from nothing to say.
        return "UNKNOWN", "request payload empty; echo and 'no information' are indistinguishable"

    width = max(len(req), len(rep)) // 2
    if zero_pad(req, width) == zero_pad(rep, width):
        return "ECHO", "reply body equals the zero-padded request body"

    # A reply that only differs in a length byte is still an echo: changing a
    # declared length must not change the verdict.
    if len(req) == len(rep):
        diffs = [i for i in range(0, len(req), 2) if req[i:i + 2] != rep[i:i + 2]]
        if len(diffs) == 1:
            return "ECHO", f"reply differs from request in exactly one byte (offset {diffs[0]//2})"
        # A reply that reproduces most of the request carries at most the few
        # bytes that differ. Calling that a device response credits the device
        # with everything the request already said.
        if 0 < len(diffs) <= max(2, len(req) // 16):
            return ("PARTIAL_ECHO",
                    f"reply reproduces the request except at {len(diffs)} byte(s): "
                    f"{[d // 2 for d in diffs][:8]}")

    return "DEVICE_RESPONSE", "reply body is not derivable from the request"


# The ONLY class that counts as evidence about a device. Everything else --
# echoes, partial echoes, page state, and replies this harness built from the
# vendor's schema -- is excluded, however convincing the client found it.
EVIDENCE_BEARING = {"DEVICE_RESPONSE"}
NON_EVIDENCE = {
    "ECHO", "PARTIAL_ECHO", "UI_LOCAL_STATE", "SYNTHESIZED",
    "SYNTHETIC_FROM_VENDOR_SCHEMA", "NO_REPLY", "UNKNOWN",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="jsonl from mchose_oracle")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = []
    p = Path(args.frames)
    if p.exists():
        if p.suffix == ".jsonl":
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        else:
            # A UI-walk inventory. Same frames, grouped by the action that
            # produced them; the audit reads them flat and keeps the label.
            doc = json.loads(p.read_text(encoding="utf-8"))
            for action, entries in (doc.get("by_action") or {}).items():
                for e in entries:
                    rows.append({**e, "ui_action": action})

    per_command: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    classified = []
    for r in rows:
        # This harness answers nothing by construction, so any reply present is
        # either absent (the normal case) or came from us -- both non-evidence.
        # The oracle stamps how a reply was produced. Trust that stamp rather
        # than re-deriving it, so the audit cannot disagree with the capture.
        stamped = r.get("evidence_class")
        if stamped == "synthetic_from_vendor_schema":
            origin = "vendor_schema"
        elif r.get("reply_hex"):
            origin = "harness"
        else:
            origin = "none"
        cls, why = classify(r.get("payload_hex"), r.get("reply_hex"), origin)
        # Key by the CZ command byte where there is one: for this family every
        # frame rides report id 0, so keying by report id alone would collapse
        # the whole protocol into a single bucket and hide which commands were
        # exercised.
        # Re-derived here, never read from the capture: the capture is corpus and
        # must not carry our decoding of itself.
        command = None
        try:
            raw = bytes.fromhex(r.get("payload_hex") or "")
            if len(raw) >= 2 and raw[0] in (0x55, 0xAA):
                command = raw[1]
        except ValueError:
            pass
        key = (f"rid{r.get('report_id')}/cmd{command:#04x}"
               if command is not None else f"rid{r.get('report_id')}")
        per_command[key][cls] += 1
        classified.append({**r, "evidence_class": cls, "reason": why})

    verdicts = {}
    for key, counts in per_command.items():
        total = sum(counts.values())
        bearing = sum(n for c, n in counts.items() if c in EVIDENCE_BEARING)
        verdicts[key] = {
            "frames": total,
            "evidence_bearing": bearing,
            "classes": dict(counts),
            "verdict": "EVIDENCE_VOID" if bearing == 0 else "USABLE",
            "consequence": (
                "any hypothesis about this command's reply semantics is UNTESTABLE, not SUPPORTED"
                if bearing == 0
                else "reply semantics may be argued from these frames"
            ),
        }

    by_action: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in classified:
        by_action[r.get("ui_action", "<none>")][r["evidence_class"]] += 1

    doc = {
        "_what": "MCHOSE echo / EVIDENCE_VOID audit, TICKET-25 point 3",
        "_per_action": {k: dict(v) for k, v in sorted(by_action.items())},
        "_source": str(p),
        "_harness_note": (
            "This oracle answers nothing by design, so a fully EVIDENCE_VOID result is the "
            "EXPECTED outcome of a capture run and is not a defect. It records that the run "
            "established REQUEST structure only. Reply semantics need a source that replies."
        ),
        "frames": len(rows),
        "per_report_id": verdicts,
        "classified": classified,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"frames: {len(rows)}")
    for k, v in sorted(verdicts.items()):
        print(f"  {k:<10} {v['verdict']:<14} frames={v['frames']:<4} classes={v['classes']}")
    print(f"-> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
