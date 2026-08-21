"""Conservative, deduplicated capture-to-operation correlation.

This module intentionally emits only CANDIDATE and STRUCTURAL_MATCH from raw
traffic.  SEQUENCE_MATCH and stronger levels require a decoder/caller context
and are never inferred from packet repetition alone.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


LEVELS = ("CANDIDATE", "STRUCTURAL_MATCH", "SEQUENCE_MATCH", "SEMANTIC_CORRELATED", "INDEPENDENTLY_CONFIRMED")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS capture_packet_fingerprints (
        id INTEGER PRIMARY KEY, capture_file_id INTEGER NOT NULL REFERENCES capture_files(id) ON DELETE CASCADE,
        transport TEXT, interface INTEGER, endpoint INTEGER, direction TEXT, report_id INTEGER,
        payload_length INTEGER NOT NULL, exact_payload_hash TEXT NOT NULL, occurrences INTEGER NOT NULL,
        first_sequence INTEGER, last_sequence INTEGER,
        UNIQUE(capture_file_id,transport,interface,endpoint,direction,report_id,payload_length,exact_payload_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_capture_fingerprint_shape ON capture_packet_fingerprints(transport,direction,report_id,payload_length);
    CREATE TABLE IF NOT EXISTS operation_capture_correlations (
        operation_id INTEGER NOT NULL REFERENCES protocol_operations(id) ON DELETE CASCADE,
        fingerprint_id INTEGER NOT NULL REFERENCES capture_packet_fingerprints(id) ON DELETE CASCADE,
        confidence_level TEXT NOT NULL CHECK(confidence_level IN ('CANDIDATE','STRUCTURAL_MATCH','SEQUENCE_MATCH','SEMANTIC_CORRELATED','INDEPENDENTLY_CONFIRMED')),
        confidence REAL NOT NULL, rationale_json TEXT NOT NULL,
        PRIMARY KEY(operation_id,fingerprint_id)
    );
    CREATE INDEX IF NOT EXISTS idx_operation_capture_level ON operation_capture_correlations(operation_id,confidence_level);
    """)


def build_capture_fingerprints_and_candidates(db_path: Path) -> dict[str, int]:
    """Deduplicate frames and create shape-only operation candidates.

    Fingerprints use exact-payload hashes; frequency is stored as one count, so
    millions of repeated RGB frames create one evidence unit.  Since imported
    USBPcap frames do not yet expose interface/endpoint/direction reliably,
    matches cannot exceed STRUCTURAL_MATCH in this pass.
    """
    stats: Counter[str] = Counter()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON")
        conn.create_function("payload_sha256", 1, lambda value: hashlib.sha256((value or "").encode()).hexdigest())
        _ensure_schema(conn)
        before = conn.execute("SELECT count(*) FROM capture_packet_fingerprints").fetchone()[0]
        conn.execute("""INSERT OR IGNORE INTO capture_packet_fingerprints(
              capture_file_id,transport,interface,endpoint,direction,report_id,payload_length,
              exact_payload_hash,occurrences,first_sequence,last_sequence)
            SELECT capture_file_id,transfer_type,interface,endpoint,direction,report_id,payload_length,
                   payload_sha256(payload_hex),count(*),min(sequence_no),max(sequence_no)
            FROM capture_transactions
            GROUP BY capture_file_id,transfer_type,interface,endpoint,direction,report_id,payload_length,payload_hex""")
        stats["fingerprints_created"] = conn.execute("SELECT count(*) FROM capture_packet_fingerprints").fetchone()[0] - before
        stats["fingerprints_total"] = conn.execute("SELECT count(*) FROM capture_packet_fingerprints").fetchone()[0]
        # A length/report-id overlap is candidate evidence; exact payload bytes
        # are intentionally not compared to symbolic static expressions.
        rows = conn.execute("""SELECT po.id operation_id,fp.id fingerprint_id,
                CASE WHEN po.report_id IS NOT NULL AND lower(po.report_id)=printf('0x%02x',fp.report_id)
                          AND coalesce(po.api_length,po.wire_length)=fp.payload_length
                     THEN 'STRUCTURAL_MATCH' ELSE 'CANDIDATE' END level
            FROM protocol_operations po JOIN capture_packet_fingerprints fp
              ON (po.api_length=fp.payload_length OR po.wire_length=fp.payload_length
                  OR (po.report_id IS NOT NULL AND lower(po.report_id)=printf('0x%02x',fp.report_id)))
            WHERE po.operation_status!='rejected'""").fetchall()
        for row in rows:
            level = row["level"]; confidence = .55 if level == "STRUCTURAL_MATCH" else .30
            conn.execute("""INSERT OR IGNORE INTO operation_capture_correlations(
                operation_id,fingerprint_id,confidence_level,confidence,rationale_json)
                VALUES(?,?,?,?,?)""", (row["operation_id"], row["fingerprint_id"], level, confidence,
                 json.dumps({"basis":"deduplicated_transport_shape","promotes_reconstructibility":False})))
            stats[level] += 1
        conn.commit()
    return dict(stats)
