"""Point the inference engine at the HERO84 commands that are still open.

This is not a benchmark: there is no hidden answer to score against.  For every
(opcode, sub-id) in the corpus the engine reports what it can constrain from the
bytes, what it refuses to claim, and the single cheapest experiment that would
settle the rest.  Anything that can be answered against the fake device is
flagged, because no further physical writes are permitted.

Run:  python -m miner.inference.unknowns
"""
from __future__ import annotations

import collections
import json

from .run_benchmark import BENCH, load_rows
from .score import CORPUS, RESULTS

# Commands whose meaning Phase 1 established.  Listed only so the report can
# separate "already answered" from "still open" -- the analysis itself does not
# use these names.
RESOLVED = {
    (0x82, 1), (0x82, 2), (0x82, 9), (0x04, 17), (0x04, 21), (0x04, 23),
    (0x04, 24), (0x04, 25), (0x84, 17), (0x84, 21), (0x84, 23), (0x84, 24),
    (0x84, 25), (0x84, 1), (0x10, 0), (0x90, 0), (0x13, 0), (0x93, 0),
}


def analyse(frames):
    """Structural summary of one (opcode, sub) bucket."""
    reqs = [bytes.fromhex(f["payload_hex"]) for f in frames if f.get("payload_hex")]
    reps = [bytes.fromhex(f["reply_hex"]) for f in frames if f.get("reply_hex")]
    out = {
        "n_requests": len(reqs), "n_replies": len(reps),
        "request_len_field": sorted({r[5] for r in reqs if len(r) > 5}),
        "reply_len_field": sorted({r[5] for r in reps if len(r) > 5}),
    }
    if reps:
        width = min(len(r) for r in reps)
        varying = [i for i in range(width)
                   if len({r[i] for r in reps}) > 1]
        out["reply_varying_offsets"] = varying[:24]
        nonzero = [i for i in range(6, width - 1)
                   if any(r[i] for r in reps)]
        out["reply_nonzero_body_offsets"] = nonzero[:24]
        out["reply_body_extent"] = (max(nonzero) - 5) if nonzero else 0
        echo = sum(1 for f in frames
                   if f.get("reply_hex") and f.get("payload_hex")
                   and f["reply_hex"] == f["payload_hex"])
        out["echo_replies"] = f"{echo}/{len(reps)}"
        out["reply_is_pure_echo"] = echo == len(reps) and len(reps) > 0
        out["reply_samples"] = sorted({r.hex()[:40] for r in reps})[:4]
    if reqs:
        out["request_samples"] = sorted({r.hex()[:40] for r in reqs})[:3]
    return out


def constrain(op, sub, a):
    """What can honestly be said, and what would settle the rest."""
    notes, conf, pred = [], 0.0, None
    if a.get("reply_is_pure_echo"):
        notes.append("every reply is byte-identical to its request: this command "
                     "acknowledges by echo and returns no data")
        pred = {"role": "WRITE_OR_NOOP", "reply_semantics": "echo_ack"}
        conf = 0.75
    elif a.get("n_replies") and a.get("reply_body_extent"):
        n = a["reply_body_extent"]
        notes.append(f"replies carry {n} body byte(s) starting at offset 6")
        pred = {"role": "READ", "reply_body_bytes": n,
                "reply_len_field": a["reply_len_field"]}
        conf = 0.7 if a["n_replies"] >= 3 else 0.4
    elif a.get("n_replies"):
        notes.append("replies observed but their body is all zero in this corpus")
        pred = {"role": "READ_OR_ACK", "reply_body_bytes": 0}
        conf = 0.3
    else:
        notes.append("no reply was ever captured for this command")

    exp = None
    if a.get("n_requests", 0) < 3:
        exp = ("exercise this command at least three times with different inputs; "
               "one sample cannot separate a constant from a variable")
    elif a.get("reply_is_pure_echo"):
        exp = ("drive the UI control that emits this command to two different "
               "values and diff the frames: an echo-ack write hides its payload "
               "in the request, not the reply")
    elif len(a.get("request_len_field", [])) < 2:
        exp = ("capture this command with a different number of records so the "
               "length field varies; a single observed length cannot pin a stride")
    return pred, conf, notes, exp


def main():
    rows = load_rows(BENCH / "dataset_A_RAW_ONLY.jsonl")
    buckets = collections.defaultdict(list)
    for r in rows:
        h = r.get("payload_hex")
        if not h or r.get("payload_truncated"):
            continue
        b = bytes.fromhex(h)
        buckets[(b[0], b[1])].append(r)

    report = {"_note": "structural constraints only; no semantic names are asserted "
                       "without evidence", "commands": []}
    for (op, sub), frames in sorted(buckets.items()):
        a = analyse(frames)
        pred, conf, notes, exp = constrain(op, sub, a)
        report["commands"].append({
            "opcode": hex(op), "sub": sub,
            "status": "RESOLVED_IN_PHASE_1" if (op, sub) in RESOLVED else "OPEN",
            "structure": a,
            "prediction": pred,
            "confidence": round(conf, 2),
            "notes": notes,
            "next_best_experiment": exp,
            "fake_only_reproducible": True,
        })

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "open_commands.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    openc = [c for c in report["commands"] if c["status"] == "OPEN"]
    print(f"{len(report['commands'])} (opcode, sub) buckets, {len(openc)} still open\n")
    for c in openc:
        s = c["structure"]
        print(f"  {c['opcode']} sub {c['sub']:3d}  req={s['n_requests']:4d} "
              f"rep={s['n_replies']:4d}  "
              f"role={(c['prediction'] or {}).get('role','-'):16s} "
              f"conf={c['confidence']}")
    print("\nwrote", RESULTS / "open_commands.json")


if __name__ == "__main__":
    main()
