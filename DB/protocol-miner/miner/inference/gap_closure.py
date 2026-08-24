"""Close the 0x83 collection gap by APPENDING to the frozen blind datasets.

The stride of opcode 0x83 is the one record layout no amount of engine repair
can settle: every captured 0x83 body is 6 or 18 bytes, and 2, 3 and 6 all
divide both.  Only a frame in which 0x83 operates on a single item can separate
them, and the corpus behind `benchmark/` contains none.

`raw/physical_macro_assignment_20260823.jsonl` does contain two, and has since
2026-08-23.  They never reached the corpus because `build_corpus.py`'s reader
looked for `hex` and `hex_prefix` but not `hex_full`, which is the spelling that
session used, so the whole byte-complete capture was reduced to the single line
that happened to be truncated.  The gap was a pipeline bug, not a missing trip
to the lab.

Why this appends to `benchmark/` rather than rebuilding the corpus from raw/:
re-running `build_corpus.py` today no longer reproduces the datasets the
benchmark was frozen on.  The upstream emulator trace
(`reports/oracle/aula_web/HERO_84_HE/raw_full_trace.jsonl`) has grown from 2425
frames to 4324 since 2026-08-22 and nothing pinned its hash, so a rebuild would
change thousands of frames at once and the effect of closing one collection gap
would be unreadable inside that. Appending N frames to the exact frozen datasets
keeps every existing transaction id where it was, keeps the held-out packets
identical, and makes the difference between the two runs exactly this capture.

The provenance that has to travel with these frames: the two 0x83 GET requests
were composed by the capture session itself from the vendor bundle's own
serializer, not emitted by the vendor UI.  The device's replies to them are
genuine.  A reader who considers a host-composed request inadmissible evidence
should read the engine-only run instead; both are reported.

Run:  python -m miner.inference.gap_closure
"""
from __future__ import annotations

import json
import pathlib
import shutil

from . import blind, build_corpus

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
BENCH = CORPUS / "benchmark"
OUT = CORPUS / "benchmark_gapclosed"

MODES = {
    "A_RAW_ONLY": {},
    "B_CONTROLLED_ACTIONS": {"keep_ui": True, "keep_phase": True},
    "C_PARTIAL_PROTOCOL": {"keep_ui": True, "keep_phase": True},
}


def extra_transactions():
    rows = build_corpus.load_physical_jsonl(
        build_corpus.RAW / build_corpus.GAP_CLOSURE[0],
        "PHYSICAL", build_corpus.GAP_CLOSURE[1])
    for r in rows:
        r["provenance"] = "collection gap closure, appended 2026-08-24"
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    extra = extra_transactions()

    for name, kw in MODES.items():
        src = BENCH / f"dataset_{name}.jsonl"
        lines = src.read_text(encoding="utf-8").splitlines()
        next_id = max(json.loads(l)["id"] for l in lines if l.strip()) + 1
        appended = []
        for i, tx in enumerate(extra):
            tx = dict(tx)
            tx["id"] = next_id + i
            appended.append(json.dumps(blind.strip(tx, **kw), ensure_ascii=False))
        dest = OUT / f"dataset_{name}.jsonl"
        dest.write_text("\n".join(lines + appended) + "\n", encoding="utf-8")
        print(f"{name}: {len(lines)} + {len(appended)} -> {dest.name}")

    shutil.copyfile(BENCH / "schema_C_partial.json", OUT / "schema_C_partial.json")
    shutil.copyfile(BENCH / "mode_C_hidden_manifest.json",
                    OUT / "mode_C_hidden_manifest.json")

    # leakage audit, same probes blind.py uses
    bad = []
    for name in MODES:
        text = (OUT / f"dataset_{name}.jsonl").read_text(encoding="utf-8")
        for probe in ("Lt.", "sync_", "fetch_", "polling", "actuation", "rapid",
                      "deadzone", "win_lock", "checksum", "travel", "macro"):
            if probe in text:
                bad.append((name, probe, text.count(probe)))
    print("leakage audit:", bad if bad else "clean")

    # what the appended frames actually add, stated in bytes
    lens = {}
    for tx in extra:
        h = tx.get("payload_hex")
        if h and len(h) == 126 and not tx.get("payload_truncated"):
            b = bytes.fromhex(h)
            lens.setdefault(hex(b[0]), []).append(b[5])
    print("appended full-width request bodies by opcode:",
          {k: sorted(v) for k, v in sorted(lens.items())})


if __name__ == "__main__":
    main()
