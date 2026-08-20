"""Collectors registry and factory supporting canonical brands and bulk sources."""

from typing import Type, Optional
from ingest.brands.canonical import BrandDef, get_brand_by_slug, ALL_CANONICAL_BRANDS
from ingest.collectors.base import BaseCollector
from ingest.collectors.aula import AulaCollector
from ingest.collectors.atk_vxe import AtkCollector, VxeCollector, AtkVxeCollector
from ingest.collectors.epomaker import EpomakerCollector
from ingest.collectors.keychron import KeychronCollector
from ingest.collectors.generic_collector import GenericBrandCollector
from ingest.collectors.qmk import QmkCollector, QmkMetadataResolver
from ingest.collectors.libratbag import LibratbagCollector, LibratbagDeviceParser, LibratbagProtocolExtractor
from ingest.collectors.openrgb import OpenRGBCollector, OpenRGBDetectorParser, OpenRGBByteProtocolExtractor
from ingest.collectors.signalrgb import SignalRGBCollector, SignalRGBPluginParser
from ingest.collectors.openrazer import OpenRazerCollector, OpenRazerDriverParser
from ingest.collectors.solaar import SolaarCollector, SolaarDescriptorParser
from ingest.collectors.rivalcfg import RivalcfgCollector, RivalcfgProfileParser
from ingest.collectors.wooting import WootingCollector
from ingest.collectors.corsair_ckb import CorsairCkbCollector, CorsairCkbParser
from ingest.collectors.logitech_docs import LogitechDocsCollector
from ingest.collectors.artemis_rgbnet import ArtemisRGBNetCollector, ArtemisRGBNetParser
from ingest.collectors.linux_hid import LinuxHIDCollector, LinuxHIDParser

SPECIALIZED_COLLECTORS: dict[str, Type[BaseCollector]] = {
    "aula": AulaCollector,
    "atk": AtkCollector,
    "vxe": VxeCollector,
    "epomaker": EpomakerCollector,
    "keychron": KeychronCollector,
}


def get_collector_for_brand(brand_slug: str, *args, **kwargs) -> Optional[BaseCollector]:
    """Factory to instantiate the appropriate collector for any canonical brand."""
    slug_clean = brand_slug.strip().lower()
    brand_def = get_brand_by_slug(slug_clean)
    if not brand_def:
        return None

    if slug_clean in SPECIALIZED_COLLECTORS:
        cls = SPECIALIZED_COLLECTORS[slug_clean]
        return cls(*args, **kwargs)

    return GenericBrandCollector(brand_def, *args, **kwargs)


__all__ = [
    "BaseCollector",
    "AulaCollector",
    "AtkVxeCollector",
    "EpomakerCollector",
    "KeychronCollector",
    "GenericBrandCollector",
    "QmkCollector",
    "QmkMetadataResolver",
    "LibratbagCollector",
    "LibratbagDeviceParser",
    "LibratbagProtocolExtractor",
    "OpenRGBCollector",
    "OpenRGBDetectorParser",
    "OpenRGBByteProtocolExtractor",
    "SignalRGBCollector",
    "SignalRGBPluginParser",
    "OpenRazerCollector",
    "OpenRazerDriverParser",
    "SolaarCollector",
    "SolaarDescriptorParser",
    "RivalcfgCollector",
    "RivalcfgProfileParser",
    "WootingCollector",
    "CorsairCkbCollector",
    "CorsairCkbParser",
    "LogitechDocsCollector",
    "ArtemisRGBNetCollector",
    "ArtemisRGBNetParser",
    "LinuxHIDCollector",
    "LinuxHIDParser",
    "SPECIALIZED_COLLECTORS",
    "get_collector_for_brand",
]
