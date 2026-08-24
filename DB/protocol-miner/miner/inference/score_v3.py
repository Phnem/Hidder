"""Open the answer key and score a frozen run.  Playbook v4 SS1.3, steps 5-6.

This is the only program in the v3 pipeline permitted to read
ground_truth/truth.json, and it refuses to read it at all until the freeze
verifies.  Verification covers more than `score.py`'s did:

* the manifest's own sha256, against the digest written beside it, so a
  manifest cannot be rewritten to agree with a drifted tree;
* every hashed engine and scorer source, this file included;
* the datasets, so a corpus rebuild cannot be slipped under a frozen score;
* the frozen predictions.

There is deliberately no `--allow-drift`.  `score.py` has one, and a gate with
a documented bypass is not a gate.

The metric definitions are imported from `score.py` rather than restated, so a
v1 number and a v3 number mean the same thing.

Run:  python -m miner.inference.score_v3 --label v3_engine_only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

from .score import score_mode

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"
RESULTS = CORPUS / "results"
TRUTH = CORPUS / "ground_truth" / "truth.json"

#: Playbook v4 SS3.2.  HIGH_CONFIDENCE_WRONG is the one that cannot be relaxed:
#: the others say how much we know, that one says whether we lie confidently.
GATE = {
    "HIGH_CONFIDENCE_WRONG": ("==", 0),
    "EXACT_PACKET_MATCH": (">=", 0.9),
    "BYTE_ACCURACY": (">=", 0.999),
    "FIELD_OFFSET_ACCURACY": (">=", 0.95),
    "ENDIANNESS_ACCURACY": ("==", 1.0),
    "CHECKSUM_RECOVERY": ("is", True),
}


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class FreezeViolation(SystemExit):
    pass


def verify_freeze(frozen: pathlib.Path):
    mpath = frozen / "MANIFEST.sha256.json"
    dpath = frozen / "MANIFEST.digest.txt"
    problems = []
    if not mpath.exists():
        raise FreezeViolation(f"FREEZE VIOLATION: no manifest in {frozen}")
    if not dpath.exists():
        raise FreezeViolation(f"FREEZE VIOLATION: no manifest digest in {frozen}")
    if sha256(mpath) != dpath.read_text(encoding="utf-8").strip():
        raise FreezeViolation(
            "FREEZE VIOLATION: the manifest does not match its own digest")

    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    for f, h in manifest["engine_files"].items():
        src = HERE / f
        if not src.exists():
            problems.append(f"hashed source {f} is gone")
        elif sha256(src) != h:
            problems.append(f"source {f} changed since the freeze")
    for name, spec in manifest["datasets"].items():
        ds = CORPUS / spec["path"]
        if not ds.exists():
            problems.append(f"dataset {spec['path']} is gone")
        elif sha256(ds) != spec["sha256"]:
            problems.append(f"dataset {spec['path']} changed since the freeze")
    for name, h in manifest["predictions"].items():
        p = frozen / f"predictions_{name}.json"
        if not p.exists():
            problems.append(f"predictions_{name}.json is gone")
        elif sha256(p) != h:
            problems.append(f"predictions_{name}.json changed since the freeze")
    if problems:
        raise FreezeViolation("FREEZE VIOLATION:\n  " + "\n  ".join(problems))
    return manifest


def _packet_match(v):
    a, b = str(v).split("/")
    return float(a) / float(b) if float(b) else 0.0


def _value(metric, s):
    v = s[metric]
    return _packet_match(v) if metric == "EXACT_PACKET_MATCH" else v


def gate_verdict(scores, gate_modes):
    """GREEN only if every gate criterion holds in every gate mode."""
    failures = []
    for mode in gate_modes:
        s = scores[mode]
        for metric, (op, want) in GATE.items():
            got = _value(metric, s)
            ok = (got == want if op in ("==", "is")
                  else got is not None and got >= want)
            if not ok:
                failures.append({"mode": mode, "metric": metric,
                                 "required": f"{op} {want}", "observed": s[metric]})
    return {"verdict": "GREEN" if not failures else "RED",
            "gate_modes": list(gate_modes), "failures": failures,
            "criteria": {k: f"{v[0]} {v[1]}" for k, v in GATE.items()}}


BLIND_CAVEAT = ("blind here means no algorithm changed after the count was taken "
                "- not that no intermediate value was ever seen")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args(argv)

    frozen = BENCH / f"frozen_{a.label}"
    manifest = verify_freeze(frozen)
    print(f"freeze verified: {frozen.name} "
          f"({manifest['engine_module']}, {len(manifest['engine_files'])} sources)")

    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    RESULTS.mkdir(parents=True, exist_ok=True)

    out = {
        "label": a.label,
        "engine_module": manifest["engine_module"],
        "manifest_sha256": (frozen / "MANIFEST.digest.txt").read_text(
            encoding="utf-8").strip(),
        "freeze_verified": True,
        "blind_caveat": BLIND_CAVEAT,
        "mode_C_note": ("C_PARTIAL_PROTOCOL is handed a redacted copy of the family "
                        "schema. It is reported, never submitted."),
        "modes": {},
    }
    for name in manifest["predictions"]:
        pred = json.loads((frozen / f"predictions_{name}.json").read_text(
            encoding="utf-8"))
        out["modes"][name] = score_mode(pred, truth)

    out["gate"] = gate_verdict(out["modes"], manifest["gate_modes"])

    dest = RESULTS / f"scores_{a.label}.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    for name, s in out["modes"].items():
        tag = "GATE" if name in manifest["gate_modes"] else "not a gate submission"
        print(f"\n=== {name}  [{tag}] ===")
        for k in ("FIELD_OFFSET_ACCURACY", "FIELD_TYPE_ACCURACY",
                  "ENDIANNESS_ACCURACY", "CHECKSUM_RECOVERY",
                  "EXACT_PACKET_MATCH", "BYTE_ACCURACY", "UNKNOWN_BYTE_RATE",
                  "WRONG_BYTES", "HIGH_CONFIDENCE_WRONG"):
            print(f"  {k:24s} {s[k]}")
        if s["high_confidence_wrong_detail"]:
            for d in s["high_confidence_wrong_detail"]:
                print(f"    HCW {d['check']}: predicted {d['predicted']} "
                      f"expected {d['expected']} at {d['confidence']:.2f}")
        if s["failed_checks"]:
            print("  failed:", [f["check"] for f in s["failed_checks"]])

    print(f"\nGATE: {out['gate']['verdict']}")
    for f in out["gate"]["failures"]:
        print(f"  {f['mode']} {f['metric']}: required {f['required']}, "
              f"observed {f['observed']}")
    print("wrote", dest)
    return out


if __name__ == "__main__":
    main()
