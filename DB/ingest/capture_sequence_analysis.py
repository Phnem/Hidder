"""Conservative capture sessions and USB control request/response lifecycles.

No row created here refers to a protocol operation or assigns a domain semantic.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


SESSIONS = "capture_device_sessions"
SESSIONS_STAGING = "capture_device_sessions_staging"
MEMBERSHIPS = "capture_transaction_sessions"
MEMBERSHIPS_STAGING = "capture_transaction_sessions_staging"
PAIRS = "capture_control_request_response_pairs"
PAIRS_STAGING = "capture_control_request_response_pairs_staging"
FAMILY_MEMBERSHIPS = "capture_transaction_structural_families"
FAMILY_MEMBERSHIPS_STAGING = "capture_transaction_structural_families_staging"
TRANSITIONS = "capture_session_family_transitions"
TRANSITIONS_STAGING = "capture_session_family_transitions_staging"
EXCLUSIONS = "capture_sequence_exclusions"
EXCLUSIONS_STAGING = "capture_sequence_exclusions_staging"
SEQUENCES = "capture_observed_sequences"
SEQUENCES_STAGING = "capture_observed_sequences_staging"
MAX_CONTROL_LIFECYCLE_FRAME_GAP = 16


def _session_key(capture_file_id: int, bus_id: int, device_address: int) -> str:
    return f"capture:{capture_file_id}:bus:{bus_id}:device:{device_address}"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _create_session_tables(conn: sqlite3.Connection) -> None:
    conn.execute(f"""CREATE TABLE {SESSIONS_STAGING}(
        session_key TEXT PRIMARY KEY,
        capture_file_id INTEGER NOT NULL REFERENCES capture_files(id) ON DELETE CASCADE,
        usb_bus_id INTEGER NOT NULL,
        device_address INTEGER NOT NULL,
        first_frame_number INTEGER NOT NULL,
        last_frame_number INTEGER NOT NULL,
        first_timestamp REAL,
        last_timestamp REAL,
        transaction_count INTEGER NOT NULL,
        segmentation_method TEXT NOT NULL
    )""")
    conn.execute(f"""CREATE TABLE {MEMBERSHIPS_STAGING}(
        raw_transaction_id INTEGER PRIMARY KEY REFERENCES capture_transactions(id) ON DELETE CASCADE,
        session_key TEXT NOT NULL
    )""")


def build_capture_device_sessions(db_path: Path) -> dict[str, int]:
    """Group decoded traffic by immutable capture/bus/device boundaries.

    A capture boundary is intentionally not bridged by timestamp heuristics;
    this makes session provenance deterministic and idempotent.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        if not _table_exists(conn, "capture_decoded_transactions"):
            raise RuntimeError("decoded USB transaction table has not been published")
        conn.execute(f"DROP TABLE IF EXISTS {MEMBERSHIPS_STAGING}")
        conn.execute(f"DROP TABLE IF EXISTS {SESSIONS_STAGING}")
        _create_session_tables(conn)
        conn.create_function("session_key", 3, _session_key, deterministic=True)
        conn.execute(f"""INSERT INTO {SESSIONS_STAGING}(
                session_key,capture_file_id,usb_bus_id,device_address,first_frame_number,last_frame_number,
                first_timestamp,last_timestamp,transaction_count,segmentation_method)
            SELECT session_key(capture_file_id,usb_bus_id,device_address),capture_file_id,usb_bus_id,device_address,
                   min(frame_number),max(frame_number),min(timestamp),max(timestamp),count(*),'capture_bus_device_boundary'
            FROM capture_decoded_transactions
            WHERE decode_status='decoded' AND usb_bus_id IS NOT NULL AND device_address IS NOT NULL
            GROUP BY capture_file_id,usb_bus_id,device_address""")
        conn.execute(f"""INSERT INTO {MEMBERSHIPS_STAGING}(raw_transaction_id,session_key)
            SELECT raw_transaction_id,session_key(capture_file_id,usb_bus_id,device_address)
            FROM capture_decoded_transactions
            WHERE decode_status='decoded' AND usb_bus_id IS NOT NULL AND device_address IS NOT NULL""")
        counts = {"sessions": conn.execute(f"SELECT count(*) FROM {SESSIONS_STAGING}").fetchone()[0],
                  "session_memberships": conn.execute(f"SELECT count(*) FROM {MEMBERSHIPS_STAGING}").fetchone()[0]}
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        for production, staging in ((MEMBERSHIPS, MEMBERSHIPS_STAGING), (SESSIONS, SESSIONS_STAGING)):
            if _table_exists(conn, production):
                conn.execute(f"ALTER TABLE {production} RENAME TO {production}_previous")
            conn.execute(f"ALTER TABLE {staging} RENAME TO {production}")
            if _table_exists(conn, f"{production}_previous"):
                conn.execute(f"DROP TABLE {production}_previous")
        conn.execute(f"CREATE INDEX idx_capture_transaction_sessions_session ON {MEMBERSHIPS}(session_key)")
        conn.commit()
    return counts


