"""Incrementally parse downloaded SignalRGB USBData captures.

This pass deliberately does not rebuild typed derivatives or rescan source
trees.  It adds only new, successfully downloaded PCAP/PCAPNG attachments to
the existing CommunityCapture source root.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from ingest.full_reingest import COLLECTOR_VERSION, FullTypedReprocessor, SourceRoot, _sha256


def parse_downloaded_signalrgb_captures(db_path: Path, manifest_path: Path,
                                        attachments_root: Path) -> dict[str, int]:
    """Record capture frames from downloaded SignalRGB attachments only."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captures = [entry for entry in manifest["attachments"]
                if entry.get("status") == "downloaded" and entry.get("kind") == "capture"]
    processor = FullTypedReprocessor(db_path, db_path.parent.parent)
    stats: Counter[str] = Counter(captures_discovered=len(captures))
    with processor.connection() as conn:
        root_row = conn.execute("""SELECT id,root_name,local_path,repository_url,commit_sha,branch,trust_class
                                   FROM source_roots
                                   WHERE root_name='signalrgb-usbdata-attachments'
                                   ORDER BY id DESC LIMIT 1""").fetchone()
        if root_row is None:
            raise RuntimeError("SignalRGB attachment source root has not been registered")
        root_id = root_row[0]
        root = SourceRoot(root_row[1], Path(root_row[2]), root_row[3], root_row[4], root_row[5], root_row[6])
        for entry in captures:
            path = (attachments_root / entry["local_name"]).resolve()
            if not path.is_file():
                stats["missing_local_files"] += 1
                continue
            relative = path.relative_to(root.path).as_posix()
            file_row = conn.execute("""SELECT id FROM source_files
                                       WHERE source_root_id=? AND relative_path=?""", (root_id, relative)).fetchone()
            if file_row:
                source_file_id = file_row[0]
                if conn.execute("SELECT 1 FROM capture_files WHERE source_file_id=?", (source_file_id,)).fetchone():
                    stats["captures_already_parsed"] += 1
                    continue
            else:
                source_file_id = conn.execute("""INSERT INTO source_files(
                    source_root_id,relative_path,content_hash,size,relevant,parsed,parser_name,
                    parse_status,bytes_scanned,collector_version)
                    VALUES(?,?,?,?,1,0,'SignalRGBCapturePass','not_applicable',?,?)
                    RETURNING id""",
                    (root_id, relative, entry["sha256"], path.stat().st_size,
                     path.stat().st_size, COLLECTOR_VERSION)).fetchone()[0]
            # The manifest hash is an acquisition assertion; calculate it again
            # before parsing so a local replacement cannot be promoted as capture
            # evidence under the old identity.
            if _sha256(path) != entry["sha256"]:
                conn.execute("""UPDATE source_files SET parsed=1,parse_status='parse_failed',
                                failure_detail='attachment SHA-256 mismatch' WHERE id=?""", (source_file_id,))
                stats["hash_mismatches"] += 1
                continue
            status, counts, detail = processor._parse_capture(conn, root, source_file_id, path)
            conn.execute("""UPDATE source_files SET parsed=1,parse_status=?,warning=?,failure_detail=?,
                            facts_extracted=?,operations_extracted=?,layouts_extracted=?,sequences_extracted=?
                            WHERE id=?""",
                         (status, detail if status != "parse_failed" else None,
                          detail if status == "parse_failed" else None, counts["facts"],
                          counts["operations"], counts["layouts"], counts["sequences"], source_file_id))
            stats[status] += 1
            stats.update(counts)
        conn.execute("""UPDATE source_roots SET
                        files_total=(SELECT count(*) FROM source_files WHERE source_root_id=?),
                        files_relevant=(SELECT count(*) FROM source_files WHERE source_root_id=? AND relevant=1),
                        files_processed=(SELECT count(*) FROM source_files WHERE source_root_id=? AND relevant=1 AND parsed=1),
                        files_failed=(SELECT count(*) FROM source_files WHERE source_root_id=? AND parse_status='parse_failed'),
                        bytes_scanned=(SELECT coalesce(sum(bytes_scanned),0) FROM source_files WHERE source_root_id=?)
                        WHERE id=?""", (root_id, root_id, root_id, root_id, root_id, root_id))
        conn.commit()
    return dict(stats)
