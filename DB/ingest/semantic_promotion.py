"""Targeted semantic promotion over already indexed source files.

Unlike ``FullTypedReprocessor.run()``, this module neither resets typed tables
nor walks every source tree.  It revisits selected source-file records so new
semantic rules can promote only the requested evidence.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterable

from ingest.full_reingest import CODE_LANGUAGES, FullTypedReprocessor, SourceRoot


def promote_sources(db_path: Path, root_names: Iterable[str]) -> dict[str, int]:
    requested = tuple(root_names)
    if not requested:
        return {}
    processor = FullTypedReprocessor(db_path, db_path.parent.parent)
    stats: Counter[str] = Counter()
    placeholders = ",".join("?" for _ in requested)
    with processor.connection() as conn:
        roots = conn.execute(f"""SELECT id,root_name,local_path,repository_url,commit_sha,branch,trust_class
                                 FROM source_roots WHERE root_name IN ({placeholders})
                                 ORDER BY id""", requested).fetchall()
        for root_row in roots:
            root_id, name, local_path, repo, commit, branch, trust = root_row
            root = SourceRoot(name, Path(local_path), repo, commit, branch, trust)
            files = conn.execute("""SELECT id,relative_path FROM source_files
                                    WHERE source_root_id=? AND relevant=1
                                    ORDER BY id""", (root_id,)).fetchall()
            for source_file_id, relative in files:
                path = root.path / relative
                if path.suffix.lower() not in CODE_LANGUAGES or not path.is_file():
                    continue
                before = conn.total_changes
                try:
                    status, counts, _ = processor._parse_code(conn, root, source_file_id, relative, path.read_bytes())
                except Exception:
                    stats["promotion_parse_failed"] += 1
                    continue
                stats["files_considered"] += 1
                if status == "parsed_protocol_data":
                    stats["files_with_protocol_entities"] += 1
                stats.update(counts)
                stats["database_changes"] += conn.total_changes - before
            conn.commit()
    return dict(stats)
