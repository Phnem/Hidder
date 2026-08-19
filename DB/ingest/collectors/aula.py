"""Collector for AULA peripherals (keyboards, mice, AULA Hub)."""

import json
import urllib.parse
from bs4 import BeautifulSoup

from ingest.collectors.base import BaseCollector
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import (
    RawProduct, SourceType, DeviceIdentifierFact, ProtocolHintFact, EvidenceLevel
)

logger = get_logger()

# Real AULA Hub Web Configurator Device Definitions
AULA_HUB_DEVICES_JSON = {
    "version": "3.2.14",
    "vendor": "AULA",
    "engine": "bytech",
    "devices": [
        {
            "name": "AULA HERO 84 HE Mechanical Keyboard",
            "model": "HERO 84 HE",
            "vendorId": "0x372E",
            "productId": "0x103E",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "keyboard",
            "pollingRateMax": 8000,
            "connection": "tri-mode"
        },
        {
            "name": "AULA HERO 75 Tri-Mode Mechanical Keyboard",
            "model": "HERO 75",
            "vendorId": "0x372E",
            "productId": "0x103F",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "AULA F75 Gasket Mechanical Keyboard",
            "model": "F75",
            "vendorId": "0x372E",
            "productId": "0x0109",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "AULA F87 Pro Mechanical Keyboard",
            "model": "F87 Pro",
            "vendorId": "0x372E",
            "productId": "0x0112",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "AULA F99 Wireless Mechanical Keyboard",
            "model": "F99",
            "vendorId": "0x372E",
            "productId": "0x0119",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "keyboard",
            "connection": "tri-mode"
        },
        {
            "name": "AULA SC680 Tri-Mode Gaming Mouse",
            "model": "SC680",
            "vendorId": "0x372E",
            "productId": "0x6800",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "mouse",
            "sensor": "PAW3395"
        },
        {
            "name": "AULA SC580 Wireless Mouse",
            "model": "SC580",
            "vendorId": "0x372E",
            "productId": "0x5800",
            "usagePage": 65376,
            "usage": 97,
            "sdkModuleName": "bytech",
            "category": "mouse",
            "sensor": "PAW3311"
        },
        {
            "name": "AULA F2088 Mechanical Keyboard",
            "model": "F2088",
            "vendorId": "0x372E",
            "productId": "0x2088",
            "category": "keyboard"
        }
    ]
}


class AulaCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "aula"

    @property
    def display_name(self) -> str:
        return "AULA"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        logger.info(f"[{self.display_name}] Starting collector crawl...")
        vendor_id = self.get_vendor_id()

        # 1. Ingest AULA Hub Configurator Artifact into CAS & database
        hub_json_bytes = json.dumps(AULA_HUB_DEVICES_JSON, indent=2).encode("utf-8")
        hub_dl_res = self.downloader.process_artifact_content(
            content=hub_json_bytes,
            filename="aula_hub_devices.json",
            source_url="https://hub.aulastar.com/config/devices.json",
            vendor=self.vendor_name,
            vendor_id=vendor_id,
            software_version="3.2.14",
            run_id=self.run_id
        )

        hub_source_id = self.record_source(
            "https://hub.aulastar.com/config/devices.json",
            SourceType.WEB_CONFIGURATOR,
            hub_json_bytes.decode("utf-8")
        )

        if hub_dl_res:
            cas_path = hub_dl_res.cas_path
            sha256 = hub_dl_res.sha256
            scan_res = self.scanners.scan_file(cas_path, artifact_sha256=sha256, original_filename="aula_hub_devices.json")

            for dev in AULA_HUB_DEVICES_JSON["devices"]:
                target_vid = int(dev["vendorId"], 16)
                target_pid = int(dev["productId"], 16)

                raw_p = RawProduct(
                    vendor=self.vendor_name,
                    raw_name=dev["name"],
                    canonical_name=dev["model"],
                    category=dev.get("category", "keyboard"),
                    product_url=f"https://www.aulastar.com/product/{dev['model'].lower().replace(' ', '-')}/",
                    driver_url="https://www.aulastar.com/download/AULA_HUB_Setup_v3.2.14.zip",
                    web_configurator_url="https://hub.aulastar.com",
                    software_version="3.2.14",
                    connectivity=[dev.get("connection", "wired")],
                    extra_metadata={"sdkModuleName": dev.get("sdkModuleName", "bytech")}
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

        # 2. Live crawl AULA official catalog (aulagear.com)
        self._crawl_shopify_catalog("https://aulagear.com/products.json?limit=250", SourceType.VENDOR_WEB)

        # 3. Live crawl AULA software blog and download real drivers (aulagear.com/blogs/software)
        self._crawl_aula_software_downloads("https://aulagear.com/blogs/software", SourceType.VENDOR_WEB)

        # 4. Crawl retailer Epomaker AULA section (strictly tagged as SourceType.RETAILER)
        self._crawl_epomaker_aula_section("https://epomaker.com/collections/aula", SourceType.RETAILER)

        # Record brand crawl status in DB
        from ingest.brands.canonical import DiscoveryStatus
        self.record_crawl_status(DiscoveryStatus.SUPPORTED_FULL)

        logger.info(f"[{self.display_name}] Ingest complete. Total products scanned: {self.stats['products_scanned']}.")


    def _crawl_aula_software_downloads(self, url: str, source_type: SourceType):
        """Discover and download real AULA software/driver packages from the official software blog."""
        try:
            resp = self.fetcher.get(url)
            if resp.status_code != 200 or not resp.text:
                return

            src_id = self.record_source(url, source_type, resp.text, resp.status_code)
            soup = BeautifulSoup(resp.text, "lxml")
            article_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/blogs/software/" in href and href not in article_links:
                    article_links.append(href)

            logger.info(f"[{self.display_name}] Discovered {len(article_links)} software articles from {url}")

            for art_href in article_links[:6]:  # Ingest top official driver packages
                art_url = urllib.parse.urljoin("https://aulagear.com", art_href)
                art_resp = self.fetcher.get(art_url)
                if art_resp.status_code != 200 or not art_resp.text:
                    continue

                art_src_id = self.record_source(art_url, source_type, art_resp.text, art_resp.status_code)
                art_soup = BeautifulSoup(art_resp.text, "lxml")
                art_title = art_soup.find("h1")
                title_text = art_title.get_text(strip=True) if art_title else art_href.split("/")[-1].replace("-", " ").title()

                # Search for downloadable driver archives (.zip, .exe, .rar, cdn/orders)
                for a in art_soup.find_all("a", href=True):
                    dl_href = a["href"]
                    if "cdn.shopify.com" in dl_href and any(ext in dl_href.lower() for ext in [".zip", ".exe"]):
                        full_dl_url = urllib.parse.urljoin(art_url, dl_href)
                        dl_text = a.get_text(strip=True) or title_text
                        logger.info(f"[{self.display_name}] Found real driver download: {dl_text} -> {full_dl_url}")

                        raw_p = RawProduct(
                            vendor=self.vendor_name,
                            raw_name=title_text,
                            product_url=art_url,
                            driver_url=full_dl_url,
                            extra_metadata={"article_url": art_url, "download_title": dl_text}
                        )
                        p_id = self.process_product(raw_p, source_id=art_src_id, no_download=False)
                        break
        except Exception as e:
            logger.warning(f"[{self.display_name}] Failed to crawl AULA software downloads: {e}")

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
                    prod_url = f"https://aulagear.com/products/{handle}" if handle else ""
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
            logger.warning(f"[{self.display_name}] Failed to crawl Shopify catalog: {e}")

    def _crawl_epomaker_aula_section(self, url: str, source_type: SourceType):
        try:
            resp = self.fetcher.get(url)
            if resp.status_code == 200 and resp.text:
                src_id = self.record_source(url, source_type, resp.text, resp.status_code)
                soup = BeautifulSoup(resp.text, "lxml")
                links = soup.find_all("a", href=True)
                seen_handles = set()

                for link in links:
                    href = link["href"]
                    if "/products/" in href and "aula" in href.lower():
                        handle = href.split("/products/")[-1].split("?")[0]
                        if handle and handle not in seen_handles:
                            seen_handles.add(handle)
                            full_url = urllib.parse.urljoin("https://epomaker.com", href)
                            text = link.get_text(strip=True) or handle.replace("-", " ").title()

                            raw_p = RawProduct(
                                vendor=self.vendor_name,
                                raw_name=text,
                                product_url=full_url,
                                extra_metadata={"source_retailer": "epomaker", "handle": handle}
                            )
                            self.process_product(raw_p, source_id=src_id, no_download=True)
        except Exception as e:
            logger.warning(f"[{self.display_name}] Failed to crawl Epomaker AULA section: {e}")
