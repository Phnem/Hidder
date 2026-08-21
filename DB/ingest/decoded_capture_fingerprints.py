"""Exact payload fingerprints derived only from published decoded USB traffic.

This pass deliberately has no operation tables or semantic matching logic.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


EXACT_TABLE = "capture_exact_payload_fingerprints"
EXACT_STAGING_TABLE = "capture_exact_payload_fingerprints_staging"
STRUCTURAL_TABLE = "capture_structural_packet_families"
STRUCTURAL_STAGING_TABLE = "capture_structural_packet_families_staging"


def _create_exact_table(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"""CREATE TABLE {name}(
        id INTEGER PRIMARY KEY,
        transfer_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        interface INTEGER,
        endpoint INTEGER,
        control_stage TEXT,
        bm_request_type INTEGER,
        b_request INTEGER,
        hid_report_type TEXT,
        hid_report_id INTEGER,
        report_id_source TEXT,
        payload_length INTEGER NOT NULL,
        exact_payload_sha256 TEXT NOT NULL,
        representative_payload BLOB NOT NULL,
        occurrences INTEGER NOT NULL,
        capture_count INTEGER NOT NULL
    )""")


def build_exact_payload_fingerprints(db_path: Path) -> dict[str, int]:
    """Deduplicate decoded payloads without including transport-instance fields.

    The temporary table is the only mutation until the final table swap.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        published = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_decoded_transactions'").fetchone()
        if not published:
            raise RuntimeError("decoded USB transaction table has not been published")
        conn.create_function("payload_sha256", 1, lambda blob: hashlib.sha256(blob).hexdigest(), deterministic=True)
        conn.execute(f"DROP TABLE IF EXISTS {EXACT_STAGING_TABLE}")
        _create_exact_table(conn, EXACT_STAGING_TABLE)
        conn.commit()
        conn.execute(f"""INSERT INTO {EXACT_STAGING_TABLE}(
                transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,
                hid_report_type,hid_report_id,report_id_source,payload_length,exact_payload_sha256,
                representative_payload,occurrences,capture_count)
            SELECT transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,
                   hid_report_type,hid_report_id,report_id_source,decoded_payload_length,
                   payload_sha256(decoded_payload),min(decoded_payload),count(*),count(DISTINCT capture_file_id)
            FROM capture_decoded_transactions
            WHERE decode_status='decoded' AND decoded_payload IS NOT NULL
            GROUP BY transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,
                     hid_report_type,hid_report_id,report_id_source,decoded_payload_length,
                     payload_sha256(decoded_payload)""")
        total = conn.execute(f"SELECT count(*) FROM {EXACT_STAGING_TABLE}").fetchone()[0]
        occurrences = conn.execute(f"SELECT coalesce(sum(occurrences),0) FROM {EXACT_STAGING_TABLE}").fetchone()[0]
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (EXACT_TABLE,)).fetchone():
            conn.execute(f"ALTER TABLE {EXACT_TABLE} RENAME TO {EXACT_TABLE}_previous")
        conn.execute(f"ALTER TABLE {EXACT_STAGING_TABLE} RENAME TO {EXACT_TABLE}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (f"{EXACT_TABLE}_previous",)).fetchone():
            conn.execute(f"DROP TABLE {EXACT_TABLE}_previous")
        conn.execute(f"""CREATE INDEX idx_capture_exact_payload_shape ON {EXACT_TABLE}(
                     transfer_type,direction,interface,endpoint,hid_report_type,hid_report_id,payload_length)""")
        conn.execute(f"CREATE INDEX idx_capture_exact_payload_hash ON {EXACT_TABLE}(exact_payload_sha256)")
        conn.commit()
    return {"exact_payload_fingerprints": total, "decoded_payload_occurrences": occurrences}


def _create_structural_table(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"""CREATE TABLE {name}(
        id INTEGER PRIMARY KEY,
        transfer_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        interface INTEGER,
        endpoint INTEGER,
        control_stage TEXT,
        bm_request_type INTEGER,
        b_request INTEGER,
        hid_report_type TEXT,
        hid_report_id INTEGER,
        report_id_source TEXT,
        payload_length INTEGER NOT NULL,
        prefix_anchor BLOB NOT NULL,
        stable_byte_mask BLOB NOT NULL,
        variable_byte_mask BLOB NOT NULL,
        stable_bytes BLOB NOT NULL,
        exact_member_count INTEGER NOT NULL,
        occurrences INTEGER NOT NULL
    )""")


def _structural_key(row: sqlite3.Row) -> tuple[object, ...]:
    payload = bytes(row["representative_payload"])
    return tuple(row[name] for name in (
        "transfer_type", "direction", "interface", "endpoint", "control_stage", "bm_request_type",
        "b_request", "hid_report_type", "hid_report_id", "report_id_source", "payload_length",
    )) + (payload[:min(3, len(payload))],)


def _family_values(key: tuple[object, ...], stable: bytearray, stable_mask: bytearray,
                   member_count: int, occurrences: int) -> tuple[object, ...]:
    stable_bytes = bytes(value if mask else 0 for value, mask in zip(stable, stable_mask))
    variable_mask = bytes(0 if mask else 0xFF for mask in stable_mask)
    return key + (bytes(stable_mask), variable_mask, stable_bytes, member_count, occurrences)


def build_structural_packet_families(db_path: Path, batch_size: int = 10_000) -> dict[str, int]:
    """Create conservative structural families from exact fingerprints.

    A family is anchored by the first three payload bytes (or fewer for a short
    report) plus transport/report shape.  This deliberately favours splitting
    over false merging.  Within an anchor, stable and variable masks are
    derived byte-for-byte from every exact member.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (EXACT_TABLE,)).fetchone():
            raise RuntimeError("exact payload fingerprint table has not been published")
        conn.execute(f"DROP TABLE IF EXISTS {STRUCTURAL_STAGING_TABLE}")
        _create_structural_table(conn, STRUCTURAL_STAGING_TABLE)
        conn.commit()
        columns = "transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,hid_report_type,hid_report_id,report_id_source,payload_length,representative_payload,occurrences"
        rows = conn.execute(f"SELECT {columns} FROM {EXACT_TABLE} ORDER BY transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,hid_report_type,hid_report_id,report_id_source,payload_length,representative_payload")
        insert = f"""INSERT INTO {STRUCTURAL_STAGING_TABLE}(
            transfer_type,direction,interface,endpoint,control_stage,bm_request_type,b_request,hid_report_type,
            hid_report_id,report_id_source,payload_length,prefix_anchor,stable_byte_mask,variable_byte_mask,
            stable_bytes,exact_member_count,occurrences) VALUES({','.join('?' for _ in range(17))})"""
        current_key: tuple[object, ...] | None = None
        stable: bytearray | None = None
        stable_mask: bytearray | None = None
        member_count = occurrences = 0
        batch: list[tuple[object, ...]] = []
        family_count = 0
        for row in rows:
            key = _structural_key(row)
            payload = bytes(row["representative_payload"])
            if current_key != key:
                if current_key is not None:
                    batch.append(_family_values(current_key, stable, stable_mask, member_count, occurrences))
                    family_count += 1
                current_key, stable = key, bytearray(payload)
                stable_mask, member_count, occurrences = bytearray(b"\xff" * len(payload)), 1, row["occurrences"]
            else:
                for index, value in enumerate(payload):
                    if stable_mask[index] and stable[index] != value:
                        stable_mask[index] = 0
                member_count += 1
                occurrences += row["occurrences"]
            if len(batch) >= batch_size:
                conn.executemany(insert, batch); conn.commit(); batch.clear()
        if current_key is not None:
            batch.append(_family_values(current_key, stable, stable_mask, member_count, occurrences)); family_count += 1
        if batch:
            conn.executemany(insert, batch); conn.commit()
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (STRUCTURAL_TABLE,)).fetchone():
            conn.execute(f"ALTER TABLE {STRUCTURAL_TABLE} RENAME TO {STRUCTURAL_TABLE}_previous")
        conn.execute(f"ALTER TABLE {STRUCTURAL_STAGING_TABLE} RENAME TO {STRUCTURAL_TABLE}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (f"{STRUCTURAL_TABLE}_previous",)).fetchone():
            conn.execute(f"DROP TABLE {STRUCTURAL_TABLE}_previous")
        conn.execute(f"""CREATE INDEX idx_capture_structural_packet_shape ON {STRUCTURAL_TABLE}(
                     transfer_type,direction,interface,endpoint,hid_report_type,hid_report_id,payload_length)""")
        conn.commit()
    return {"structural_packet_families": family_count}


def write_decode_fingerprint_report(db_path: Path, report_path: Path) -> dict[str, object]:
    """Write an audit report for decoded traffic and non-semantic fingerprints."""
    with sqlite3.connect(db_path) as conn:
        def count(table: str) -> int:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        decoded_status = dict(conn.execute("SELECT decode_status,count(*) FROM capture_decoded_transactions GROUP BY decode_status"))
        failures = dict(conn.execute("SELECT coalesce(failure_reason,'none'),count(*) FROM capture_decoded_transactions WHERE decode_status!='decoded' GROUP BY failure_reason"))
        def grouped(column: str) -> list[dict[str, object]]:
            return [dict(zip((column, "exact_fingerprints", "occurrences"), row)) for row in conn.execute(
                f"SELECT {column},count(*),sum(occurrences) FROM {EXACT_TABLE} GROUP BY {column} ORDER BY count(*) DESC")]
        raw = count("capture_transactions")
        decoded = decoded_status.get("decoded", 0)
        exact = count(EXACT_TABLE)
        structural = count(STRUCTURAL_TABLE)
        correlation_levels = {level: 0 for level in (
            "CANDIDATE", "STRUCTURAL_MATCH", "SEQUENCE_MATCH", "SEMANTIC_CORRELATED", "INDEPENDENTLY_CONFIRMED")}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operation_capture_correlations'").fetchone():
            correlation_levels.update(dict(conn.execute("SELECT confidence_level,count(*) FROM operation_capture_correlations GROUP BY confidence_level")))
        requirement_rows = conn.execute("SELECT requirement,state,count(*) FROM operation_requirement_states GROUP BY requirement,state ORDER BY count(*) DESC").fetchall()
        reconstructibility = dict(conn.execute("SELECT classification,count(*) FROM device_reconstructibility GROUP BY classification"))
        sessions = count("capture_device_sessions") if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_device_sessions'").fetchone() else 0
        urb_pairs = count("capture_control_request_response_pairs") if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_control_request_response_pairs'").fetchone() else 0
        report: dict[str, object] = {
            "semantic_correlation_performed": False,
            "raw_transactions": raw,
            "decode": {"statuses": decoded_status, "failure_reasons": failures},
            "exact_payload_fingerprints": exact,
            "structural_packet_families": structural,
            "compression_ratio": {
                "raw_to_exact": raw / exact if exact else None,
                "decoded_to_structural": decoded / structural if structural else None,
            },
            "correlations": correlation_levels,
            "operations_with_any_capture_match": 0,
            "operations_with_semantic_capture_match": 0,
            "operations_with_independent_capture_evidence": 0,
            "capture_sessions": sessions,
            "control_urb_lifecycle_pairs": urb_pairs,
            "reconstructibility_unchanged": reconstructibility,
            "top_unknown_requirements": [dict(zip(("requirement", "state", "count"), row)) for row in requirement_rows if row[1] == "UNKNOWN"][:10],
            "top_conflicted_requirements": [dict(zip(("requirement", "state", "count"), row)) for row in requirement_rows if row[1] == "CONFLICTED"][:10],
            "fingerprints_by": {
                "transfer_type": grouped("transfer_type"),
                "direction": grouped("direction"),
                "report_id": grouped("hid_report_id"),
                "packet_length": grouped("payload_length"),
            },
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
