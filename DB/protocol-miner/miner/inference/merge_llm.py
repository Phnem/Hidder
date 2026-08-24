"""Merge surviving LLM hypotheses into the deterministic set and re-score.

Only claims the verifier actually exercised and failed to refute may be merged
(SUPPORTED or WEAKENED).  REFUTED claims are dropped, ABSTAINED claims carry no
prediction, and UNTESTABLE claims are carried through for the record but are
not allowed to change a score they could not be checked against.

Produces results/scores_llm.json next to the frozen deterministic scores, so
DETERMINISTIC_ONLY and DETERMINISTIC_PLUS_LLM sit side by side.

Run:  python -m miner.inference.merge_llm
"""
from __future__ import annotations

import json

from . import reconstruct, score
from .verify_llm import normalise
from .run_benchmark import FROZEN, enum_tasks

MERGEABLE = {"SUPPORTED", "WEAKENED"}

def main():
    ver = json.loads(
        (score.RESULTS / "llm_verification.json").read_text(encoding="utf-8"))
    base = json.loads(
        (FROZEN / "predictions_A_RAW_ONLY.json").read_text(encoding="utf-8"))
    holdout = json.loads((FROZEN / "holdout_packets.json").read_text(encoding="utf-8"))
    truth = json.loads(
        (score.CORPUS / "ground_truth" / "truth.json").read_text(encoding="utf-8"))

    merged = {h["target"]: dict(h) for h in base["hypotheses"]}
    accepted, rejected, carried = [], [], []
    for r in ver["results"]:
        verdict = r["verification"]["verdict"]
        t, pred = r["target"], r.get("prediction")
        if pred is None:
            continue
        if verdict in MERGEABLE:
            new = normalise(t, pred)
            prior = (merged.get(t) or {}).get("prediction")
            # A merge, not a replacement: the LLM overrides the fields it spoke
            # to and leaves the rest of the deterministic finding standing.
            # Wholesale replacement silently discarded correct deterministic
            # sub-fields the LLM simply had no opinion about.
            if isinstance(new, dict) and isinstance(prior, dict):
                new = {**prior, **new}
            merged[t] = {
                "target": t, "prediction": new,
                "confidence": r.get("confidence", 0.0),
                "status": "SUPPORTED", "evidence_count": len(r.get("supporting_ids") or []),
                "supporting": [r.get("rationale", "")], "contradicting": [],
                "alternatives": r.get("alternatives") or [],
                "next_best_experiment": r.get("next_best_experiment"),
                "notes": f"from LLM pass, verifier verdict {verdict}",
            }
            accepted.append((t, verdict))
        elif verdict == "REFUTED":
            rejected.append(t)
        else:
            carried.append(t)

    hyps = list(merged.values())
    sch = reconstruct.Schema(hyps)
    recon = []
    for task in holdout:
        built, _ = sch.build(task["opcode"], task["sub"], task["body"])
        res = reconstruct.compare(built, task["truth_hex"])
        res.update(id=task["id"], opcode=hex(task["opcode"]), sub=task["sub"])
        recon.append(res)

    pred = {"mode": "A_RAW_ONLY_PLUS_LLM", "n_packets": base["n_packets"],
            "hypotheses": hyps, "reconstruction": recon,
            "enum_generalisation": base["enum_generalisation"]}
    s = score.score_mode(pred, truth)

    det = json.loads((score.RESULTS / "scores.json").read_text(
        encoding="utf-8"))["modes"]["A_RAW_ONLY"]

    out = {
        "accepted_from_llm": accepted,
        "refuted_and_dropped": rejected,
        "untestable_carried_not_scored": carried,
        "DETERMINISTIC_ONLY": det,
        "DETERMINISTIC_PLUS_LLM": s,
    }
    (score.RESULTS / "scores_llm.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")

    print(f"accepted {len(accepted)} LLM claims, dropped {len(rejected)} refuted, "
          f"{len(carried)} untestable\n")
    keys = ["FIELD_OFFSET_ACCURACY", "FIELD_TYPE_ACCURACY", "ENDIANNESS_ACCURACY",
            "CHECKSUM_RECOVERY", "EXACT_PACKET_MATCH", "BYTE_ACCURACY",
            "UNKNOWN_BYTE_RATE", "WRONG_BYTES", "HIGH_CONFIDENCE_WRONG"]
    print(f"{'metric':26s} {'det-only':>12s} {'det+LLM':>12s}")
    for k in keys:
        print(f"{k:26s} {str(det[k]):>12s} {str(s[k]):>12s}")
    print(f"{'checks':26s} {det['checks_passed']}/{det['checks_total']:<10} "
          f"{s['checks_passed']}/{s['checks_total']}")
    print("\ndet-only failed:", [f["check"] for f in det["failed_checks"]])
    print("det+LLM failed:", [f["check"] for f in s["failed_checks"]])
    print("\nwrote", score.RESULTS / "scores_llm.json")


if __name__ == "__main__":
    main()
