"""Typed, serializable evidence-first data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ConfidenceClass(StrEnum):
    VERIFIED_VENDOR_ARTIFACT = "VerifiedVendorArtifact"
    VERIFIED_STRUCTURED_MAPPING = "VerifiedStructuredMapping"
    VERIFIED_SOURCE_CODE = "VerifiedSourceCode"
    VERIFIED_DYNAMIC_VENDOR_SOFTWARE = "VerifiedDynamicVendorSoftware"
    HARDWARE_VERIFIED_EXCHANGE = "HardwareVerifiedExchange"
    INFERRED_STRONG = "InferredStrong"
    INFERRED_WEAK = "InferredWeak"
    ASSUMED = "Assumed"
    UNSUPPORTED = "Unsupported"
    CONTRADICTED = "Contradicted"


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    original_filename: str
    source_type: str
    sha256: str
    size: int
    detected_type: str
    retrieved_at: str
    source_url: str | None = None
    parent_artifact: str | None = None
    schema: str = "peripheral.artifact/1"

    def json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    artifact_sha256: str
    extractor: str
    extractor_version: str
    kind: str
    value: Any
    source_path: str
    confidence: ConfidenceClass
    schema: str = "peripheral.observation/1"

    def json(self) -> dict[str, Any]:
        result = asdict(self)
        result["confidence"] = self.confidence.value
        return result


@dataclass
class ProtocolCandidate:
    family_candidate: str | None = None
    identity: list[dict[str, Any]] = field(default_factory=list)
    topology: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    dangerous_commands: list[dict[str, Any]] = field(default_factory=list)
    firmware_branches: list[dict[str, Any]] = field(default_factory=list)
    model_overrides: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    schema: str = "peripheral.protocol-candidate/1"

    def json(self) -> dict[str, Any]:
        return asdict(self)
