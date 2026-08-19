"""Static HTML & JSON-LD catalog discovery adapter."""

import json
import re
import urllib.parse
from bs4 import BeautifulSoup

from ingest.adapters.base import BaseAdapter, AdapterDiscoveryResult
from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import RawProduct, RawArtifact

logger = get_logger()


def is_trusted_brand_url(target_url: str, brand: BrandDef, base_page_url: str) -> bool:
    """Enforce strict domain trust boundary for brand catalog discovery."""
    try:
        t_netloc = urllib.parse.urlsplit(target_url).netloc.lower().split(":")[0]
        if not t_netloc:
            return True
        
        b_netloc = urllib.parse.urlsplit(base_page_url).netloc.lower().split(":")[0]
        allowed = {b_netloc}
        if brand.website:
            w_netloc = urllib.parse.urlsplit(brand.website).netloc.lower().split(":")[0]
            if w_netloc:
                allowed.add(w_netloc)
                parts = w_netloc.split(".")
                if len(parts) >= 2:
                    allowed.add(".".join(parts[-2:]))
        
        for d in allowed:
            if t_netloc == d or t_netloc.endswith(f".{d}"):
                return True
        return False
    except Exception:
        return False


RE_GENERIC_NAV_TEXT = re.compile(
    r'^(keyboards?|mice|mouse|headsets?|microphones?|switches?|accessories|mousepads?|more info|learn more|shop now|view all|products?|overview|support|software & services|services|terminals|desktop sets|office keyboards?|gaming mice|wireless mice|wired mice|mac keyboards?|industrial keyboards?|multi-device keyboards?|discover|cover|um microphone series|ngale microphone series|mx standard|mx special|mx low profile|mx ultra low profile|mx multipoint)$',
    re.IGNORECASE
)


class StaticHtmlCatalogAdapter(BaseAdapter):
    def discover(self, brand: BrandDef) -> AdapterDiscoveryResult:
        urls_to_try = brand.catalog_urls or ([brand.website] if brand.website else [])
        if not urls_to_try:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, "No catalog URLs defined")

        all_products: list[RawProduct] = []
        from ingest.normalize.models import is_software_filename, RE_NON_PERIPHERAL

        for page_url in urls_to_try:
            try:
                resp = self.fetcher.get(page_url, allow_fallback=False)
                if resp.status_code == 403 or resp.status_code == 503:
                    return AdapterDiscoveryResult([], [], DiscoveryStatus.BLOCKED_WAF, f"HTTP {resp.status_code} WAF on {page_url}")
                if resp.status_code != 200 or not resp.text:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")

                # 1. Look for JSON-LD schema
                for script in soup.find_all("script", type="application/ld+json"):
                    try:
                        ld = json.loads(script.string or "{}")
                        items = []
                        if isinstance(ld, list):
                            items = ld
                        elif isinstance(ld, dict):
                            if ld.get("@type") == "Product":
                                items = [ld]
                            elif "itemListElement" in ld:
                                items = ld["itemListElement"]

                        for item in items:
                            if isinstance(item, dict) and (item.get("@type") == "Product" or "name" in item):
                                name = item.get("name")
                                url = item.get("url") or page_url
                                if name:
                                    full_prod_url = urllib.parse.urljoin(page_url, url)
                                    if not is_trusted_brand_url(full_prod_url, brand, page_url):
                                        continue
                                    if RE_NON_PERIPHERAL.search(name) or is_software_filename(name):
                                        continue
                                    all_products.append(RawProduct(
                                        vendor=brand.canonical_name,
                                        raw_name=name,
                                        product_url=full_prod_url,
                                        image_url=item.get("image"),
                                        source_url=page_url,
                                        extra_metadata={"metadata_confidence": 0.80}
                                    ))
                    except Exception:
                        pass

                # 2. Look for product links if JSON-LD was empty
                if not all_products:
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        if "/product" in href or "/item" in href or "/keyboard" in href or "/mouse" in href or "/audio" in href:
                            raw_text = a.get_text().strip()
                            clean_text = re.sub(r'[\r\n\t]+', ' ', raw_text)
                            clean_text = re.sub(r'[\$€£¥]\s*[\d\.,\s–-]+', '', clean_text)
                            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                            if 3 < len(clean_text) < 80:
                                if RE_GENERIC_NAV_TEXT.match(clean_text):
                                    continue
                                full_url = urllib.parse.urljoin(page_url, href)
                                if not is_trusted_brand_url(full_url, brand, page_url):
                                    continue
                                if RE_NON_PERIPHERAL.search(clean_text) or is_software_filename(clean_text):
                                    continue
                                all_products.append(RawProduct(
                                    vendor=brand.canonical_name,
                                    raw_name=clean_text,
                                    product_url=full_url,
                                    source_url=page_url,
                                    extra_metadata={"metadata_confidence": 0.60}
                                ))

            except Exception as e:
                logger.debug(f"[{brand.canonical_name}] Error crawling static page {page_url}: {e}")

        if not all_products:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, "No product listings found on catalog page")


        return AdapterDiscoveryResult(
            products=all_products,
            artifacts=[],
            status=DiscoveryStatus.METADATA_ONLY
        )
