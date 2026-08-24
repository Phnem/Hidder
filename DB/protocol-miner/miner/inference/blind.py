"""Build the three blind datasets for the inference benchmark.

Mode A  RAW_ONLY           bytes, direction, session, ordering, request/reply only
Mode B  CONTROLLED_ACTIONS + the UI value the operator changed, before and after
Mode C  PARTIAL_PROTOCOL   + a redacted copy of the family schema, with specific
                             fields / values / commands deliberately removed

Leakage control is the whole point of this module.  Everything that could carry
the answer is stripped: official JS function names, vendor notes, semantic
filenames, decoded field names.  Mode B keeps UI labels on purpose -- a real
analyst driving a real configurator can always see which control they clicked;
that is the capability being measured, not leakage.

Run:  python -m miner.inference.blind
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORPUS = ROOT / "reports" / "protocol_knowledge" / "aula" / "HERO_84_HE"
OBS = CORPUS / "observations" / "transactions.jsonl"
BENCH = CORPUS / "benchmark"

# Keys that must never reach an inference run.
LEAKY = {"fn", "vendor_note", "semantic_context", "note"}

# What mode C hides.  Everything else from the family schema is handed over.
# These are exactly the things the benchmark asks the engine to rediscover.
MODE_C_HIDDEN = {
    "checksum": "formula and parameters removed",
    "enum:polling_level": "the whole raw->Hz map removed",
    "field:0x13.travel.scale": "raw->mm factor removed",
    "field:0x13.key_id.endianness": "byte order removed",
    "record:0x19": "the entire rapid-trigger record layout removed",
    "record:0x16": "the entire deadzone record layout removed",
    "pair:0x10/0x90": "this GET/SET pair removed from the known list",
    "register:23": "register 23's meaning removed",
    "register:19": "register 19's meaning removed",
    "field:0x1a.name_length": "profile-name length/sentinel encoding removed",
}


def load(path=None):
    out = []
    for line in (path or OBS).open(encoding="utf-8"):
        out.append(json.loads(line))
    return out


def strip(tx, keep_ui=False, keep_phase=False):
    r = {
        "id": tx["id"],
        "session": tx["session"],
        "seq": tx["seq"],
        "source": tx["source"],
        "direction": tx["direction"],
        "report_id": tx.get("report_id"),
        "payload_hex": tx.get("payload_hex"),
        "reply_hex": tx.get("reply_hex"),
    }
    if tx.get("payload_truncated"):
        r["payload_truncated"] = True
    if tx.get("ts"):
        r["ts"] = tx["ts"]
    if keep_ui:
        for k in ("ui_before", "ui_after"):
            if tx.get(k):
                r[k] = tx[k]
    if keep_phase and tx.get("phase"):
        r["phase"] = tx["phase"]
    return r


def partial_schema():
    """The 70-90% of the family schema mode C is allowed to start from.

    Written by hand rather than derived from truth.json so that the hidden
    parts are hidden by construction, not by a filter that could leak.
    """
    return {
        "_note": "Known family schema handed to the engine in PARTIAL_PROTOCOL mode. Some parts are deliberately absent; the engine must rediscover them.",
        "framing": {
            "report_id_tx": 9,
            "payload_len": 63,
            "opcode_offset": 0,
            "sub_offset": 1,
            "length_offset": 5,
            "body_offset": 6,
            "checksum_offset": 62,
            "constant_offsets": {"2": 0, "4": 0}
        },
        "checksum": None,
        "get_set_rule": "GET = SET | 0x80",
        "known_get_set_pairs": [
            ["0x03", "0x83"], ["0x04", "0x84"], ["0x06", "0x86"],
            ["0x13", "0x93"], ["0x15", "0x95"], ["0x1a", "0x9a"]
        ],
        "feature_register_mechanism": {
            "set_opcode": "0x04",
            "get_opcode": "0x84",
            "register_id_at": 1,
            "length_at": 5,
            "value_at": 6,
            "known_registers": {
                "1": {"name": "light_mode", "len": 7},
                "17": {"name": "os_mode_mac", "len": 1, "type": "bool_u8"},
                "21": {"name": "win_lock", "len": 1, "type": "bool_u8"},
                "24": {"name": "key_combo_enable", "len": 1, "type": "bool_u8"},
                "25": {"name": "auto_calibrate_enable", "len": 1, "type": "bool_u8"},
                "30": {"name": "mechanical_debounce_us_time", "len": 2, "type": "u16be"}
            }
        },
        "known_record_layouts": {
            "0x13": {
                "body_offset": 6, "stride": 5,
                "fields": [
                    {"rel_offset": 0, "length": 2, "semantic": "key_id"},
                    {"rel_offset": 2, "length": 2, "semantic": "travel"},
                    {"rel_offset": 4, "length": 1, "type": "u8", "semantic": "distance_global"}
                ]
            },
            "0x83": {"body_offset": 6, "stride": 2,
                     "fields": [{"rel_offset": 0, "length": 2, "type": "u16be", "semantic": "key_id"}]}
        },
        "hidden_from_this_view": sorted(MODE_C_HIDDEN.keys())
    }


def main():
    BENCH.mkdir(parents=True, exist_ok=True)
    txs = load()

    modes = {
        "A_RAW_ONLY": [strip(t) for t in txs],
        "B_CONTROLLED_ACTIONS": [strip(t, keep_ui=True, keep_phase=True) for t in txs],
        "C_PARTIAL_PROTOCOL": [strip(t, keep_ui=True, keep_phase=True) for t in txs],
    }

    for name, rows in modes.items():
        p = BENCH / f"dataset_{name}.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name}: {len(rows)} rows -> {p.name}")

    (BENCH / "schema_C_partial.json").write_text(
        json.dumps(partial_schema(), indent=1, ensure_ascii=False), encoding="utf-8")
    (BENCH / "mode_C_hidden_manifest.json").write_text(
        json.dumps(MODE_C_HIDDEN, indent=1, ensure_ascii=False), encoding="utf-8")

    # leakage audit
    bad = []
    for name in modes:
        text = (BENCH / f"dataset_{name}.jsonl").read_text(encoding="utf-8")
        for probe in ("Lt.", "sync_", "fetch_", "polling", "actuation", "rapid",
                      "deadzone", "win_lock", "checksum", "travel"):
            if probe in text:
                bad.append((name, probe, text.count(probe)))
    print("leakage audit:", bad if bad else "clean")


if __name__ == "__main__":
    main()
