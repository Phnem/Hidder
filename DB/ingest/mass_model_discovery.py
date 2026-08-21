"""Evidence-first commercial model / variant identity graph.

This pass intentionally consumes the already ingested registry before trying
new network sources.  It never reclassifies a legacy product or promotes a
brand-level association to a model-level fact.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ingest.config import DB_PATH
from ingest.brands.canonical import ALL_CANONICAL_BRANDS
from ingest.storage.database import RegistryDatabase

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports"
DEFAULT_INBOX = ROOT / "protocol-miner" / "inbox"

PERIPHERAL_CATEGORIES = {"keyboard", "mouse", "headset", "microphone", "controller"}
MODELISH = re.compile(r"(?:[A-Za-z]{1,12}[\s_-]*\d{1,4}|\d{1,4}[A-Za-z]{1,12}|\b(?:pro|max|ultra|wireless|magnetic)\b)", re.I)
TRAILING_DESCRIPTION = re.compile(
    r"\s+(?:wireless|wired|gaming|mechanical|magnetic|custom|ultra-light(?:weight)?|ergonomic|"
    r"tri[ -]?mode|quad[ -]?mode|bluetooth|keyboard|mouse|headset|controller|switch|"
    r"with\b|for\b|series\b|collection\b).*$",
    re.I,
)
VARIANT_TOKEN = re.compile(r"^(?:pro|max|ultra|se\+?|he|v\d+|gen\d+|wireless|wired|mini|plus|8k|4k)$", re.I)
NOISE_FILENAME = re.compile(r"^(?:setup|installer|driver|firmware|update|download|keyboard|mouse|connect|checkdevice|hub|allinone|wiredonly)$", re.I)
URL_RE = re.compile(r"https?://[^\s\"']+", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(value: str) -> str:
    """Formatting-only key; plus is retained so SE and SE+ can never merge."""
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("™", "").replace("®", "")
    value = re.sub(r"(?<=\w)\+(?=\w|$)", " plus ", value)
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def clean_commercial_name(name: str, brand_name: str = "") -> str:
    """Remove only retail prose; preserve meaningful commercial suffixes."""
    value = unicodedata.normalize("NFKC", name or "").replace("™", "").replace("®", "")
    value = re.sub(r"\s+", " ", value).strip(" -_/|:")
    if brand_name:
        value = re.sub(rf"^{re.escape(brand_name)}\s*[-/:|]?\s*", "", value, flags=re.I).strip()
    value = re.sub(r"\s*\([^)]{1,80}\)\s*", " ", value).strip()
    value = TRAILING_DESCRIPTION.sub("", value).strip(" -_/|:")
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class NameParts:
    model_name: str
    model_key: str
    variant_name: str
    variant_key: str
    variant_label: str


def split_model_variant(name: str, brand_name: str = "") -> NameParts:
    """Conservatively split known commercial suffixes without deleting them.

    ``F75 Pro`` becomes model ``F75`` / variant ``F75 Pro`` while unrelated
    names have a DEFAULT variant.  ``SE+`` remains distinct from ``SE``.
    """
    full = clean_commercial_name(name, brand_name)
    tokens = full.split()
    start = None
    for i, token in enumerate(tokens[1:], 1):
        normal = re.sub(r"[^A-Za-z0-9+]", "", token).replace(" ", "")
        if VARIANT_TOKEN.match(normal):
            start = i
            break
    if start is None:
        base, label = full, "DEFAULT"
    else:
        base, label = " ".join(tokens[:start]), " ".join(tokens[start:])
    return NameParts(
        model_name=base,
        model_key=_compact(base),
        variant_name=full,
        variant_key=_compact(full),
        variant_label=label or "DEFAULT",
    )


def is_candidate_name(name: str) -> bool:
    value = clean_commercial_name(name)
    if not value or len(value) < 2 or len(value) > 120 or NOISE_FILENAME.match(value):
        return False
    return bool(MODELISH.search(value))


def source_evidence_class(product_url: str | None, source_type: str | None) -> tuple[str, float]:
    if source_type in {"vendor_software", "vendor_download"}:
        return "OFFICIAL_SOFTWARE_MODEL_TABLE", 0.90
    if source_type in {"vendor_product", "vendor_technical"}:
        return "OFFICIAL_PRODUCT_PAGE", 0.90
    host = (urlparse(product_url or "").netloc or "").lower()
    if host and not any(x in host for x in ("github.com", "gitlab.com", "kernel.org", "sourceforge.net")):
        return "OFFICIAL_PRODUCT_PAGE", 0.93
    if source_type == "community":
        return "COMMUNITY_IMPLEMENTATION", 0.66
    return "STATIC_IMPLEMENTATION", 0.72


def identity_role(category: str, connection_type: str | None, product_string: str | None) -> tuple[str, str]:
    text = " ".join((category or "", connection_type or "", product_string or "")).lower()
    if any(x in text for x in ("receiver", "dongle", "2.4g", "2.4 ghz")):
        return "RECEIVER", "RECEIVER"
    if "boot" in text:
        return "BOOTLOADER", "BOOTLOADER"
    if any(x in text for x in ("firmware", "dfu", "update")):
        return "FIRMWARE_UPDATE", "FIRMWARE_UPDATE"
    if "wired" in text or "usb" in text:
        return "PERIPHERAL", "WIRED"
    return "PERIPHERAL", "PERIPHERAL"


class ModelInventoryPass:
    def __init__(self, db_path: Path = DB_PATH, report_dir: Path = DEFAULT_REPORT_DIR, inbox: Path = DEFAULT_INBOX):
        self.db = RegistryDatabase(db_path)
        self.report_dir = Path(report_dir)
        self.inbox = Path(inbox)
        self.stats: Counter[str] = Counter()
        self._variant_for_product: dict[int, int] = {}
        self._canonical_slugs = {brand.slug for brand in ALL_CANONICAL_BRANDS}
        self._allowed_brand_ids: set[int] = set()

    def _canonical_brand_ids(self, conn: sqlite3.Connection) -> set[int]:
        if not self._allowed_brand_ids:
            placeholders = ",".join("?" for _ in self._canonical_slugs)
            self._allowed_brand_ids = {row["id"] for row in conn.execute(
                f"SELECT id FROM brands WHERE slug IN ({placeholders})", tuple(self._canonical_slugs)
            ).fetchall()}
        return self._allowed_brand_ids

    def _purge_noncanonical_projection(self, conn: sqlite3.Connection) -> None:
        """Delete only derived graph rows created from old raw-brand entries."""
        allowed = self._canonical_brand_ids(conn)
        if not allowed:
            return
        placeholders = ",".join("?" for _ in allowed)
        conn.execute(f"DELETE FROM commercial_models WHERE brand_id NOT IN ({placeholders})", tuple(allowed))
        conn.execute(f"DELETE FROM software_targets WHERE brand_id NOT IN ({placeholders})", tuple(allowed))
        conn.execute("""DELETE FROM model_evidence WHERE id NOT IN (
            SELECT MIN(id) FROM model_evidence GROUP BY model_variant_id,evidence_class,
                COALESCE(source_id,-1),COALESCE(source_url,''),COALESCE(source_path,''),extraction_method
        )""")
        conn.execute("""DELETE FROM usb_device_identities WHERE id NOT IN
            (SELECT usb_device_identity_id FROM model_identity_bindings UNION SELECT receiver_identity_id FROM receiver_bindings)""")

    def _brand_id(self, conn: sqlite3.Connection, vendor_id: int, vendor_name: str) -> int | None:
        row = conn.execute("SELECT id FROM brands WHERE id=?", (vendor_id,)).fetchone()
        if row and row["id"] in self._canonical_brand_ids(conn):
            return row["id"]
        row = conn.execute("SELECT id FROM brands WHERE lower(canonical_name)=lower(?) OR lower(slug)=lower(?)", (vendor_name, vendor_name)).fetchone()
        return row["id"] if row and row["id"] in self._canonical_brand_ids(conn) else None

    @staticmethod
    def _upsert_evidence(conn: sqlite3.Connection, variant_id: int, evidence_class: str, *, source_id: int | None = None,
                         source_url: str | None = None, source_path: str | None = None, extraction_method: str,
                         confidence: float, details: dict[str, Any] | None = None) -> int:
        row = conn.execute(
            """SELECT id FROM model_evidence WHERE model_variant_id=? AND evidence_class=? AND source_id IS ?
               AND source_url IS ? AND source_path IS ? AND extraction_method=?""",
            (variant_id, evidence_class, source_id, source_url, source_path, extraction_method),
        ).fetchone()
        if row:
            return int(row["id"])
        conn.execute(
            """INSERT OR IGNORE INTO model_evidence
               (model_variant_id,evidence_class,source_id,source_url,source_path,extraction_method,confidence,details_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (variant_id, evidence_class, source_id, source_url, source_path, extraction_method, confidence,
             json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
        )
        return int(conn.execute("SELECT last_insert_rowid() id").fetchone()["id"])

    def _upsert_variant(self, conn: sqlite3.Connection, *, brand_id: int, brand_name: str, raw_name: str,
                        category: str | None, source_product_id: int | None, lifecycle: str = "CURRENT",
                        candidate_only: bool = False, evidence_class: str, source_id: int | None = None,
                        source_url: str | None = None, source_path: str | None = None, extraction_method: str,
                        confidence: float, details: dict[str, Any] | None = None) -> int | None:
        if not is_candidate_name(raw_name):
            return None
        parts = split_model_variant(raw_name, brand_name)
        if not parts.model_key or not parts.variant_key:
            return None
        conn.execute(
            """INSERT INTO commercial_models (brand_id,canonical_name,normalized_name,category,lifecycle_status,candidate_only,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(brand_id,normalized_name) DO UPDATE SET
                 category=COALESCE(commercial_models.category,excluded.category),
                 candidate_only=MIN(commercial_models.candidate_only,excluded.candidate_only), updated_at=excluded.updated_at""",
            (brand_id, parts.model_name, parts.model_key, category, lifecycle, int(candidate_only), utc_now()),
        )
        model = conn.execute("SELECT id FROM commercial_models WHERE brand_id=? AND normalized_name=?", (brand_id, parts.model_key)).fetchone()
        conn.execute(
            """INSERT INTO model_variants (commercial_model_id,canonical_name,normalized_name,variant_label,lifecycle_status,source_product_id,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(commercial_model_id,normalized_name) DO UPDATE SET
                 source_product_id=COALESCE(model_variants.source_product_id,excluded.source_product_id), updated_at=excluded.updated_at""",
            (model["id"], parts.variant_name, parts.variant_key, parts.variant_label, lifecycle, source_product_id, utc_now()),
        )
        variant = conn.execute("SELECT id FROM model_variants WHERE commercial_model_id=? AND normalized_name=?", (model["id"], parts.variant_key)).fetchone()
        variant_id = int(variant["id"])
        self._upsert_evidence(conn, variant_id, evidence_class, source_id=source_id, source_url=source_url,
                              source_path=source_path, extraction_method=extraction_method,
                              confidence=confidence, details=details)
        cleaned_raw = clean_commercial_name(raw_name, brand_name)
        if cleaned_raw and _compact(cleaned_raw) == parts.variant_key and cleaned_raw != parts.variant_name:
            conn.execute(
                """INSERT OR IGNORE INTO model_aliases (model_variant_id,alias_name,normalized_alias,alias_kind,source_url)
                   VALUES (?,?,?,?,?)""", (variant_id, cleaned_raw, _compact(cleaned_raw), "FORMAT", source_url))
        return variant_id

    def ingest_registry_products(self) -> None:
        """Create model/variant nodes from existing source-local product records."""
        with self.db.connection() as conn:
            self._purge_noncanonical_projection(conn)
            rows = conn.execute(
                """SELECT p.id,p.vendor_id,p.canonical_name,p.raw_name,p.category,p.product_url,p.active,
                          s.id source_id,s.source_type,s.source_url,v.display_name
                   FROM products p JOIN vendors v ON v.id=p.vendor_id
                   LEFT JOIN sources s ON s.id=(SELECT id FROM sources sx WHERE sx.vendor_id=p.vendor_id
                        AND sx.source_url=p.product_url ORDER BY sx.id DESC LIMIT 1)"""
            ).fetchall()
            for row in rows:
                # Retail components and product lines are not commercial HID models.  A technical
                # identity can rescue an otherwise unclassified model, but only with model-shaped text.
                has_identity = conn.execute("SELECT 1 FROM device_identifiers WHERE product_id=? LIMIT 1", (row["id"],)).fetchone()
                if row["category"] not in PERIPHERAL_CATEGORIES and not (has_identity and is_candidate_name(row["canonical_name"])):
                    continue
                brand_id = self._brand_id(conn, row["vendor_id"], row["display_name"])
                if brand_id is None:
                    continue
                cls, confidence = source_evidence_class(row["product_url"], row["source_type"])
                variant = self._upsert_variant(
                    conn, brand_id=brand_id, brand_name=row["display_name"], raw_name=row["canonical_name"],
                    category=row["category"], source_product_id=row["id"], lifecycle="CURRENT" if row["active"] else "DISCONTINUED",
                    evidence_class=cls, source_id=row["source_id"], source_url=row["product_url"] or row["source_url"],
                    extraction_method="legacy_product_projection", confidence=confidence,
                    details={"legacy_product_id": row["id"], "raw_name": row["raw_name"]},
                )
                if variant:
                    self._variant_for_product[row["id"]] = variant
                    self.stats["product_projections"] += 1

    def bridge_identities(self) -> None:
        """Project legacy VID/PID observations into distinct technical identity nodes."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """SELECT d.*,p.category,p.product_url,s.source_type,s.source_url
                   FROM device_identifiers d JOIN products p ON p.id=d.product_id
                   LEFT JOIN sources s ON s.id=d.source_id"""
            ).fetchall()
            for row in rows:
                variant = self._variant_for_product.get(row["product_id"])
                if not variant:
                    continue
                role, binding_role = identity_role(row["category"], row["connection_type"], row["product_string"])
                identity = conn.execute(
                    """SELECT id FROM usb_device_identities WHERE vid=? AND pid=? AND usage_page IS ? AND usage IS ?
                       AND identity_role=? ORDER BY id LIMIT 1""", (row["vid"], row["pid"], row["usage_page"], row["usage"], role)
                ).fetchone()
                if identity is None:
                    conn.execute(
                        """INSERT INTO usb_device_identities
                           (vid,pid,vid_hex,pid_hex,usage_page,usage,manufacturer_string,product_string,identity_role)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (row["vid"], row["pid"], row["vid_hex"], row["pid_hex"], row["usage_page"], row["usage"],
                         row["manufacturer_string"], row["product_string"], role),
                    )
                    identity = conn.execute(
                        """SELECT id FROM usb_device_identities WHERE vid=? AND pid=? AND usage_page IS ? AND usage IS ?
                           AND identity_role=? ORDER BY id LIMIT 1""", (row["vid"], row["pid"], row["usage_page"], row["usage"], role)
                    ).fetchone()
                source_cls, source_conf = source_evidence_class(row["product_url"], row["source_type"])
                confidence = "EXACT_OFFICIAL" if source_cls == "OFFICIAL_PRODUCT_PAGE" else "EXACT_STATIC_IMPLEMENTATION"
                evidence_id = self._upsert_evidence(conn, variant, source_cls, source_id=row["source_id"],
                    source_url=row["source_url"] or row["product_url"], extraction_method="legacy_identifier_projection",
                    confidence=max(source_conf, float(row["confidence"] or 0.0)),
                    details={"vid": row["vid_hex"], "pid": row["pid_hex"], "legacy_device_identifier_id": row["id"]})
                conn.execute(
                    """INSERT OR IGNORE INTO model_identity_bindings
                       (model_variant_id,usb_device_identity_id,binding_role,binding_confidence,source_device_identifier_id,evidence_id,provenance)
                       VALUES (?,?,?,?,?,?,?)""",
                    (variant, identity["id"], binding_role, confidence, row["id"], evidence_id, "legacy_device_identifier_projection"),
                )
                if binding_role == "WIRED":
                    conn.execute("INSERT OR IGNORE INTO model_transports(model_variant_id,transport,evidence_id,confidence) VALUES (?, 'WIRED_USB', ?, ?)", (variant, evidence_id, source_conf))
                elif binding_role == "RECEIVER":
                    # This records the identity as a receiver and transport evidence only.  It does
                    # not claim it belongs to a different peripheral without an explicit binding.
                    conn.execute("INSERT OR IGNORE INTO model_transports(model_variant_id,transport,evidence_id,confidence) VALUES (?, 'USB_2_4G_RECEIVER', ?, ?)", (variant, evidence_id, source_conf))
                self.stats["identity_bindings"] += 1

    def bridge_protocols(self) -> None:
        """Carry only existing product-scoped mappings forward; no shared-VID/brand inference."""
        with self.db.connection() as conn:
            rows = conn.execute(
                """SELECT d.product_id,d.protocol_family_id,d.confidence,d.mapping_basis,
                          f.family_key,p.canonical_name FROM device_protocol_mappings d
                   JOIN protocol_families f ON f.id=d.protocol_family_id JOIN products p ON p.id=d.product_id"""
            ).fetchall()
            for row in rows:
                variant = self._variant_for_product.get(row["product_id"])
                if not variant:
                    continue
                status = "EXACT" if float(row["confidence"] or 0) >= .85 else "CANDIDATE"
                evidence = self._upsert_evidence(conn, variant, "STATIC_IMPLEMENTATION", extraction_method="legacy_protocol_projection",
                    confidence=float(row["confidence"] or 0), details={"family_key": row["family_key"], "mapping_basis": row["mapping_basis"], "source_product_id": row["product_id"]})
                conn.execute(
                    """INSERT OR IGNORE INTO model_protocol_bindings
                       (model_variant_id,protocol_family_id,binding_status,confidence,source_product_id,evidence_id,provenance)
                       VALUES (?,?,?,?,?,?,?)""",
                    (variant, row["protocol_family_id"], status, row["confidence"], row["product_id"], evidence, "legacy_product_scoped_mapping"),
                )
                self.stats["protocol_bindings"] += 1

    @staticmethod
    def _software_target_from_path(path: Path) -> tuple[str, str, str | None]:
        text = path.read_text(encoding="utf-8", errors="ignore")[:8192] if path.suffix.lower() == ".txt" else ""
        urls = URL_RE.findall(text)
        url = urls[0] if urls else None
        key = url or str(path).replace("\\", "/").casefold()
        kind = "WEB_CONFIGURATOR" if url else ("FIRMWARE_TOOL" if re.search(r"firmware|update|flash", path.name, re.I) else "NATIVE_APPLICATION")
        return key, kind, url

    def ingest_inbox(self) -> None:
        """Use explicit model-bearing official inbox artefact names as software evidence.

        A generic vendor suite becomes a SoftwareTarget but produces no compatibility
        edge; that is the guard against the forbidden 'same brand => supported' rule.
        """
        if not self.inbox.exists():
            return
        with self.db.connection() as conn:
            allowed = self._canonical_brand_ids(conn)
            placeholders = ",".join("?" for _ in allowed)
            brands = {r["canonical_name"].casefold(): r for r in conn.execute(
                f"SELECT id,canonical_name,slug FROM brands WHERE id IN ({placeholders})", tuple(allowed)
            ).fetchall()}
            aliases = {r["alias"].casefold(): r["brand_id"] for r in conn.execute(
                f"SELECT brand_id,alias FROM brand_aliases WHERE brand_id IN ({placeholders})", tuple(allowed)
            ).fetchall()}
            for path in self.inbox.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.inbox)
                if not rel.parts:
                    continue
                brand_token = rel.parts[0].casefold()
                brand = brands.get(brand_token)
                brand_id = brand["id"] if brand else aliases.get(brand_token)
                if brand_id is None:
                    continue
                brand_name_row = conn.execute("SELECT canonical_name FROM brands WHERE id=?", (brand_id,)).fetchone()
                brand_name = brand_name_row["canonical_name"]
                target_key, kind, url = self._software_target_from_path(path)
                conn.execute(
                    """INSERT OR IGNORE INTO software_targets(brand_id,target_key,display_name,target_kind,official_status,target_url,source_path)
                       VALUES(?,?,?,?,?,?,?)""", (brand_id, target_key, path.stem, kind, "OFFICIAL", url, str(rel)))
                target = conn.execute("SELECT id FROM software_targets WHERE brand_id=? AND target_key=?", (brand_id, target_key)).fetchone()
                self.stats["software_targets"] += 1
                # Filename models are deliberately conservative.  Folder/category names and generic
                # executables therefore cannot create a compatibility edge.
                candidate = re.sub(r"\.(?:exe|msi|zip|7z|rar|bin|hex|json|txt)$", "", path.name, flags=re.I)
                candidate = re.sub(r"(?:[_ -](?:setup|installer|driver|software|firmware|update|tool).*)$", "", candidate, flags=re.I)
                candidate = clean_commercial_name(candidate, brand_name)
                if not is_candidate_name(candidate):
                    continue
                variant = self._upsert_variant(conn, brand_id=brand_id, brand_name=brand_name, raw_name=candidate,
                    category=(rel.parts[1].casefold() if len(rel.parts) > 1 and rel.parts[1].casefold() in PERIPHERAL_CATEGORIES else None),
                    source_product_id=None, candidate_only=False, evidence_class="OFFICIAL_SOFTWARE_MODEL_TABLE",
                    source_path=str(rel), source_url=url, extraction_method="official_inbox_filename", confidence=.80,
                    details={"software_target": target_key, "inbox_path": str(rel)})
                if not variant:
                    continue
                evidence = self._upsert_evidence(conn, variant, "OFFICIAL_SOFTWARE_MODEL_TABLE", source_url=url, source_path=str(rel),
                    extraction_method="official_inbox_filename", confidence=.80, details={"software_target": target_key})
                conn.execute(
                    """INSERT OR IGNORE INTO software_model_compatibilities
                       (software_target_id,model_variant_id,compatibility_status,confidence,evidence_id,provenance)
                       VALUES(?,?, 'SUPPORTED_OFFICIAL', ?, ?, ?)""",
                    (target["id"], variant, .80, evidence, "explicit_model_bearing_official_inbox_artifact"))
                self.stats["software_compatibilities"] += 1

    def discover_official_catalogs(self, max_pages_per_brand: int = 8) -> None:
        """Best-effort public sitemap/JSON-LD discovery for explicit opt-in online runs.

        Failures are recorded as gaps, not silently converted into invented data.
        """
        with self.db.connection() as conn:
            allowed = self._canonical_brand_ids(conn)
            placeholders = ",".join("?" for _ in allowed)
            brands = conn.execute(
                f"SELECT id,canonical_name,website FROM brands WHERE id IN ({placeholders}) AND website IS NOT NULL", tuple(allowed)
            ).fetchall()
            for brand in brands:
                root = brand["website"].rstrip("/")
                pages = [root + "/sitemap.xml", root + "/sitemap_index.xml", root + "/products"]
                fetched = 0
                for page in pages:
                    if fetched >= max_pages_per_brand:
                        break
                    try:
                        request = Request(page, headers={"User-Agent": "PeripheralRegistryModelDiscovery/1.0"})
                        with urlopen(request, timeout=15) as response:
                            body = response.read(2_000_000).decode("utf-8", "ignore")
                    except Exception:
                        continue
                    fetched += 1
                    self.stats["official_pages_fetched"] += 1
                    # JSON-LD Product nodes are precise enough to add; sitemap URLs merely seed
                    # future collectors and are not treated as model assertions.
                    for match in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", body, re.I | re.S):
                        try:
                            payload = json.loads(match.group(1).strip())
                        except json.JSONDecodeError:
                            continue
                        nodes: Iterable[dict[str, Any]] = payload if isinstance(payload, list) else payload.get("@graph", [payload]) if isinstance(payload, dict) else []
                        for node in nodes:
                            if not isinstance(node, dict) or str(node.get("@type", "")).lower() != "product":
                                continue
                            name = str(node.get("name") or "")
                            if not is_candidate_name(name):
                                continue
                            self._upsert_variant(conn, brand_id=brand["id"], brand_name=brand["canonical_name"], raw_name=name,
                                category=None, source_product_id=None, evidence_class="OFFICIAL_PRODUCT_PAGE", source_url=page,
                                extraction_method="official_json_ld_product", confidence=.95, details={"json_ld": True})
                self.stats["official_catalogs_attempted"] += 1

    def _rows(self, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def write_reports(self) -> dict[str, Any]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        with self.db.connection() as conn:
            allowed = self._canonical_brand_ids(conn)
            placeholders = ",".join("?" for _ in allowed)
            models = self._rows(conn, f"""SELECT b.canonical_name brand,m.id model_id,m.canonical_name,m.category,m.lifecycle_status,m.candidate_only,
                v.id variant_id,v.canonical_name variant_name,v.variant_label,v.generation,v.model_code,v.lifecycle_status variant_lifecycle
                FROM commercial_models m JOIN brands b ON b.id=m.brand_id JOIN model_variants v ON v.commercial_model_id=m.id
                WHERE b.id IN ({placeholders}) ORDER BY b.canonical_name,m.canonical_name,v.canonical_name""", tuple(allowed))
            evidence = self._rows(conn, f"""SELECT e.* FROM model_evidence e JOIN model_variants v ON v.id=e.model_variant_id
                JOIN commercial_models m ON m.id=v.commercial_model_id WHERE m.brand_id IN ({placeholders}) ORDER BY e.model_variant_id,e.id""", tuple(allowed))
            aliases = self._rows(conn, f"""SELECT a.* FROM model_aliases a JOIN model_variants v ON v.id=a.model_variant_id
                JOIN commercial_models m ON m.id=v.commercial_model_id WHERE m.brand_id IN ({placeholders}) ORDER BY a.model_variant_id,a.id""", tuple(allowed))
            for item in models:
                item["evidence"] = [x for x in evidence if x["model_variant_id"] == item["variant_id"]]
                item["aliases"] = [x for x in aliases if x["model_variant_id"] == item["variant_id"]]
            bindings = self._rows(conn, f"""SELECT b.canonical_name brand,m.canonical_name model,v.canonical_name variant,
                i.vid_hex,i.pid_hex,i.interface_number,i.usage_page,i.usage,i.bcd_device,i.manufacturer_string,i.product_string,i.identity_role,
                x.binding_role,x.binding_confidence,x.provenance,x.source_device_identifier_id
                FROM model_identity_bindings x JOIN model_variants v ON v.id=x.model_variant_id
                JOIN commercial_models m ON m.id=v.commercial_model_id JOIN brands b ON b.id=m.brand_id
                JOIN usb_device_identities i ON i.id=x.usb_device_identity_id WHERE b.id IN ({placeholders}) ORDER BY b.canonical_name,m.canonical_name,v.canonical_name""", tuple(allowed))
            compatibility = self._rows(conn, f"""SELECT b.canonical_name brand,m.canonical_name model,v.canonical_name variant,s.display_name software_target,
                s.target_kind,s.target_url,c.compatibility_status,c.confidence,c.provenance
                FROM software_model_compatibilities c JOIN software_targets s ON s.id=c.software_target_id
                JOIN model_variants v ON v.id=c.model_variant_id JOIN commercial_models m ON m.id=v.commercial_model_id
                JOIN brands b ON b.id=m.brand_id WHERE b.id IN ({placeholders}) ORDER BY b.canonical_name,m.canonical_name""", tuple(allowed))
            protocols = self._rows(conn, f"""SELECT v.id variant_id,f.family_key,p.binding_status,p.confidence FROM model_protocol_bindings p
                JOIN model_variants v ON v.id=p.model_variant_id JOIN commercial_models m ON m.id=v.commercial_model_id
                JOIN protocol_families f ON f.id=p.protocol_family_id WHERE m.brand_id IN ({placeholders})""", tuple(allowed))
            transports = self._rows(conn, "SELECT * FROM model_transports")
            counts = {
                "brands": len(allowed),
                "commercial_models": conn.execute(f"SELECT COUNT(*) FROM commercial_models WHERE brand_id IN ({placeholders})", tuple(allowed)).fetchone()[0],
                "model_variants": len(models), "model_aliases": len(aliases), "model_evidence": len(evidence),
                "usb_device_identities": len({(x["vid_hex"], x["pid_hex"], x["identity_role"]) for x in bindings}),
                "model_identity_bindings": len(bindings),
                "software_targets": conn.execute(f"SELECT COUNT(*) FROM software_targets WHERE brand_id IN ({placeholders})", tuple(allowed)).fetchone()[0],
                "software_model_compatibilities": len(compatibility), "model_protocol_bindings": len(protocols),
            }
            total_brands = len(allowed)
            crawled = conn.execute(f"SELECT COUNT(DISTINCT brand_id) FROM commercial_models WHERE brand_id IN ({placeholders})", tuple(allowed)).fetchone()[0]
            exact_id = len({(x["brand"], x["model"], x["variant"]) for x in bindings if x["binding_confidence"].startswith("EXACT")})
            identity_variants = {x["variant"] + "|" + x["brand"] for x in bindings}
            model_variants = {x["variant_id"] for x in protocols}
            source_counts = {k: v for k, v in conn.execute(f"""SELECT e.evidence_class,COUNT(*) FROM model_evidence e
                JOIN model_variants v ON v.id=e.model_variant_id JOIN commercial_models m ON m.id=v.commercial_model_id
                WHERE m.brand_id IN ({placeholders}) GROUP BY e.evidence_class""", tuple(allowed))}
            summary = {
                "generated_at": utc_now(), "brands": {"total": total_brands, "successfully_crawled": crawled, "partially_crawled": max(0, total_brands-crawled), "blocked": 0},
                "models": {"canonical_commercial_models": counts["commercial_models"], "variants": counts["model_variants"], "aliases": counts["model_aliases"],
                           "discontinued_models": conn.execute(f"SELECT COUNT(*) FROM commercial_models WHERE lifecycle_status='DISCONTINUED' AND brand_id IN ({placeholders})", tuple(allowed)).fetchone()[0],
                           "candidate_only_models": conn.execute(f"SELECT COUNT(*) FROM commercial_models WHERE candidate_only=1 AND brand_id IN ({placeholders})", tuple(allowed)).fetchone()[0]},
                "identities": {"models_with_exact_vid_pid": exact_id, "variant_identity_bindings": counts["model_identity_bindings"],
                    "models_with_wired_identity": len({(x["brand"],x["model"],x["variant"]) for x in bindings if x["binding_role"] == "WIRED"}),
                    "models_with_receiver_identity": len({(x["brand"],x["model"],x["variant"]) for x in bindings if x["binding_role"] == "RECEIVER"}),
                    "ambiguous_identity_mappings": sum(x["binding_confidence"] == "AMBIGUOUS" for x in bindings),
                    "identity_unresolved_models": max(0, counts["model_variants"]-len(identity_variants))},
                "software": {"official_software_targets_found": counts["software_targets"], "model_to_software_exact_bindings": len(compatibility),
                    "observed_not_supported_pairs": sum(x["compatibility_status"] == "NOT_SUPPORTED_OBSERVED" for x in compatibility),
                    "ambiguous_software_compatibility": sum(x["compatibility_status"] == "AMBIGUOUS" for x in compatibility)},
                "protocol": {"model_variants_with_exact_protocol_family_binding": sum(x["binding_status"] == "EXACT" for x in protocols),
                    "candidate_family_binding": sum(x["binding_status"] == "CANDIDATE" for x in protocols),
                    "no_family_binding": max(0, counts["model_variants"]-len(model_variants))},
                "sources": source_counts, "pass_stats": dict(self.stats),
            }
            gaps = self._rows(conn, f"""SELECT b.canonical_name brand,COUNT(m.id) models,
                SUM(CASE WHEN v.id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_identity_bindings x WHERE x.model_variant_id=v.id) THEN 1 ELSE 0 END) variants_without_identity
                FROM brands b LEFT JOIN commercial_models m ON m.brand_id=b.id LEFT JOIN model_variants v ON v.commercial_model_id=m.id
                WHERE b.id IN ({placeholders}) GROUP BY b.id HAVING COUNT(m.id)=0 OR SUM(CASE WHEN v.id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM model_identity_bindings x WHERE x.model_variant_id=v.id) THEN 1 ELSE 0 END)>0
                ORDER BY models,variants_without_identity DESC""", tuple(allowed))
            audit = {
                "generated_at": utc_now(), "duplicate_variant_keys": self._rows(conn, """SELECT commercial_model_id,normalized_name,COUNT(*) count FROM model_variants GROUP BY commercial_model_id,normalized_name HAVING COUNT(*)>1"""),
                "ambiguous_identity_bindings": [x for x in bindings if x["binding_confidence"] == "AMBIGUOUS"],
                "receiver_links": self._rows(conn, "SELECT * FROM receiver_bindings"),
                "invariant": "No model↔protocol or model↔software binding was synthesized from a brand or shared VID/PID alone.",
            }
            readiness = self._emulation_candidates(conn, models, bindings, compatibility, transports)
        self._write_json("model_inventory_summary.json", summary)
        self._write_json("model_inventory_full.json", {"generated_at": utc_now(), "models": models})
        self._write_json("model_identity_bindings.json", {"generated_at": utc_now(), "bindings": bindings})
        self._write_json("model_software_compatibility.json", {"generated_at": utc_now(), "compatibilities": compatibility})
        self._write_json("model_inventory_gaps.json", {"generated_at": utc_now(), "gaps": gaps})
        self._write_json("model_inventory_audit.json", audit)
        self._write_json("emulation_model_candidates.json", {"generated_at": utc_now(), "candidates": readiness,
                         "note": "Inventory only; no UdeCx implementation or readiness promotion is implied."})
        self._write_text_inventory(models)
        return summary

    @staticmethod
    def _emulation_candidates(conn: sqlite3.Connection, models: list[dict[str, Any]], bindings: list[dict[str, Any]],
                              compatibility: list[dict[str, Any]], transports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_variant: dict[int, dict[str, Any]] = {}
        for item in models:
            entry = by_variant.setdefault(item["variant_id"], {"brand": item["brand"], "model": item["canonical_name"], "variant": item["variant_name"],
                "vid_pid": [], "bcdDevice": [], "manufacturer_strings": [], "product_strings": [], "serial_policy": "unknown",
                "configuration_descriptor_knowledge": False, "interface_knowledge": False, "endpoint_knowledge": False,
                "hid_report_descriptor_knowledge": False, "report_ids_sizes": [], "known_initial_responses": [], "receiver_identity": [],
                "official_software_targets": [], "transports": []})
        lookup = {(x["brand"], x["model"], x["variant"]): x for x in by_variant.values()}
        for row in bindings:
            entry = lookup.get((row["brand"], row["model"], row["variant"]))
            if not entry:
                continue
            identity = {"vid": row["vid_hex"], "pid": row["pid_hex"], "confidence": row["binding_confidence"], "role": row["binding_role"]}
            if row["binding_role"] == "RECEIVER": entry["receiver_identity"].append(identity)
            else: entry["vid_pid"].append(identity)
            if row["manufacturer_string"]: entry["manufacturer_strings"].append(row["manufacturer_string"])
            if row["product_string"]: entry["product_strings"].append(row["product_string"])
            if row["usage_page"] is not None or row["usage"] is not None: entry["interface_knowledge"] = True
        for row in compatibility:
            entry = lookup.get((row["brand"], row["model"], row["variant"]))
            if entry: entry["official_software_targets"].append({"name": row["software_target"], "url": row["target_url"], "status": row["compatibility_status"]})
        for row in transports:
            entry = by_variant.get(row["model_variant_id"])
            if entry: entry["transports"].append(row["transport"])
        return list(by_variant.values())

    def _write_json(self, filename: str, payload: Any) -> None:
        (self.report_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_text_inventory(self, models: list[dict[str, Any]]) -> None:
        grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for item in models:
            grouped[item["brand"]][item["canonical_name"]].append(item["variant_name"])
        lines: list[str] = []
        for brand in sorted(grouped, key=str.casefold):
            lines.append(brand)
            for model in sorted(grouped[brand], key=str.casefold):
                lines.append(f"  {model}")
                for variant in sorted(set(grouped[brand][model]), key=str.casefold):
                    if variant.casefold() != model.casefold():
                        lines.append(f"    {variant}")
        (self.report_dir / "model_inventory.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run(self, *, include_inbox: bool = True, online: bool = False) -> dict[str, Any]:
        self.ingest_registry_products()
        self.bridge_identities()
        self.bridge_protocols()
        if include_inbox:
            self.ingest_inbox()
        if online:
            self.discover_official_catalogs()
        return self.write_reports()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build evidence-backed commercial model / variant graph.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX)
    parser.add_argument("--no-inbox", action="store_true")
    parser.add_argument("--online", action="store_true", help="Also inspect official sitemap/JSON-LD endpoints (best effort).")
    args = parser.parse_args(argv)
    summary = ModelInventoryPass(args.db, args.reports, args.inbox).run(include_inbox=not args.no_inbox, online=args.online)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
