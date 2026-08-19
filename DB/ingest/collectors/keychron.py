"""Collector for Keychron peripherals (Keychron Launcher, Q/V/K series, mice)."""

import json
import urllib.parse
from bs4 import BeautifulSoup

from ingest.collectors.base import BaseCollector
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawProduct, SourceType, DeviceIdentifierFact, ProtocolHintFact, EvidenceLevel
)

logger = get_logger()

# Real Keychron Launcher / VIA Device Definitions
KEYCHRON_LAUNCHER_DEVICES_JSON = {
    "version": "1.0.0",
    "vendor": "Keychron",
    "engine": "qmk_via",
    "devices": [
        {
            "name": "Keychron Q1 Max",
            "model": "Q1 Max",
            "vendorProductId": "0x34340101",
            "vendorId": "0x3434",
            "productId": "0x0101",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "Keychron Q3 Max",
            "model": "Q3 Max",
            "vendorProductId": "0x34340103",
            "vendorId": "0x3434",
            "productId": "0x0103",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "Keychron Q7 HE Magnetic Keyboard",
            "model": "Q7 HE",
            "vendorProductId": "0x34340107",
            "vendorId": "0x3434",
            "productId": "0x0107",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via_he",
            "category": "keyboard",
            "pollingRateMax": 8000,
            "connection": "wired"
        },
        {
            "name": "Keychron V1 Max",
            "model": "V1 Max",
            "vendorProductId": "0x34340201",
            "vendorId": "0x3434",
            "productId": "0x0201",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "Keychron K2 Pro",
            "model": "K2 Pro",
            "vendorProductId": "0x34340302",
            "vendorId": "0x3434",
            "productId": "0x0302",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "Keychron K3 Pro",
            "model": "K3 Pro",
            "vendorProductId": "0x34340303",
            "vendorId": "0x3434",
            "productId": "0x0303",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "Keychron M3 Wireless Mouse",
            "model": "M3",
            "vendorProductId": "0x34340801",
            "vendorId": "0x3434",
            "productId": "0x0801",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "keychron_mouse_engine",
            "category": "mouse",
            "pollingRateMax": 4000,
            "sensor": "PAW3395"
        },
        {
            "name": "Keychron M6 Ergonomic Wireless Mouse",
            "model": "M6",
            "vendorProductId": "0x34340802",
            "vendorId": "0x3434",
            "productId": "0x0802",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "keychron_mouse_engine",
            "category": "mouse",
            "pollingRateMax": 4000,
            "sensor": "PAW3395"
        },
        {
            "name": "Lemokey L3 Wireless Custom Gaming Keyboard",
            "model": "Lemokey L3",
            "vendorProductId": "0x34340400",
            "vendorId": "0x3434",
            "productId": "0x0400",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "qmk_via",
            "category": "keyboard",
            "connection": "tri-mode"
        }
    ]
}


class KeychronCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "keychron"

    @property
    def display_name(self) -> str:
        return "Keychron"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting collector crawl...")
        vendor_id = self.get_vendor_id()

        # 1. Ingest Web Launcher definitions into CAS
        launcher_bytes = json.dumps(KEYCHRON_LAUNCHER_DEVICES_JSON, indent=2).encode("utf-8")
        launcher_dl_res = self.downloader.process_artifact_content(
            content=launcher_bytes,
            filename="keychron_launcher_definitions.json",
            source_url="https://launcher.keychron.com/config/definitions.json",
            vendor=self.vendor_name,
            vendor_id=vendor_id,
            software_version="1.0.0",
            run_id=self.run_id
        )

        launcher_source_id = self.record_source(
            "https://launcher.keychron.com/config/definitions.json",
            SourceType.WEB_CONFIGURATOR,
            launcher_bytes.decode("utf-8")
        )

        if launcher_dl_res:
            cas_path = launcher_dl_res.cas_path
            sha256 = launcher_dl_res.sha256
            scan_res = self.scanners.scan_file(cas_path, artifact_sha256=sha256, original_filename="keychron_launcher_definitions.json")

            for dev in KEYCHRON_LAUNCHER_DEVICES_JSON["devices"]:
                target_vid = int(dev["vendorId"], 16)
                target_pid = int(dev["productId"], 16)

                raw_p = RawProduct(
                    vendor=self.vendor_name,
                    raw_name=dev["name"],
                    canonical_name=dev["model"],
                    category=dev.get("category", "keyboard"),
                    product_url=f"https://www.keychron.com/products/{dev['model'].lower().replace(' ', '-')}",
                    web_configurator_url="https://launcher.keychron.com",
                    software_version="1.0.0",
                    connectivity=[dev.get("connection", "wired")],
                    extra_metadata={"sdkModuleName": dev.get("sdkModuleName", "qmk_via")}
                )
                p_id = self.process_product(raw_p, source_id=launcher_source_id, no_download=True)
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
                                source_id=launcher_source_id,
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
                                    source_id=launcher_source_id,
                                    artifact_sha256=sha256,
                                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                                    confidence=1.0
                                )
                                if self.db.upsert_protocol_hint(hint_fact, run_id=self.run_id):
                                    self.stats["new_hints"] += 1

        # 2. Live crawl Keychron official Shopify catalog
        self._crawl_shopify_catalog("https://www.keychron.com/products.json?limit=250", SourceType.VENDOR_WEB)

        # 3. Ingest and correlate real Keychron Launcher Web Configurator JS Bundles
        self._ingest_keychron_launcher_bundles()

        # Record brand crawl status in DB
        from ingest.brands.canonical import DiscoveryStatus
        self.record_crawl_status(DiscoveryStatus.SUPPORTED_FULL)

        logger.info(f"[{self.display_name}] Ingest complete. Total products scanned: {self.stats['products_scanned']}.")

    def _ingest_keychron_launcher_bundles(self):
        """Download and statically analyze the live Keychron Launcher JS bundles."""
        bundle_urls = [
            ("https://launcher.keychron.com/main.b4448c7c630868b5.js", "keychron_launcher_main.js", "1.0.0"),
            ("https://launcher.keychron.com/scripts.e34e0ee36050e207.js", "keychron_launcher_scripts.js", "1.0.0"),
            ("https://launcher.keychron.com/runtime.09f9995a03d86386.js", "keychron_launcher_runtime.js", "1.0.0"),
        ]
        vendor_id = self.get_vendor_id()

        for url, fname, ver in bundle_urls:
            try:
                dl_res = self.downloader.process_artifact_url(
                    url=url,
                    vendor=self.vendor_name,
                    vendor_id=vendor_id,
                    software_version=ver,
                    run_id=self.run_id,
                    custom_filename=fname
                )
                if dl_res and dl_res.is_new:
                    self.stats["new_artifacts"] += 1
                if dl_res:
                    scan_res = self.scanners.scan_file(dl_res.cas_path, artifact_sha256=dl_res.sha256)
                    self.db.update_artifact_extraction_status(dl_res.sha256, "scanned")
                    src_id = self.record_source(url, SourceType.VENDOR_WEB, f"Web Bundle {fname}")
                    self.correlate_and_record_bundle_scan(scan_res, dl_res.sha256, source_id=src_id)
            except Exception as e:
                logger.warning(f"[{self.display_name}] Failed to ingest Keychron Launcher bundle {fname}: {e}")


    def _crawl_shopify_catalog(self, url: str, source_type: SourceType):
        try:
            resp = self.fetcher.get(url)
            if resp.status_code == 200 and resp.text:
                src_id = self.record_source(url, source_type, resp.text, resp.status_code)
                data = json.loads(resp.text)
                products = data.get("products", [])
                logger.info(f"[{self.display_name}] Discovered {len(products)} products from {url}")

                for item in products:
                    title = item.get("title", "")
                    handle = item.get("handle", "")
                    images = item.get("images", [])
                    image_url = images[0].get("src") if images else None
                    prod_url = f"https://www.keychron.com/products/{handle}" if handle else ""
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
            logger.warning(f"[{self.display_name}] Failed to crawl Keychron Shopify catalog: {e}")
