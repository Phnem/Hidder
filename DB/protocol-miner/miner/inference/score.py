"""Score frozen predictions against the sealed ground truth.

This program is the ONLY thing allowed to open ground_truth/truth.json, and it
runs strictly after benchmark/frozen/MANIFEST.sha256.json exists.  It verifies
the manifest first: if the engine or the predictions changed since freezing,
the score is refused rather than silently recomputed.

Run:  python -m miner.inference.score
"""
from __future__ import annotations

import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"
FROZEN = BENCH / "frozen"
RESULTS = CORPUS / "results"

HIGH_CONFIDENCE = 0.80


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_freeze(manifest, strict=True):
    problems = []
    for f, h in manifest["engine_files"].items():
        got = sha256(HERE / f)
        if got != h:
            problems.append(f"engine file {f} changed since freeze")
    for name, h in manifest["predictions"].items():
        got = sha256(FROZEN / f"predictions_{name}.json")
        if got != h:
            problems.append(f"predictions_{name}.json changed since freeze")
    if problems and strict:
        raise SystemExit("FREEZE VIOLATION:\n  " + "\n  ".join(problems))
    return problems


def _get(hyps, target):
    for h in hyps:
        if h["target"] == target:
            return h
    return None


def score_mode(pred, truth):
    hyps = pred["hypotheses"]
    t_fr = truth["framing"]
    checks = []          # (name, correct, confidence, predicted, expected)

    def check(name, target, expected, extract=lambda p: p):
        h = _get(hyps, target)
        if h is None:
            checks.append((name, None, 0.0, None, expected))
            return
        got = extract(h["prediction"]) if h["prediction"] is not None else None
        ok = None if got is None else (got == expected)
        checks.append((name, ok, h["confidence"], got, expected))

    check("opcode_offset", "framing.opcode_offset", t_fr["opcode_offset"])
    check("sub_offset", "framing.sub_offset", t_fr["sub_offset"])
    check("length_offset", "framing.length_offset", t_fr["length_offset"])
    check("body_offset", "framing.body_offset", t_fr["body_offset"])

    # constants: truth lists 2,3,4; extra zero-padding offsets are not errors,
    # but a wrong value at a truth offset is.
    hc = _get(hyps, "framing.constant_offsets")
    got_c = {int(k): v for k, v in (hc["prediction"] or {}).items()} if hc else {}
    for off, val in t_fr["constant_offsets"].items():
        off = int(off)
        checks.append((f"constant@{off}", got_c.get(off) == val,
                       hc["confidence"] if hc else 0.0, got_c.get(off), val))

    # checksum
    hk = _get(hyps, "checksum")
    tk = truth["checksum"]["equivalent_affine"]
    ck_ok = False
    if hk and hk["prediction"]:
        p = hk["prediction"]
        ck_ok = (p.get("kind") == "affine_over_sum"
                 and p.get("a") == tk["a"] and p.get("b") == tk["b"]
                 and p.get("offset") == t_fr["checksum_offset"]
                 and p.get("range") == tk["range"])
    checks.append(("checksum", ck_ok, hk["confidence"] if hk else 0.0,
                   hk["prediction"] if hk else None, tk))

    # GET/SET pairing
    hg = _get(hyps, "get_set_rule")
    truth_pairs = {tuple(p) for p in truth["get_set_pairs"]}
    pair_hits = pair_false = 0
    rule_ok = False
    if hg and hg["prediction"]:
        rule_ok = hg["prediction"].get("rule") == "or_0x80"
        got_pairs = {(a, b) for a, b in hg["prediction"].get("pairs", [])}
        pair_hits = len(got_pairs & truth_pairs)
        pair_false = len(got_pairs - truth_pairs)
    checks.append(("get_set_rule", rule_ok, hg["confidence"] if hg else 0.0,
                   (hg["prediction"] or {}).get("rule") if hg else None, "or_0x80"))

    # record layouts: stride + endianness of the leading field
    rec_stride = rec_endian = rec_total = 0
    rec_detail = []
    for op, spec in truth["record_layouts"].items():
        if "stride" not in spec:
            continue
        h = _get(hyps, f"record.{op}")
        rec_total += 1
        got = h["prediction"] if h and h["prediction"] else {}
        s_ok = got.get("stride") == spec["stride"]
        want_type = spec["fields"][0].get("type")
        e_ok = got.get("first_field", {}).get("type") == want_type
        rec_stride += bool(s_ok)
        rec_endian += bool(e_ok)
        rec_detail.append({"opcode": op, "stride_ok": bool(s_ok), "endian_ok": bool(e_ok),
                           "predicted": got.get("stride"), "expected": spec["stride"],
                           "confidence": h["confidence"] if h else 0.0})
        checks.append((f"record{op}.stride", s_ok, h["confidence"] if h else 0.0,
                       got.get("stride"), spec["stride"]))
        checks.append((f"record{op}.endianness", e_ok, h["confidence"] if h else 0.0,
                       got.get("first_field", {}).get("type"), want_type))

    # reconstruction
    rec = pred.get("reconstruction", [])
    exact = sum(1 for r in rec if r["exact"])
    byte_acc = (sum(r["byte_accuracy"] for r in rec) / len(rec)) if rec else 0.0
    unk_rate = (sum(r["unknown_rate"] for r in rec) / len(rec)) if rec else 0.0
    wrong_bytes = sum(r["wrong"] for r in rec)
    total_bytes = sum(r["total"] for r in rec)

    # enums / scales
    enums = pred.get("enum_generalisation", {})
    enum_summary = {}
    for k, v in enums.items():
        enum_summary[k] = {
            "model": v["model"], "exact": v["exact"], "abstained": v["abstained"],
            "wrong": v["wrong"], "total": v["total"],
            "accuracy": round(v["exact"] / v["total"], 4) if v["total"] else 0.0,
        }

    graded = [c for c in checks if c[1] is not None]
    hcw = [{"check": c[0], "confidence": c[2], "predicted": c[3], "expected": c[4]}
           for c in checks if c[1] is False and c[2] >= HIGH_CONFIDENCE]
    abstained = [c[0] for c in checks if c[1] is None]

    return {
        "mode": pred["mode"],
        "n_packets": pred["n_packets"],
        "FIELD_OFFSET_ACCURACY": _rate(checks, ("opcode_offset", "sub_offset",
                                                "length_offset", "body_offset")),
        "FIELD_TYPE_ACCURACY": round(rec_stride / rec_total, 4) if rec_total else None,
        "ENDIANNESS_ACCURACY": round(rec_endian / rec_total, 4) if rec_total else None,
        "SCALE_ACCURACY": enum_summary.get("actuation_travel_mm", {}).get("accuracy"),
        "ENUM_ACCURACY": enum_summary.get("polling_rate_hz", {}).get("accuracy"),
        "CHECKSUM_RECOVERY": bool(ck_ok),
        "COMMAND_PAIR_ACCURACY": {
            "rule_correct": rule_ok,
            "true_pairs_found": pair_hits,
            "true_pairs_total": len(truth_pairs),
            "false_pairs": pair_false,
        },
        "EXACT_PACKET_MATCH": f"{exact}/{len(rec)}",
        "BYTE_ACCURACY": round(byte_acc, 4),
        "UNKNOWN_BYTE_RATE": round(unk_rate, 4),
        "WRONG_BYTES": f"{wrong_bytes}/{total_bytes}",
        "HIGH_CONFIDENCE_WRONG": len(hcw),
        "high_confidence_wrong_detail": hcw,
        "abstentions": abstained,
        "checks_passed": sum(1 for c in graded if c[1]),
        "checks_total": len(graded),
        "enum_detail": enum_summary,
        "record_detail": rec_detail,
        "failed_checks": [{"check": c[0], "predicted": c[3], "expected": c[4],
                           "confidence": c[2]}
                          for c in checks if c[1] is False],
    }


