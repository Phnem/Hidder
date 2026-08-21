import sqlite3
from pathlib import Path

from ingest.usbpcap_decode_pass import decode_all_captures, decode_summary
from ingest.decoded_capture_fingerprints import build_exact_payload_fingerprints, build_structural_packet_families
from ingest.capture_sequence_analysis import (build_capture_device_sessions, pair_control_urb_lifecycles,
                                              build_structural_family_memberships, build_session_family_transitions)


def _seed(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE capture_files(id INTEGER PRIMARY KEY);
        CREATE TABLE capture_transactions(
            id INTEGER PRIMARY KEY, capture_file_id INTEGER NOT NULL REFERENCES capture_files(id),
            sequence_no INTEGER NOT NULL, timestamp REAL, transfer_type TEXT, payload_hex TEXT NOT NULL);
        INSERT INTO capture_files VALUES(1);
        """)
        control = "1c006086621a0dbdffff000000001b00000200020000024800000000210904020100400004010001" + "00" * 62
        complete = "1c006086621a0dbdffff00000000080001020002008002120000000312010002000000400f325550010101020001"
        conn.execute("INSERT INTO capture_transactions VALUES(1,1,18,1.0,'linktype:249',?)", (control,))
        conn.execute("INSERT INTO capture_transactions VALUES(3,1,20,3.0,'linktype:249',?)", (control,))
        conn.execute("INSERT INTO capture_transactions VALUES(2,1,19,2.0,'linktype:1','010203')")
        conn.execute("INSERT INTO capture_transactions VALUES(4,1,21,4.0,'linktype:249',?)", (complete,))


def test_decode_pass_keeps_raw_immutable_and_publishes_only_after_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "registry.sqlite"; _seed(db_path)
    stats = decode_all_captures(db_path, batch_size=1)
    assert stats["transactions_total"] == 4
    assert stats["decoded"] == 3 and stats["unsupported"] == 1
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute("SELECT payload_hex FROM capture_transactions WHERE id=1").fetchone()[0]
        decoded = conn.execute("""SELECT frame_number,device_address,interface,transfer_type,direction,
                                        bm_request_type,b_request,w_value,w_index,w_length,hid_report_type,
                                        hid_report_id,decoded_payload_length,decoded_payload,decode_status
                                 FROM capture_decoded_transactions WHERE raw_transaction_id=1""").fetchone()
        assert raw.startswith("1c00")
        assert decoded[:13] == (19, 2, 1, "control", "host_to_device", 0x21, 9, 0x0204, 1, 64,
                                "output", 4, 64)
        assert bytes(decoded[13])[:4] == bytes.fromhex("04010001")
    assert decode_summary(db_path)["statuses"] == {"decoded": 3, "unsupported": 1}
    fingerprint_stats = build_exact_payload_fingerprints(db_path)
    assert fingerprint_stats == {"exact_payload_fingerprints": 2, "decoded_payload_occurrences": 3}
    assert build_structural_packet_families(db_path) == {"structural_packet_families": 2}
    assert build_exact_payload_fingerprints(db_path) == {"exact_payload_fingerprints": 2, "decoded_payload_occurrences": 3}
    assert build_structural_packet_families(db_path) == {"structural_packet_families": 2}
    assert build_capture_device_sessions(db_path) == {"sessions": 1, "session_memberships": 3}
    assert pair_control_urb_lifecycles(db_path)["paired"] == 1
    memberships = build_structural_family_memberships(db_path)
    assert memberships["eligible"] == memberships["assigned"] == 3
    assert build_session_family_transitions(db_path)["distinct_family_transitions"] >= 1
    # A repeated decoder pass replaces the derivative atomically rather than
    # accumulating a second copy of decoded evidence.
    assert decode_all_captures(db_path, batch_size=1)["transactions_total"] == 4
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM capture_decoded_transactions").fetchone()[0] == 4
