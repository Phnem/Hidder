"""Atomic, evidence-preserving USBPcap decode pass.

Raw frames in ``capture_transactions`` are never updated.  Decoding is first
written to a staging table in bounded batches; only a successful full pass
atomically replaces the decoded derivative table.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from pathlib import Path

from ingest.usbpcap_decoder import USBPcapURB, decode_usbpcap_frame_detailed


PRODUCTION_TABLE = "capture_decoded_transactions"
STAGING_TABLE = "capture_decoded_transactions_staging"


def _create_table(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"""CREATE TABLE {name} (
        id INTEGER PRIMARY KEY,
        raw_transaction_id INTEGER NOT NULL UNIQUE REFERENCES capture_transactions(id) ON DELETE CASCADE,
        capture_file_id INTEGER NOT NULL REFERENCES capture_files(id) ON DELETE CASCADE,
        frame_number INTEGER NOT NULL,
        timestamp REAL,
        urb_id_hex TEXT,
        urb_status INTEGER,
        urb_function INTEGER,
        irp_info INTEGER,
        usb_bus_id INTEGER,
        device_address INTEGER,
        interface INTEGER,
        transfer_type TEXT,
        endpoint INTEGER,
        direction TEXT,
        control_stage TEXT,
        bm_request_type INTEGER,
        b_request INTEGER,
        w_value INTEGER,
        w_index INTEGER,
        w_length INTEGER,
        hid_report_type TEXT,
        hid_report_id INTEGER,
        report_id_source TEXT,
        decoded_payload_length INTEGER,
        decoded_payload BLOB,
        decode_status TEXT NOT NULL CHECK(decode_status IN ('decoded','unsupported','failed')),
        failure_reason TEXT,
        raw_frame_sha256 TEXT NOT NULL
    )""")


def _report_type(value: int | None) -> str | None:
    return {1: "input", 2: "output", 3: "feature"}.get(value) if value is not None else None


def _decoded_values(row: sqlite3.Row) -> tuple[object, ...]:
    raw = bytes.fromhex(row["payload_hex"])
    raw_hash = hashlib.sha256(raw).hexdigest()
    common: list[object] = [row["id"], row["capture_file_id"], row["sequence_no"] + 1,
                            row["timestamp"]]
    if row["transfer_type"] != "linktype:249":
        return tuple(common + [None] * 21 + ["unsupported", "unsupported_linktype", raw_hash])
    urb, reason = decode_usbpcap_frame_detailed(raw)
    if urb is None:
        return tuple(common + [None] * 21 + ["failed", reason, raw_hash])
    setup = urb.setup or {}
    is_hid_control = urb.transfer_type == "control" and setup.get("bRequest") in {1, 9}
    report_type_code = (setup.get("wValue", 0) >> 8) & 0xFF if is_hid_control else None
    # Interrupt reports have no setup header.  Their first byte is preserved as
    # an explicit candidate, not asserted as a descriptor-verified report ID.
    report_id = (setup.get("wValue", 0) & 0xFF) if is_hid_control else (urb.payload[0] if urb.transfer_type == "interrupt" and urb.payload else None)
    report_id_source = "control_setup" if is_hid_control else ("payload_byte_candidate" if report_id is not None else None)
    interface = setup.get("wIndex") if urb.transfer_type == "control" and urb.control_stage == "setup" else None
    return tuple(common + [
        f"0x{urb.urb_id:016x}", urb.urb_status, urb.urb_function, urb.irp_info,
        urb.bus_id, urb.device_address, interface, urb.transfer_type, urb.endpoint,
        urb.direction, urb.control_stage, setup.get("bmRequestType"), setup.get("bRequest"),
        setup.get("wValue"), setup.get("wIndex"), setup.get("wLength"),
        _report_type(report_type_code), report_id, report_id_source, len(urb.payload),
        urb.payload, "decoded", None, raw_hash,
    ])


_INSERT_SQL = f"""INSERT INTO {STAGING_TABLE}(
    raw_transaction_id,capture_file_id,frame_number,timestamp,urb_id_hex,urb_status,urb_function,irp_info,
    usb_bus_id,device_address,interface,transfer_type,endpoint,direction,control_stage,bm_request_type,
    b_request,w_value,w_index,w_length,hid_report_type,hid_report_id,report_id_source,
    decoded_payload_length,decoded_payload,decode_status,failure_reason,raw_frame_sha256)
    VALUES({','.join('?' for _ in range(28))})"""


def decode_all_captures(db_path: Path, batch_size: int = 10_000) -> dict[str, int]:
    """Decode every raw transaction into staging, then atomically publish it.

    The pass is idempotent: an interrupted run leaves only a disposable staging
    table and keeps the prior production derivative untouched.
    """
    stats: Counter[str] = Counter()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"DROP TABLE IF EXISTS {STAGING_TABLE}")
        _create_table(conn, STAGING_TABLE)
        conn.commit()
        cursor = conn.execute("""SELECT id,capture_file_id,sequence_no,timestamp,transfer_type,payload_hex
                                 FROM capture_transactions ORDER BY id""")
        batch: list[tuple[object, ...]] = []
        for row in cursor:
            values = _decoded_values(row)
            batch.append(values)
            stats[values[-3]] += 1
            if values[-2]:
                stats[f"reason:{values[-2]}"] += 1
            if len(batch) >= batch_size:
                conn.executemany(_INSERT_SQL, batch)
                conn.commit()
                batch.clear()
        if batch:
            conn.executemany(_INSERT_SQL, batch)
            conn.commit()
        stats["transactions_total"] = sum(stats[key] for key in ("decoded", "unsupported", "failed"))
        # All indexes are created before publication, so readers see an already
        # queryable derivative graph once this small DDL transaction commits.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (PRODUCTION_TABLE,)).fetchone():
            conn.execute(f"ALTER TABLE {PRODUCTION_TABLE} RENAME TO {PRODUCTION_TABLE}_previous")
        conn.execute(f"ALTER TABLE {STAGING_TABLE} RENAME TO {PRODUCTION_TABLE}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (f"{PRODUCTION_TABLE}_previous",)).fetchone():
            conn.execute(f"DROP TABLE {PRODUCTION_TABLE}_previous")
        conn.execute(f"CREATE INDEX idx_capture_decoded_transactions_capture_frame ON {PRODUCTION_TABLE}(capture_file_id, frame_number)")
        conn.execute(f"CREATE INDEX idx_capture_decoded_transactions_shape ON {PRODUCTION_TABLE}(transfer_type, direction, interface, endpoint, hid_report_id, decoded_payload_length)")
        conn.execute(f"CREATE INDEX idx_capture_decoded_transactions_status ON {PRODUCTION_TABLE}(decode_status, failure_reason)")
        conn.commit()
    return dict(stats)


def decode_summary(db_path: Path) -> dict[str, object]:
    """Return compact published-pass health metrics without semantic inference."""
    with sqlite3.connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (PRODUCTION_TABLE,)).fetchone():
            return {"published": False}
        statuses = dict(conn.execute(f"SELECT decode_status,count(*) FROM {PRODUCTION_TABLE} GROUP BY decode_status"))
        failures = dict(conn.execute(f"SELECT coalesce(failure_reason,'none'),count(*) FROM {PRODUCTION_TABLE} WHERE decode_status!='decoded' GROUP BY failure_reason"))
    return {"published": True, "statuses": statuses, "failure_reasons": failures}
