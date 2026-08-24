"""Run the deterministic engine over the three blind datasets and FREEZE.

Freezing is the point.  The engine source is hashed, the predictions are
written, and a SHA-256 manifest is produced BEFORE ground_truth/truth.json is
opened by anything.  Scoring is a separate program that reads the frozen
artifacts; it cannot feed anything back.

Run:  python -m miner.inference.run_benchmark
"""
from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import pathlib
import platform
import subprocess
import sys
from datetime import datetime, timezone

from . import engine, numeric, reconstruct

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"
FROZEN = BENCH / "frozen"

ENGINE_FILES = ["engine.py", "numeric.py", "hypothesis.py", "blind.py", "build_corpus.py"]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_rev():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def load_rows(path):
    return [json.loads(line) for line in path.open(encoding="utf-8")]


# Held-out packets are chosen structurally, never by what they mean: the first
# fully-observed frame of every distinct (opcode, sub-id) pair, capped so that
# no single opcode dominates.  They are removed from the engine's input, so the
# engine has genuinely never seen them when it is asked to rebuild them.
HOLDOUT_PER_OPCODE = 2
HOLDOUT_TOTAL = 18

# The framing convention used only to CARVE the task input (opcode, sub, body)
# out of a held-out frame. The engine is told none of it; it must re-derive the
# header, the length byte, the padding and the check byte by itself.
TASK_SUB_OFF, TASK_LEN_OFF, TASK_BODY_OFF = 1, 5, 6


def pick_holdout(rows):
    seen_pair, per_op, out = set(), collections.Counter(), []
    for r in rows:
        h = r.get("payload_hex")
        if not h or r.get("payload_truncated") or len(h) != 126:
            continue
        b = bytes.fromhex(h)
        key = (b[0], b[TASK_SUB_OFF])
        if key in seen_pair or per_op[b[0]] >= HOLDOUT_PER_OPCODE:
            continue
        seen_pair.add(key)
        per_op[b[0]] += 1
        blen = b[TASK_LEN_OFF]
        out.append({
            "id": r["id"],
            "opcode": b[0],
            "sub": b[TASK_SUB_OFF],
            "body": list(b[TASK_BODY_OFF:TASK_BODY_OFF + blen]),
            "truth_hex": h,
        })
        if len(out) >= HOLDOUT_TOTAL:
            break
    return out


def enum_tasks():
    """Leave-one-out generalisation for the value ladders.

    Scope note that belongs in the report: only two of the seven polling pairs
    were ever exercised on the physical device. The remaining five come from the
    vendor's own option table, so this task measures the numeric MODEL FITTER's
    ability to generalise a ladder, not end-to-end recovery from traffic alone.
    """
    return {
        "polling_rate_hz": {
            "pairs": [[125, 3], [250, 2], [500, 1], [1000, 0],
                      [2000, 6], [4000, 5], [8000, 4]],
            "observed_physically": [[125, 3], [250, 2]],
        },
        "actuation_travel_mm": {
            "pairs": [[4.00, 400], [0.77, 77], [3.38, 338], [1.04, 104], [0.10, 10]],
            "observed_physically": [],
        },
        "sleep_seconds": {
            "pairs": [[30, 1], [60, 2], [90, 3], [120, 4], [180, 6], [300, 10],
                      [600, 20], [900, 30], [1200, 40], [1800, 60], [3600, 120]],
            "observed_physically": [],
        },
    }


def main():
    FROZEN.mkdir(parents=True, exist_ok=True)
    schema_c = json.loads((BENCH / "schema_C_partial.json").read_text(encoding="utf-8"))

    modes = {
        "A_RAW_ONLY": (BENCH / "dataset_A_RAW_ONLY.jsonl", None),
        "B_CONTROLLED_ACTIONS": (BENCH / "dataset_B_CONTROLLED_ACTIONS.jsonl", None),
        "C_PARTIAL_PROTOCOL": (BENCH / "dataset_C_PARTIAL_PROTOCOL.jsonl", schema_c),
    }

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_rev": git_rev(),
        "python": platform.python_version(),
        "engine_files": {f: sha256(HERE / f) for f in ENGINE_FILES},
        "datasets": {},
        "predictions": {},
        "policy": "predictions frozen before ground_truth/truth.json was opened",
    }

    holdout = pick_holdout(load_rows(modes["A_RAW_ONLY"][0]))
    holdout_ids = {h["id"] for h in holdout}
    (FROZEN / "holdout_packets.json").write_text(
        json.dumps(holdout, indent=1), encoding="utf-8")
    print(f"held out {len(holdout)} packets from every engine run\n")

    for name, (path, schema) in modes.items():
        rows = [r for r in load_rows(path) if r["id"] not in holdout_ids]
        hyps = engine.run(rows, partial_schema=schema)
        hyp_dicts = [dataclasses.asdict(h) for h in hyps]

        sch = reconstruct.Schema(hyp_dicts)
        recon = []
        for task in holdout:
            built, unk = sch.build(task["opcode"], task["sub"], task["body"])
            res = reconstruct.compare(built, task["truth_hex"])
            res["id"] = task["id"]
            res["opcode"] = hex(task["opcode"])
            res["sub"] = task["sub"]
            recon.append(res)

        enums = {}
        for key, spec in enum_tasks().items():
            loo = numeric.leave_one_out([tuple(p) for p in spec["pairs"]])
            model, alts = numeric.fit([tuple(p) for p in spec["pairs"]])
            enums[key] = {
                "model": model.describe(),
                "generalises": model.generalises,
                "n_observed_physically": len(spec["observed_physically"]),
                "leave_one_out": [
                    {"held": list(h), "predicted": str(g),
                     "correct": bool(ok), "abstained": g is None}
                    for h, g, ok in loo],
                "exact": sum(1 for _, _, ok in loo if ok),
                "abstained": sum(1 for _, g, ok in loo if g is None and not ok),
                "wrong": sum(1 for _, g, ok in loo if g is not None and not ok),
                "total": len(loo),
            }

        out = FROZEN / f"predictions_{name}.json"
        payload = {
            "mode": name,
            "n_packets": len(rows),
            "hypotheses": hyp_dicts,
            "reconstruction": recon,
            "enum_generalisation": enums,
        }
        out.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str),
                       encoding="utf-8")
        manifest["datasets"][name] = sha256(path)
        manifest["predictions"][name] = sha256(out)
        known = sum(1 for h in hyps if h.prediction is not None)
        exact = sum(1 for r in recon if r["exact"])
        print(f"{name}: {len(hyps)} hypotheses, {known} predicted, "
              f"{len(hyps) - known} UNKNOWN | exact packet rebuilds {exact}/{len(recon)}")

    (FROZEN / "MANIFEST.sha256.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    print("\nfrozen ->", FROZEN)
    print("manifest sha256:", sha256(FROZEN / "MANIFEST.sha256.json"))


if __name__ == "__main__":
    main()
