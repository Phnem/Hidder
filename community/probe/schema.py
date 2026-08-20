"""Peripheral Community Research Probe — Data Schema Specification.

Schema: peripheral.community-observation/1
Strictly enforces privacy, passive observation provenance, marker window attribution,
pairwise A -> B -> A change/restore correlation, and checksum candidate separation.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "peripheral.community-observation/1"
TOOL_VERSION = "0.3.0"


@dataclass
class DeviceIdentity:
    category: str  # "keyboard" | "mouse"
    user_reported_model: str = ""  # What user typed (e.g. "AULA HERO 84 HE")
    detected_product_string: str = ""  # Product string if genuine commercial name
    detected_manufacturer_string: str = ""  # Manufacturer string (e.g. "AULA")
    resolved_model: str | None = None  # Resolved model name (None if only generic driver strings exist)
    resolved_model_confidence: str = "unresolved"  # "unresolved" | "registry_verified" | "user_reported"
    keyboard_type: str | None = None  # "mechanical" | "hall_effect" | "unknown" | None
    vid: str = ""  # Hex string "0x372E"
    pid: str = ""  # Hex string "0x103E"
    release_number: str = ""
    hid_collections: list[dict[str, Any]] = field(default_factory=list)
    report_ids: list[int] = field(default_factory=list)
    connection_type: str = "usb"  # "usb" | "2.4g_dongle" | "bluetooth" | "unknown"

    @property
    def model_name(self) -> str:
        """Backward compatibility helper."""
        return self.resolved_model or self.user_reported_model or self.detected_product_string or "Unknown Device"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["model_name"] = self.model_name
        return d


@dataclass
class VendorSoftwareInfo:
    process_basename: str = ""  # e.g. "msedge.exe", "AULA.exe" - NEVER full path
    file_version: str | None = None
    publisher: str | None = None
    architecture: str = "x64"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GuidedAction:
    action_id: str
    category: str
    instruction: str
    expected_semantic: dict[str, Any]
    started_at: float
    finished_at: float = 0.0
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed" | "skipped_by_user" | "timeout" | "aborted"
    skip_reason: str | None = None
    restore_attempted: bool = False
    restore_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.finished_at > self.started_at:
            d["duration_seconds"] = round(self.finished_at - self.started_at, 3)
        return d


@dataclass
class TransportObservation:
    timestamp: float
    process_basename: str
    api: str  # "sendReport" | "sendFeatureReport" | "receiveFeatureReport" | "inputreport" | "WriteFile"
    direction: str  # "out" | "in" | "feature_out" | "feature_in"
    report_id: int
    bytes_hex: str
    byte_length: int
    action_id: str | None = None
    device_id: str | None = None
    repeat_count: int = 1
    first_seen: float | None = None
    last_seen: float | None = None
    capture_source: str = "webhid_api_observer"  # "webhid_api_observer" | "webhid_inputreport" | "win32_api_hook"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.first_seen is None:
            d["first_seen"] = self.timestamp
        if self.last_seen is None:
            d["last_seen"] = self.timestamp
        return d


@dataclass
class TransitionDelta:
    offset: int
    before: str | None
    changed: str
    restored: str | None
    field_role: str = "semantic_field"  # "semantic_field" | "checksum_candidate" | "header" | "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CorrelationCandidate:
    semantic: str
    change_action_id: str
    restore_action_id: str | None = None
    candidate_reports: list[int] = field(default_factory=list)
    changed_offsets: list[int] = field(default_factory=list)
    semantic_offsets: list[int] = field(default_factory=list)
    checksum_offsets: list[int] = field(default_factory=list)
    baseline_reports: list[str] = field(default_factory=list)
    baseline_available: bool = False
    change_reports: list[str] = field(default_factory=list)
    restore_reports: list[str] = field(default_factory=list)
    transitions: list[TransitionDelta] = field(default_factory=list)
    restore_matches_original: bool = False
    confidence: str = "CommunityGuidedObservation"
    notes: str | None = None

    # Backward compatibility properties
    @property
    def action_id(self) -> str:
        return self.change_action_id

    @property
    def before_values(self) -> list[str]:
        return self.baseline_reports

    @property
    def after_values(self) -> list[str]:
        return self.change_reports

    @property
    def restored_values(self) -> list[str]:
        return self.restore_reports

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic": self.semantic,
            "change_action_id": self.change_action_id,
            "restore_action_id": self.restore_action_id,
            "candidate_reports": self.candidate_reports,
            "changed_offsets": self.changed_offsets,
            "semantic_offsets": self.semantic_offsets,
            "checksum_offsets": self.checksum_offsets,
            "baseline_reports": self.baseline_reports,
            "baseline_available": self.baseline_available,
            "change_reports": self.change_reports,
            "restore_reports": self.restore_reports,
            "transitions": [t.to_dict() if hasattr(t, "to_dict") else asdict(t) for t in self.transitions],
            "restore_matches_original": self.restore_matches_original,
            "confidence": self.confidence,
            "notes": self.notes,
            # Backward compatibility fields
            "action_id": self.action_id,
            "before_values": self.before_values,
            "after_values": self.after_values,
            "restored_values": self.restored_values,
        }


@dataclass
class QualityScore:
    score: int  # 0..100
    rating: str  # "Excellent protocol evidence" | "Guided actions only — no protocol traffic"
    device_bound: bool = True
    vendor_process_bound: bool = True
    traffic_observed: bool = True
    idle_baseline_captured: bool = True
    change_restore_pairs_count: int = 0
    known_input_actions_count: int = 0
    analog_actions_count: int = 0
    dropped_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaptureMetadata:
    mechanism: str = "browser_webhid_api_observer"  # "browser_webhid_api_observer" | "win32_user_mode_api_hook"
    observer_attached: bool = False
    target_process: str = ""
    browser: str | None = None
    page_origin: str | None = None
    hooks_requested: list[str] = field(default_factory=list)
    hooks_installed: list[str] = field(default_factory=list)
    real_device_session_observed: bool = True
    device_handle_bound: bool = False
    started_at: str = ""
    ended_at: str = ""
    observer_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CommunityObservationBundle:
    schema: str = SCHEMA_VERSION
    tool_version: str = TOOL_VERSION
    submission_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    completed: bool = True
    is_demo: bool = False
    
    device: DeviceIdentity = field(default_factory=lambda: DeviceIdentity(category="keyboard"))
    software: VendorSoftwareInfo = field(default_factory=VendorSoftwareInfo)
    capture: CaptureMetadata = field(default_factory=CaptureMetadata)
    guided_actions: list[GuidedAction] = field(default_factory=list)
    transport_observations: list[TransportObservation] = field(default_factory=list)
    correlations: list[CorrelationCandidate] = field(default_factory=list)
    quality: QualityScore = field(default_factory=lambda: QualityScore(score=0, rating=""))
    privacy_scrubbed: bool = True
    payload_sha256: str = ""

    def compute_sha256(self) -> str:
        data = self.to_dict()
        data["payload_sha256"] = ""
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tool_version": self.tool_version,
            "submission_id": self.submission_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "completed": self.completed,
            "is_demo": self.is_demo,
            "device": self.device.to_dict(),
            "software": self.software.to_dict(),
            "capture": self.capture.to_dict(),
            "guided_actions": [a.to_dict() for a in self.guided_actions],
            "transport_observations": [t.to_dict() for t in self.transport_observations],
            "correlations": [c.to_dict() for c in self.correlations],
            "quality": self.quality.to_dict(),
            "privacy_scrubbed": self.privacy_scrubbed,
            "payload_sha256": self.payload_sha256,
        }
