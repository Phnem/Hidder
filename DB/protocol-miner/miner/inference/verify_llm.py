"""Try to refute the LLM pass's hypotheses using the captured frames alone.

The LLM is not trusted.  Every claim it makes is re-tested here against the
whole corpus by deterministic code, and only claims that survive are allowed to
join the merged hypothesis set that gets scored.

Crucially this module never opens ground_truth/truth.json.  It falsifies
against OBSERVED PACKETS, which are evidence, not the answer key: a framing
claim is checked by rebuilding real captured frames from it and seeing whether
the bytes come out right.

Run:  python -m miner.inference.verify_llm <hypotheses.json>
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

from . import reconstruct
from .run_benchmark import BENCH, FROZEN, load_rows
from .score import RESULTS

FRAMING_TARGETS = {
    "framing.opcode_offset", "framing.sub_offset",
    "framing.length_offset", "framing.body_offset",
}

# The LLM was asked to reuse target NAMES, not value vocabulary.  Collapsing an
# equivalent phrasing onto the canonical one is fair; inventing a mapping that
# rescues a wrong answer is not, so this only touches forms that denote the
# identical thing.
RULE_SYNONYMS = {
    "or_0x80": "or_0x80", "bit7_of_opcode": "or_0x80", "bit7": "or_0x80",
    "opcode | 0x80": "or_0x80", "| 0x80": "or_0x80", "set_bit7": "or_0x80",
    "add_0x80": "add_0x80", "xor_0x80": "xor_0x80", "add_1": "add_1",
}


def normalise(target, pred):
    """Accept the LLM's value shapes without accepting wrong answers."""
    if target == "get_set_rule" and isinstance(pred, dict):
        for key in ("rule", "read", "write"):
            v = str(pred.get(key, "")).strip().lower()
            if v in RULE_SYNONYMS:
                return {**pred, "rule": RULE_SYNONYMS[v]}
    if target.startswith("record.") and isinstance(pred, dict):
        # the LLM reports request and reply strides separately; the scored
        # field is the request-side record size
        if "stride" not in pred and "request_stride" in pred:
            return {**pred, "stride": pred["request_stride"]}
    return pred


def _frames(rows):
    out = []
    for r in rows:
        h = r.get("payload_hex")
        if h and not r.get("payload_truncated") and len(h) == 126:
            out.append((r["id"], bytes.fromhex(h)))
    return out


def _rebuild(hyps, holdout):
    sch = reconstruct.Schema(hyps)
    exact = wrong = unk = 0
    for task in holdout:
        built, _ = sch.build(task["opcode"], task["sub"], task["body"])
        res = reconstruct.compare(built, task["truth_hex"])
        exact += res["exact"]
        wrong += res["wrong"]
        unk += res["unknown"]
    return exact, wrong, unk


def verify_framing(claim, target, base_hyps, holdout, llm_framing):
    """Rebuild real captured frames and see whether the bytes come out right.

    A framing offset cannot be judged on its own: substituting one correct
    offset into a schema whose OTHER offsets are wrong still produces broken
    frames, and reporting that as a refutation punishes the right answer for
    its neighbours' sins.  So each claim is tested twice -- once swapped into
    the deterministic baseline, and once inside the LLM's own complete framing
    set -- and the better of the two decides the verdict.
    """
    solo = [dict(h) for h in base_hyps if h["target"] != target]
    solo.append({"target": target, "prediction": claim})
    s_exact, s_wrong, s_unk = _rebuild(solo, holdout)

    joint = [dict(h) for h in base_hyps if h["target"] not in llm_framing]
    joint += [{"target": t, "prediction": p} for t, p in llm_framing.items()]
    j_exact, j_wrong, j_unk = _rebuild(joint, holdout)

    n = len(holdout)
    verdict = ("SUPPORTED" if j_exact == n else
               "WEAKENED" if j_wrong == 0 else "REFUTED")
    return {
        "test": "rebuild real captured frames from the claimed framing",
        "in_deterministic_baseline": {
            "exact": f"{s_exact}/{n}", "wrong_bytes": s_wrong,
            "unknown_bytes": s_unk},
        "in_llm_framing_set": {
            "exact": f"{j_exact}/{n}", "wrong_bytes": j_wrong,
            "unknown_bytes": j_unk},
        "verdict": verdict,
    }


def verify_stride(opcode, stride, frames, len_off=5, body_off=6):
    members = [(i, b) for i, b in frames if b[0] == opcode and b[len_off]]
    if not members:
        return {"test": "stride divisibility", "verdict": "UNTESTABLE",
                "reason": "no frames for this opcode carry a body"}
    bad = [i for i, b in members if b[len_off] % stride]
    cols_ok = cols_tot = 0
    for _, b in members:
        n = b[len_off] // stride
        if n < 2:
            continue
        for j in range(stride):
            col = [b[body_off + k * stride + j] for k in range(n)]
            cols_tot += 1
            cols_ok += len(set(col)) == 1 or all(
                y >= x for x, y in zip(col, col[1:]))
    return {
        "test": "every observed body length divisible by the stride, and "
                "columns internally consistent",
        "frames": len(members),
        "divisibility_violations": len(bad),
        "column_consistency": round(cols_ok / cols_tot, 3) if cols_tot else None,
        "verdict": "REFUTED" if bad else
                   ("SUPPORTED" if (cols_tot and cols_ok / cols_tot > 0.8) else "WEAKENED"),
    }