def _create_pairs_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""CREATE TABLE {PAIRS_STAGING}(
        request_transaction_id INTEGER PRIMARY KEY REFERENCES capture_transactions(id) ON DELETE CASCADE,
        response_transaction_id INTEGER NOT NULL UNIQUE REFERENCES capture_transactions(id) ON DELETE CASCADE,
        session_key TEXT NOT NULL,
        pairing_level TEXT NOT NULL CHECK(pairing_level='URB_LIFECYCLE'),
        confidence REAL NOT NULL,
        rationale_json TEXT NOT NULL
    )""")


def pair_control_urb_lifecycles(db_path: Path, batch_size: int = 10_000) -> dict[str, int]:
    """Pair a nearby SETUP/COMPLETE sharing a non-reused USBPcap URB identity."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        if not _table_exists(conn, SESSIONS):
            raise RuntimeError("capture/device sessions have not been published")
        conn.execute(f"DROP TABLE IF EXISTS {PAIRS_STAGING}")
        _create_pairs_table(conn)
        rows = conn.execute("""SELECT raw_transaction_id,capture_file_id,usb_bus_id,device_address,urb_id_hex,
                                      frame_number,control_stage
                               FROM capture_decoded_transactions
                               WHERE decode_status='decoded' AND transfer_type='control'
                                 AND control_stage IN ('setup','complete') AND urb_id_hex IS NOT NULL
                                 AND urb_id_hex != '0x0000000000000000'
                               ORDER BY capture_file_id,urb_id_hex,frame_number""")
        active: dict[tuple[int, str], sqlite3.Row] = {}
        batch: list[tuple[object, ...]] = []
        stats: Counter[str] = Counter()
        insert = f"INSERT INTO {PAIRS_STAGING} VALUES(?,?,?,?,?,?)"
        for row in rows:
            key = (row["capture_file_id"], row["urb_id_hex"])
            if row["control_stage"] == "setup":
                active[key] = row; stats["setup_seen"] += 1
                continue
            setup = active.pop(key, None)
            frame_gap = row["frame_number"] - setup["frame_number"] if setup is not None else None
            if setup is None or frame_gap <= 0:
                stats["unpaired_complete"] += 1
                continue
            if frame_gap > MAX_CONTROL_LIFECYCLE_FRAME_GAP:
                stats["unpaired_complete_frame_gap"] += 1
                continue
            session = _session_key(row["capture_file_id"], row["usb_bus_id"], row["device_address"])
            rationale = json.dumps({"basis": "same_capture_same_nonzero_urb_id_setup_then_complete",
                                    "frame_gap": frame_gap, "max_frame_gap": MAX_CONTROL_LIFECYCLE_FRAME_GAP,
                                    "promotes_reconstructibility": False})
            batch.append((setup["raw_transaction_id"], row["raw_transaction_id"], session, "URB_LIFECYCLE", 1.0, rationale))
            stats["paired"] += 1
            if len(batch) >= batch_size:
                conn.executemany(insert, batch); conn.commit(); batch.clear()
        stats["unpaired_setup"] = len(active)
        if batch:
            conn.executemany(insert, batch); conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if _table_exists(conn, PAIRS):
            conn.execute(f"ALTER TABLE {PAIRS} RENAME TO {PAIRS}_previous")
        conn.execute(f"ALTER TABLE {PAIRS_STAGING} RENAME TO {PAIRS}")
        if _table_exists(conn, f"{PAIRS}_previous"):
            conn.execute(f"DROP TABLE {PAIRS}_previous")
        conn.execute(f"CREATE INDEX idx_capture_control_pairs_session ON {PAIRS}(session_key)")
        conn.commit()
    return dict(stats)


