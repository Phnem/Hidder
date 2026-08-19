"""SQLite scanner for embedded databases inside desktop driver packages."""

import sqlite3
from pathlib import Path
from typing import NamedTuple

from ingest.logging_setup import log_scan, get_logger
from ingest.normalize.evidence import DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
from ingest.normalize.identifiers import extract_vid_pid_from_text, normalize_vid_pid

logger = get_logger()


class SqliteScanResult(NamedTuple):
    identifiers: list[DeviceIdentifierFact]
    hints: list[ProtocolHintFact]
    facts: list[GenericFact]


class SqliteScanner:
    def scan_file(self, file_path: Path, artifact_sha256: str, product_id: int | None = None) -> SqliteScanResult:
        identifiers: list[DeviceIdentifierFact] = []
        hints: list[ProtocolHintFact] = []
        facts: list[GenericFact] = []

        try:
            log_scan(f"Scanning embedded SQLite database: {file_path.name}")
            # Open strictly in read-only URI mode
            uri = f"file:{file_path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row

            try:
                # Find all tables
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

                for tbl in tables:
                    try:
                        cursor = conn.execute(f"SELECT * FROM {tbl} LIMIT 500")
                        col_names = [desc[0].lower() for desc in cursor.description]
                        
                        for row in cursor.fetchall():
                            row_dict = {col_names[i]: row[i] for i in range(len(col_names))}
                            
                            # Check VID/PID columns
                            vid_col = next((c for c in ["vid", "vendor_id", "vendorid"] if c in row_dict), None)
                            pid_col = next((c for c in ["pid", "product_id", "productid"] if c in row_dict), None)
                            
                            if vid_col and pid_col and row_dict[vid_col] is not None and row_dict[pid_col] is not None:
                                norm = normalize_vid_pid(row_dict[vid_col], row_dict[pid_col])
                                if norm:
                                    identifiers.append(DeviceIdentifierFact(
                                        product_id=product_id,
                                        vid=norm.vid,
                                        pid=norm.pid,
                                        vid_hex=norm.vid_hex,
                                        pid_hex=norm.pid_hex,
                                        product_string=str(row_dict.get("name") or row_dict.get("model") or ""),
                                        artifact_sha256=artifact_sha256,
                                        evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                                        confidence=1.0
                                    ))

                            # Scan row text for VID/PID strings
                            row_text = " ".join(str(v) for v in row if v is not None)
                            for item in extract_vid_pid_from_text(row_text):
                                if not any(i.vid == item.vid and i.pid == item.pid for i in identifiers):
                                    identifiers.append(DeviceIdentifierFact(
                                        product_id=product_id,
                                        vid=item.vid,
                                        pid=item.pid,
                                        vid_hex=item.vid_hex,
                                        pid_hex=item.pid_hex,
                                        artifact_sha256=artifact_sha256,
                                        evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                                        confidence=0.85
                                    ))
                    except Exception:
                        continue
            finally:
                conn.close()

        except Exception as e:
            logger.debug(f"[scan] SQLite scan error for {file_path.name}: {e}")

        return SqliteScanResult(identifiers, hints, facts)
