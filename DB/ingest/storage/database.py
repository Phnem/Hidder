"""SQLite storage manager for staging registry."""

import json
import hashlib
from urllib.parse import urlparse
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Generator

from ingest.config import DB_PATH
from ingest.logging_setup import log_db, log_change, get_logger
from ingest.normalize.evidence import (
    RawSource, RawArtifact, DeviceIdentifierFact, ProtocolHintFact, GenericFact
)
from ingest.normalize.models import generate_identity_key, simplify_name
from ingest.storage.schema import SCHEMA_SQL


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        """Create tables and default brand/vendor seeds."""
        with self.connection() as conn:
            # Check existing columns on artifacts before executescript
            try:
                cols = [c["name"] for c in conn.execute("PRAGMA table_info(artifacts)").fetchall()]
                if cols:
                    if "etag" not in cols:
                        conn.execute("ALTER TABLE artifacts ADD COLUMN etag TEXT")
                    if "last_modified" not in cols:
                        conn.execute("ALTER TABLE artifacts ADD COLUMN last_modified TEXT")
                    if "normalized_url" not in cols:
                        conn.execute("ALTER TABLE artifacts ADD COLUMN normalized_url TEXT")
            except Exception:
                pass

            conn.executescript(SCHEMA_SQL)
            self._migrate_forensic_evidence(conn)
            self._migrate_scope_aware_facts(conn)
            self._migrate_full_typed_reingest(conn)
            self._migrate_operation_validation(conn)
            self._migrate_scope_aware_operations(conn)
            self._migrate_mapping_provenance(conn)
            self._migrate_source_content_cache(conn)
            self._migrate_external_attachments(conn)
            from ingest.brands.canonical import ALL_CANONICAL_BRANDS
            for b in ALL_CANONICAL_BRANDS:
                # 1. Seed brand
                conn.execute(
                    """INSERT OR IGNORE INTO brands (slug, canonical_name, brand_type, website, active)
                       VALUES (?, ?, ?, ?, 1)""",
                    (b.slug, b.canonical_name, b.brand_type.value, b.website)
                )
                b_row = conn.execute("SELECT id FROM brands WHERE slug = ?", (b.slug,)).fetchone()
                if b_row:
                    b_id = b_row["id"]
                    # 2. Seed vendor for backwards-compatibility
                    conn.execute(
                        "INSERT OR IGNORE INTO vendors (id, name, display_name, website) VALUES (?, ?, ?, ?)",
                        (b_id, b.slug, b.canonical_name, b.website)
                    )
                    # 3. Seed aliases
                    for alias in b.aliases:
                        conn.execute(
                            "INSERT OR IGNORE INTO brand_aliases (brand_id, alias, provenance) VALUES (?, ?, 'canonical_registry')",
                            (b_id, alias)
                        )

            # 4. Seed relationships
            for b in ALL_CANONICAL_BRANDS:
                src_row = conn.execute("SELECT id FROM brands WHERE slug = ?", (b.slug,)).fetchone()
                if src_row and b.relationships:
                    src_id = src_row["id"]
                    for rel in b.relationships:
                        tgt_row = conn.execute("SELECT id FROM brands WHERE slug = ?", (rel.target_slug,)).fetchone()
                        if tgt_row:
                            tgt_id = tgt_row["id"]
                            conn.execute(
                                """INSERT OR IGNORE INTO brand_relationships (source_brand_id, target_brand_id, relationship_type, confidence, provenance)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (src_id, tgt_id, rel.rel_type.value, rel.confidence, rel.provenance)
                            )

    @staticmethod
    def _canonical_value(value: str) -> str:
        """Stabilize structured values without treating arbitrary text as JSON."""
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return str(value)
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _migrate_forensic_evidence(self, conn: sqlite3.Connection) -> None:
        """Backfill the evidence-aware layer once, preserving legacy fact rows.

        Old ingestion recorded exactly one source on each row.  The backfill makes
        that limitation explicit as partial provenance instead of inventing line
        locations or independent confirmations.
        """
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 1").fetchone():
            return
        rows = conn.execute(
            "SELECT id, product_id, key, value, source_id, evidence_level, confidence FROM facts"
        ).fetchall()
        for row in rows:
            value = self._canonical_value(row["value"])
            value_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT OR IGNORE INTO normalized_facts
                   (product_id, canonical_key, canonical_value, value_hash)
                   VALUES (?, ?, ?, ?)""",
                (row["product_id"], row["key"], value, value_hash),
            )
            nf = conn.execute(
                "SELECT id FROM normalized_facts WHERE product_id IS ? AND canonical_key = ? AND value_hash = ?",
                (row["product_id"], row["key"], value_hash),
            ).fetchone()
            conn.execute(
                """INSERT OR IGNORE INTO fact_evidence
                   (normalized_fact_id, source_id, collector_name, collector_version,
                    extraction_method, trust_class, confidence, evidence_level,
                    independent_source_group, provenance_status)
                   VALUES (?, ?, 'legacy_ingestion', 'pre-forensic', 'legacy_backfill',
                           'Unknown', ?, ?, 'legacy', 'partial')""",
                (nf["id"], row["source_id"], row["confidence"], row["evidence_level"]),
            )
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (1)")

    def _migrate_scope_aware_facts(self, conn: sqlite3.Connection) -> None:
        """Add explicit scope without changing legacy fact consumers."""
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 2").fetchone():
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(normalized_facts)")}
        if "scope_type" not in columns:
            conn.execute("ALTER TABLE normalized_facts ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'device'")
        if "scope_key" not in columns:
            conn.execute("ALTER TABLE normalized_facts ADD COLUMN scope_key TEXT")
        if "semantic" not in columns:
            conn.execute("ALTER TABLE normalized_facts ADD COLUMN semantic TEXT")
        conn.execute("UPDATE normalized_facts SET scope_key=COALESCE(scope_key, 'product:' || COALESCE(product_id, id)), semantic=COALESCE(semantic, canonical_key)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_facts_scope ON normalized_facts(scope_type, scope_key, semantic, value_hash)")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (2)")

    def _migrate_full_typed_reingest(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 3").fetchone():
            return
        source_file_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_files)")}
        for name, declaration in {
            "bytes_scanned": "INTEGER NOT NULL DEFAULT 0",
            "collector_version": "TEXT",
            "facts_extracted": "INTEGER NOT NULL DEFAULT 0",
            "operations_extracted": "INTEGER NOT NULL DEFAULT 0",
            "layouts_extracted": "INTEGER NOT NULL DEFAULT 0",
            "sequences_extracted": "INTEGER NOT NULL DEFAULT 0",
            "failure_detail": "TEXT",
        }.items():
            if name not in source_file_columns:
                conn.execute(f"ALTER TABLE source_files ADD COLUMN {name} {declaration}")
        operation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(protocol_operations)")}
        for name, declaration in {
            "request_method": "TEXT", "response_method": "TEXT", "opcode": "TEXT",
            "command_class": "TEXT", "command_id": "TEXT", "endpoint": "INTEGER",
            "interface": "INTEGER", "usage_page": "INTEGER", "usage": "INTEGER",
            "report_id_in_buffer": "INTEGER", "dynamic_fields_json": "TEXT",
            "preconditions_json": "TEXT", "timeout_ms": "INTEGER", "delay_ms": "INTEGER",
            "side_effect": "TEXT", "persistence": "TEXT", "risk_state": "TEXT",
            "production_safe": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in operation_columns:
                conn.execute(f"ALTER TABLE protocol_operations ADD COLUMN {name} {declaration}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_protocol_operations_semantic ON protocol_operations(semantic, product_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_evidence_group ON fact_evidence(independent_source_group, normalized_fact_id)")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (3)")

    def _migrate_operation_validation(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 4").fetchone():
            return
        risk_columns = {row["name"] for row in conn.execute("PRAGMA table_info(command_risks)")}
        if "operation_id" not in risk_columns:
            conn.execute("ALTER TABLE command_risks ADD COLUMN operation_id INTEGER REFERENCES protocol_operations(id)")
        root_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_roots)")}
        for name, declaration in {
            "trust_class": "TEXT NOT NULL DEFAULT 'Unknown'",
            "lineage_group": "TEXT",
            "files_total": "INTEGER NOT NULL DEFAULT 0",
            "files_relevant": "INTEGER NOT NULL DEFAULT 0",
            "files_processed": "INTEGER NOT NULL DEFAULT 0",
            "files_failed": "INTEGER NOT NULL DEFAULT 0",
            "bytes_scanned": "INTEGER NOT NULL DEFAULT 0",
            "collector_version": "TEXT",
        }.items():
            if name not in root_columns:
                conn.execute(f"ALTER TABLE source_roots ADD COLUMN {name} {declaration}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_command_risks_operation ON command_risks(operation_id) WHERE operation_id IS NOT NULL")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (4)")

    def _migrate_scope_aware_operations(self, conn: sqlite3.Connection) -> None:
        """Allow typed operations to live at family or device scope.

        Earlier databases made ``product_id`` mandatory.  The rebuild is
        deliberately lossless for existing operation rows; dependent derived
        tables are rebuilt because they are regenerated by the audit pass.
        """
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 5").fetchone():
            return
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(protocol_operations)")}
        already_scoped = {"operation_key", "scope_type", "scope_key", "protocol_family_id"}.issubset(columns) and not columns["product_id"]["notnull"]
        if already_scoped:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_protocol_operations_scope ON protocol_operations(scope_type, scope_key, semantic)")
            conn.execute("INSERT INTO audit_schema_versions(version) VALUES (5)")
            return

        conn.execute("DROP TABLE IF EXISTS operation_completeness")
        conn.execute("DROP TABLE IF EXISTS operation_evidence")
        conn.execute("DROP TABLE IF EXISTS protocol_sequence_steps")
        conn.execute("DROP TABLE IF EXISTS command_risks")
        conn.execute("""CREATE TABLE protocol_operations_scoped (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK(scope_type IN ('device','protocol_family')),
            scope_key TEXT NOT NULL,
            product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
            protocol_family_id INTEGER REFERENCES protocol_families(id) ON DELETE CASCADE,
            protocol_family TEXT, semantic TEXT NOT NULL, transport TEXT,
            api_semantics TEXT, report_id TEXT, api_length INTEGER, wire_length INTEGER,
            direction TEXT CHECK(direction IN ('host_to_device','device_to_host','bidirectional')),
            request_encoding_json TEXT, response_encoding_json TEXT, checksum_json TEXT,
            sequencing_json TEXT, initialization_json TEXT, capability_mapping_json TEXT,
            confidence REAL NOT NULL DEFAULT 0.0, source_trust TEXT NOT NULL DEFAULT 'Unknown',
            operation_status TEXT NOT NULL DEFAULT 'candidate' CHECK(operation_status IN ('candidate','implemented','observed','hardware_verified','rejected')),
            source_fact_id INTEGER REFERENCES normalized_facts(id), request_method TEXT,
            response_method TEXT, opcode TEXT, command_class TEXT, command_id TEXT,
            endpoint INTEGER, interface INTEGER, usage_page INTEGER, usage INTEGER,
            report_id_in_buffer INTEGER, dynamic_fields_json TEXT, preconditions_json TEXT,
            timeout_ms INTEGER, delay_ms INTEGER, side_effect TEXT, persistence TEXT,
            risk_state TEXT, production_safe INTEGER NOT NULL DEFAULT 0 CHECK(production_safe IN (0,1)),
            CHECK((scope_type='device' AND product_id IS NOT NULL) OR
                  (scope_type='protocol_family' AND protocol_family IS NOT NULL))
        )""")
        old_names = [row["name"] for row in conn.execute("PRAGMA table_info(protocol_operations)")]
        transferable = [name for name in old_names if name not in {"id"}]
        target_names = {row["name"] for row in conn.execute("PRAGMA table_info(protocol_operations_scoped)")}
        transferable = [name for name in transferable if name in target_names]
        select_columns = ",".join(transferable)
        insert_columns = ",".join(["id", "operation_key", "scope_type", "scope_key", *transferable])
        conn.execute(f"""INSERT INTO protocol_operations_scoped({insert_columns})
            SELECT id,
                   'legacy:' || id,
                   'device',
                   'product:' || product_id,
                   {select_columns}
            FROM protocol_operations""")
        conn.execute("DROP TABLE protocol_operations")
        conn.execute("ALTER TABLE protocol_operations_scoped RENAME TO protocol_operations")
        conn.executescript("""
            CREATE INDEX idx_protocol_operations_product ON protocol_operations(product_id, protocol_family);
            CREATE INDEX idx_protocol_operations_scope ON protocol_operations(scope_type, scope_key, semantic);
            CREATE INDEX idx_protocol_operations_semantic ON protocol_operations(semantic, product_id);
            CREATE TABLE protocol_sequence_steps (
                sequence_id INTEGER NOT NULL REFERENCES protocol_sequences(id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL, operation_id INTEGER REFERENCES protocol_operations(id),
                step_kind TEXT NOT NULL, condition_json TEXT, expected_response_json TEXT,
                delay_ms INTEGER, PRIMARY KEY(sequence_id, step_index));
            CREATE TABLE operation_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id INTEGER NOT NULL REFERENCES protocol_operations(id) ON DELETE CASCADE,
                source_file_id INTEGER REFERENCES source_files(id), source_id INTEGER REFERENCES sources(id),
                extraction_method TEXT NOT NULL, trust_class TEXT NOT NULL, lineage_group TEXT NOT NULL,
                confidence REAL NOT NULL, line_start INTEGER, line_end INTEGER, symbol TEXT);
            CREATE UNIQUE INDEX idx_operation_evidence_file_unique
                ON operation_evidence(operation_id, source_file_id, extraction_method)
                WHERE source_file_id IS NOT NULL;
            CREATE UNIQUE INDEX idx_operation_evidence_source_unique
                ON operation_evidence(operation_id, source_id, extraction_method)
                WHERE source_id IS NOT NULL;
            CREATE INDEX idx_operation_evidence_lineage ON operation_evidence(operation_id, lineage_group);
            CREATE TABLE operation_completeness (
                operation_id INTEGER PRIMARY KEY REFERENCES protocol_operations(id) ON DELETE CASCADE,
                score INTEGER NOT NULL, missing_requirements_json TEXT NOT NULL,
                complete INTEGER NOT NULL CHECK(complete IN (0,1)), explanation TEXT NOT NULL);
            CREATE TABLE command_risks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_fact_id INTEGER REFERENCES normalized_facts(id) ON DELETE CASCADE,
                risk_class TEXT NOT NULL CHECK(risk_class IN ('read_only','volatile_write','persistent_write','destructive','unknown_risk')),
                rationale TEXT NOT NULL,
                operation_id INTEGER REFERENCES protocol_operations(id),
                UNIQUE(normalized_fact_id));
            CREATE UNIQUE INDEX idx_command_risks_operation ON command_risks(operation_id) WHERE operation_id IS NOT NULL;
        """)
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (5)")

    def _migrate_mapping_provenance(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 6").fetchone():
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(device_protocol_mappings)")}
        if "source_file_id" not in columns:
            conn.execute("ALTER TABLE device_protocol_mappings ADD COLUMN source_file_id INTEGER REFERENCES source_files(id)")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (6)")

    def _migrate_source_content_cache(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 7").fetchone():
            return
        conn.execute("""CREATE TABLE IF NOT EXISTS source_content_cache(
            absolute_path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL, verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (7)")

    def _migrate_external_attachments(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM audit_schema_versions WHERE version = 8").fetchone():
            return
        conn.executescript("""CREATE TABLE IF NOT EXISTS external_attachments(
            external_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, issue_iid INTEGER,
            issue_url TEXT, attachment_url TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL, content_sha256 TEXT, size INTEGER,
            content_type TEXT, error TEXT, source_created_at TEXT,
            last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS idx_external_attachments_status
                ON external_attachments(source_name,status,kind);""")
        conn.execute("INSERT INTO audit_schema_versions(version) VALUES (8)")

    def get_or_create_vendor(self, name: str, display_name: str, website: Optional[str] = None) -> int:
        clean_name = name.strip().lower()
        with self.connection() as conn:
            # Check brands first
            b_row = conn.execute("SELECT id, canonical_name FROM brands WHERE slug = ? OR canonical_name = ?", (clean_name, display_name)).fetchone()
            if b_row:
                b_id = b_row["id"]
                conn.execute(
                    "INSERT OR IGNORE INTO vendors (id, name, display_name, website) VALUES (?, ?, ?, ?)",
                    (b_id, clean_name, display_name or b_row["canonical_name"], website)
                )
                return b_id

            row = conn.execute("SELECT id FROM vendors WHERE name = ?", (clean_name,)).fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO vendors (name, display_name, website) VALUES (?, ?, ?)",
                (clean_name, display_name, website)
            )
            v_id = cur.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO brands (id, slug, canonical_name, website, active) VALUES (?, ?, ?, ?, 1)",
                (v_id, clean_name, display_name, website)
            )
            return v_id

    def record_brand_crawl_status(
        self,
        brand_id: int,
        run_id: Optional[str],
        status: str,
        products_count: int = 0,
        devices_count: int = 0,
        artifacts_count: int = 0,
        artifacts_bytes: int = 0,
        vid_pids_count: int = 0,
        hints_count: int = 0,
        tech_evidence_products: int = 0,
        blocking_reason: Optional[str] = None
    ):
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO brand_crawl_status (
                    brand_id, run_id, status, products_count, devices_count, 
                    artifacts_count, artifacts_bytes, vid_pids_count, hints_count, 
                    tech_evidence_products, blocking_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (brand_id, run_id, status, products_count, devices_count,
                 artifacts_count, artifacts_bytes, vid_pids_count, hints_count,
                 tech_evidence_products, blocking_reason)
            )

    def get_brand_with_details(self, search: str) -> Optional[dict[str, Any]]:
        search_clean = search.strip()
        with self.connection() as conn:
            # 1. Exact match on slug or canonical name
            row = conn.execute(
                "SELECT id, slug, canonical_name, brand_type, parent_brand_id, website, active FROM brands WHERE slug = ? OR canonical_name = ? COLLATE NOCASE",
                (search_clean.lower(), search_clean)
            ).fetchone()
            # 2. Match on alias
            if not row:
                row = conn.execute(
                    """SELECT b.id, b.slug, b.canonical_name, b.brand_type, b.parent_brand_id, b.website, b.active 
                       FROM brands b JOIN brand_aliases a ON b.id = a.brand_id 
                       WHERE a.alias = ? COLLATE NOCASE""",
                    (search_clean,)
                ).fetchone()
            # 3. Fallback to LIKE
            if not row:
                row = conn.execute(
                    "SELECT id, slug, canonical_name, brand_type, parent_brand_id, website, active FROM brands WHERE canonical_name LIKE ?",
                    (f"%{search_clean}%",)
                ).fetchone()
            if not row:
                return None
            b_id = row["id"]
            res = dict(row)

            # Aliases
            aliases = conn.execute("SELECT alias, language_or_region, provenance FROM brand_aliases WHERE brand_id = ?", (b_id,)).fetchall()
            res["aliases"] = [dict(a) for a in aliases]

            # Relationships
            rels = conn.execute(
                """SELECT br.relationship_type, br.confidence, br.provenance, b2.canonical_name as target_brand, b2.slug as target_slug
                   FROM brand_relationships br
                   JOIN brands b2 ON br.target_brand_id = b2.id
                   WHERE br.source_brand_id = ?""",
                (b_id,)
            ).fetchall()
            res["relationships"] = [dict(r) for r in rels]

            # Latest Status
            st = conn.execute(
                "SELECT status, products_count, devices_count, artifacts_count, artifacts_bytes, vid_pids_count, hints_count, tech_evidence_products, blocking_reason, crawled_at FROM brand_crawl_status WHERE brand_id = ? ORDER BY id DESC LIMIT 1",
                (b_id,)
            ).fetchone()
            res["latest_status"] = dict(st) if st else None

            # Counts from DB
            p_cnt = conn.execute("SELECT COUNT(*) FROM products WHERE vendor_id = ?", (b_id,)).fetchone()[0]
            d_cnt = conn.execute("SELECT COUNT(*) FROM products WHERE vendor_id = ? AND category IN ('keyboard', 'mouse', 'headset', 'keypad', 'trackball')", (b_id,)).fetchone()[0]
            art_cnt = conn.execute("SELECT COUNT(*) FROM artifacts WHERE vendor_id = ?", (b_id,)).fetchone()[0]
            res["total_products"] = p_cnt
            res["total_hardware_devices"] = d_cnt
            res["total_artifacts"] = art_cnt

            return res

    def list_all_brands(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT b.id, b.slug, b.canonical_name, b.brand_type, b.website,
                       s.status as latest_status, s.products_count, s.devices_count, 
                       s.artifacts_count, s.artifacts_bytes, s.vid_pids_count, 
                       s.hints_count, s.tech_evidence_products, s.blocking_reason, s.crawled_at
                FROM brands b
                LEFT JOIN (
                    SELECT bcs.* FROM brand_crawl_status bcs
                    INNER JOIN (
                        SELECT brand_id, MAX(id) as max_id FROM brand_crawl_status GROUP BY brand_id
                    ) latest ON bcs.id = latest.max_id
                ) s ON b.id = s.brand_id
                ORDER BY b.canonical_name ASC
            """).fetchall()
            return [dict(r) for r in rows]

    def record_source(self, source: RawSource) -> int:
        vendor_id = self.get_or_create_vendor(source.vendor, source.vendor)
        now = source.retrieved_at.isoformat()
        with self.connection() as conn:
            existing = conn.execute(
                "SELECT id FROM sources WHERE vendor_id = ? AND source_url = ?",
                (vendor_id, source.url)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE sources SET 
                       retrieved_at = ?, http_status = ?, content_hash = ?, etag = ?, last_modified = ? 
                       WHERE id = ?""",
                    (now, source.http_status, source.content_hash, source.etag, source.last_modified, existing["id"])
                )
                return existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO sources (source_url, source_type, vendor_id, retrieved_at, http_status, content_hash, etag, last_modified)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (source.url, source.source_type.value, vendor_id, now,
                     source.http_status, source.content_hash, source.etag, source.last_modified)
                )
                return cur.lastrowid

    def get_source_cache_headers(self, url: str) -> tuple[Optional[str], Optional[str]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT etag, last_modified FROM sources WHERE source_url = ? ORDER BY id DESC LIMIT 1",
                (url,)
            ).fetchone()
            if row:
                return row["etag"], row["last_modified"]
            return None, None

    def upsert_product(
        self,
        vendor_id: int,
        raw_name: str,
        canonical_name: str,
        category: str,
        identity_key: Optional[str] = None,
        product_url: Optional[str] = None,
        image_url: Optional[str] = None,
        category_confidence: float = 0.5,
        metadata_confidence: float = 0.5,
        source_id: Optional[int] = None,
        evidence_level: int = 1,
        run_id: Optional[str] = None
    ) -> tuple[int, bool]:
        """
        Insert or update a product with Identity Key Deduplication and Metadata Evidence Resolution.
        Stronger sources preserve primary canonical name, raw name, URL, and category.
        """
        try:
            category_confidence = float(category_confidence)
        except (ValueError, TypeError):
            category_confidence = 0.5

        try:
            metadata_confidence = float(metadata_confidence)
        except (ValueError, TypeError):
            metadata_confidence = 0.5

        if not identity_key:
            identity_key = generate_identity_key("", canonical_name or raw_name)

        now = utc_now_iso()
        with self.connection() as conn:
            row = conn.execute(
                """SELECT id, identity_key, canonical_name, raw_name, category, 
                          category_confidence, metadata_confidence, product_url, image_url, active 
                   FROM products WHERE vendor_id = ? AND identity_key = ?""",
                (vendor_id, identity_key)
            ).fetchone()

            if row:
                p_id = row["id"]
                existing_cat_conf = row["category_confidence"] if row["category_confidence"] is not None else 0.0
                existing_meta_conf = row["metadata_confidence"] if row["metadata_confidence"] is not None else 0.0

                # 1. Category Resolution
                if category_confidence >= existing_cat_conf:
                    final_category = category
                    final_cat_conf = category_confidence
                else:
                    final_category = row["category"]
                    final_cat_conf = existing_cat_conf

                # 2. Metadata Evidence Resolution with Strict Source-Role Precedence
                if metadata_confidence > existing_meta_conf:
                    final_canonical = canonical_name
                    final_raw = raw_name
                    final_url = product_url or row["product_url"]
                    final_img = image_url or row["image_url"]
                    final_meta_conf = metadata_confidence
                elif metadata_confidence == existing_meta_conf:
                    final_canonical = row["canonical_name"] or canonical_name
                    final_raw = row["raw_name"] or raw_name
                    if "/blogs/" in (product_url or "") and "/products/" in (row["product_url"] or ""):
                        final_url = row["product_url"]
                    else:
                        final_url = product_url or row["product_url"]
                    final_img = row["image_url"] or image_url
                    final_meta_conf = existing_meta_conf

                    if raw_name != row["raw_name"]:
                        self._record_alias_conn(conn, p_id, raw_name, product_url, source_id, evidence_level)
                else:
                    # Retain stronger primary metadata, record incoming observation as alias with its own source_id
                    final_canonical = row["canonical_name"]
                    final_raw = row["raw_name"]
                    final_url = row["product_url"] or product_url
                    final_img = row["image_url"] or image_url
                    final_meta_conf = existing_meta_conf

                    if raw_name != row["raw_name"]:
                        self._record_alias_conn(conn, p_id, raw_name, product_url, source_id, evidence_level)

                conn.execute(
                    """UPDATE products SET 
                        canonical_name = ?, raw_name = ?, category = ?, 
                        category_confidence = ?, metadata_confidence = ?,
                        product_url = ?, image_url = ?, 
                        last_seen = ?, active = 1 
                       WHERE id = ?""",
                    (final_canonical, final_raw, final_category, final_cat_conf, final_meta_conf, final_url, final_img, now, p_id)
                )
                log_db(f"UPDATE product id={p_id} ({final_canonical}) category='{final_category}' (cat_conf={final_cat_conf:.2f}, meta_conf={final_meta_conf:.2f})")
                return p_id, False
            else:
                # Check for possible duplicate variants before inserting
                similar_rows = conn.execute(
                    "SELECT id, identity_key, canonical_name FROM products WHERE vendor_id = ?",
                    (vendor_id,)
                ).fetchall()

                duplicate_candidates = []
                for s_row in similar_rows:
                    s_id_key = s_row["identity_key"]
                    if (identity_key in s_id_key or s_id_key in identity_key) and abs(len(identity_key) - len(s_id_key)) <= 4 and len(identity_key) >= 3:
                        duplicate_candidates.append((s_row["id"], f"Similar model variant ('{s_row['canonical_name']}' vs '{canonical_name}')", 0.65))

                cur = conn.execute(
                    """INSERT INTO products (vendor_id, identity_key, canonical_name, raw_name, category, category_confidence, metadata_confidence, product_url, image_url, first_seen, last_seen, active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                    (vendor_id, identity_key, canonical_name, raw_name, category, category_confidence, metadata_confidence, product_url, image_url, now, now)
                )
                p_id = cur.lastrowid
                log_db(f"INSERT product id={p_id} '{canonical_name}' (key='{identity_key}', category='{category}', conf={category_confidence:.2f})")

                # Record variant candidates in possible_duplicates table
                for dup_id, reason, conf in duplicate_candidates:
                    self._record_possible_duplicate_conn(conn, dup_id, p_id, reason, conf)

                if run_id:
                    self._record_change(conn, run_id, "product", str(p_id), "NEW", {"name": canonical_name, "category": category})
                return p_id, True

    def _record_alias_conn(self, conn: sqlite3.Connection, product_id: int, alias_name: str, alias_url: Optional[str], source_id: Optional[int], evidence_level: int):
        if not alias_name:
            return
        now = utc_now_iso()
        conn.execute(
            """INSERT INTO product_aliases (product_id, alias_name, alias_url, source_id, evidence_level, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id, alias_name) DO UPDATE SET last_seen = ?""",
            (product_id, alias_name, alias_url, source_id, evidence_level, now, now, now)
        )

    def get_artifact_by_url(self, normalized_url: str) -> Optional[dict[str, Any]]:
        """Look up known artifact metadata by normalized URL for pre-download caching."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT au.normalized_url, au.original_url, au.final_url, au.etag, 
                          au.last_modified, au.sha256, au.size, au.status, au.vendor_id,
                          au.last_seen, a.filename, a.software_version
                   FROM artifact_urls au
                   LEFT JOIN artifacts a ON au.sha256 = a.sha256
                   WHERE au.normalized_url = ?
                   ORDER BY au.id DESC LIMIT 1""",
                (normalized_url,)
            ).fetchone()
            if row:
                return dict(row)
            
            # Fallback to artifacts table original_url matching
            art_row = conn.execute(
                """SELECT original_url, final_url, etag, last_modified, sha256, size, filename, software_version, vendor_id, last_seen
                   FROM artifacts
                   WHERE original_url = ? OR normalized_url = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (normalized_url, normalized_url)
            ).fetchone()
            if art_row:
                d = dict(art_row)
                d["status"] = "downloaded"
                return d
            return None

    def record_artifact_url(
        self,
        normalized_url: str,
        original_url: str,
        final_url: Optional[str],
        etag: Optional[str],
        last_modified: Optional[str],
        sha256: Optional[str],
        vendor_id: int,
        size: Optional[int] = None,
        status: str = "downloaded"
    ):
        """Record or update artifact URL retrieval metadata."""
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO artifact_urls (normalized_url, original_url, final_url, etag, last_modified, sha256, vendor_id, size, status, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(normalized_url) DO UPDATE SET
                       original_url = excluded.original_url,
                       final_url = COALESCE(excluded.final_url, artifact_urls.final_url),
                       etag = COALESCE(excluded.etag, artifact_urls.etag),
                       last_modified = COALESCE(excluded.last_modified, artifact_urls.last_modified),
                       sha256 = COALESCE(excluded.sha256, artifact_urls.sha256),
                       size = COALESCE(excluded.size, artifact_urls.size),
                       status = excluded.status,
                       last_seen = ?""",
                (normalized_url, original_url, final_url, etag, last_modified, sha256, vendor_id, size, status, now, now, now)
            )

    def update_artifact_url_last_seen(self, normalized_url: str):
        """Update last_seen timestamp on artifact_urls and matching artifacts."""
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute("UPDATE artifact_urls SET last_seen = ? WHERE normalized_url = ?", (now, normalized_url))
            conn.execute("UPDATE artifacts SET last_seen = ? WHERE original_url = ? OR normalized_url = ?", (now, normalized_url, normalized_url))

    def upsert_artifact(self, artifact: RawArtifact, vendor_id: int, run_id: Optional[str] = None) -> tuple[str, bool, bool]:
        """
        Record artifact metadata.
        Returns: (sha256, is_new_artifact, is_hash_changed_for_same_url)
        """
        if not artifact.sha256:
            raise ValueError("Artifact must have a calculated sha256 before upsert")

        now = utc_now_iso()
        norm_url = getattr(artifact, "normalized_url", None)
        etag = getattr(artifact, "etag", None)
        last_mod = getattr(artifact, "last_modified", None)

        with self.connection() as conn:
            url_match = conn.execute(
                "SELECT sha256, filename FROM artifacts WHERE original_url = ? AND sha256 != ?",
                (artifact.original_url, artifact.sha256)
            ).fetchone()

            is_hash_changed = False
            if url_match:
                is_hash_changed = True
                old_sha = url_match["sha256"]
                log_change(
                    f"Artifact content CHANGED at same URL '{artifact.original_url}'! Old SHA: {old_sha[:12]}... -> New SHA: {artifact.sha256[:12]}..."
                )
                if run_id:
                    self._record_change(conn, run_id, "artifact", artifact.sha256, "HASH_CHANGED", {
                        "url": artifact.original_url, "old_sha256": old_sha, "new_sha256": artifact.sha256
                    })

            existing = conn.execute(
                "SELECT sha256 FROM artifacts WHERE sha256 = ?",
                (artifact.sha256,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE artifacts SET 
                       last_seen = ?, 
                       final_url = COALESCE(?, final_url),
                       content_type = COALESCE(?, content_type),
                       etag = COALESCE(?, etag),
                       last_modified = COALESCE(?, last_modified),
                       normalized_url = COALESCE(?, normalized_url),
                       software_version = COALESCE(?, software_version) 
                       WHERE sha256 = ?""",
                    (now, artifact.final_url, artifact.content_type, etag, last_mod, norm_url, artifact.software_version, artifact.sha256)
                )
                log_db(f"UPDATE artifact SHA256: {artifact.sha256[:12]}... last_seen updated")
                return artifact.sha256, False, is_hash_changed
            else:
                conn.execute(
                    """INSERT INTO artifacts (sha256, filename, size, original_url, final_url, content_type, etag, last_modified, normalized_url, downloaded_at, software_version, extraction_status, vendor_id, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    (artifact.sha256, artifact.filename, artifact.size, artifact.original_url,
                     artifact.final_url, artifact.content_type, etag, last_mod, norm_url, now, artifact.software_version, vendor_id, now, now)
                )
                log_db(f"INSERT artifact SHA256: {artifact.sha256[:12]}... ('{artifact.filename}', {artifact.size / 1024 / 1024:.2f} MB)")
                if run_id:
                    self._record_change(conn, run_id, "artifact", artifact.sha256, "NEW", {
                        "filename": artifact.filename, "size": artifact.size, "url": artifact.original_url
                    })
                return artifact.sha256, True, is_hash_changed

    def update_artifact_extraction_status(self, sha256: str, status: str):
        with self.connection() as conn:
            conn.execute("UPDATE artifacts SET extraction_status = ? WHERE sha256 = ?", (status, sha256))

    def link_product_artifact(self, product_id: int, artifact_sha256: str, relation_type: str = "driver"):
        with self.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO product_artifacts (product_id, artifact_sha256, relation_type) VALUES (?, ?, ?)",
                (product_id, artifact_sha256, relation_type)
            )

    def _resolve_source_id_conn(self, conn: sqlite3.Connection, source_id: Optional[int], artifact_sha256: Optional[str], product_id: Optional[int]) -> Optional[int]:
        """Ensure all technical evidence has strict provenance by resolving or creating a valid Source row."""
        if source_id is not None:
            return source_id
        if artifact_sha256:
            art = conn.execute("SELECT original_url, vendor_id FROM artifacts WHERE sha256 = ?", (artifact_sha256,)).fetchone()
            if art:
                url = art["original_url"] or f"artifact://{artifact_sha256}"
                v_id = art["vendor_id"]
                src = conn.execute("SELECT id FROM sources WHERE vendor_id = ? AND source_url = ?", (v_id, url)).fetchone()
                if src:
                    return src["id"]
                now = utc_now_iso()
                cur = conn.execute(
                    "INSERT INTO sources (source_url, source_type, vendor_id, retrieved_at, http_status, content_hash) VALUES (?, 'vendor_software', ?, ?, 200, ?)",
                    (url, v_id, now, artifact_sha256)
                )
                return cur.lastrowid
        if product_id:
            prod = conn.execute("SELECT vendor_id, product_url FROM products WHERE id = ?", (product_id,)).fetchone()
            if prod:
                v_id = prod["vendor_id"]
                url = prod["product_url"] or f"product://{product_id}"
                src = conn.execute("SELECT id FROM sources WHERE vendor_id = ? AND source_url = ?", (v_id, url)).fetchone()
                if src:
                    return src["id"]
                now = utc_now_iso()
                cur = conn.execute(
                    "INSERT INTO sources (source_url, source_type, vendor_id, retrieved_at, http_status) VALUES (?, 'vendor_product', ?, ?, 200)",
                    (url, v_id, now)
                )
                return cur.lastrowid
        return None

    def upsert_device_identifier(self, fact: DeviceIdentifierFact, run_id: Optional[str] = None) -> bool:

        """Insert or update a device identifier (VID/PID). Returns True if new."""
        now = utc_now_iso()
        with self.connection() as conn:
            resolved_source_id = self._resolve_source_id_conn(conn, fact.source_id, fact.artifact_sha256, fact.product_id)
            existing = conn.execute(
                """SELECT id FROM device_identifiers 
                   WHERE product_id = ? AND vid = ? AND pid = ? 
                   AND COALESCE(usage_page, -1) = COALESCE(?, -1) 
                   AND COALESCE(usage, -1) = COALESCE(?, -1)
                   AND COALESCE(connection_type, '') = COALESCE(?, '')""",
                (fact.product_id, fact.vid, fact.pid, fact.usage_page, fact.usage, fact.connection_type)
            ).fetchone()

            if existing:
                conn.execute("UPDATE device_identifiers SET last_seen = ?, source_id = COALESCE(?, source_id) WHERE id = ?", (now, resolved_source_id, existing["id"]))
                return False
            else:
                conn.execute(
                    """INSERT INTO device_identifiers (product_id, vid, pid, vid_hex, pid_hex, manufacturer_string, product_string, usage_page, usage, connection_type, source_id, artifact_sha256, evidence_level, confidence, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fact.product_id, fact.vid, fact.pid, fact.vid_hex, fact.pid_hex,
                     fact.manufacturer_string, fact.product_string, fact.usage_page, fact.usage,
                     fact.connection_type, resolved_source_id, fact.artifact_sha256,
                     int(fact.evidence_level), fact.confidence, now, now)
                )
                if run_id:
                    self._record_change(conn, run_id, "vid_pid", f"{fact.vid_hex}:{fact.pid_hex}", "NEW", {
                        "product_id": fact.product_id, "vid_hex": fact.vid_hex, "pid_hex": fact.pid_hex
                    })
                return True

    def upsert_protocol_hint(self, fact: ProtocolHintFact, run_id: Optional[str] = None) -> bool:
        """Insert or update a protocol hint. Returns True if new."""
        now = utc_now_iso()
        with self.connection() as conn:
            resolved_source_id = self._resolve_source_id_conn(conn, fact.source_id, fact.artifact_sha256, fact.product_id)
            existing = conn.execute(
                """SELECT id FROM protocol_hints 
                   WHERE product_id = ? AND hint_key = ? AND hint_value = ?""",
                (fact.product_id, fact.hint_key, fact.hint_value)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE protocol_hints SET last_seen = ?, source_id = COALESCE(?, source_id), artifact_sha256 = COALESCE(?, artifact_sha256) WHERE id = ?",
                    (now, resolved_source_id, fact.artifact_sha256, existing["id"])
                )
                return False
            else:
                conn.execute(
                    """INSERT INTO protocol_hints (product_id, hint_key, hint_value, source_id, artifact_sha256, evidence_level, confidence, context, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fact.product_id, fact.hint_key, fact.hint_value, resolved_source_id,
                     fact.artifact_sha256, int(fact.evidence_level), fact.confidence, fact.context, now, now)
                )
                if run_id:
                    self._record_change(conn, run_id, "protocol_hint", f"{fact.hint_key}:{fact.hint_value}", "NEW", {
                        "product_id": fact.product_id, "key": fact.hint_key, "val": fact.hint_value
                    })
                return True

    def upsert_generic_fact(self, fact: GenericFact, run_id: Optional[str] = None) -> bool:
        now = utc_now_iso()
        with self.connection() as conn:
            resolved_source_id = self._resolve_source_id_conn(conn, fact.source_id, fact.artifact_sha256, fact.product_id)
            existing = conn.execute(
                """SELECT id FROM facts 
                   WHERE product_id = ? AND key = ? AND value = ?""",
                (fact.product_id, fact.key, fact.value)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE facts SET last_seen = ?, source_id = COALESCE(?, source_id), artifact_sha256 = COALESCE(?, artifact_sha256) WHERE id = ?",
                    (now, resolved_source_id, fact.artifact_sha256, existing["id"])
                )
                self._upsert_normalized_fact_evidence_conn(conn, fact, resolved_source_id)
                return False
            else:
                cur = conn.execute(
                    """INSERT INTO facts (product_id, key, value, source_id, artifact_sha256, evidence_level, confidence, is_inference, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fact.product_id, fact.key, fact.value, resolved_source_id, fact.artifact_sha256,
                     int(fact.evidence_level), fact.confidence, 1 if fact.is_inference else 0, now, now)
                )
                self._upsert_normalized_fact_evidence_conn(conn, fact, resolved_source_id)
                return True

    @classmethod
    def _source_trust_and_group(cls, conn: sqlite3.Connection, source_id: Optional[int]) -> tuple[str, str]:
        if source_id is None:
            return "Unknown", "unknown"
        row = conn.execute("SELECT source_type, source_url FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return "Unknown", "unknown"
        source_type = row["source_type"]
        trust = {
            "official_repository": "OfficialSDK",
            "vendor_technical": "OfficialSpecification",
            "vendor_software": "OfficialSDK",
            "vendor_product": "OfficialSpecification",
            "vendor_download": "OfficialSDK",
            "open_source": "UpstreamImplementation",
            "community": "CommunityImplementation",
        }.get(source_type, "Unknown")
        parsed = urlparse(row["source_url"])
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc in {"github.com", "gitlab.com"} and len(parts) >= 2:
            group = f"{parsed.netloc}/{parts[0]}/{parts[1]}"
        else:
            group = parsed.netloc or "unknown"
        return trust, group.lower()

    def _upsert_normalized_fact_evidence_conn(
        self, conn: sqlite3.Connection, fact: GenericFact, source_id: Optional[int]
    ) -> None:
        """Attach evidence without collapsing independent sources into one fact row."""
        value = self._canonical_value(fact.value)
        value_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT OR IGNORE INTO normalized_facts
               (product_id, canonical_key, canonical_value, value_hash, last_seen)
               VALUES (?, ?, ?, ?, ?)""",
            (fact.product_id, fact.key, value, value_hash, utc_now_iso()),
        )
        normalized = conn.execute(
            "SELECT id FROM normalized_facts WHERE product_id IS ? AND canonical_key = ? AND value_hash = ?",
            (fact.product_id, fact.key, value_hash),
        ).fetchone()
        trust, group = self._source_trust_and_group(conn, source_id)
        evidence_exists = conn.execute(
            """SELECT 1 FROM fact_evidence
               WHERE normalized_fact_id=? AND source_id IS ? AND collector_name='registry_database'
                 AND collector_version='forensic-1' AND extraction_method='collector_upsert'
               LIMIT 1""",
            (normalized["id"], source_id),
        ).fetchone()
        if not evidence_exists:
            conn.execute(
                """INSERT INTO fact_evidence
               (normalized_fact_id, source_id, collector_name, collector_version,
                extraction_method, trust_class, confidence, evidence_level,
                independent_source_group, provenance_status)
               VALUES (?, ?, 'registry_database', 'forensic-1', 'collector_upsert',
                       ?, ?, ?, ?, 'partial')""",
                (normalized["id"], source_id, trust, fact.confidence, int(fact.evidence_level), group),
            )

    def _record_change(self, conn: sqlite3.Connection, run_id: str, entity_type: str, entity_id: str, change_type: str, details: dict):
        conn.execute(
            "INSERT OR IGNORE INTO crawl_runs (id, started_at, status) VALUES (?, ?, 'running')",
            (run_id, utc_now_iso())
        )
        conn.execute(
            "INSERT INTO changes (run_id, entity_type, entity_id, change_type, details_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, entity_type, entity_id, change_type, json.dumps(details, ensure_ascii=False))
        )

    def _record_possible_duplicate_conn(self, conn: sqlite3.Connection, id_a: int, id_b: int, reason: str, confidence: float = 0.5):
        p_min, p_max = min(id_a, id_b), max(id_a, id_b)
        conn.execute(
            """INSERT OR REPLACE INTO possible_duplicates (product_id_a, product_id_b, reason, confidence)
               VALUES (?, ?, ?, ?)""",
            (p_min, p_max, reason, confidence)
        )

    def record_possible_duplicate(self, id_a: int, id_b: int, reason: str, confidence: float = 0.5):
        with self.connection() as conn:
            self._record_possible_duplicate_conn(conn, id_a, id_b, reason, confidence)

    def get_possible_duplicates(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            query = """
            SELECT d.id, d.product_id_a, d.product_id_b, d.reason, d.confidence,
                   p1.canonical_name as name_a, p1.raw_name as raw_a, p1.product_url as url_a,
                   p2.canonical_name as name_b, p2.raw_name as raw_b, p2.product_url as url_b,
                   v.display_name as vendor_name
            FROM possible_duplicates d
            JOIN products p1 ON d.product_id_a = p1.id
            JOIN products p2 ON d.product_id_b = p2.id
            JOIN vendors v ON p1.vendor_id = v.id
            WHERE d.reviewed = 0
            ORDER BY d.confidence DESC
            """
            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]

    def start_crawl_run(self, run_id: str):
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                "UPDATE crawl_runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                (now,)
            )
            conn.execute(
                "INSERT INTO crawl_runs (id, started_at, status) VALUES (?, ?, 'running')",
                (run_id, now)
            )

    def finish_crawl_run(self, run_id: str, stats: dict[str, int], status: str = "completed"):
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """UPDATE crawl_runs SET 
                   finished_at = ?, status = ?,
                   products_scanned = ?, new_products = ?, updated_products = ?,
                   new_artifacts = ?, changed_artifacts = ?,
                   new_vid_pids = ?, new_hints = ?, errors_count = ?
                   WHERE id = ?""",
                (now, status,
                 stats.get("products_scanned", 0),
                 stats.get("new_products", 0),
                 stats.get("updated_products", 0),
                 stats.get("new_artifacts", 0),
                 stats.get("changed_artifacts", 0),
                 stats.get("new_vid_pids", 0),
                 stats.get("new_hints", 0),
                 stats.get("errors_count", 0),
                 run_id)
            )

    def get_summary_counts(self) -> dict[str, int]:
        with self.connection() as conn:
            total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            active_products = conn.execute("SELECT COUNT(*) FROM products WHERE active = 1").fetchone()[0]
            devices_count = conn.execute("SELECT COUNT(*) FROM products WHERE category IN ('keyboard', 'mouse', 'headset', 'microphone')").fetchone()[0]
            ambiguous_count = conn.execute("SELECT COUNT(*) FROM products WHERE category = 'other' OR category_confidence < 0.5").fetchone()[0]
            total_duplicates = conn.execute("SELECT COUNT(*) FROM possible_duplicates").fetchone()[0]
            total_artifacts = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            total_bytes = conn.execute("SELECT COALESCE(SUM(size), 0) FROM artifacts").fetchone()[0]
            total_vid_pids = conn.execute("SELECT COUNT(DISTINCT vid || ':' || pid) FROM device_identifiers").fetchone()[0]
            total_hints = conn.execute("SELECT COUNT(*) FROM protocol_hints").fetchone()[0]
            total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            total_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            total_runs = conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0]
            return {
                "total_products": total_products,
                "active_products": active_products,
                "devices_count": devices_count,
                "total_hardware_devices": devices_count,
                "total_accessories": max(0, total_products - devices_count),
                "ambiguous_count": ambiguous_count,
                "total_duplicates": total_duplicates,
                "total_artifacts": total_artifacts,
                "total_bytes": total_bytes,
                "total_artifact_mb": total_bytes / 1024 / 1024,
                "total_vid_pids": total_vid_pids,
                "total_hints": total_hints,
                "total_facts": total_facts,
                "total_sources": total_sources,
                "total_runs": total_runs,
            }

    def get_product_with_details(self, search: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            query = """
            SELECT p.id, p.canonical_name, p.raw_name, p.category, p.category_confidence, p.metadata_confidence, p.product_url, p.first_seen, p.last_seen, p.active,
                   v.display_name as vendor_name
            FROM products p
            JOIN vendors v ON p.vendor_id = v.id
            WHERE p.canonical_name LIKE ? OR p.raw_name LIKE ? OR p.id = ?
            """
            rows = conn.execute(query, (f"%{search}%", f"%{search}%", search if search.isdigit() else -1)).fetchall()
            results = []
            for r in rows:
                p_id = r["id"]
                item = dict(r)
                
                # Identifiers
                ids = conn.execute(
                    "SELECT vid_hex, pid_hex, manufacturer_string, product_string, usage_page, usage, connection_type, evidence_level, confidence FROM device_identifiers WHERE product_id = ?",
                    (p_id,)
                ).fetchall()
                item["identifiers"] = [dict(x) for x in ids]
                
                # Hints
                hints = conn.execute(
                    "SELECT hint_key, hint_value, evidence_level, context FROM protocol_hints WHERE product_id = ?",
                    (p_id,)
                ).fetchall()
                item["protocol_hints"] = [dict(x) for x in hints]
                
                # Artifacts
                arts = conn.execute(
                    """SELECT a.sha256, a.filename, a.size, a.original_url, a.software_version, pa.relation_type 
                       FROM artifacts a
                       JOIN product_artifacts pa ON a.sha256 = pa.artifact_sha256
                       WHERE pa.product_id = ?""",
                    (p_id,)
                ).fetchall()
                item["artifacts"] = [dict(x) for x in arts]
                
                # Aliases
                aliases = conn.execute(
                    "SELECT alias_name, alias_url, evidence_level FROM product_aliases WHERE product_id = ?",
                    (p_id,)
                ).fetchall()
                item["aliases"] = [dict(x) for x in aliases]

                # Generic Facts
                fcts = conn.execute(
                    "SELECT key, value, evidence_level, is_inference FROM facts WHERE product_id = ?",
                    (p_id,)
                ).fetchall()
                item["facts"] = [dict(x) for x in fcts]
                
                results.append(item)
            return results

    def get_artifact_with_details(self, search: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            query = """
            SELECT a.sha256, a.filename, a.size, a.original_url, a.final_url, a.content_type, 
                   a.downloaded_at, a.software_version, a.extraction_status, a.first_seen, a.last_seen,
                   v.display_name as vendor_name
            FROM artifacts a
            LEFT JOIN vendors v ON a.vendor_id = v.id
            WHERE a.sha256 LIKE ? OR a.filename LIKE ? OR a.original_url LIKE ?
            """
            rows = conn.execute(query, (f"{search}%", f"%{search}%", f"%{search}%")).fetchall()
            results = []
            for r in rows:
                sha = r["sha256"]
                item = dict(r)
                
                # Linked products
                prods = conn.execute("""
                    SELECT p.id, p.canonical_name, p.category, pa.relation_type
                    FROM product_artifacts pa
                    JOIN products p ON pa.product_id = p.id
                    WHERE pa.artifact_sha256 = ?
                """, (sha,)).fetchall()
                item["products"] = [dict(x) for x in prods]

                # Device identifiers from this artifact
                devs = conn.execute("""
                    SELECT d.vid_hex, d.pid_hex, d.product_string, d.usage_page, d.usage, p.canonical_name
                    FROM device_identifiers d
                    JOIN products p ON d.product_id = p.id
                    WHERE d.artifact_sha256 = ?
                """, (sha,)).fetchall()
                item["identifiers"] = [dict(x) for x in devs]

                # Protocol hints from this artifact
                hints = conn.execute("""
                    SELECT h.hint_key, h.hint_value, p.canonical_name
                    FROM protocol_hints h
                    JOIN products p ON h.product_id = p.id
                    WHERE h.artifact_sha256 = ?
                """, (sha,)).fetchall()
                item["protocol_hints"] = [dict(x) for x in hints]

                results.append(item)
            return results
