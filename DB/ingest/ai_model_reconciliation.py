"""Stage AI model lists, reconcile them against evidence, and report precision.

The input is a discovery corpus, never a truth source.  This module does not
write to the commercial model graph unless its explicit ``--promote`` switch is
used; normal runs write only the reconciliation staging tables and reports.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from ingest.brands.canonical import ALL_CANONICAL_BRANDS
from ingest.config import DB_PATH
from ingest.mass_model_discovery import ModelInventoryPass, clean_commercial_name, split_model_variant
from ingest.storage.database import RegistryDatabase

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path(r"C:\Users\2004i\Downloads\brends.txt")
REPORTS = ROOT / "reports"
AIS = ("QWEN", "GEMINI", "CLAUDE", "META_AI")
ARTIFACT_RE = re.compile(r"\b(?:driver|software|installer|setup|firmware|flash|update tool|\.exe\b|\.msi\b|\.zip\b|\.bin\b|\.hex\b)\b", re.I)
ACCESSORY_RE = re.compile(r"\b(?:keycap|switch(?:es)?|plate|desk\s*mat|mouse\s*pad|wrist\s*rest|cable|case|kit|module|barebone|numpad|keypad)\b", re.I)
RECEIVER_RE = re.compile(r"\b(?:receiver|dongle)\b", re.I)
COSMETIC_RE = re.compile(r"\b(?:black|white|pink|red|blue|green|silver|gold|gray|grey|edition|theme|limited|"
                          r"anniversary|collab|crimson|sakura|ocean|nara|panda|naraka|mint|colour|color)\b", re.I)
BRAND_LINE = re.compile(r"^\s*(?:===\s*)?(?:БРЕНД\s*:\s*)?([A-Za-z0-9·&.'+\- ]{2,80})(?:\s*===)?\s*$", re.I)
INLINE_CATEGORY = re.compile(r"^\s*(Мыши|МЫШИ|Клавиатуры|КЛАВИАТУРЫ)\s*:\s*(.*)$")
CATEGORY_LINE = re.compile(r"^\s*(?:\[\s*)?(МЫШИ|Мыши|КЛАВИАТУРЫ|Клавиатуры)\s*(?:\])?\s*(?:\([^)]*\))?\s*:?[\s-]*$")
BULLET = re.compile(r"^\s*(?:[-•*])\s+(.+?)\s*$")
SECTION = re.compile(r"^\s*(QWEN|GEMINI|CLAUDE|META\s*AI)\s*:\s*$", re.I)


def compact_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("™", "").replace("®", "")
    value = re.sub(r"(?<=\w)\+(?=\w|$)", " plus ", value)
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def normalise_candidate(value: str, brand: str = "") -> tuple[str, str]:
    text = clean_commercial_name(value, brand)
    text = re.sub(r"\s+", " ", text).strip(" -–—")
    return text, compact_name(text)


@dataclass(frozen=True)
class RawCandidate:
    source_ai: str
    raw_brand: str
    category: str
    raw_model_name: str
    line_number: int


def _split_inline(value: str) -> Iterable[str]:
    if re.search(r"\b(?:нет|не производит|собственных)\b", value, re.I):
        return []
    return [part.strip() for part in re.split(r"\s*,\s*", value) if part.strip()]


def parse_ai_sections(text: str, known_brands: set[str]) -> list[RawCandidate]:
    """Parse the four intentionally different list formats without guessing prose."""
    result: list[RawCandidate] = []
    source: str | None = None
    brand: str | None = None
    category: str | None = None
    lines = text.splitlines()
    for lineno, original in enumerate(lines, 1):
        line = original.strip()
        match = SECTION.match(line)
        if match:
            source = {"META AI": "META_AI"}.get(match.group(1).upper(), match.group(1).upper())
            brand = category = None
            continue
        if source not in AIS or not line:
            continue
        inline = INLINE_CATEGORY.match(line)
        if inline and brand:
            category = "MOUSE" if inline.group(1).casefold().startswith("мыш") else "KEYBOARD"
            for name in _split_inline(inline.group(2)):
                result.append(RawCandidate(source, brand, category, name, lineno))
            continue
        cat = CATEGORY_LINE.search(line)
        if cat:
            category = "MOUSE" if cat.group(1).casefold().startswith("мыш") else "KEYBOARD"
            continue
        # Bracketed headings are unambiguous.  Bare headings are accepted only
        # when they match a canonical/known alias, avoiding narrative prose.
        bracket = re.fullmatch(r"\[([^]]+)\]", line)
        candidate_brand = bracket.group(1).strip() if bracket else None
        if not candidate_brand:
            plain = BRAND_LINE.match(line)
            if plain:
                possible = plain.group(1).strip().strip("-=")
                previous = lines[lineno - 2].strip() if lineno >= 2 else ""
                following = lines[lineno].strip() if lineno < len(lines) else ""
                delimited = bool(re.fullmatch(r"[-=]{8,}", previous) or re.fullmatch(r"[-=]{8,}", following))
                if possible.casefold() in known_brands or delimited:
                    candidate_brand = possible
        if candidate_brand:
            brand, category = candidate_brand, None
            continue
        bullet = BULLET.match(line)
        if bullet and brand and category:
            value = bullet.group(1).strip()
            if not re.fullmatch(r"(?:NONE|нет|мышей нет|клавиатур нет|бренд не производит.*)", value, re.I):
                result.append(RawCandidate(source, brand, category, value, lineno))
    return result


class AIModelReconciliation:
    def __init__(self, db_path: Path = DB_PATH, input_path: Path = DEFAULT_INPUT, report_dir: Path = REPORTS):
        self.db = RegistryDatabase(db_path)
        self.input_path = Path(input_path)
        self.report_dir = Path(report_dir)
        self.brand_lookup: dict[str, tuple[int, str]] = {}
        self.official: dict[tuple[int, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        self.strong: dict[tuple[int, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.repair_stats: Counter[str] = Counter()
        self.previous_repair_stats: dict[str, int] = {}
        quality_path = self.report_dir / "ai_source_quality.json"
        if quality_path.exists():
            try:
                prior_quality = json.loads(quality_path.read_text(encoding="utf-8"))
                self.previous_repair_stats = {
                    key: int(value)
                    for key, value in prior_quality.get("verifier_repair", {}).items()
                    if isinstance(value, (int, float))
                }
            except (OSError, ValueError, TypeError):
                pass

    def _load_brand_lookup(self, conn: sqlite3.Connection) -> None:
        canonical_slugs = {item.slug for item in ALL_CANONICAL_BRANDS}
        placeholders = ",".join("?" for _ in canonical_slugs)
        for row in conn.execute(f"SELECT id,slug,canonical_name FROM brands WHERE slug IN ({placeholders})", tuple(canonical_slugs)):
            value = (row["id"], row["canonical_name"])
            self.brand_lookup[row["canonical_name"].casefold()] = value
            self.brand_lookup[row["slug"].casefold()] = value
        for row in conn.execute(f"SELECT a.alias,a.brand_id,b.canonical_name FROM brand_aliases a JOIN brands b ON b.id=a.brand_id WHERE b.slug IN ({placeholders})", tuple(canonical_slugs)):
            self.brand_lookup[row["alias"].casefold()] = (row["brand_id"], row["canonical_name"])
        # These are canonical brand aliases already represented by brand-line
        # configuration/relationships, not new inferred brand mappings.
        for alias, slug in {"asus rog": "asus", "logitech g": "logitech", "fnatic gear": "fnatic", "pulsar gaming gears": "pulsar", "cougar gaming": "cougar"}.items():
            row = conn.execute("SELECT id,canonical_name FROM brands WHERE slug=?", (slug,)).fetchone()
            if row:
                self.brand_lookup.setdefault(alias, (row["id"], row["canonical_name"]))

    @staticmethod
    def _registrable_domain(host: str) -> str:
        parts = host.casefold().removeprefix("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host.casefold()

    def _refresh_official_domains(self, conn: sqlite3.Connection) -> dict[int, dict[str, set[str]]]:
        """Persist a provenance-bearing domain set, including official subdomains."""
        by_slug = {item.slug: item for item in ALL_CANONICAL_BRANDS}
        for brand in ALL_CANONICAL_BRANDS:
            brand_row = conn.execute("SELECT id FROM brands WHERE slug=?", (brand.slug,)).fetchone()
            if not brand_row:
                continue
            urls = [brand.website, brand.shopify_url or "", *brand.catalog_urls, *brand.sitemap_urls, *brand.download_urls,
                    *(entry[0] for entry in brand.web_configurator_urls)]
            for url in urls:
                host = urlparse(url).netloc.casefold().removeprefix("www.")
                if host:
                    conn.execute("""INSERT OR IGNORE INTO official_brand_domains
                        (brand_id,hostname,registrable_domain,provenance,source_url,confidence) VALUES(?,?,?,?,?,1.0)""",
                        (brand_row["id"], host, self._registrable_domain(host), "CANONICAL_BRAND_CONFIGURATION", url))
        # A source already classified as vendor-owned is independent provenance
        # for the host and lets product/support siblings share its root domain.
        for row in conn.execute("""SELECT s.source_url,s.source_type,v.name vendor_slug FROM sources s JOIN vendors v ON v.id=s.vendor_id
            WHERE s.source_type IN ('vendor_product','vendor_download','vendor_software','vendor_web','web_configurator')"""):
            if row["vendor_slug"] not in by_slug:
                continue
            brand_row = conn.execute("SELECT id FROM brands WHERE slug=?", (row["vendor_slug"],)).fetchone()
            host = urlparse(row["source_url"]).netloc.casefold().removeprefix("www.")
            if brand_row and host:
                conn.execute("""INSERT OR IGNORE INTO official_brand_domains
                    (brand_id,hostname,registrable_domain,provenance,source_url,confidence) VALUES(?,?,?,?,?,.98)""",
                    (brand_row["id"], host, self._registrable_domain(host), "RECORDED_VENDOR_OWNED_SOURCE", row["source_url"]))
        domains: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for row in conn.execute("SELECT brand_id,hostname,registrable_domain,provenance FROM official_brand_domains"):
            domains[row["brand_id"]][row["hostname"]].add(row["provenance"])
            domains[row["brand_id"]][row["registrable_domain"]].add(row["provenance"])
        return domains

    @staticmethod
    def _tier1_category(raw_name: str, source_url: str | None, legacy_category: str) -> tuple[str | None, str]:
        text = f"{raw_name or ''} {source_url or ''}".casefold()
        mouse = bool(re.search(r"\b(?:mouse|mice)\b|/mouse", text))
        keyboard = bool(re.search(r"\bkeyboard\b|/keyboard", text))
        if mouse and not keyboard:
            return "MOUSE", "OFFICIAL_TITLE_OR_URL"
        if keyboard and not mouse:
            return "KEYBOARD", "OFFICIAL_TITLE_OR_URL"
        if legacy_category in {"keyboard", "mouse"}:
            return ("KEYBOARD" if legacy_category == "keyboard" else "MOUSE"), "LEGACY_METADATA_FALLBACK"
        return None, "UNRESOLVED"

    def _official_hosts(self) -> dict[str, set[str]]:
        """Compatibility helper for callers; authoritative matching uses DB provenance."""
        hosts: dict[str, set[str]] = defaultdict(set)
        for brand in ALL_CANONICAL_BRANDS:
            urls = [brand.website, brand.shopify_url or "", *brand.catalog_urls, *brand.sitemap_urls, *brand.download_urls,
                    *(entry[0] for entry in brand.web_configurator_urls)]
            for url in urls:
                host = urlparse(url).netloc.casefold()
                if host:
                    hosts[brand.slug].add(host.removeprefix("www."))
        return hosts

    def build_truth_corpus(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """Build a per-brand official set before candidate matching.

        It trusts only official product/download/software sources or URLs whose
        host is explicitly configured on the canonical brand definition.
        """
        self.official = defaultdict(lambda: defaultdict(list))
        self.strong = defaultdict(lambda: defaultdict(set))
        domains = self._refresh_official_domains(conn)
        rows = conn.execute("""SELECT p.id product_id,p.canonical_name,p.raw_name,p.category,p.product_url,v.name vendor_slug,
            s.source_type,s.source_url,COUNT(d.id) identities
            FROM products p JOIN vendors v ON v.id=p.vendor_id LEFT JOIN device_identifiers d ON d.product_id=p.id
            LEFT JOIN sources s ON s.id=(SELECT sx.id FROM sources sx WHERE sx.vendor_id=p.vendor_id AND sx.source_url=p.product_url LIMIT 1)
            GROUP BY p.id""").fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            brand = self.brand_lookup.get((row["vendor_slug"] or "").casefold())
            if not brand:
                continue
            host = (urlparse(row["product_url"] or row["source_url"] or "").netloc.casefold().removeprefix("www."))
            domain_provenance = domains.get(brand[0], {}).get(host, set()) | domains.get(brand[0], {}).get(self._registrable_domain(host), set())
            is_official = row["source_type"] in {"vendor_product", "vendor_download", "vendor_software", "vendor_web", "web_configurator"} or bool(domain_provenance)
            category, category_source = self._tier1_category(row["raw_name"], row["product_url"] or row["source_url"], row["category"])
            if category is None:
                continue
            name, key = normalise_candidate(row["canonical_name"], brand[1])
            if not key:
                continue
            source = row["product_url"] or row["source_url"]
            category_conflict = row["category"] in {"keyboard", "mouse"} and category != row["category"].upper()
            if category_conflict and is_official and category_source == "OFFICIAL_TITLE_OR_URL":
                conn.execute("""INSERT INTO legacy_category_conflicts(product_id,legacy_category,authoritative_category,official_source_url,rationale)
                    VALUES(?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET legacy_category=excluded.legacy_category,
                    authoritative_category=excluded.authoritative_category,official_source_url=excluded.official_source_url,rationale=excluded.rationale""",
                    (row["product_id"], row["category"], category, source or "", "Official product title/URL overrides conflicting legacy category."))
            previous_whitelist = self._official_hosts().get(row["vendor_slug"], set())
            domain_corrected = is_official and host not in previous_whitelist and row["source_type"] not in {"vendor_product", "vendor_download", "vendor_software", "vendor_web", "web_configurator"}
            record = {"brand_id": brand[0], "brand": brand[1], "category": category, "name": name, "normalized_name": key,
                "source_url": source, "source_type": row["source_type"] or "legacy_product", "product_id": row["product_id"], "identities": row["identities"], "official": is_official,
                "official_domain_provenance": sorted(domain_provenance), "legacy_category": row["category"], "category_source": category_source,
                "category_data_conflict": category_conflict, "official_domain_classification_corrected": domain_corrected}
            if is_official:
                self.official[(brand[0], category)][key].append(record)
            else:
                group = urlparse(source or "").netloc.casefold() or f"product:{row['product_id']}"
                if row["identities"]:
                    self.strong[(brand[0], category)][key].add(group)
            output.append(record)
        return output

    def parse_and_stage(self) -> list[RawCandidate]:
        text = self.input_path.read_text(encoding="utf-8", errors="replace")
        with self.db.connection() as conn:
            self._load_brand_lookup(conn)
            raw = parse_ai_sections(text, set(self.brand_lookup))
            # Preserve the raw/staging history.  The input-line uniqueness key
            # makes this pass idempotent without deleting unresolved candidates.
            for item in raw:
                brand = self.brand_lookup.get(item.raw_brand.casefold())
                brand_id, canonical_brand = brand if brand else (None, item.raw_brand)
                name, key = normalise_candidate(item.raw_model_name, canonical_brand if brand else "")
                if not key:
                    continue
                conn.execute("""INSERT OR IGNORE INTO ai_model_candidates(source_ai,raw_brand,canonical_brand_id,brand_status,category,raw_model_name,normalized_model_name,input_path,line_number)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (item.source_ai, item.raw_brand, brand_id, "CANONICAL" if brand else "NEW_BRAND_CANDIDATE",
                    item.category, item.raw_model_name, key, str(self.input_path), item.line_number))
            rows = conn.execute("SELECT * FROM ai_model_candidates ORDER BY id").fetchall()
            grouped: dict[tuple[Any, ...], list[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                grouped[(row["canonical_brand_id"], row["raw_brand"] if row["canonical_brand_id"] is None else "", row["brand_status"], row["category"], row["normalized_model_name"])].append(row)
            for (_, raw_brand, brand_status, category, key), candidates in grouped.items():
                first = candidates[0]
                conn.execute("""INSERT OR IGNORE INTO ai_model_unions(canonical_brand_id,raw_brand,brand_status,category,canonical_candidate_name,normalized_model_name)
                    VALUES(?,?,?,?,?,?)""", (first["canonical_brand_id"], raw_brand or first["raw_brand"], brand_status, category, first["raw_model_name"], key))
                union_id = conn.execute("""SELECT id FROM ai_model_unions WHERE canonical_brand_id IS ? AND raw_brand=?
                    AND brand_status=? AND category=? AND normalized_model_name=?""", (first["canonical_brand_id"], raw_brand or first["raw_brand"], brand_status, category, key)).fetchone()["id"]
                counts = Counter(row["source_ai"] for row in candidates)
                for ai, count in counts.items():
                    conn.execute("""INSERT INTO ai_model_votes(ai_model_union_id,source_ai,candidate_count) VALUES(?,?,?)
                        ON CONFLICT(ai_model_union_id,source_ai) DO UPDATE SET candidate_count=excluded.candidate_count""", (union_id, ai, count))
            return raw

    @staticmethod
    def _artifact_status(name: str) -> tuple[str, str, str] | None:
        if RECEIVER_RE.search(name):
            return "RECEIVER", "RECEIVER", "Name denotes a receiver/dongle, not a peripheral model."
        if ARTIFACT_RE.search(name):
            return ("FIRMWARE_ARTIFACT" if re.search(r"firmware|flash|\.bin\b|\.hex\b", name, re.I) else "SOFTWARE_ARTIFACT", "ARTIFACT", "Name denotes software or firmware artifact.")
        if ACCESSORY_RE.search(name):
            return "ACCESSORY", "NOT_MODEL", "Name denotes an accessory/component rather than a keyboard or mouse."
        return None

    def _evidence(self, conn: sqlite3.Connection, union_id: int, tier: str, evidence_class: str, record: dict[str, Any], category: str) -> None:
        source_url = record.get("source_url")
        source_ref = str(record.get("product_id") or record.get("source_ref") or "")
        existing = conn.execute("""SELECT 1 FROM model_verification_evidence WHERE ai_model_union_id=? AND evidence_tier=?
            AND evidence_class=? AND source_url IS ? AND source_ref=?""", (union_id, tier, evidence_class, source_url, source_ref)).fetchone()
        if existing:
            return
        conn.execute("""INSERT OR IGNORE INTO model_verification_evidence(ai_model_union_id,evidence_tier,evidence_class,source_url,source_ref,category,details_json)
            VALUES(?,?,?,?,?,?,?)""", (union_id, tier, evidence_class, source_url, source_ref,
            category, json.dumps(record, ensure_ascii=False, sort_keys=True)))

    @staticmethod
    def _cosmetic_base(key: str) -> str:
        return compact_name(COSMETIC_RE.sub(" ", key))

    def verify(self) -> None:
        with self.db.connection() as conn:
            self._load_brand_lookup(conn)
            self.build_truth_corpus(conn)
            unions = conn.execute("SELECT * FROM ai_model_unions ORDER BY id").fetchall()
            for row in unions:
                union_id, brand_id, category, name, key = row["id"], row["canonical_brand_id"], row["category"], row["canonical_candidate_name"], row["normalized_model_name"]
                artifact = self._artifact_status(name)
                if artifact:
                    status, classification, reason = artifact
                elif brand_id is None:
                    status, classification, reason = "UNRESOLVED", "UNKNOWN", "Brand is not in the canonical inventory; no alias was inferred."
                else:
                    official = self.official[(brand_id, category)].get(key, [])
                    other_category = self.official[(brand_id, "MOUSE" if category == "KEYBOARD" else "KEYBOARD")].get(key, [])
                    strong_groups = self.strong[(brand_id, category)].get(key, set())
                    if official:
                        status, classification, reason = "VERIFIED_OFFICIAL", "COMMERCIAL_MODEL", "Exact normalized match in official product/support/software corpus."
                        for item in official:
                            self._evidence(conn, union_id, "TIER_1_OFFICIAL", "OFFICIAL_MODEL_CORPUS", item, category)
                    elif other_category:
                        status, classification, reason = "REJECTED_WRONG_CATEGORY", "NOT_MODEL", "Exact name exists in official corpus, but under the opposite category."
                        for item in other_category:
                            self._evidence(conn, union_id, "TIER_1_OFFICIAL", "OFFICIAL_CATEGORY_CONFLICT", item, "MOUSE" if category == "KEYBOARD" else "KEYBOARD")
                    elif len(strong_groups) >= 2:
                        status, classification, reason = "VERIFIED_STRONG", "COMMERCIAL_MODEL", "Exact name has VID/PID-backed records from two independent structured source groups."
                        for source_group in sorted(strong_groups):
                            self._evidence(conn, union_id, "TIER_2_STRONG", "INDEPENDENT_STRUCTURED_IMPLEMENTATION", {"source_ref": source_group}, category)
                    else:
                        cosmetic = self._cosmetic_base(name)
                        base_matches = [item for okey, items in self.official[(brand_id, category)].items() if self._cosmetic_base(items[0]["name"]) == cosmetic]
                        if COSMETIC_RE.search(name) and base_matches:
                            status, classification, reason = "COSMETIC_VARIANT", "COSMETIC_SKU", "Colour/edition token differs while the official base model matches."
                            for item in base_matches:
                                self._evidence(conn, union_id, "TIER_1_OFFICIAL", "OFFICIAL_BASE_MODEL", item, category)
                        else:
                            status, classification, reason = "UNRESOLVED", "UNKNOWN", "No Tier 1 official or two-source Tier 2 match found; absence is not rejection."
                conn.execute("""INSERT INTO model_reconciliation_results(ai_model_union_id,status,classification,reason)
                    VALUES(?,?,?,?) ON CONFLICT(ai_model_union_id) DO UPDATE SET status=excluded.status,
                    classification=excluded.classification,reason=excluded.reason,reviewed_at=CURRENT_TIMESTAMP""", (union_id, status, classification, reason))

    def _query_results(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute("""SELECT u.id,u.raw_brand,u.brand_status,u.category,u.canonical_candidate_name,u.normalized_model_name,
            b.canonical_name brand,r.status,r.classification,r.reason
            FROM ai_model_unions u LEFT JOIN brands b ON b.id=u.canonical_brand_id
            JOIN model_reconciliation_results r ON r.ai_model_union_id=u.id ORDER BY COALESCE(b.canonical_name,u.raw_brand),u.category,u.canonical_candidate_name""").fetchall()
        votes = defaultdict(dict)
        for row in conn.execute("SELECT ai_model_union_id,source_ai,candidate_count FROM ai_model_votes"):
            votes[row["ai_model_union_id"]][row["source_ai"].casefold()] = True
        evidence = defaultdict(list)
        for row in conn.execute("SELECT * FROM model_verification_evidence ORDER BY id"):
            item = dict(row); item["details"] = json.loads(item.pop("details_json") or "{}")
            evidence[row["ai_model_union_id"]].append(item)
        results = []
        for row in rows:
            item = dict(row)
            item["brand"] = item.pop("brand") or item["raw_brand"]
            vector = {ai.casefold(): bool(votes[row["id"]].get(ai.casefold())) for ai in AIS}
            item["ai_votes"] = vector
            item["ai_vote_count"] = sum(vector.values())
            item["evidence"] = evidence[row["id"]]
            if item["status"] != "REJECTED_WRONG_CATEGORY":
                item["evidence"] = [entry for entry in item["evidence"] if entry["evidence_class"] != "OFFICIAL_CATEGORY_CONFLICT"]
            results.append(item)
        return results

    def write_reports(self, raw: list[RawCandidate]) -> dict[str, Any]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        with self.db.connection() as conn:
            truth = self.build_truth_corpus(conn)
            results = self._query_results(conn)
            raw_payload = [item.__dict__ for item in raw]
            union = [{k: v for k, v in item.items() if k not in {"evidence", "reason", "status", "classification"}} for item in results]
            unresolved = [item for item in results if item["status"] == "UNRESOLVED"]
            rejected = [item for item in results if item["status"] not in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG", "UNRESOLVED", "COSMETIC_VARIANT"}]
            quality = self._quality(raw, results, truth)
            quality["verifier_repair"] = dict(self.repair_stats)
            configured = {brand.canonical_name: 0 for brand in ALL_CANONICAL_BRANDS}
            for item in truth:
                if item["official"]:
                    configured[item["brand"]] = configured.get(item["brand"], 0) + 1
            truth_coverage = [{"brand": brand, "official_records": count, "status": "FOUND" if count else "NO_CACHED_OFFICIAL_RECORD"}
                              for brand, count in sorted(configured.items(), key=lambda pair: pair[0].casefold())]
        self._write("ai_model_candidates_raw.json", {"input": str(self.input_path), "candidates": raw_payload})
        self._write("ai_model_union.json", {"union": union})
        self._write("official_model_truth_corpus.json", {"scope": "Cached official product/support/software URLs plus provenance-backed official domains; no AI vote is evidence.",
            "official_domain_classification_corrected": sum(bool(item["official_domain_classification_corrected"]) for item in truth),
            "legacy_category_conflicts": sum(bool(item["category_data_conflict"]) and item["official"] and item["category_source"] == "OFFICIAL_TITLE_OR_URL" for item in truth),
            "coverage": truth_coverage, "records": truth})
        self._write("model_verification_results.json", {"results": results})
        self._write("model_unresolved_candidates.json", {"candidates": unresolved})
        self._write("model_rejected_candidates.json", {"candidates": rejected})
        self._write("ai_source_quality.json", quality)
        self._write_verified_txt(results)
        return {"raw_candidates": len(raw), "union": len(results), "verified": sum(r["status"].startswith("VERIFIED") for r in results), "unresolved": len(unresolved), "rejected": len(rejected)}

    @staticmethod
    def _quality(raw: list[RawCandidate], results: list[dict[str, Any]], truth: list[dict[str, Any]]) -> dict[str, Any]:
        raw_by_ai: dict[str, list[RawCandidate]] = defaultdict(list)
        for item in raw: raw_by_ai[item.source_ai].append(item)
        verified_results = [item for item in results if item["status"] in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG"}]
        verified_ids = {item["id"] for item in verified_results}
        per_ai = {}
        for ai in AIS:
            entries = raw_by_ai[ai]
            proposed = [item for item in results if item["ai_votes"].get(ai.casefold())]
            keys = {item["id"] for item in proposed}
            statuses = [item["status"] for item in proposed]
            verified = sum(status in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG"} for status in statuses)
            rejected = sum(status not in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG", "UNRESOLVED", "COSMETIC_VARIANT"} for status in statuses)
            unique_count = len(keys)
            adjudicated = verified + rejected
            per_ai[ai.lower()] = {"candidates_total": len(entries), "unique_candidates": len(keys), "verified": verified,
                "unresolved": statuses.count("UNRESOLVED"), "rejected": rejected,
                "wrong_category": statuses.count("REJECTED_WRONG_CATEGORY"), "duplicate_rate": 0 if not entries else 1-len(keys)/len(entries),
                "precision_among_adjudicated": verified / adjudicated if adjudicated else None,
                "verification_coverage": adjudicated / unique_count if unique_count else None,
                "verified_rate": verified / unique_count if unique_count else None,
                "unresolved_rate": statuses.count("UNRESOLVED") / unique_count if unique_count else None,
                "estimated_recall_against_union_verified": verified / len(verified_ids) if verified_ids else None,
                "unique_verified_contribution": sum(1 for item in verified_results if item["ai_votes"].get(ai.casefold()) and sum(item["ai_votes"].values()) == 1)}
        official_keys = {(x["brand"].casefold(), x["category"], x["normalized_name"]) for x in truth if x["official"]}
        verified_keys = {(x["brand"].casefold(), x["category"], x["normalized_model_name"]) for x in verified_results}
        return {"sources": per_ai, "union_verified": len(verified_ids), "official_truth_records": len(official_keys),
            "major_official_omissions_sample": [{"brand": b, "category": c, "normalized_name": n} for b,c,n in sorted(official_keys-verified_keys)[:250]]}

    def _write_verified_txt(self, results: list[dict[str, Any]]) -> None:
        grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"KEYBOARD": [], "MOUSE": []})
        for item in results:
            if item["status"] not in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG"}:
                continue
            grouped[item["brand"]][item["category"]].append(item["canonical_candidate_name"])
        lines: list[str] = []
        for brand in sorted(grouped, key=str.casefold):
            lines.append(brand)
            for category, label in (("KEYBOARD", "Keyboards"), ("MOUSE", "Mice")):
                lines.append(f"  {label}")
                bases: dict[str, list[str]] = defaultdict(list)
                for name in sorted(set(grouped[brand][category]), key=str.casefold):
                    parts = split_model_variant(name)
                    bases[parts.model_name].append(parts.variant_name)
                for base in sorted(bases, key=str.casefold):
                    lines.append(f"    - {base}")
                    for variant in sorted(set(bases[base]), key=str.casefold):
                        if variant.casefold() != base.casefold():
                            lines.append(f"      - {variant}")
        (self.report_dir / "verified_models.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write(self, name: str, payload: Any) -> None:
        (self.report_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def promote_verified(self) -> int:
        """Add only Tier 1/2 reconciled candidates to the graph, preserving provenance.

        This is deliberately explicit because staging output remains more useful
        than a premature graph mutation while official coverage is incomplete.
        """
        inventory = ModelInventoryPass(self.db.db_path)
        promoted = 0
        with self.db.connection() as conn:
            rows = conn.execute("""SELECT u.id,u.canonical_brand_id,b.canonical_name brand,u.category,u.canonical_candidate_name,r.status
                FROM ai_model_unions u JOIN model_reconciliation_results r ON r.ai_model_union_id=u.id
                JOIN brands b ON b.id=u.canonical_brand_id WHERE r.status IN ('VERIFIED_OFFICIAL','VERIFIED_STRONG')""").fetchall()
            for row in rows:
                evidence = conn.execute("SELECT * FROM model_verification_evidence WHERE ai_model_union_id=? ORDER BY evidence_tier,id LIMIT 1", (row["id"],)).fetchone()
                details = json.loads(evidence["details_json"] or "{}") if evidence else {}
                canonical_name = details.get("name") or row["canonical_candidate_name"]
                evidence_class = "OFFICIAL_PRODUCT_PAGE" if row["status"] == "VERIFIED_OFFICIAL" else "STATIC_IMPLEMENTATION"
                variant_id = inventory._upsert_variant(conn, brand_id=row["canonical_brand_id"], brand_name=row["brand"], raw_name=canonical_name,
                    category=row["category"].lower(), source_product_id=details.get("product_id"), evidence_class=evidence_class,
                    source_url=(evidence["source_url"] if evidence else None), source_path="ai_model_reconciliation",
                    extraction_method="ai_candidate_verified_promotion", confidence=.95 if row["status"] == "VERIFIED_OFFICIAL" else .80,
                    details={"reconciliation_union_id": row["id"], "verification_status": row["status"]})
                # Some genuine models (for example named product lines without a
                # number) intentionally fail the broad discovery heuristic.  A
                # Tier 1/2 result is stronger than that heuristic, so promote it
                # directly while retaining the exact same provenance.
                if variant_id is None:
                    parts = split_model_variant(canonical_name, row["brand"])
                    conn.execute("""INSERT OR IGNORE INTO commercial_models
                        (brand_id,canonical_name,normalized_name,category,lifecycle_status,candidate_only)
                        VALUES(?,?,?,?, 'CURRENT',0)""", (row["canonical_brand_id"], parts.model_name, parts.model_key, row["category"].lower()))
                    model = conn.execute("SELECT id FROM commercial_models WHERE brand_id=? AND normalized_name=?", (row["canonical_brand_id"], parts.model_key)).fetchone()
                    conn.execute("""INSERT OR IGNORE INTO model_variants
                        (commercial_model_id,canonical_name,normalized_name,variant_label,source_product_id)
                        VALUES(?,?,?,?,?)""", (model["id"], parts.variant_name, parts.variant_key, parts.variant_label, details.get("product_id")))
                    variant_id = conn.execute("SELECT id FROM model_variants WHERE commercial_model_id=? AND normalized_name=?", (model["id"], parts.variant_key)).fetchone()["id"]
                    inventory._upsert_evidence(conn, variant_id, evidence_class, source_url=(evidence["source_url"] if evidence else None),
                        source_path="ai_model_reconciliation", extraction_method="ai_candidate_verified_promotion",
                        confidence=.95 if row["status"] == "VERIFIED_OFFICIAL" else .80,
                        details={"reconciliation_union_id": row["id"], "verification_status": row["status"]})
                if variant_id:
                    conn.execute("UPDATE model_reconciliation_results SET promoted_model_variant_id=? WHERE ai_model_union_id=?", (variant_id, row["id"]))
                    promoted += 1
        return promoted

    def run(self, *, promote: bool = False) -> dict[str, Any]:
        with self.db.connection() as conn:
            before = {row["ai_model_union_id"]: row["status"] for row in conn.execute("SELECT ai_model_union_id,status FROM model_reconciliation_results")}
        raw = self.parse_and_stage()
        self.verify()
        with self.db.connection() as conn:
            after = {row["ai_model_union_id"]: row["status"] for row in conn.execute("SELECT ai_model_union_id,status FROM model_reconciliation_results")}
        self.repair_stats["previous_unresolved_to_verified"] = sum(before.get(key) == "UNRESOLVED" and value in {"VERIFIED_OFFICIAL", "VERIFIED_STRONG"} for key, value in after.items())
        self.repair_stats["previous_wrong_category_restored"] = sum(before.get(key) == "REJECTED_WRONG_CATEGORY" and value != "REJECTED_WRONG_CATEGORY" for key, value in after.items())
        for key in ("previous_unresolved_to_verified", "previous_wrong_category_restored"):
            self.repair_stats[key] = max(self.repair_stats[key], self.previous_repair_stats.get(key, 0))
        result = self.write_reports(raw)
        result["promoted"] = self.promote_verified() if promote else 0
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile AI model candidates against an official truth corpus.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--reports", type=Path, default=REPORTS)
    parser.add_argument("--promote", action="store_true", help="Add only VERIFIED_OFFICIAL/VERIFIED_STRONG candidates to the model graph.")
    args = parser.parse_args(argv)
    result = AIModelReconciliation(args.db, args.input, args.reports).run(promote=args.promote)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
