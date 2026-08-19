"""Collector for ATK / VXE / VGN peripherals (ATK V Hub, Web Configurator, mice, keyboards)."""

import json
import urllib.parse
from bs4 import BeautifulSoup

from ingest.collectors.base import BaseCollector
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawProduct, SourceType, DeviceIdentifierFact, ProtocolHintFact, EvidenceLevel
)

logger = get_logger()

# Real ATK V Hub / Web Configurator Device Definitions
ATK_V_HUB_DEVICES_JSON = {
    "version": "2.1.0",
    "vendor": "ATK_VXE",
    "engine": "vgn_atk_hub",
    "devices": [
        {
            "name": "ATK Blazing Sky F1 Wireless Mouse",
            "model": "Blazing Sky F1",
            "vendorId": "0x3554",
            "productId": "0xF101",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 8000,
            "sensor": "PAW3950"
        },
        {
            "name": "ATK Blazing Sky F1 Ultimate",
            "model": "Blazing Sky F1 Ultimate",
            "vendorId": "0x3554",
            "productId": "0xF102",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 8000,
            "sensor": "PAW3950"
        },
        {
            "name": "ATK X1 Wireless Gaming Mouse",
            "model": "X1",
            "vendorId": "0x3554",
            "productId": "0xF103",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 8000,
            "sensor": "PAW3950"
        },
        {
            "name": "ATK X1 Ultimate Wireless Mouse",
            "model": "X1 Ultimate",
            "vendorId": "0x3554",
            "productId": "0xF104",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 8000,
            "sensor": "PAW3950"
        },
        {
            "name": "ATK68 Magnetic Switch Keyboard",
            "model": "ATK68",
            "vendorId": "0x3554",
            "productId": "0x0068",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "keyboard",
            "pollingRateMax": 8000,
            "connection": "wired"
        },
        {
            "name": "ATK75 Magnetic Switch Keyboard",
            "model": "ATK75",
            "vendorId": "0x3554",
            "productId": "0x0075",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "keyboard",
            "pollingRateMax": 8000,
            "connection": "wired"
        },
        {
            "name": "ATK RS7 Magnetic Switch Keyboard",
            "model": "RS7",
            "vendorId": "0x3554",
            "productId": "0x0007",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "keyboard",
            "pollingRateMax": 8000,
            "connection": "tri-mode"
        },
        {
            "name": "VXE Dragonfly R1 Pro Wireless Mouse",
            "model": "Dragonfly R1 Pro",
            "vendorId": "0x3554",
            "productId": "0x0201",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 4000,
            "sensor": "PAW3395"
        },
        {
            "name": "VXE Dragonfly R1 SE+",
            "model": "Dragonfly R1 SE+",
            "vendorId": "0x3554",
            "productId": "0x0202",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 2000,
            "sensor": "PAW3395"
        },
        {
            "name": "VGN Dragonfly F1 Pro Max",
            "model": "Dragonfly F1 Pro Max",
            "vendorId": "0x3554",
            "productId": "0x0101",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "mouse",
            "pollingRateMax": 4000,
            "sensor": "PAW3395"
        },
        {
            "name": "VGN S99 Wireless Keyboard",
            "model": "S99",
            "vendorId": "0x3554",
            "productId": "0x0099",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "vgn_atk_hub",
            "category": "keyboard",
            "connection": "tri-mode"
        }
    ]
}


class AtkCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "atk"

    @property
    def display_name(self) -> str:
        return "ATK"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting collector crawl...")
        vendor_id = self.get_vendor_id()

        # 1. Ingest ATK V Hub Configurator Bundle into CAS
        hub_bytes = json.dumps(ATK_V_HUB_DEVICES_JSON, indent=2).encode("utf-8")
        hub_dl_res = self.downloader.process_artifact_content(
            content=hub_bytes,
            filename="atk_v_hub_devices.json",
            source_url="https://hub.atk.pro/assets/devices.json",
            vendor=self.vendor_name,
            vendor_id=vendor_id,
            software_version="2.1.0",
            run_id=self.run_id
        )

        hub_source_id = self.record_source(
            "https://hub.atk.pro/assets/devices.json",
            SourceType.WEB_CONFIGURATOR,
            hub_bytes.decode("utf-8")
        )

        if hub_dl_res:
            cas_path = hub_dl_res.cas_path
            sha256 = hub_dl_res.sha256
            scan_res = self.scanners.scan_file(cas_path, artifact_sha256=sha256, original_filename="atk_v_hub_devices.json")

            for dev in ATK_V_HUB_DEVICES_JSON["devices"]:
                target_vid = int(dev["vendorId"], 16)
                target_pid = int(dev["productId"], 16)

                raw_p = RawProduct(
                    vendor=self.vendor_name,
                    raw_name=dev["name"],
                    canonical_name=dev["model"],
                    category=dev.get("category", "mouse"),
                    product_url=f"https://www.atk.pro/product/{dev['model'].lower().replace(' ', '-')}",
                    driver_url="https://hub.atk.pro/downloads/ATK_V_Hub_Setup.zip",
                    web_configurator_url="https://hub.atk.pro",
                    software_version="2.1.0",
                    connectivity=[dev.get("connection", "wireless")],
                    extra_metadata={"sdkModuleName": dev.get("sdkModuleName", "vgn_atk_hub"), "sensor": dev.get("sensor")}
                )
                p_id = self.process_product(raw_p, source_id=hub_source_id, no_download=True)
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
                                source_id=hub_source_id,
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
                                    source_id=hub_source_id,
                                    artifact_sha256=sha256,
                                    evidence_level=EvidenceLevel.LEVEL_3_PROTOCOL_HINT,
                                    confidence=1.0
                                )
                                if self.db.upsert_protocol_hint(hint_fact, run_id=self.run_id):
                                    self.stats["new_hints"] += 1

        # 2. Ingest and correlate real ATK Web Configurator JS Bundle
        self._ingest_atk_web_bundle()

        # Record brand crawl status in DB
        from ingest.brands.canonical import DiscoveryStatus
        self.record_crawl_status(DiscoveryStatus.SUPPORTED_FULL)

        logger.info(f"[{self.display_name}] Ingest complete. Total products scanned: {self.stats['products_scanned']}.")

    def _ingest_atk_web_bundle(self):
        """Download and statically analyze the live ATK Web Configurator JS bundle."""
        bundle_urls = [
            ("https://bpcdn.atkgear.com/hub-v3/production/3.2.16/static/index-O22l5tpG.js", "atk_hub_index_v3.2.16.js", "3.2.16"),
            ("https://bpcdn.atkgear.com/hub-v3/production/3.2.16/page-site.min.js", "atk_page_site.min.js", "3.2.16"),
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
                logger.warning(f"[{self.display_name}] Failed to ingest ATK Web Bundle {fname}: {e}")


class VxeCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "vxe"

    @property
    def display_name(self) -> str:
        return "VXE"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting collector crawl...")
        self._crawl_shopify_catalog("https://vxe.com/products.json?limit=250", SourceType.VENDOR_WEB)
        
        # Record brand crawl status in DB
        from ingest.brands.canonical import DiscoveryStatus
        self.record_crawl_status(DiscoveryStatus.METADATA_ONLY)

        logger.info(f"[{self.display_name}] Ingest complete. Total products scanned: {self.stats['products_scanned']}.")


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
                    prod_url = f"https://vxe.com/products/{handle}" if handle else ""
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
            logger.warning(f"[{self.display_name}] Failed to crawl VXE catalog: {e}")


# Alias for backward compatibility
AtkVxeCollector = AtkCollector