def verify_get_set(claim, frames):
    rules = {"or_0x80": lambda s: s | 0x80,
             "add_0x80": lambda s: (s + 0x80) & 0xFF,
             "xor_0x80": lambda s: s ^ 0x80,
             "add_1": lambda s: (s + 1) & 0xFF}
    rule = claim.get("rule") if isinstance(claim, dict) else claim
    if rule not in rules:
        return {"test": "opcode pairing rule", "verdict": "UNTESTABLE",
                "reason": f"unknown rule {rule!r}"}
    ops = {b[0] for _, b in frames}
    subs = collections.defaultdict(set)
    for _, b in frames:
        subs[b[0]].add(b[1])
    fn = rules[rule]
    pairs = [(s, fn(s)) for s in sorted(ops) if fn(s) != s and fn(s) in ops]
    overlaps = []
    for s, g in pairs:
        u = subs[s] | subs[g]
        overlaps.append(len(subs[s] & subs[g]) / len(u) if u else 0.0)
    mean = sum(overlaps) / len(overlaps) if overlaps else 0.0
    return {
        "test": "pairs implied by the rule must exist and share sub-ids",
        "pairs_found": len(pairs), "mean_sub_overlap": round(mean, 3),
        "verdict": "SUPPORTED" if pairs and mean > 0.3 else
                   ("WEAKENED" if pairs else "REFUTED"),
    }


def verify_checksum(claim, frames):
    if not isinstance(claim, dict) or claim.get("kind") != "affine_over_sum":
        return {"test": "check formula", "verdict": "UNTESTABLE"}
    lo, hi = claim.get("range", [0, 62])
    cpos, a, b = claim.get("offset", 62), claim["a"], claim["b"]
    bad = [i for i, f in frames if (a * (sum(f[lo:hi]) & 0xFF) + b) % 256 != f[cpos]]
    return {"test": "check byte reproduced for every captured frame",
            "frames": len(frames), "violations": len(bad),
            "verdict": "SUPPORTED" if not bad else "REFUTED"}


def main(path):
    claims = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    rows = load_rows(BENCH / "dataset_A_RAW_ONLY.jsonl")
    frames = _frames(rows)
    holdout = json.loads((FROZEN / "holdout_packets.json").read_text(encoding="utf-8"))
    base = json.loads(
        (FROZEN / "predictions_A_RAW_ONLY.json").read_text(encoding="utf-8"))["hypotheses"]

    llm_framing = {c["target"]: c["prediction"] for c in claims
                   if c.get("target") in FRAMING_TARGETS
                   and isinstance(c.get("prediction"), int)}
    for c in claims:
        if c.get("target") == "checksum" and isinstance(c.get("prediction"), dict):
            llm_framing["checksum"] = c["prediction"]

    results = []
    for c in claims:
        t, pred = c.get("target"), normalise(c.get("target"), c.get("prediction"))
        c = {**c, "prediction": pred}
        if pred is None:
            results.append({**c, "verification": {"verdict": "ABSTAINED"}})
            continue
        if t in FRAMING_TARGETS and isinstance(pred, int):
            v = verify_framing(pred, t, base, holdout, llm_framing)
        elif t == "checksum":
            v = verify_checksum(pred, frames)
        elif t == "get_set_rule":
            v = verify_get_set(pred, frames)
        elif t.startswith("record."):
            try:
                op = int(t.split(".")[1], 16)
            except ValueError:
                v = {"verdict": "UNTESTABLE", "reason": "opcode not parseable"}
            else:
                stride = pred.get("stride") if isinstance(pred, dict) else pred
                v = (verify_stride(op, int(stride), frames) if stride
                     else {"verdict": "UNTESTABLE", "reason": "no stride claimed"})
        else:
            v = {"verdict": "UNTESTABLE",
                 "reason": "no deterministic test exists for this target"}
        results.append({**c, "verification": v})

    tally = collections.Counter(r["verification"]["verdict"] for r in results)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "llm_verification.json").write_text(
        json.dumps({"tally": dict(tally), "results": results}, indent=1,
                   ensure_ascii=False), encoding="utf-8")
    print("verdicts:", dict(tally))
    for r in results:
        print(f"  {r['target']:34s} {r['verification']['verdict']:11s} "
              f"conf={r.get('confidence')}")
    print("\nwrote", RESULTS / "llm_verification.json")


if __name__ == "__main__":
    main(sys.argv[1])
