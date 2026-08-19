"""Generic brand collector executing declarative adapters with strict status reporting."""

from typing import Optional

from ingest.adapters.shopify import ShopifyCatalogAdapter
from ingest.adapters.download_center import DownloadCenterAdapter
from ingest.adapters.web_configurator import WebConfiguratorAdapter
from ingest.adapters.static_html import StaticHtmlCatalogAdapter
from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.collectors.base import BaseCollector
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import SourceType
from ingest.scanners import should_skip_scan

logger = get_logger()
import hashlib


class GenericBrandCollector(BaseCollector):
    def __init__(self, brand_def: BrandDef, *args, **kwargs):
        self.brand_def = brand_def
        super().__init__(*args, **kwargs)
        self.shopify_adapter = ShopifyCatalogAdapter(self.fetcher)
        self.download_adapter = DownloadCenterAdapter(self.fetcher)
        self.web_configurator_adapter = WebConfiguratorAdapter(self.fetcher)
        self.static_html_adapter = StaticHtmlCatalogAdapter(self.fetcher)

    @property
    def vendor_name(self) -> str:
        return self.brand_def.slug

    @property
    def display_name(self) -> str:
        return self.brand_def.canonical_name

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting discovery run (Batch: {self.brand_def.batch})...")
        vendor_id = self.get_vendor_id()

        products_discovered = 0
        final_status = DiscoveryStatus.NOT_IMPLEMENTED
        blocking_reason = None

        # 1. Try Shopify Adapter if configured or website present
        shopify_res = None
        if self.brand_def.shopify_url or self.brand_def.website:
            shopify_res = self.shopify_adapter.discover(self.brand_def)
            if shopify_res.products:
                src_url = self.brand_def.shopify_url or f"{self.brand_def.website.rstrip('/')}/products.json"
                src_id = self.record_source(src_url, SourceType.VENDOR_WEB, f"Found {len(shopify_res.products)} products")
                for p in shopify_res.products:
                    self.process_product(p, source_id=src_id, no_download=(no_download or metadata_only))
                    products_discovered += 1
                final_status = DiscoveryStatus.SUPPORTED_FULL if any(p.driver_url for p in shopify_res.products) else DiscoveryStatus.METADATA_ONLY
            elif shopify_res.status in [DiscoveryStatus.BLOCKED_WAF, DiscoveryStatus.BLOCKED_REGION]:
                final_status = shopify_res.status
                blocking_reason = shopify_res.blocking_reason

        # 2. Try Static HTML / JSON-LD if shopify did not find products and no WAF block
        if products_discovered == 0 and final_status not in [DiscoveryStatus.BLOCKED_WAF, DiscoveryStatus.BLOCKED_REGION]:
            html_res = self.static_html_adapter.discover(self.brand_def)
            if html_res.products:
                src_url = self.brand_def.website or "static_html"
                src_id = self.record_source(src_url, SourceType.VENDOR_PRODUCT, f"Found {len(html_res.products)} products")
                for p in html_res.products:
                    self.process_product(p, source_id=src_id, no_download=(no_download or metadata_only))
                    products_discovered += 1
                final_status = DiscoveryStatus.METADATA_ONLY
            elif html_res.status in [DiscoveryStatus.BLOCKED_WAF, DiscoveryStatus.BLOCKED_REGION]:
                final_status = html_res.status
                blocking_reason = html_res.blocking_reason
            elif final_status == DiscoveryStatus.NOT_IMPLEMENTED:
                final_status = html_res.status
                blocking_reason = html_res.blocking_reason

        # 3. Try Download Center / Software Pages (unless metadata_only or no_download)
        dl_res = self.download_adapter.discover(self.brand_def) if not (metadata_only or no_download) else None
        if dl_res and (dl_res.products or dl_res.artifacts):
            for art in dl_res.artifacts:
                try:
                    dl_result = self.downloader.process_artifact_url(
                        url=art.original_url,
                        vendor=self.vendor_name,
                        vendor_id=vendor_id,
                        software_version=art.software_version,
                        run_id=self.run_id,
                        custom_filename=art.filename
                    )
                    if dl_result and dl_result.is_new:
                        self.stats["new_artifacts"] += 1
                    if dl_result:
                        extract_res = self.extractor.extract(dl_result.cas_path, dl_result.sha256)
                        self.db.update_artifact_extraction_status(dl_result.sha256, extract_res.status)
                        scan_res = self.scanners.scan_file(dl_result.cas_path, artifact_sha256=dl_result.sha256)
                        src_id = self.record_source(art.original_url, SourceType.VENDOR_SOFTWARE, f"Artifact {art.filename}")
                        self.correlate_and_record_bundle_scan(scan_res, dl_result.sha256, source_id=src_id)
                        if extract_res.status == "success" and extract_res.extracted_path:
                            scanned_hashes = set()
                            for file_path in extract_res.extracted_path.rglob("*"):
                                if file_path.is_file():
                                    if should_skip_scan(file_path):
                                        continue
                                    try:
                                        if file_path.stat().st_size > 50 * 1024 * 1024:
                                            continue
                                        f_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                                        if f_hash in scanned_hashes:
                                            continue
                                        scanned_hashes.add(f_hash)
                                    except Exception:
                                        continue
                                    sub_scan = self.scanners.scan_file(file_path, artifact_sha256=dl_result.sha256)
                                    self.correlate_and_record_bundle_scan(sub_scan, dl_result.sha256, source_id=src_id)
                except Exception as e:
                    logger.debug(f"[{self.display_name}] Download error for {art.filename}: {e}")

            if dl_res.products:
                src_url = self.brand_def.download_urls[0] if self.brand_def.download_urls else "download_center"
                src_id = self.record_source(src_url, SourceType.VENDOR_SOFTWARE, f"Found {len(dl_res.products)} driver entries")
                for p in dl_res.products:
                    self.process_product(p, source_id=src_id, no_download=(no_download or metadata_only))
                    products_discovered += 1

            if final_status == DiscoveryStatus.METADATA_ONLY or final_status == DiscoveryStatus.SUPPORTED_PARTIAL:
                final_status = DiscoveryStatus.SUPPORTED_FULL
            elif final_status in [DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, DiscoveryStatus.NOT_IMPLEMENTED]:
                final_status = DiscoveryStatus.SOFTWARE_ONLY

        # 4. Try Web Configurators & JS Bundles (unless metadata_only or no_download)
        cfg_res = self.web_configurator_adapter.discover(self.brand_def) if not (metadata_only or no_download) else None
        if cfg_res and cfg_res.artifacts:
            for art in cfg_res.artifacts:
                try:
                    dl_result = self.downloader.process_artifact_url(
                        url=art.original_url,
                        vendor=self.vendor_name,
                        vendor_id=vendor_id,
                        software_version=art.software_version,
                        run_id=self.run_id,
                        custom_filename=art.filename
                    )
                    if dl_result and dl_result.is_new:
                        self.stats["new_artifacts"] += 1
                    if dl_result:
                        scan_res = self.scanners.scan_file(dl_result.cas_path, artifact_sha256=dl_result.sha256)
                        self.db.update_artifact_extraction_status(dl_result.sha256, "scanned")
                        src_id = self.record_source(art.original_url, SourceType.VENDOR_WEB, f"Web Configurator {art.filename}")
                        self.correlate_and_record_bundle_scan(scan_res, dl_result.sha256, source_id=src_id)
                except Exception as e:
                    logger.debug(f"[{self.display_name}] Web Configurator error for {art.filename}: {e}")

            if final_status in [DiscoveryStatus.METADATA_ONLY, DiscoveryStatus.SUPPORTED_PARTIAL]:
                final_status = DiscoveryStatus.SUPPORTED_FULL
            elif final_status in [DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, DiscoveryStatus.NOT_IMPLEMENTED]:
                final_status = DiscoveryStatus.SOFTWARE_ONLY

        # 5. Final fallback status check
        if products_discovered == 0 and not (dl_res and dl_res.artifacts) and not (cfg_res and cfg_res.artifacts):
            if final_status not in [DiscoveryStatus.BLOCKED_WAF, DiscoveryStatus.BLOCKED_REGION]:
                final_status = DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND
                blocking_reason = "No products or software packages discovered at official endpoints"

        # Record brand crawl status in DB
        self.record_crawl_status(final_status, blocking_reason=blocking_reason)

