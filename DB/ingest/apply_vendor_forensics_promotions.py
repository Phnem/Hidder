"""Atomically promote only audited vendor-inbox static facts.

No inferred operation, product merge, family equivalence, or readiness state is
written here.  The sole writable facts are exact VID/PID declarations from
non-generic configuration artifacts and narrowly-scoped host transport API
dependencies from vendor binaries.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "registry.sqlite"
REPORT = ROOT / "reports" / "vendor_software_forensics.json"
OUT = ROOT / "reports" / "vendor_software_forensics_promotion.json"
ROOT_NAME = "vendor-inbox-forensics"
LINEAGE = "vendor-inbox-official-static"

EXCLUDED_PATH_PARTS = ("\\docs\\", "\\data\\templates\\", "\\changelog\\", "\\lib\\", "\\test\\", "\\examples\\")
PLACEHOLDER_IDS = {(0, 0), (0x1234, 0x5678), (0xFEED, 0), (4, 4)}


def _fact(conn: sqlite3.Connection, fact_type: str, scope_key: str, semantic: str, key: str,
          value: dict, file_id: int, line: int | None, method: str, confidence: float, sha: str) -> int:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value_hash = hashlib.sha256(canonical.encode()).hexdigest()
    conn.execute("""INSERT OR IGNORE INTO typed_facts
                 (fact_type,scope_type,scope_key,semantic_type,canonical_key,canonical_value_json,value_hash,confidence)
                 VALUES(?,?,?,?,?,?,?,?)""", (fact_type, "vendor_artifact", scope_key, semantic, key, canonical, value_hash, confidence))
    fact_id = conn.execute("""SELECT id FROM typed_facts WHERE fact_type=? AND scope_type='vendor_artifact'
                           AND scope_key=? AND semantic_type=? AND canonical_key=? AND value_hash=?""",
                           (fact_type, scope_key, semantic, key, value_hash)).fetchone()[0]
    conn.execute("""INSERT OR IGNORE INTO typed_fact_evidence
                 (typed_fact_id,source_file_id,line_start,line_end,symbol,extraction_method,trust_class,lineage_group,
                  confidence,provenance_status,artifact_sha256)
                 VALUES(?,?,?,?,?,?,?,?,?,'exact_file',?)""",
                 (fact_id, file_id, line, line, None, method, "OfficialVendorImplementation", LINEAGE, confidence, sha))
    return fact_id


def _source_file(conn: sqlite3.Connection, root_id: int, finding: dict) -> int:
    row = conn.execute("SELECT id FROM source_files WHERE source_root_id=? AND relative_path=? AND content_hash=?",
                       (root_id, finding["path"], finding["sha256"])).fetchone()
    if row: return row[0]
    return conn.execute("""INSERT INTO source_files
        (source_root_id,relative_path,content_hash,size,relevant,parsed,parser_name,parse_status,bytes_scanned,
         collector_version,facts_extracted,operations_extracted,layouts_extracted,sequences_extracted)
        VALUES(?,?,?,?,1,1,'DeepVendorStaticForensics','parsed_protocol_data',?,?,0,0,0,0)
        RETURNING id""", (root_id, finding["path"], finding["sha256"], finding["size"], finding["size"],
                            "deep-vendor-forensics/1")).fetchone()[0]


def _accepted_identity(finding: dict, value: dict) -> bool:
    if finding["kind"] != "CONFIG_OR_DATABASE" or finding["generic_component"]:
        return False
    low = ("\\" + finding["path"].replace("/", "\\").lower())
    if any(p in low for p in EXCLUDED_PATH_PARTS): return False
    return (value["vid"], value["pid"]) not in PLACEHOLDER_IDS


def _transport_api(profile: dict) -> list[str]:
    imports = profile.get("hid_usb_imports", [])
    return sorted(set(x for x in imports if any(t in x.lower() for t in ("hid.dll", "hidd_", "hidp_", "winusb", "libusb"))))


def run() -> dict:
    data = json.loads(REPORT.read_text(encoding="utf8"))
    staged: list[tuple[str, dict, dict]] = []
    for finding in data["findings"]:
        for ident in finding.get("text_analysis", {}).get("vid_pid", []):
            if _accepted_identity(finding, ident): staged.append(("identity", finding, ident))
        apis = _transport_api(finding.get("pe", {}))
        if apis and not finding["generic_component"]:
            staged.append(("transport", finding, {"apis": apis}))
    # Deduplicate repeated config declarations from a firmware package while
    # retaining their individual provenance when the same fact already exists.
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    before = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("source_files", "typed_facts", "typed_fact_evidence")}
    promoted: list[dict] = []
    try:
        con.execute("BEGIN IMMEDIATE")
        root = con.execute("SELECT id FROM source_roots WHERE root_name=?", (ROOT_NAME,)).fetchone()
        if root: root_id = root[0]
        else:
            root_id = con.execute("""INSERT INTO source_roots(root_name,local_path,source_kind,audit_status,trust_class,lineage_group,
                                    files_total,files_relevant,files_processed,files_failed,bytes_scanned,collector_version)
                                    VALUES(?,?, 'vendor_artifact_collection','forensic_complete','OfficialVendorImplementation',?,0,0,0,0,0,?)
                                    RETURNING id""", (ROOT_NAME, str(ROOT / "protocol-miner" / "inbox"), LINEAGE,
                                                        "deep-vendor-forensics/1")).fetchone()[0]
            con.execute("INSERT OR IGNORE INTO source_lineage(child_source_root_id,parent_source_root_id,relationship,rationale) VALUES(?,NULL,'unknown','manually collected vendor application artifacts')", (root_id,))
        for kind, finding, value in staged:
            file_id = _source_file(con, root_id, finding)
            scope = f"vendor:{finding['origin_brand']}:{finding['sha256']}"
            if kind == "identity":
                fact_id = _fact(con, "DeviceIdentity", scope, "device.usb_identity", f"vidpid:{value['vid']:04x}:{value['pid']:04x}",
                                {"vid": value["vid"], "pid": value["pid"], "origin_brand": finding["origin_brand"],
                                 "origin_artifact": finding["origin_path"]}, file_id, value["line"],
                                "deep_vendor_structured_vid_pid", .80, finding["sha256"])
            else:
                fact_id = _fact(con, "TransportContract", scope, "transport.host_api_dependency", "windows_hid_dependency",
                                {"transport": "hid_or_winusb_host_api_dependency", "apis": value["apis"],
                                 "scope_limit": "application dependency; no operation or device binding inferred"}, file_id, None,
                                "deep_vendor_pe_import_table", .72, finding["sha256"])
            promoted.append({"kind": kind, "fact_id": fact_id, "path": finding["path"], "origin_brand": finding["origin_brand"]})
        # Publish conditions: all promoted evidence points at the source root,
        # never creates an operation, and every fact has exact-file provenance.
        missing = con.execute("""SELECT count(*) FROM typed_fact_evidence e LEFT JOIN source_files f ON f.id=e.source_file_id
                               WHERE e.lineage_group=? AND f.id IS NULL""", (LINEAGE,)).fetchone()[0]
        assert missing == 0
        assert con.execute("SELECT count(*) FROM protocol_operations WHERE source_trust='OfficialVendorImplementation' AND operation_status='vendor_forensics' ").fetchone()[0] == 0
        con.execute("UPDATE source_roots SET files_total=?,files_relevant=?,files_processed=?,bytes_scanned=? WHERE id=?",
                    (len({p[1]['sha256'] for p in staged}), len({p[1]['sha256'] for p in staged}), len({p[1]['sha256'] for p in staged}),
                     sum(p[1]['size'] for p in staged), root_id))
        con.commit()
        after = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
        result = {"status": "PASS", "atomic_publish": "COMMITTED", "source_root": ROOT_NAME, "root_id": root_id,
                  "staged": len(staged), "promoted": len(promoted), "promotions": promoted,
                  "counts_before": before, "counts_after": after,
                  "by_kind": {k: sum(p["kind"] == k for p in promoted) for k in ("identity", "transport")},
                  "negative_assertions": {"operations_created": 0, "product_bindings_created": 0, "family_merges_created": 0}}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    stage = OUT.with_suffix(".staging.json")
    stage.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    stage.replace(OUT)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=True))
