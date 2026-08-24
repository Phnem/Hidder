"""Assemble a leak-free working package for the LLM inference pass.

The LLM pass must not be able to reach ground_truth/truth.json, the earlier
markdown reports, or the official JS.  So instead of pointing an assistant at
the repository, we copy a sanitised subset into a scratch directory and let it
work only there.

Run:  python -m miner.inference.make_llm_package <outdir>
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"
FROZEN = BENCH / "frozen"

SAMPLES_PER_KEY = 6


def main(outdir):
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(l) for l in (BENCH / "dataset_A_RAW_ONLY.jsonl").open(encoding="utf-8")]
    # Sample evenly across (opcode, sub) so no single chatty command dominates.
    buckets = collections.defaultdict(list)
    for r in rows:
        h = r.get("payload_hex")
        if not h:
            continue
        b = bytes.fromhex(h)
        buckets[(b[0], b[1] if len(b) > 1 else 0)].append(r)
    sample = []
    for key in sorted(buckets):
        sample.extend(buckets[key][:SAMPLES_PER_KEY])
    with (out / "packets.jsonl").open("w", encoding="utf-8") as fh:
        for r in sample:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    pred = json.loads((FROZEN / "predictions_A_RAW_ONLY.json").read_text(encoding="utf-8"))
    (out / "deterministic_hypotheses.json").write_text(
        json.dumps(pred["hypotheses"], indent=1, ensure_ascii=False), encoding="utf-8")

    ui = [r for r in rows if r.get("ui_after") or r.get("ui_before")]
    ui_rows = [json.loads(l) for l in
               (BENCH / "dataset_B_CONTROLLED_ACTIONS.jsonl").open(encoding="utf-8")]
    ui_rows = [r for r in ui_rows if r.get("ui_after") or r.get("phase")]
    with (out / "controlled_actions.jsonl").open("w", encoding="utf-8") as fh:
        for r in ui_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = collections.Counter()
    for r in rows:
        if r.get("payload_hex"):
            stats[r["payload_hex"][:2]] += 1
    (out / "opcode_histogram.json").write_text(
        json.dumps(dict(sorted(stats.items())), indent=1), encoding="utf-8")

    print(f"package -> {out}")
    print(f"  packets.jsonl            {len(sample)} sampled frames "
          f"(from {len(rows)} total, {len(buckets)} distinct opcode/sub keys)")
    print(f"  controlled_actions.jsonl {len(ui_rows)} annotated frames")
    print(f"  deterministic_hypotheses.json {len(pred['hypotheses'])} hypotheses")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "llm_package")
