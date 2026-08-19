"""Collectors registry and factory supporting all 87 canonical brands."""

from typing import Type, Optional
from ingest.brands.canonical import BrandDef, get_brand_by_slug, ALL_CANONICAL_BRANDS
from ingest.collectors.base import BaseCollector
from ingest.collectors.aula import AulaCollector
from ingest.collectors.atk_vxe import AtkCollector, VxeCollector, AtkVxeCollector
from ingest.collectors.epomaker import EpomakerCollector
from ingest.collectors.keychron import KeychronCollector
from ingest.collectors.generic_collector import GenericBrandCollector

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
    "SPECIALIZED_COLLECTORS",
    "get_collector_for_brand",
]
