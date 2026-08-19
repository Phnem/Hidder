"""Generic Shopify catalog and download discovery adapter."""

import json
import re
import urllib.parse
from typing import Optional
from bs4 import BeautifulSoup

from ingest.adapters.base import BaseAdapter, AdapterDiscoveryResult
from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.logging_setup import get_logger
from ingest.normalize.evidence import RawProduct, RawArtifact

logger = get_logger()

RE_DOWNLOAD_LINK = re.compile(
    r'href=[\'"]([^\'"]+\.(?:zip|exe|msi|7z|dmg|pkg|json|inf))(?:\?[^\'"]*)?[\'"]',
    re.IGNORECASE
)


class ShopifyCatalogAdapter(BaseAdapter):
    def discover(self, brand: BrandDef, max_pages: int = 5) -> AdapterDiscoveryResult:
        base_url = brand.shopify_url
        if not base_url:
            if brand.website:
                base_url = f"{brand.website.rstrip('/')}/products.json?limit=250"
            else:
                return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, "No website or shopify URL defined")

        parsed_base = urllib.parse.urlparse(base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

        all_products: list[RawProduct] = []
        all_artifacts: list[RawArtifact] = []

        for page in range(1, max_pages + 1):
            if "?" in base_url:
                url = f"{base_url}&page={page}"
            else:
                url = f"{base_url}?limit=250&page={page}"

            try:
                resp = self.fetcher.get(url, allow_fallback=False)
                if resp.status_code == 403 or resp.status_code == 503:
                    if not all_products:
                        return AdapterDiscoveryResult([], [], DiscoveryStatus.BLOCKED_WAF, f"HTTP {resp.status_code} WAF / anti-bot protection")
                    break

                if resp.status_code == 404:
                    if not all_products:
                        return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, "products.json returned 404")
                    break

                if resp.status_code != 200:
                    if not all_products:
                        return AdapterDiscoveryResult([], [], DiscoveryStatus.PARSE_FAILED, f"HTTP {resp.status_code}")
                    break

                data = json.loads(resp.text)
                prods = data.get("products", [])
                if not prods:
                    break

                for p in prods:
                    title = p.get("title", "").strip()
                    handle = p.get("handle", "").strip()
                    if not title:
                        continue

                    prod_url = f"{origin}/products/{handle}" if handle else brand.website
                    product_type = p.get("product_type", "").strip()
                    tags = p.get("tags", [])
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",") if t.strip()]

                    img_url = None
                    images = p.get("images", [])
                    if images and isinstance(images, list):
                        img_url = images[0].get("src")

                    body_html = p.get("body_html", "") or ""
                    driver_url = None
                    if body_html:
                        dl_matches = RE_DOWNLOAD_LINK.findall(body_html)
                        if dl_matches:
                            driver_url = urllib.parse.urljoin(origin, dl_matches[0])

                    raw_prod = RawProduct(
                        vendor=brand.canonical_name,
                        raw_name=title,
                        product_url=prod_url,
                        driver_url=driver_url,
                        image_url=img_url,
                        source_url=url,
                        extra_metadata={
                            "product_type": product_type,
                            "tags": tags,
                            "handle": handle,
                            "metadata_confidence": 0.85,
                        }
                    )
                    all_products.append(raw_prod)

                # If returned less than 250, we reached the end of catalog
                if len(prods) < 250:
                    break

            except Exception as e:
                logger.debug(f"[{brand.canonical_name}] Shopify crawl exception on page {page}: {e}")
                if not all_products:
                    return AdapterDiscoveryResult([], [], DiscoveryStatus.PARSE_FAILED, str(e))
                break

        if not all_products:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_OFFICIAL_CATALOG_FOUND, "No products found in catalog")

        return AdapterDiscoveryResult(
            products=all_products,
            artifacts=all_artifacts,
            status=DiscoveryStatus.SUPPORTED_FULL if any(p.driver_url for p in all_products) else DiscoveryStatus.SUPPORTED_PARTIAL
        )
