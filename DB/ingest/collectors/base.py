"""Base collector interface and common pipeline logic."""

import abc
import hashlib
from typing import Optional, NamedTuple
from pathlib import Path

from ingest.artifacts.cache import ArtifactCache
from ingest.artifacts.downloader import ArtifactDownloader
from ingest.artifacts.extractor import SafeExtractor
from ingest.logging_setup import (
    get_logger, log_discovery, log_fact, log_hint, log_dedupe, log_change
)
from ingest.network.fetcher import TieredFetcher
from ingest.normalize.evidence import (
    RawProduct, RawSource, SourceType, DeviceIdentifierFact, ProtocolHintFact, GenericFact, EvidenceLevel
)
from ingest.brands.canonical import DiscoveryStatus
from ingest.normalize.models import normalize_product_name, detect_category
from ingest.scanners import ScannerDispatcher
from ingest.storage.database import RegistryDatabase

logger = get_logger()



class CollectorStats(NamedTuple):
    products_scanned: int = 0
    new_products: int = 0
    new_artifacts: int = 0
    new_vid_pids: int = 0
    new_hints: int = 0
    collector_errors: int = 0
    parse_failures: int = 0
    errors: int = 0


class BaseCollector(abc.ABC):
    def __init__(
        self,
        fetcher: TieredFetcher,
        cache: ArtifactCache,
        extractor: SafeExtractor,
        scanners: ScannerDispatcher,
        db: RegistryDatabase,
        run_id: str,
        downloader: Optional[ArtifactDownloader] = None
    ):
        self.fetcher = fetcher
        self.cache = cache
        self.extractor = extractor
        self.scanners = scanners
        self.db = db
        self.run_id = run_id
        self.downloader = downloader or ArtifactDownloader(fetcher=fetcher, cache=cache, db=db)
        self.stats = {
            "products_scanned": 0,
            "new_products": 0,
            "new_artifacts": 0,
            "new_vid_pids": 0,
            "new_hints": 0,
            "collector_errors": 0,
            "parse_failures": 0,
            "errors": 0
        }

    @property
    @abc.abstractmethod
    def vendor_name(self) -> str:
        """Vendor identifier (e.g. 'aula')."""
        pass

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Vendor human name (e.g. 'AULA')."""
        pass

    @abc.abstractmethod
    def collect(self, metadata_only: bool = False, no_download: bool = False):
        """Execute vendor collection workflow."""
        pass

    def get_vendor_id(self) -> int:
        return self.db.get_or_create_vendor(self.vendor_name, self.display_name)

    def record_source(self, url: str, source_type: SourceType, html_content: str, http_status: int = 200) -> int:
        """Record web source for strict provenance tracking."""
        content_hash = hashlib.sha256(html_content.encode("utf-8")).hexdigest()
        raw_source = RawSource(
            url=url,
            source_type=source_type,
            vendor=self.vendor_name,
            http_status=http_status,
            content_hash=content_hash
        )
        return self.db.record_source(raw_source)

    def process_product(self, raw_prod: RawProduct, source_id: Optional[int] = None, no_download: bool = False) -> Optional[int]:
        """Normalize, record in SQLite staging DB, download artifacts and execute static scanners."""
        try:
            from ingest.normalize.models import generate_identity_key, evaluate_category, is_software_filename, RE_NON_PERIPHERAL

            # 1. Strictly reject software filenames and non-peripheral items from becoming Product rows
            if is_software_filename(raw_prod.raw_name) or is_software_filename(raw_prod.canonical_name or ""):
                logger.debug(f"[{self.display_name}] Skipping software filename as product: '{raw_prod.raw_name}'")
                if raw_prod.driver_url and not no_download:
                    vendor_id = self.get_vendor_id()
                    self._process_product_artifact(0, raw_prod, vendor_id, source_id)
                return None

            if RE_NON_PERIPHERAL.search(raw_prod.raw_name):
                logger.debug(f"[{self.display_name}] Skipping non-peripheral item: '{raw_prod.raw_name}'")
                return None

            self.stats["products_scanned"] += 1
            
            # Canonical normalization & Identity key
            canonical_name = raw_prod.canonical_name or normalize_product_name(self.display_name, raw_prod.raw_name)
            identity_key = generate_identity_key(self.display_name, canonical_name or raw_prod.raw_name)
            tags_list = raw_prod.extra_metadata.get("tags", []) if raw_prod.extra_metadata else []
            product_type = raw_prod.extra_metadata.get("product_type") if raw_prod.extra_metadata else None

            if raw_prod.category and not raw_prod.extra_metadata.get("handle"):
                category = raw_prod.category
                category_conf = 1.0
                metadata_conf = 1.0
            else:
                cat_eval = evaluate_category(
                    name=raw_prod.raw_name,
                    product_url=raw_prod.product_url or "",
                    tags=tags_list,
                    product_type=product_type
                )
                category = raw_prod.category or cat_eval.category
                category_conf = 1.0 if raw_prod.category else cat_eval.confidence

                # Assign metadata confidence based on source role
                if raw_prod.extra_metadata.get("metadata_confidence"):
                    metadata_conf = float(raw_prod.extra_metadata["metadata_confidence"])
                elif raw_prod.extra_metadata.get("source_retailer"):
                    metadata_conf = 0.60
                elif "/blogs/software" in (raw_prod.product_url or "") or "/download" in (raw_prod.product_url or ""):
                    metadata_conf = 0.40
                else:
                    metadata_conf = 0.85

            log_discovery(self.display_name, canonical_name, category, raw_prod.product_url or "N/A")

            vendor_id = self.get_vendor_id()
            p_id, is_new_product = self.db.upsert_product(
                vendor_id=vendor_id,
                raw_name=raw_prod.raw_name,
                canonical_name=canonical_name,
                category=category,
                identity_key=identity_key,
                product_url=raw_prod.product_url,
                image_url=raw_prod.image_url,
                category_confidence=category_conf,
                metadata_confidence=metadata_conf,
                source_id=source_id,
                evidence_level=2 if raw_prod.category else 1,
                run_id=self.run_id
            )

            if is_new_product:
                self.stats["new_products"] += 1

            # Save basic metadata facts (Level 1)
            for k, v in raw_prod.extra_metadata.items():
                if v:
                    self.db.upsert_generic_fact(
                        GenericFact(
                            product_id=p_id,
                            key=k,
                            value=str(v),
                            source_id=source_id,
                            evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                            confidence=1.0
                        ),
                        run_id=self.run_id
                    )

            if raw_prod.driver_url:
                self.db.upsert_generic_fact(
                    GenericFact(
                        product_id=p_id,
                        key="driver_download_url",
                        value=raw_prod.driver_url,
                        source_id=source_id,
                        evidence_level=EvidenceLevel.LEVEL_1_METADATA,
                        confidence=1.0
                    ),
                    run_id=self.run_id
                )

            # Process artifact download & static analysis
            if raw_prod.driver_url and not no_download:
                self._process_product_artifact(p_id, raw_prod, vendor_id, source_id)

            return p_id

        except Exception as e:
            self.stats["collector_errors"] += 1
            self.stats["errors"] += 1
            logger.error(f"[{self.display_name}] Error processing product '{raw_prod.raw_name}': {e}", exc_info=True)
            return None

    def _process_product_artifact(self, product_id: int, raw_prod: RawProduct, vendor_id: int, source_id: Optional[int]):
        """Download artifact, unpack safely, and dispatch static file scanners."""
        url = raw_prod.driver_url
        if not url:
            return

        dl_res = self.downloader.process_artifact_url(
            url=url,
            vendor=self.vendor_name,
            vendor_id=vendor_id,
            software_version=raw_prod.software_version,
            product_id=product_id if product_id > 0 else None,
            run_id=self.run_id
        )

        if not dl_res:
            return

        if dl_res.is_new:
            self.stats["new_artifacts"] += 1

        sha256 = dl_res.sha256
        cas_file_path = dl_res.cas_path

        # 1. First scan the artifact container itself (e.g. PE string search)
        direct_scan = self.scanners.scan_file(cas_file_path, artifact_sha256=sha256, product_id=product_id if product_id > 0 else None)
        self._record_scan_results(product_id, direct_scan, source_id, sha256)

        # 2. Attempt safe extraction
        extract_res = self.extractor.extract(cas_file_path, sha256)
        self.db.update_artifact_extraction_status(sha256, extract_res.status)

        # 3. If extracted files are present, recursively scan interesting static files
        if extract_res.status == "success" and extract_res.extracted_path:
            for file_path in extract_res.extracted_path.rglob("*"):
                if file_path.is_file():
                    scan_res = self.scanners.scan_file(file_path, artifact_sha256=sha256, product_id=product_id if product_id > 0 else None)
                    self._record_scan_results(product_id, scan_res, source_id, sha256)

    def _record_scan_results(self, product_id: int, scan_res, source_id: Optional[int], sha256: str):
        # If scan result contains structured device records from manifests/configs, correlate specifically
        if scan_res.device_records:
            self.correlate_and_record_bundle_scan(scan_res, sha256, source_id=source_id)
            return

        if product_id <= 0:
            return

        # Guard against monolithic bundles dumping 10+ uncorrelated VID/PIDs to a single model
        idents = scan_res.identifiers
        if len(idents) > 6:
            logger.debug(f"[{self.display_name}] Skipping broadcast of {len(idents)} VID/PIDs to product #{product_id}")
            return

        # Save device identifiers (VID/PID)
        for ident in idents:
            ident.product_id = product_id
            ident.source_id = source_id
            ident.artifact_sha256 = sha256
            is_new = self.db.upsert_device_identifier(ident, run_id=self.run_id)
            if is_new:
                self.stats["new_vid_pids"] += 1
                log_fact(f"Product #{product_id}", "VID/PID", f"{ident.vid_hex}:{ident.pid_hex}", f"Artifact {sha256[:8]}")

        # Save protocol hints
        for hint in scan_res.hints:
            hint.product_id = product_id
            hint.source_id = source_id
            hint.artifact_sha256 = sha256
            is_new = self.db.upsert_protocol_hint(hint, run_id=self.run_id)
            if is_new:
                self.stats["new_hints"] += 1
                log_hint(f"Product #{product_id}", hint.hint_key, hint.hint_value, f"Artifact {sha256[:8]}")

        # Save facts
        for fact in scan_res.facts:
            fact.product_id = product_id
            fact.source_id = source_id
            fact.artifact_sha256 = sha256
            self.db.upsert_generic_fact(fact, run_id=self.run_id)

    def correlate_and_record_bundle_scan(self, scan_res, artifact_sha256: str, source_id: Optional[int] = None):
        """
        Conservatively correlate discovered device records from an artifact bundle
        with products belonging to this vendor in the database.
        """
        vendor_id = self.get_vendor_id()
        with self.db.connection() as conn:
            products = conn.execute(
                "SELECT id, canonical_name, raw_name, identity_key FROM products WHERE vendor_id = ?",
                (vendor_id,)
            ).fetchall()

        for rec in scan_res.device_records:
            rec_model = (rec.model or rec.name).strip()
            if not rec_model:
                continue

            from ingest.normalize.models import generate_identity_key
            rec_key = generate_identity_key(self.display_name, rec_model)
            if len(rec_key) < 2:
                continue

            # Find conservative matching product for this vendor
            matched_p = None
            for p in products:
                p_key = p["identity_key"]
                p_can = p["canonical_name"]
                
                # 1. Exact identity key match
                if p_key == rec_key:
                    matched_p = p
                    break
                
                # 2. Strict exact token / name equality (prevent 2-letter substring collision like 'gk' matching 'gk61')
                if len(p_key) >= 4 and len(rec_key) >= 4:
                    can_clean = p_can.lower().replace(" ", "").replace("-", "").replace("_", "")
                    rec_clean = rec_model.lower().replace(" ", "").replace("-", "").replace("_", "")
                    if can_clean == rec_clean or p_key == rec_clean or rec_key == can_clean:
                        matched_p = p
                        break

            if matched_p:
                p_id = matched_p["id"]
                # Link product to artifact
                self.db.link_product_artifact(p_id, artifact_sha256, relation_type="configurator_bundle")

                # Record device identifier
                ident_fact = DeviceIdentifierFact(
                    product_id=p_id,
                    vid=rec.vid,
                    pid=rec.pid,
                    vid_hex=rec.vid_hex,
                    pid_hex=rec.pid_hex,
                    product_string=rec.name or matched_p["canonical_name"],
                    usage_page=rec.usage_page,
                    usage=rec.usage,
                    source_id=source_id,
                    artifact_sha256=artifact_sha256,
                    evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                    confidence=0.95
                )
                if self.db.upsert_device_identifier(ident_fact, run_id=self.run_id):
                    self.stats["new_vid_pids"] += 1
                    log_fact(f"Product #{p_id} ({matched_p['canonical_name']})", "VID/PID", f"{rec.vid_hex}:{rec.pid_hex}", f"Real Bundle {artifact_sha256[:8]}")

                # Record scoped protocol hints for this device
                for hk, hv in rec.hints.items():
                    hint_fact = ProtocolHintFact(
                        product_id=p_id,
                        hint_key=hk,
                        hint_value=str(hv),
                        source_id=source_id,
                        artifact_sha256=artifact_sha256,
                        evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                        confidence=0.90
                    )
                    if self.db.upsert_protocol_hint(hint_fact, run_id=self.run_id):
                        self.stats["new_hints"] += 1
                        log_hint(f"Product #{p_id} ({matched_p['canonical_name']})", hk, str(hv), f"Real Bundle {artifact_sha256[:8]}")

    def record_crawl_status(
        self,
        final_status: DiscoveryStatus,
        blocking_reason: Optional[str] = None
    ):
        """Record brand crawl status metrics accurately in database."""
        vendor_id = self.get_vendor_id()
        with self.db.connection() as conn:
            p_cnt = conn.execute("SELECT COUNT(*) FROM products WHERE vendor_id = ?", (vendor_id,)).fetchone()[0]
            d_cnt = conn.execute("SELECT COUNT(*) FROM products WHERE vendor_id = ? AND category IN ('keyboard', 'mouse', 'headset', 'microphone')", (vendor_id,)).fetchone()[0]
            art_cnt = conn.execute("SELECT COUNT(*) FROM artifacts WHERE vendor_id = ?", (vendor_id,)).fetchone()[0]
            art_bytes = conn.execute("SELECT COALESCE(SUM(size), 0) FROM artifacts WHERE vendor_id = ?", (vendor_id,)).fetchone()[0]
            vid_cnt = conn.execute("SELECT COUNT(DISTINCT vid || ':' || pid) FROM device_identifiers d JOIN products p ON d.product_id = p.id WHERE p.vendor_id = ?", (vendor_id,)).fetchone()[0]
            hint_cnt = conn.execute("SELECT COUNT(*) FROM protocol_hints h JOIN products p ON h.product_id = p.id WHERE p.vendor_id = ?", (vendor_id,)).fetchone()[0]
            tech_prods = conn.execute("SELECT COUNT(DISTINCT p.id) FROM products p WHERE p.vendor_id = ? AND (EXISTS(SELECT 1 FROM device_identifiers WHERE product_id = p.id) OR EXISTS(SELECT 1 FROM protocol_hints WHERE product_id = p.id))", (vendor_id,)).fetchone()[0]

        # If status is SUPPORTED_FULL but 0 artifacts were discovered/retrieved, downgrade to METADATA_ONLY or NO_SOFTWARE_FOUND
        if final_status == DiscoveryStatus.SUPPORTED_FULL and art_cnt == 0:
            final_status = DiscoveryStatus.METADATA_ONLY if p_cnt > 0 else DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND

        self.db.record_brand_crawl_status(
            brand_id=vendor_id,
            run_id=self.run_id,
            status=final_status.value,
            products_count=p_cnt,
            devices_count=d_cnt,
            artifacts_count=art_cnt,
            artifacts_bytes=art_bytes,
            vid_pids_count=vid_cnt,
            hints_count=hint_cnt,
            tech_evidence_products=tech_prods,
            blocking_reason=blocking_reason
        )
        logger.info(f"[{self.display_name}] Crawl finished: Status={final_status.value} | Products={p_cnt} | Devices={d_cnt} | Artifacts={art_cnt} ({art_bytes/1024/1024:.2f} MB) | VID/PIDs={vid_cnt}")

