"""Measured report and deterministic source spot checks for full re-ingestion."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _sample(rows: list[sqlite3.Row], count: int) -> list[sqlite3.Row]:
    if len(rows) <= count:
        return rows
    return [rows[round(i * (len(rows) - 1) / (count - 1))] for i in range(count)]


def _source_path(row: sqlite3.Row) -> Path | None:
    if not row["local_path"] or not row["relative_path"]:
        return None
    return Path(row["local_path"]) / Path(row["relative_path"])


def _spot_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    specs = {
        "operation": ("""SELECT po.id entity_id,po.semantic needle,sr.local_path,sf.relative_path
            FROM protocol_operations po JOIN operation_evidence oe ON oe.operation_id=po.id
            JOIN source_files sf ON sf.id=oe.source_file_id JOIN source_roots sr ON sr.id=sf.source_root_id
            GROUP BY po.id ORDER BY po.id""", 30),
        "layout": ("""SELECT pl.id entity_id,pl.layout_name needle,sr.local_path,sf.relative_path
            FROM packet_layouts pl JOIN source_files sf ON sf.id=pl.source_file_id
            JOIN source_roots sr ON sr.id=sf.source_root_id ORDER BY pl.id""", 30),
        "struct": ("""SELECT sv.id entity_id,sv.struct_name needle,sr.local_path,sv.source_path relative_path
            FROM struct_validations sv JOIN source_roots sr ON sr.id=sv.source_root_id ORDER BY sv.id""", 30),
        "mapping": ("""SELECT dpm.product_id entity_id,'' needle,sr.local_path,sf.relative_path
            FROM device_protocol_mappings dpm JOIN source_files sf ON sf.id=dpm.source_file_id
            JOIN source_roots sr ON sr.id=sf.source_root_id ORDER BY dpm.product_id,dpm.protocol_family_id""", 30),
        "sequence": ("""SELECT ps.id entity_id,'' needle,sr.local_path,sf.relative_path
            FROM protocol_sequences ps JOIN source_files sf ON sf.id=ps.source_file_id
            JOIN source_roots sr ON sr.id=sf.source_root_id ORDER BY ps.id""", 20),
    }
    for kind, (sql, wanted) in specs.items():
        for row in _sample(conn.execute(sql).fetchall(), wanted):
            path = _source_path(row)
            exists = bool(path and path.is_file())
            needle_found = True
            if exists and row["needle"] and kind in {"layout", "struct"}:
                needle_found = row["needle"].lower() in path.read_text(encoding="utf-8", errors="replace").lower()
            checks.append({"kind": kind, "entity_id": row["entity_id"], "path": str(path) if path else None,
                           "exists": exists, "needle": row["needle"], "needle_found": needle_found,
                           "passed": exists and needle_found})
    return {"total": len(checks), "passed": sum(x["passed"] for x in checks),
            "failed": sum(not x["passed"] for x in checks), "checks": checks}


def build_full_report(db_path: Path, workspace: Path, output_json: Path, output_md: Path) -> dict[str, Any]:
    db_path = db_path.resolve(); workspace = workspace.resolve()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    try:
        coverage = _rows(conn, """SELECT sr.root_name,sr.source_kind,sr.repository_url,sr.commit_sha,sr.trust_class,
            sr.files_total,sr.files_relevant,sr.files_processed,sr.files_failed,sr.bytes_scanned,
            sum(sf.parse_status='parsed_protocol_data') protocol_data,
            sum(sf.parse_status='parsed_identity_only') identity_only,
            sum(sf.parse_status='parsed_metadata_only') metadata_only,
            sum(sf.parse_status='duplicate_or_derived') duplicate,
            sum(sf.parse_status='parsed_no_relevant_facts') no_relevant_facts,
            sum(sf.parse_status='unsupported_format') unsupported,
            sum(sf.parse_status='test_fixture') test_fixture,
            sum(sf.parse_status='documentation_only') documentation_only
            FROM source_roots sr LEFT JOIN source_files sf ON sf.source_root_id=sr.id
            GROUP BY sr.id ORDER BY sr.root_name""")
        data = {
            "products": _scalar(conn, "SELECT count(*) FROM products"),
            "device_identifiers": _scalar(conn, "SELECT count(*) FROM device_identifiers"),
            "unique_vid_pid": _scalar(conn, "SELECT count(*) FROM (SELECT vid,pid FROM device_identifiers WHERE vid IS NOT NULL AND pid IS NOT NULL GROUP BY vid,pid)"),
            "protocol_families": _scalar(conn, "SELECT count(*) FROM protocol_families"),
            "normalized_facts": _scalar(conn, "SELECT count(*) FROM normalized_facts"),
            "evidence_records": _scalar(conn, "SELECT count(*) FROM fact_evidence"),
            "typed_facts": _scalar(conn, "SELECT count(*) FROM typed_facts"),
            "typed_fact_evidence": _scalar(conn, "SELECT count(*) FROM typed_fact_evidence"),
            "lineage_groups": _scalar(conn, "SELECT count(DISTINCT lineage_group) FROM typed_fact_evidence"),
            "conflicts": _scalar(conn, "SELECT count(*) FROM fact_conflicts"),
            "packet_layouts": _scalar(conn, "SELECT count(*) FROM packet_layouts"),
            "validated_structs": _scalar(conn, "SELECT count(*) FROM struct_validations WHERE status='validated'"),
            "protocol_operations": _scalar(conn, "SELECT count(*) FROM protocol_operations"),
            "protocol_sequences": _scalar(conn, "SELECT count(*) FROM protocol_sequences"),
            "runtime_observations": _scalar(conn, "SELECT count(*) FROM runtime_observations"),
            "capture_transactions": _scalar(conn, "SELECT count(*) FROM capture_transactions"),
        }
        cross = {
            "typed_facts_ge2_evidence": _scalar(conn, "SELECT count(*) FROM (SELECT typed_fact_id FROM typed_fact_evidence GROUP BY typed_fact_id HAVING count(*)>=2)"),
            "typed_facts_ge2_independent": _scalar(conn, "SELECT count(*) FROM (SELECT typed_fact_id FROM typed_fact_evidence GROUP BY typed_fact_id HAVING count(DISTINCT lineage_group)>=2)"),
            "typed_facts_ge3_independent": _scalar(conn, "SELECT count(*) FROM (SELECT typed_fact_id FROM typed_fact_evidence GROUP BY typed_fact_id HAVING count(DISTINCT lineage_group)>=3)"),
            "operations_ge2_independent": _scalar(conn, "SELECT count(*) FROM (SELECT operation_id FROM operation_evidence GROUP BY operation_id HAVING count(DISTINCT lineage_group)>=2)"),
            "operations_official_plus_implementation": _scalar(conn, """SELECT count(*) FROM (SELECT operation_id FROM operation_evidence GROUP BY operation_id
                HAVING max(trust_class IN ('OfficialSpecification','OfficialSDK','OfficialVendorImplementation'))=1
                   AND max(trust_class IN ('KernelImplementation','UpstreamImplementation','IndependentImplementation','ReverseEngineeredImplementation','CommunityImplementation'))=1
                   AND count(DISTINCT lineage_group)>=2)"""),
            "operations_implementation_plus_capture": _scalar(conn, """SELECT count(*) FROM (SELECT operation_id FROM operation_evidence GROUP BY operation_id
                HAVING max(trust_class LIKE '%Implementation')=1 AND max(trust_class='CommunityCapture')=1)"""),
        }
        reconstructibility = _rows(conn, """SELECT coalesce(p.category,'other') category,dr.classification,count(*) count
            FROM device_reconstructibility dr JOIN products p ON p.id=dr.product_id
            GROUP BY coalesce(p.category,'other'),dr.classification ORDER BY category,dr.classification""")
        risks = _rows(conn, "SELECT risk_class,count(*) count FROM command_risks GROUP BY risk_class ORDER BY risk_class")
        risk_samples = _rows(conn, """SELECT cr.risk_class,po.semantic,po.scope_key,po.direction,cr.rationale
            FROM command_risks cr JOIN protocol_operations po ON po.id=cr.operation_id
            WHERE po.id IN (SELECT min(po2.id) FROM protocol_operations po2 JOIN command_risks cr2 ON cr2.operation_id=po2.id GROUP BY cr2.risk_class)
            ORDER BY cr.risk_class""")
        captures = _rows(conn, """SELECT sr.root_name,count(cf.id) captures,sum(cf.packet_count) packets,sum(cf.transaction_count) transactions
            FROM capture_files cf JOIN source_files sf ON sf.id=cf.source_file_id
            JOIN source_roots sr ON sr.id=sf.source_root_id
            GROUP BY sr.id ORDER BY sr.root_name""")
        source_results = _rows(conn, """SELECT sr.root_name,sr.files_processed,sr.files_failed,
            count(DISTINCT tfe.typed_fact_id) facts,count(DISTINCT oe.operation_id) operations,
            count(DISTINCT pl.id) layouts,count(DISTINCT ps.id) sequences
            FROM source_roots sr LEFT JOIN source_files sf ON sf.source_root_id=sr.id
            LEFT JOIN typed_fact_evidence tfe ON tfe.source_file_id=sf.id
            LEFT JOIN operation_evidence oe ON oe.source_file_id=sf.id
            LEFT JOIN packet_layouts pl ON pl.source_file_id=sf.id
            LEFT JOIN protocol_sequences ps ON ps.source_file_id=sf.id
            GROUP BY sr.id ORDER BY sr.root_name""")
        validation = {
            "layouts_by_status": _rows(conn, "SELECT validation_status,count(*) count FROM packet_layouts GROUP BY validation_status"),
            "structs_by_status": _rows(conn, "SELECT status,count(*) count FROM struct_validations GROUP BY status"),
            "operations_complete": _scalar(conn, "SELECT count(*) FROM operation_completeness WHERE complete=1"),
            "production_safe": _scalar(conn, "SELECT count(*) FROM protocol_operations WHERE production_safe=1"),
            "hardware_verified": _scalar(conn, "SELECT count(*) FROM device_reconstructibility WHERE hardware_validation_state='verified'"),
        }
        top_gaps = _rows(conn, """SELECT missing_requirements_json,count(*) count FROM operation_completeness
            WHERE complete=0 GROUP BY missing_requirements_json ORDER BY count DESC LIMIT 30""")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        orphans = {
            "typed_fact_evidence": _scalar(conn, "SELECT count(*) FROM typed_fact_evidence e LEFT JOIN typed_facts f ON f.id=e.typed_fact_id WHERE f.id IS NULL"),
            "operation_evidence": _scalar(conn, "SELECT count(*) FROM operation_evidence e LEFT JOIN protocol_operations o ON o.id=e.operation_id WHERE o.id IS NULL"),
            "sequence_steps": _scalar(conn, "SELECT count(*) FROM protocol_sequence_steps s LEFT JOIN protocol_sequences q ON q.id=s.sequence_id WHERE q.id IS NULL"),
            "source_lineage": _scalar(conn, "SELECT count(*) FROM source_lineage l LEFT JOIN source_roots r ON r.id=l.child_source_root_id WHERE r.id IS NULL"),
        }
        malformed_json = 0
        for table, column in (("typed_facts", "canonical_value_json"), ("protocol_operations", "request_encoding_json"),
                              ("operation_completeness", "missing_requirements_json")):
            malformed_json += _scalar(conn, f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND json_valid({column})=0")
        spot = _spot_checks(conn)
        manifest_path = workspace / "data" / "signalrgb-usbdata" / "manifest.json"
        usbdata = json.loads(manifest_path.read_text(encoding="utf-8"))["stats"] if manifest_path.exists() else {}
        report = {
            "database": {"relative_path": "data/registry.sqlite", "absolute_path": str(db_path),
                         "size_bytes": db_path.stat().st_size, "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
                         "schema_version": _scalar(conn, "SELECT max(version) FROM audit_schema_versions"),
                         "tables": _scalar(conn, "SELECT count(*) FROM sqlite_master WHERE type='table'"),
                         "integrity_check": integrity, "foreign_key_check": len(fk_rows)},
            "source_coverage": {"roots": len(coverage), "files_total": sum(x["files_total"] for x in coverage),
                                "relevant_files": sum(x["files_relevant"] for x in coverage),
                                "processed_relevant": sum(x["files_processed"] for x in coverage),
                                "unexplained_skipped": _scalar(conn, "SELECT count(*) FROM source_files WHERE relevant=1 AND parsed=0"),
                                "parse_failures": sum(x["files_failed"] for x in coverage), "by_root": coverage},
            "data": data, "cross_evidence": cross, "reconstructibility": reconstructibility,
            "risk": {"counts": risks, "samples": risk_samples}, "captures": captures,
            "signalrgb_usbdata": usbdata, "source_specific": source_results,
            "validation": validation, "top_gaps": top_gaps, "spot_checks": spot,
            "integrity": {"orphans": orphans, "malformed_json": malformed_json},
        }
    finally:
        conn.close()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# FULL TYPED RE-INGESTION RESULT", "", "Measured machine-readable details: `full_typed_reingestion_report.json`.", "",
          "## Database", "", f"- Path: `{report['database']['absolute_path']}`", f"- SHA256: `{report['database']['sha256']}`",
          f"- Integrity: `{report['database']['integrity_check']}`; FK violations: {report['database']['foreign_key_check']}", "",
          "## Coverage", "", f"- Roots: {report['source_coverage']['roots']}", f"- Files: {report['source_coverage']['files_total']}",
          f"- Relevant/processed: {report['source_coverage']['relevant_files']}/{report['source_coverage']['processed_relevant']}",
          f"- Unexplained skipped: {report['source_coverage']['unexplained_skipped']}", f"- Proven parse failures: {report['source_coverage']['parse_failures']}", "",
          "## Typed results", "", f"- Typed facts/evidence: {data['typed_facts']}/{data['typed_fact_evidence']}",
          f"- Operations/sequences/layouts: {data['protocol_operations']}/{data['protocol_sequences']}/{data['packet_layouts']}",
          f"- Captures/transactions: {sum(x['captures'] for x in captures)}/{data['capture_transactions']}",
          f"- Complete operations: {validation['operations_complete']}; ProductionSafe: {validation['production_safe']}", "",
          "## Validation", "", f"- Spot checks: {spot['passed']}/{spot['total']} passed", f"- Malformed stored JSON: {malformed_json}",
          f"- Orphans: {sum(orphans.values())}"]
    output_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    return report