def build_structural_family_memberships(db_path: Path, batch_size: int = 10_000) -> dict[str, int]:
    """Publish complete ordered sequence membership or leave production intact."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA journal_mode=WAL")
        if not _table_exists(conn, "capture_structural_packet_families") or not _table_exists(conn, MEMBERSHIPS):
            raise RuntimeError("structural families and capture sessions are required")
        conn.execute(f"DROP TABLE IF EXISTS {FAMILY_MEMBERSHIPS_STAGING}")
        conn.execute(f"DROP TABLE IF EXISTS {EXCLUSIONS_STAGING}")
        conn.execute(f"DROP TABLE IF EXISTS {SEQUENCES_STAGING}")
        conn.execute(f"""CREATE TABLE {FAMILY_MEMBERSHIPS_STAGING}(
            raw_transaction_id INTEGER PRIMARY KEY REFERENCES capture_transactions(id) ON DELETE CASCADE,
            session_key TEXT NOT NULL,
            sequence_key TEXT NOT NULL,
            sequence_position INTEGER NOT NULL,
            frame_number INTEGER NOT NULL,
            timestamp REAL,
            structural_family_id INTEGER NOT NULL REFERENCES capture_structural_packet_families(id) ON DELETE CASCADE
        )""")
        conn.execute(f"CREATE TABLE {EXCLUSIONS_STAGING}(raw_transaction_id INTEGER PRIMARY KEY,reason TEXT NOT NULL)")
        conn.execute(f"CREATE TABLE {SEQUENCES_STAGING}(sequence_key TEXT PRIMARY KEY,session_key TEXT NOT NULL,transaction_count INTEGER NOT NULL)")
        families = {}
        for row in conn.execute("SELECT * FROM capture_structural_packet_families"):
            key = tuple(row[name] for name in ('transfer_type','direction','interface','endpoint','control_stage','bm_request_type','b_request','hid_report_type','hid_report_id','report_id_source','payload_length')) + (bytes(row['prefix_anchor']),)
            if key in families: raise RuntimeError('ambiguous structural family key')
            families[key] = row['id']
        rows = conn.execute(f"""SELECT d.*,s.session_key FROM capture_decoded_transactions d
            LEFT JOIN {MEMBERSHIPS} s ON s.raw_transaction_id=d.raw_transaction_id
            WHERE d.decode_status='decoded' ORDER BY s.session_key,d.frame_number,d.timestamp,d.raw_transaction_id""")
        members=[]; excluded=[]; positions=Counter(); session_counts=Counter(); eligible=0
        insert_member=f"INSERT INTO {FAMILY_MEMBERSHIPS_STAGING} VALUES(?,?,?,?,?,?,?)"
        for row in rows:
            eligible += 1; session=row['session_key']
            if session is None: excluded.append((row['raw_transaction_id'],'missing_session')); continue
            payload=bytes(row['decoded_payload']); key=tuple(row[name] for name in ('transfer_type','direction','interface','endpoint','control_stage','bm_request_type','b_request','hid_report_type','hid_report_id','report_id_source','decoded_payload_length'))+(payload[:3],)
            family=families.get(key)
            if family is None: excluded.append((row['raw_transaction_id'],'no_structural_family')); continue
            positions[session]+=1; session_counts[session]+=1
            members.append((row['raw_transaction_id'],session,session,positions[session],row['frame_number'],row['timestamp'],family))
            if len(members)>=batch_size: conn.executemany(insert_member,members); members.clear()
            if len(excluded)>=batch_size: conn.executemany(f"INSERT INTO {EXCLUSIONS_STAGING} VALUES(?,?)",excluded); excluded.clear()
        if members: conn.executemany(insert_member,members)
        if excluded: conn.executemany(f"INSERT INTO {EXCLUSIONS_STAGING} VALUES(?,?)",excluded)
        conn.executemany(f"INSERT INTO {SEQUENCES_STAGING} VALUES(?,?,?)",((key,key,count) for key,count in session_counts.items()))
        assigned=conn.execute(f"SELECT count(*) FROM {FAMILY_MEMBERSHIPS_STAGING}").fetchone()[0]; excluded_count=conn.execute(f"SELECT count(*) FROM {EXCLUSIONS_STAGING}").fetchone()[0]
        duplicates=conn.execute(f"SELECT count(*) FROM (SELECT raw_transaction_id FROM {FAMILY_MEMBERSHIPS_STAGING} GROUP BY raw_transaction_id HAVING count(*)>1)").fetchone()[0]
        ordering=conn.execute(f"SELECT count(*) FROM (SELECT sequence_key,sequence_position,frame_number,lag(frame_number) OVER(PARTITION BY sequence_key ORDER BY sequence_position) prior FROM {FAMILY_MEMBERSHIPS_STAGING}) WHERE frame_number<prior").fetchone()[0]
        if assigned+excluded_count!=eligible or duplicates or ordering: raise RuntimeError('sequence membership invariant audit failed')
        conn.commit(); conn.execute("BEGIN IMMEDIATE")
        for production,staging in ((FAMILY_MEMBERSHIPS,FAMILY_MEMBERSHIPS_STAGING),(EXCLUSIONS,EXCLUSIONS_STAGING),(SEQUENCES,SEQUENCES_STAGING)):
            if _table_exists(conn,production): conn.execute(f"ALTER TABLE {production} RENAME TO {production}_previous")
            conn.execute(f"ALTER TABLE {staging} RENAME TO {production}")
            if _table_exists(conn,f"{production}_previous"): conn.execute(f"DROP TABLE {production}_previous")
        conn.execute(f"CREATE INDEX idx_capture_transaction_family_session ON {FAMILY_MEMBERSHIPS}(session_key,structural_family_id)")
        conn.commit()
    return {"eligible":eligible,"assigned":assigned,"excluded":excluded_count,"duplicates":duplicates,"ordering_violations":ordering,"sequences":len(session_counts)}


def build_session_family_transitions(db_path: Path) -> dict[str, int]:
    """Deduplicate adjacent identical packet families and count their transitions."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA journal_mode=WAL")
        if not _table_exists(conn, FAMILY_MEMBERSHIPS):
            raise RuntimeError("structural family memberships have not been published")
        conn.execute(f"DROP TABLE IF EXISTS {TRANSITIONS_STAGING}")
        conn.execute(f"""CREATE TABLE {TRANSITIONS_STAGING}(
            from_structural_family_id INTEGER NOT NULL REFERENCES capture_structural_packet_families(id) ON DELETE CASCADE,
            to_structural_family_id INTEGER NOT NULL REFERENCES capture_structural_packet_families(id) ON DELETE CASCADE,
            occurrences INTEGER NOT NULL,
            session_count INTEGER NOT NULL,
            PRIMARY KEY(from_structural_family_id,to_structural_family_id)
        )""")
        conn.execute(f"""INSERT INTO {TRANSITIONS_STAGING}(from_structural_family_id,to_structural_family_id,occurrences,session_count)
            WITH ordered AS (
                SELECT m.session_key,m.structural_family_id,d.frame_number,
                       lag(m.structural_family_id) OVER (PARTITION BY m.session_key ORDER BY d.frame_number) AS previous_family
                FROM {FAMILY_MEMBERSHIPS} m
                JOIN capture_decoded_transactions d ON d.raw_transaction_id=m.raw_transaction_id
            ), collapsed AS (
                SELECT session_key,structural_family_id,frame_number
                FROM ordered WHERE previous_family IS NULL OR previous_family != structural_family_id
            ), paired AS (
                SELECT session_key,structural_family_id AS from_family,
                       lead(structural_family_id) OVER (PARTITION BY session_key ORDER BY frame_number) AS to_family
                FROM collapsed
            )
            SELECT from_family,to_family,count(*),count(DISTINCT session_key)
            FROM paired WHERE to_family IS NOT NULL GROUP BY from_family,to_family""")
        transitions = conn.execute(f"SELECT count(*) FROM {TRANSITIONS_STAGING}").fetchone()[0]
        conn.commit(); conn.execute("BEGIN IMMEDIATE")
        if _table_exists(conn, TRANSITIONS):
            conn.execute(f"ALTER TABLE {TRANSITIONS} RENAME TO {TRANSITIONS}_previous")
        conn.execute(f"ALTER TABLE {TRANSITIONS_STAGING} RENAME TO {TRANSITIONS}")
        if _table_exists(conn, f"{TRANSITIONS}_previous"):
            conn.execute(f"DROP TABLE {TRANSITIONS}_previous")
        conn.execute(f"CREATE INDEX idx_capture_family_transitions_to ON {TRANSITIONS}(to_structural_family_id)")
        conn.commit()
    return {"distinct_family_transitions": transitions}
