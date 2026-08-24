"""Pre-register and freeze an inference run.  Playbook v4 SS1.3, steps 1-4.

`run_benchmark.py` froze exactly one run, of exactly one engine, into exactly
one directory.  This module does the same job for any (engine, dataset,
output) triple so that more than one run can exist without any of them
overwriting the pre-registered artifacts of another.

What is hashed, and why more than run_benchmark hashed:

* every engine source the run actually imports -- v1 hashed five files but not
  `reconstruct.py`, which builds the packets that EXACT_PACKET_MATCH is
  computed from, nor the freezer itself;
* the datasets, so a corpus rebuild cannot be slipped in under a frozen score;
* the scorer sources, because the metric definitions are as much a part of a
  pre-registration as the predictions are -- a scorer edited after the fact can
  move a number as surely as an engine can;
* the manifest itself, into a sibling digest file, so the manifest cannot be
  edited to match whatever the tree happens to hold.

Nothing in this module may open ground_truth/.  Run it, then run score_v3.

Run:  python -m miner.inference.freeze --engine engine_v3 --label v3_engine_only
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import pathlib
import platform
import subprocess
from datetime import datetime, timezone

from . import numeric, reconstruct
from .run_benchmark import enum_tasks, load_rows, pick_holdout

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"

#: Every source that can move a prediction.  If it is imported by the run or by
#: the scorer, it is hashed.
HASHED_SOURCES = [
    "engine.py", "engine_v2.py", "engine_v3.py", "engine_broken_control.py",
    "numeric.py", "hypothesis.py", "reconstruct.py", "blind.py",
    "build_corpus.py", "gap_closure.py", "run_benchmark.py", "freeze.py",
    "score.py", "score_v3.py",
]

MODES = {
    "A_RAW_ONLY": ("dataset_A_RAW_ONLY.jsonl", False),
    "B_CONTROLLED_ACTIONS": ("dataset_B_CONTROLLED_ACTIONS.jsonl", False),
    "C_PARTIAL_PROTOCOL": ("dataset_C_PARTIAL_PROTOCOL.jsonl", True),
}

#: Modes whose numbers are a gate submission.  Mode C is handed a redacted copy
#: of the family schema, so it measures what prior knowledge buys, not what the
#: pipeline recovers from vendor artifacts alone.  It is recorded and reported,
#: and it is never a submission.
GATE_MODES = ["A_RAW_ONLY", "B_CONTROLLED_ACTIONS"]


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_rev():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def freeze(engine_name, label, bench=BENCH, note=None):
    eng = importlib.import_module(f".{engine_name}", __package__)
    bench = pathlib.Path(bench).resolve()
    frozen = bench / f"frozen_{label}"
    frozen.mkdir(parents=True, exist_ok=True)
    schema_c = json.loads((bench / "schema_C_partial.json").read_text(encoding="utf-8"))

    manifest = {
        "label": label,
        "engine_module": engine_name,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_rev": git_rev(),
        "python": platform.python_version(),
        "gate_modes": GATE_MODES,
        "benchmark_dir": bench.relative_to(CORPUS).as_posix(),
        "engine_files": {f: sha256(HERE / f) for f in HASHED_SOURCES},
        "datasets": {},
        "predictions": {},
        "policy": "predictions frozen before ground_truth/truth.json was opened",
        "note": note,
    }

    holdout = pick_holdout(load_rows(bench / MODES["A_RAW_ONLY"][0]))
    holdout_ids = {h["id"] for h in holdout}
    (frozen / "holdout_packets.json").write_text(
        json.dumps(holdout, indent=1), encoding="utf-8")
    print(f"[{label}] held out {len(holdout)} packets from every engine run")

    for name, (fname, wants_schema) in MODES.items():
        path = bench / fname
        rows = [r for r in load_rows(path) if r["id"] not in holdout_ids]
        hyps = eng.run(rows, partial_schema=schema_c if wants_schema else None)
        hyp_dicts = [dataclasses.asdict(h) for h in hyps]

        sch = reconstruct.Schema(hyp_dicts)
        recon = []
        for task in holdout:
            built, _unk = sch.build(task["opcode"], task["sub"], task["body"])
            res = reconstruct.compare(built, task["truth_hex"])
            res["id"] = task["id"]
            res["opcode"] = hex(task["opcode"])
            res["sub"] = task["sub"]
            recon.append(res)

        enums = {}
        for key, spec in enum_tasks().items():
            loo = numeric.leave_one_out([tuple(p) for p in spec["pairs"]])
            model, _alts = numeric.fit([tuple(p) for p in spec["pairs"]])
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

        out = frozen / f"predictions_{name}.json"
        out.write_text(json.dumps({
            "mode": name,
            "engine_module": engine_name,
            "is_gate_submission": name in GATE_MODES,
            "n_packets": len(rows),
            "hypotheses": hyp_dicts,
            "reconstruction": recon,
            "enum_generalisation": enums,
        }, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        manifest["datasets"][name] = {
            "path": path.relative_to(CORPUS).as_posix(), "sha256": sha256(path)}
        manifest["predictions"][name] = sha256(out)
        known = sum(1 for h in hyps if h.prediction is not None)
        exact = sum(1 for r in recon if r["exact"])
        print(f"[{label}] {name}: {len(hyps)} hypotheses, {known} predicted, "
              f"{len(hyps) - known} UNKNOWN | exact rebuilds {exact}/{len(recon)}")

    mpath = frozen / "MANIFEST.sha256.json"
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    digest = sha256(mpath)
    (frozen / "MANIFEST.digest.txt").write_text(digest + "\n", encoding="utf-8")
    print(f"[{label}] frozen -> {frozen}")
    print(f"[{label}] manifest sha256: {digest}")
    return frozen, digest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="engine_v3")
    ap.add_argument("--label", required=True)
    ap.add_argument("--bench", default=str(BENCH),
                    help="directory holding dataset_*.jsonl and schema_C_partial.json")
    ap.add_argument("--note", default=None)
    a = ap.parse_args(argv)
    freeze(a.engine, a.label, bench=pathlib.Path(a.bench), note=a.note)


if __name__ == "__main__":
    main()
