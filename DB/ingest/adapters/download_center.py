"""Download and support center discovery adapter."""

import re
import urllib.parse
from bs4 import BeautifulSoup

from ingest.adapters.base import BaseAdapter, AdapterDiscoveryResult
from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.logging_setup import get_logger
from ingest.network.url_norm import normalize_artifact_url
from ingest.normalize.evidence import RawProduct, RawArtifact

logger = get_logger()

RE_VERSION = re.compile(r'(?:v|version|ver|v\.|setup[_\-\s]*|driver[_\-\s]*)([0-9]+(?:\.[0-9]+)+(?:[_\-][a-zA-Z0-9]+)?)', re.IGNORECASE)
RE_FILE_EXT = re.compile(r'\.(?:zip|exe|msi|7z|dmg|pkg|json|inf)(?:\?.*)?$', re.IGNORECASE)


class DownloadCenterAdapter(BaseAdapter):
    def discover(self, brand: BrandDef) -> AdapterDiscoveryResult:
        if not brand.download_urls and not brand.driver_packages:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_SOFTWARE_FOUND, "No download URLs configured")

        all_products: list[RawProduct] = []
        all_artifacts: list[RawArtifact] = []
        seen_art_urls: set[str] = set()

        # 1. Process explicit driver packages if configured
        for url, fname, ver, model in brand.driver_packages:
            norm_u = normalize_artifact_url(url)
            if norm_u not in seen_art_urls:
                seen_art_urls.add(norm_u)
                art = RawArtifact(
                    original_url=url,
                    filename=fname,
                    vendor=brand.canonical_name,
                    software_version=ver,
                    related_products=[model] if model else []
                )
                all_artifacts.append(art)
            if model:
                prod = RawProduct(
                    vendor=brand.canonical_name,
                    raw_name=model,
                    driver_url=url,
                    software_version=ver,
                    source_url=url,
                    extra_metadata={"metadata_confidence": 0.40}
                )
                all_products.append(prod)

        # 2. Scrape download URLs
        for dl_page_url in brand.download_urls:
            try:
                resp = self.fetcher.get(dl_page_url, allow_fallback=False)
                if resp.status_code != 200 or not resp.text:
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", href=True)
                for a in links:
                    href = a["href"].strip()
                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue

                    full_url = urllib.parse.urljoin(dl_page_url, href)
                    if RE_FILE_EXT.search(full_url):
                        norm_u = normalize_artifact_url(full_url)
                        if norm_u not in seen_art_urls:
                            seen_art_urls.add(norm_u)
                            link_text = a.get_text().strip()
                            parsed = urllib.parse.urlparse(full_url)
                            fname = parsed.path.split("/")[-1] or "driver.bin"

                            ver_m = RE_VERSION.search(fname) or RE_VERSION.search(link_text)
                            version_str = ver_m.group(1) if ver_m else None

                            art = RawArtifact(
                                original_url=full_url,
                                filename=fname,
                                vendor=brand.canonical_name,
                                software_version=version_str
                            )
                            all_artifacts.append(art)

                        # Only emit RawProduct if link text is a legitimate human model name (not a filename or generic link)
                        if link_text:
                            clean_title = link_text.strip()
                            from ingest.normalize.models import is_software_filename
                            if (
                                3 < len(clean_title) < 80
                                and not is_software_filename(clean_title)
                                and not is_software_filename(fname)
                                and not any(kw in clean_title.lower() for kw in [
                                    "download", "click here", "manual", "guide", "here", "software",
                                    "driver", "firmware", "setup", "installer", "update", "patch", "zip", "exe"
                                ])
                            ):
                                prod = RawProduct(
                                    vendor=brand.canonical_name,
                                    raw_name=clean_title,
                                    driver_url=full_url,
                                    software_version=version_str,
                                    source_url=dl_page_url,
                                    extra_metadata={"metadata_confidence": 0.40}
                                )
                                all_products.append(prod)


            except Exception as e:
                logger.debug(f"[{brand.canonical_name}] Error crawling download center {dl_page_url}: {e}")

        if not all_artifacts and not all_products:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_SOFTWARE_FOUND, "No downloadable software found on support pages")

        return AdapterDiscoveryResult(
            products=all_products,
            artifacts=all_artifacts,
            status=DiscoveryStatus.SUPPORTED_FULL
        )
