"""Build the HERO84-only protocol corpus.

Collects every independent evidence source the project holds for the AULA
HERO 84 HE (372E:103E, family kb_by_v3_wired / controller class Lt) into one
normalised transaction dataset.

Hard rule enforced here: raw bytes and decoded semantics live in *separate*
files.  Nothing written under raw/ or observations/ carries a decoded field
name.  Everything semantic goes to ground_truth/ and is only opened after the
blind benchmark is frozen.

Run:  python -m miner.inference.build_corpus
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
RAW = CORPUS / "raw"
OBS = CORPUS / "observations"

# The fake-oracle trace tags every send with a JS stack. That stack contains
# official function names, which are semantic leakage for the blind benchmark.
# We keep them, but only in observations/ (never in the blind slices), and the
# masker strips them.
STACK_FN = re.compile(r"at (?:async )?(Lt\.[A-Za-z_][\w]*)")


def _hex_to_bytes(h):
    return bytes.fromhex(h) if h else None


def load_pdevemu_physical():
    """29 full TX/RX pairs captured from the real device by pdevemu."""
    sys.path.insert(0, str(ROOT))
    from pdevemu import ground_truth as gt

    out = []
    for src_name in ("HERO84_PHYSICAL_CAPTURE", "HERO84_PHYSICAL_CAPTURE_FOLLOWUP"):
        for i, rec in enumerate(getattr(gt, src_name)):
            tx, rx = rec.get("tx_hex"), rec.get("rx_hex")
            if not tx:
                continue
            out.append({
                "source": "PHYSICAL",
                "session": f"pdevemu:{src_name}",
                "seq": i,
                "direction": "HOST_TO_DEVICE",
                "report_id": 9,
                "payload_hex": tx,
                "reply_hex": rx,
                "vendor_note": rec.get("note"),
            })
    return out


def load_physical_jsonl(path, source, session):
    out, pending_tx = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "_meta" in rec:
            continue
        # Capture sessions have used three spellings for the same field.  The
        # loader only knew two of them, so a whole byte-complete session was
        # silently reduced to the single line that happened to be truncated.
        full = rec.get("hex") or rec.get("hex_full")
        h = full or rec.get("hex_prefix")
        if not h:
            continue
        truncated = full is None
        if rec["dir"] == "TX":
            if pending_tx:
                out.append(pending_tx)
            pending_tx = {
                "source": source,
                "session": session,
                "seq": rec.get("seq", rec.get("t")),
                "direction": "HOST_TO_DEVICE",
                "report_id": rec.get("report_id", 9),
                "payload_hex": h,
                "payload_truncated": truncated,
                "reply_hex": None,
                "fn": rec.get("fn"),
                "phase": rec.get("phase"),
                "ui_before": rec.get("ui_before"),
                "ui_after": rec.get("ui_after"),
                "vendor_note": rec.get("note"),
            }
        else:  # RX attaches to the most recent TX
            if pending_tx is not None:
                pending_tx["reply_hex"] = h
                pending_tx["reply_truncated"] = truncated
                out.append(pending_tx)
                pending_tx = None
    if pending_tx:
        out.append(pending_tx)
    return out


def load_fake_oracle(path, limit=None):
    """Fake-WebHID oracle trace: official JS driving the emulated device."""
    out, pending = [], None
    n = 0
    for line in path.open(encoding="utf-8"):
        rec = json.loads(line)
        m = rec.get("method")
        if m == "sendReport":
            if pending:
                out.append(pending)
            fns = STACK_FN.findall(rec.get("stack") or "")
            pending = {
                "source": "FAKE_ORACLE",
                "session": "oracle:HERO_84_HE",
                "seq": n,
                "direction": "HOST_TO_DEVICE",
                "report_id": rec.get("report_id"),
                "payload_hex": rec.get("bytes_hex"),
                "reply_hex": None,
                "fn": fns[0] if fns else None,
                "ts": rec.get("timestamp"),
                "ui_action_id": rec.get("ui_action_id"),
                "semantic_context": rec.get("semantic_context"),
            }
            n += 1
            if limit and n > limit:
                break
        elif m == "simulateInputReport" and pending is not None:
            pending["reply_hex"] = rec.get("bytes_hex")
            out.append(pending)
            pending = None
    if pending:
        out.append(pending)
    return out


#: The macro-assignment session of 2026-08-23 closes a collection gap: it is the
#: only capture in which opcode 0x83 is issued for a single item, so it is the
#: only frame that can tell a two-byte record apart from a six-byte one.  It is
#: appended LAST, after every source the baseline already had, so that no
#: existing transaction id moves and the two corpora stay comparable frame for
#: frame.  Two of its request frames were composed by the capture session itself
#: from the vendor bundle's own serializer rather than emitted by the vendor UI;
#: the device's replies to them are genuine.  That provenance is recorded on
#: every row it contributes.
#: The session label is deliberately neutral: `strip()` carries the session id
#: into the blind datasets, and the capture's own filename names the feature.
GAP_CLOSURE = ("physical_macro_assignment_20260823.jsonl",
               "chrome:session_d:20260823")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=("baseline", "gapclosed"),
                    default="baseline")
    args = ap.parse_args(argv)

    OBS.mkdir(parents=True, exist_ok=True)
    txs = []
    txs += load_pdevemu_physical()
    txs += load_physical_jsonl(
        RAW / "physical_init_20260822.jsonl", "PHYSICAL", "chrome:init:20260822")
    txs += load_physical_jsonl(
        RAW / "physical_device_settings_20260822.jsonl",
        "PHYSICAL", "chrome:device_settings:20260822")

    oracle_src = ROOT / "reports/oracle/aula_web/HERO_84_HE/raw_full_trace.jsonl"
    if oracle_src.exists():
        txs += load_fake_oracle(oracle_src)

    if args.variant == "gapclosed":
        extra = load_physical_jsonl(RAW / GAP_CLOSURE[0], "PHYSICAL", GAP_CLOSURE[1])
        for t in extra:
            t["provenance"] = "collection gap closure, appended 2026-08-24"
        txs += extra
        print(f"gap closure: +{len(extra)} transactions from {GAP_CLOSURE[0]}")

    for i, t in enumerate(txs):
        t["id"] = i

    out = OBS / ("transactions.jsonl" if args.variant == "baseline"
                 else "transactions_gapclosed.jsonl")
    with out.open("w", encoding="utf-8") as fh:
        for t in txs:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    by_source = {}
    paired = 0
    for t in txs:
        by_source[t["source"]] = by_source.get(t["source"], 0) + 1
        if t.get("reply_hex"):
            paired += 1
    print(f"wrote {out} : {len(txs)} transactions")
    print(f"  by source: {by_source}")
    print(f"  with reply: {paired}")
    groups = {}
    for t in txs:
        if t.get("payload_hex"):
            g = t["payload_hex"][:2]
            groups[g] = groups.get(g, 0) + 1
    print("  tx group byte histogram:",
          dict(sorted(groups.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
