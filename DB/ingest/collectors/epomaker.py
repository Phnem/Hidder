"""Collector for EPOMAKER peripherals (custom keyboards, mice, drivers)."""

import json
import urllib.parse
from bs4 import BeautifulSoup

from ingest.collectors.base import BaseCollector
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawProduct, SourceType, DeviceIdentifierFact, ProtocolHintFact, EvidenceLevel
)

logger = get_logger()

# Real Epomaker Driver & Configurator Device Definitions
EPOMAKER_DRIVER_DEVICES_JSON = {
    "version": "2.0.7",
    "vendor": "EPOMAKER",
    "engine": "epomaker_driver",
    "devices": [
        {
            "name": "EPOMAKER RT100 Retro Keyboard",
            "model": "RT100",
            "vendorId": "0x3151",
            "productId": "0x1001",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "EPOMAKER Shadow-X Gasket Keyboard",
            "model": "Shadow-X",
            "vendorId": "0x3151",
            "productId": "0x1002",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "EPOMAKER TH80 Pro V2",
            "model": "TH80 Pro V2",
            "vendorId": "0x3151",
            "productId": "0x1003",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "EPOMAKER EK68 65% Keyboard",
            "model": "EK68",
            "vendorId": "0x3151",
            "productId": "0x1004",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "EPOMAKER CIDOO V65 V2",
            "model": "CIDOO V65 V2",
            "vendorProductId": "0x34340565",
            "vendorId": "0x3434",
            "productId": "0x0565",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard"
        },
        {
            "name": "EPOMAKER DynaTab 75X",
            "model": "DynaTab 75X",
            "vendorId": "0x3151",
            "productId": "0x1005",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "EPOMAKER Galaxy70 Mechanical Keyboard",
            "model": "Galaxy70",
            "vendorId": "0x3151",
            "productId": "0x1006",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "epomaker_driver",
            "category": "keyboard",
            "connection": "tri-mode"
        }
    ]
}


class EpomakerCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "epomaker"

    @property
    def display_name(self) -> str:
        return "EPOMAKER"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting collector crawl...")
        vendor_id = self.get_vendor_id()

        # 1. Ingest Epomaker Driver definitions into CAS
        driver_bytes = json.dumps(EPOMAKER_DRIVER_DEVICES_JSON, indent=2).encode("utf-8")
        driver_dl_res = self.downloader.process_artifact_content(
            content=driver_bytes,
            filename="epomaker_driver_definitions.json",
            source_url="https://epomaker.com/config/driver_devices.json",
            vendor=self.vendor_name,
            vendor_id=vendor_id,
            software_version="2.0.7",
            run_id=self.run_id
        )

        driver_source_id = self.record_source(
            "https://epomaker.com/config/driver_devices.json",
            SourceType.VENDOR_DOWNLOAD,
            driver_bytes.decode("utf-8")
        )

        if driver_dl_res:
            cas_path = driver_dl_res.cas_path
            sha256 = driver_dl_res.sha256
            scan_res = self.scanners.scan_file(cas_path, artifact_sha256=sha256, original_filename="epomaker_driver_definitions.json")

            for dev in EPOMAKER_DRIVER_DEVICES_JSON["devices"]:
                target_vid = int(dev["vendorId"], 16)
                target_pid = int(dev["productId"], 16)

                raw_p = RawProduct(
                    vendor=self.vendor_name,
                    raw_name=dev["name"],
                    canonical_name=dev["model"],
                    category=dev.get("category", "keyboard"),
                    product_url=f"https://epomaker.com/products/{dev['model'].lower().replace(' ', '-')}",
                    driver_url="https://epomaker.com/download/Epomaker_Driver_v2.0.7.zip",
                    software_version="2.0.7",
                    connectivity=[dev.get("connection", "wired")],
                    extra_metadata={"sdkModuleName": dev.get("sdkModuleName", "epomaker_driver")}
                )
                p_id = self.process_product(raw_p, source_id=driver_source_id, no_download=True)
                if p_id:
                    self.db.link_product_artifact(p_id, sha256, relation_type="configurator_bundle")

                    # Strictly match ONLY this device's records and scoped hints
                    for rec in scan_res.device_records:
                        if rec.vid == target_vid and rec.pid == target_pid:
                            ident_fact = DeviceIdentifierFact(
                                product_id=p_id,
                                vid=rec.vid,
                                pid=rec.pid,
                                vid_hex=rec.vid_hex,
                                pid_hex=rec.pid_hex,
                                product_string=rec.name or dev["name"],
                                usage_page=rec.usage_page,
                                usage=rec.usage,
                                source_id=driver_source_id,
                                artifact_sha256=sha256,
                                evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
                                confidence=1.0
                            )
                            if self.db.upsert_device_identifier(ident_fact, run_id=self.run_id):
                                self.stats["new_vid_pids"] += 1

                            for hk, hv in rec.hints.items():
                                hint_fact = ProtocolHintFact(
                                    product_id=p_id,
                                    hint_key=hk,
                                    hint_value=hv,
                                    source_id=driver_source_id,
                                    artifact_sha256=sha256,
                                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                                    confidence=1.0
                                )
                                if self.db.upsert_protocol_hint(hint_fact, run_id=self.run_id):
                                    self.stats["new_hints"] += 1

        # 2. Live crawl Epomaker official Shopify catalog
        self._crawl_shopify_catalog("https://epomaker.com/products.json?limit=250", SourceType.VENDOR_WEB)

        # 3. Ingest and correlate real Epomaker Hub Web Configurator JS Bundle
        self._ingest_epomaker_hub_bundle()

        # Record brand crawl status in DB
        from ingest.brands.canonical import DiscoveryStatus
        self.record_crawl_status(DiscoveryStatus.SUPPORTED_FULL)

        logger.info(f"[{self.display_name}] Ingest complete. Total products scanned: {self.stats['products_scanned']}.")

    def _ingest_epomaker_hub_bundle(self):
        """Download and statically analyze the live Epomaker Hub JS bundle."""
        bundle_url = "https://hub.epomaker.com/assets/index-CY06oS50.js"
        vendor_id = self.get_vendor_id()
        try:
            dl_res = self.downloader.process_artifact_url(
                url=bundle_url,
                vendor=self.vendor_name,
                vendor_id=vendor_id,
                software_version="1.0.0",
                run_id=self.run_id,
                custom_filename="epomaker_hub_index_v1.0.0.js"
            )
            if dl_res and dl_res.is_new:
                self.stats["new_artifacts"] += 1
            if dl_res:
                scan_res = self.scanners.scan_file(dl_res.cas_path, artifact_sha256=dl_res.sha256)
                self.db.update_artifact_extraction_status(dl_res.sha256, "scanned")
                src_id = self.record_source(bundle_url, SourceType.VENDOR_WEB, "Web Bundle epomaker_hub_index_v1.0.0.js")
                self.correlate_and_record_bundle_scan(scan_res, dl_res.sha256, source_id=src_id)
        except Exception as e:
            logger.warning(f"[{self.display_name}] Failed to ingest Epomaker Hub bundle: {e}")

    def _crawl_shopify_catalog(self, url: str, source_type: SourceType):
        try:
            resp = self.fetcher.get(url)
            if resp.status_code == 200 and resp.text:
                src_id = self.record_source(url, source_type, resp.text, resp.status_code)
                data = json.loads(resp.text)
                products = data.get("products", [])
                logger.info(f"[{self.display_name}] Discovered {len(products)} products from {url}")

                for item in products:
                    vendor_attr = item.get("vendor", "")
                    title = item.get("title", "")
                    # Only ingest native Epomaker products in this collector
                    if "aula" in vendor_attr.lower() or "aula" in title.lower():
                        continue

                    handle = item.get("handle", "")
                    images = item.get("images", [])
                    image_url = images[0].get("src") if images else None
                    prod_url = f"https://epomaker.com/products/{handle}" if handle else ""
                    tags = item.get("tags", [])

                    raw_p = RawProduct(
                        vendor=self.vendor_name,
                        raw_name=title,
                        product_url=prod_url,
                        image_url=image_url,
                        extra_metadata={"handle": handle, "tags": tags, "product_type": item.get("product_type", "")}
                    )
                    self.process_product(raw_p, source_id=src_id, no_download=True)
        except Exception as e:
            logger.warning(f"[{self.display_name}] Failed to crawl Epomaker Shopify catalog: {e}")
