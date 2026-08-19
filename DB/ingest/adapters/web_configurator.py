from ingest.adapters.base import BaseAdapter, AdapterDiscoveryResult
from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.logging_setup import get_logger
from ingest.network.url_norm import normalize_artifact_url
from ingest.normalize.evidence import RawProduct, RawArtifact

logger = get_logger()


class WebConfiguratorAdapter(BaseAdapter):
    def discover(self, brand: BrandDef) -> AdapterDiscoveryResult:
        if not brand.web_configurator_urls:
            return AdapterDiscoveryResult([], [], DiscoveryStatus.NO_SOFTWARE_FOUND, "No web configurator URLs configured")

        all_artifacts: list[RawArtifact] = []
        seen_urls: set[str] = set()

        for url, fname, ver in brand.web_configurator_urls:
            norm_u = normalize_artifact_url(url)
            if norm_u not in seen_urls:
                seen_urls.add(norm_u)
                art = RawArtifact(
                    original_url=url,
                    filename=fname,
                    vendor=brand.canonical_name,
                    software_version=ver
                )
                all_artifacts.append(art)

        return AdapterDiscoveryResult(
            products=[],
            artifacts=all_artifacts,
            status=DiscoveryStatus.SUPPORTED_FULL
        )
