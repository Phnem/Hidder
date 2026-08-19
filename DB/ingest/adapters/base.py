"""Base discovery adapter interfaces and models."""

from typing import Optional, NamedTuple
from pydantic import BaseModel, Field

from ingest.brands.canonical import BrandDef, DiscoveryStatus
from ingest.network.fetcher import TieredFetcher
from ingest.normalize.evidence import RawProduct, RawArtifact


class AdapterDiscoveryResult(NamedTuple):
    products: list[RawProduct]
    artifacts: list[RawArtifact]
    status: DiscoveryStatus
    blocking_reason: Optional[str] = None


class BaseAdapter:
    def __init__(self, fetcher: TieredFetcher):
        self.fetcher = fetcher

    def discover(self, brand: BrandDef) -> AdapterDiscoveryResult:
        raise NotImplementedError