def _rate(checks, names):
    sel = [c for c in checks if c[0] in names and c[1] is not None]
    return round(sum(1 for c in sel if c[1]) / len(sel), 4) if sel else None


def main(strict=True):
    manifest = json.loads((FROZEN / "MANIFEST.sha256.json").read_text(encoding="utf-8"))
    problems = verify_freeze(manifest, strict=strict)
    truth = json.loads((CORPUS / "ground_truth" / "truth.json").read_text(encoding="utf-8"))

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {"freeze_verified": not problems, "freeze_problems": problems, "modes": {}}
    for name in manifest["predictions"]:
        pred = json.loads((FROZEN / f"predictions_{name}.json").read_text(encoding="utf-8"))
        out["modes"][name] = score_mode(pred, truth)

    (RESULTS / "scores.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for name, s in out["modes"].items():
        print(f"\n=== {name} ===")
        for k in ("FIELD_OFFSET_ACCURACY", "FIELD_TYPE_ACCURACY", "ENDIANNESS_ACCURACY",
                  "SCALE_ACCURACY", "ENUM_ACCURACY", "CHECKSUM_RECOVERY",
                  "EXACT_PACKET_MATCH", "BYTE_ACCURACY", "UNKNOWN_BYTE_RATE",
                  "WRONG_BYTES", "HIGH_CONFIDENCE_WRONG"):
            print(f"  {k:24s} {s[k]}")
        print(f"  {'COMMAND_PAIR':24s} {s['COMMAND_PAIR_ACCURACY']}")
        print(f"  {'checks':24s} {s['checks_passed']}/{s['checks_total']}")
        if s["failed_checks"]:
            print("  failed:", [f["check"] for f in s["failed_checks"]])
    print("\nwrote", RESULTS / "scores.json")


if __name__ == "__main__":
    import sys
    main(strict="--allow-drift" not in sys.argv)
