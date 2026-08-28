"""Device Validation Certificate for Guided Hardware Validation Flow.

Schema: vetro.hardware-validation-certificate.v2

Durable proof that an exact device scope has completed a full hardware validation
session (typed allowlisted operations -> GET baseline -> SET test -> observable ->
rollback -> GET baseline verification).

Promotes exact compatible hardware to VALIDATED / plug-and-play on subsequent launches
without repeated validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_DEVICE_CERTIFICATE = "vetro.hardware-validation-certificate.v2"


@dataclass
class DeviceValidationCertificate:
    schema: str = SCHEMA_DEVICE_CERTIFICATE
    certificate_id: str = ""
    created_at: float = field(default_factory=time.time)
    terminal_verdict: str = "COMPLETE_PASS"  # COMPLETE_PASS / FAILED
    
    # Strict Device Identity Scope
    vendor: str = ""
    model: str = ""
    variant: str = ""
    vid: str = ""
    pid: str = ""
    descriptor_hash: str = ""
    firmware_branch: str = ""
    connection_mode: str = ""
    protocol_family: str = ""
    knowledge_revision: str = ""
    
    # Build & Provenance Metadata
    app_version: str = "0.3.0"
    engine_version: str = "0.3.0"
    build_commit: str = ""
    
    # Validation Execution Details
    validated_capability_groups: list[str] = field(default_factory=list)
    individual_operations: list[dict[str, Any]] = field(default_factory=list)
    baseline_hashes: dict[str, str] = field(default_factory=dict)
    observables: list[dict[str, Any]] = field(default_factory=list)
    rollback_results: dict[str, bool] = field(default_factory=dict)
    final_state_verified: bool = False
    evidence_hashes: list[str] = field(default_factory=list)
    representative_coverage_explanation: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "certificate_id": self.certificate_id,
            "created_at": self.created_at,
            "terminal_verdict": self.terminal_verdict,
            "identity": {
                "vendor": self.vendor,
                "model": self.model,
                "variant": self.variant,
                "vid": self.vid,
                "pid": self.pid,
                "descriptor_hash": self.descriptor_hash,
                "firmware_branch": self.firmware_branch,
                "connection_mode": self.connection_mode,
                "protocol_family": self.protocol_family,
                "knowledge_revision": self.knowledge_revision,
            },
            "build": {
                "app_version": self.app_version,
                "engine_version": self.engine_version,
                "build_commit": self.build_commit,
            },
            "validation": {
                "validated_capability_groups": self.validated_capability_groups,
                "individual_operations": self.individual_operations,
                "baseline_hashes": self.baseline_hashes,
                "observables": self.observables,
                "rollback_results": self.rollback_results,
                "final_state_verified": self.final_state_verified,
                "evidence_hashes": self.evidence_hashes,
                "representative_coverage_explanation": self.representative_coverage_explanation,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceValidationCertificate:
        ident = data.get("identity", {})
        bld = data.get("build", {})
        val = data.get("validation", {})
        return cls(
            schema=data.get("schema", SCHEMA_DEVICE_CERTIFICATE),
            certificate_id=data.get("certificate_id", ""),
            created_at=data.get("created_at", 0.0),
            terminal_verdict=data.get("terminal_verdict", "FAILED"),
            vendor=ident.get("vendor", ""),
            model=ident.get("model", ""),
            variant=ident.get("variant", ""),
            vid=ident.get("vid", ""),
            pid=ident.get("pid", ""),
            descriptor_hash=ident.get("descriptor_hash", ""),
            firmware_branch=ident.get("firmware_branch", ""),
            connection_mode=ident.get("connection_mode", ""),
            protocol_family=ident.get("protocol_family", ""),
            knowledge_revision=ident.get("knowledge_revision", ""),
            app_version=bld.get("app_version", "0.3.0"),
            engine_version=bld.get("engine_version", "0.3.0"),
            build_commit=bld.get("build_commit", ""),
            validated_capability_groups=val.get("validated_capability_groups", []),
            individual_operations=val.get("individual_operations", []),
            baseline_hashes=val.get("baseline_hashes", {}),
            observables=val.get("observables", []),
            rollback_results=val.get("rollback_results", {}),
            final_state_verified=val.get("final_state_verified", False),
            evidence_hashes=val.get("evidence_hashes", []),
            representative_coverage_explanation=val.get("representative_coverage_explanation", {}),
        )

    def is_valid_for(
        self,
        *,
        vid: str,
        pid: str,
        descriptor_hash: str,
        firmware_branch: str,
        connection_mode: str,
        knowledge_revision: str = "",
    ) -> bool:
        """Verify if certificate matches current physical device identity and knowledge."""
        if self.terminal_verdict != "COMPLETE_PASS":
            return False
        if not self.final_state_verified:
            return False
        if self.vid.lower() != vid.lower() or self.pid.lower() != pid.lower():
            return False
        if self.descriptor_hash and descriptor_hash and self.descriptor_hash != descriptor_hash:
            return False
        if self.firmware_branch and firmware_branch and firmware_branch != "unknown":
            if not firmware_branch.startswith(self.firmware_branch) and not self.firmware_branch.startswith(firmware_branch):
                return False
        if self.connection_mode and connection_mode and self.connection_mode != "any":
            if self.connection_mode != connection_mode:
                return False
        if knowledge_revision and self.knowledge_revision:
            if knowledge_revision != self.knowledge_revision:
                return False
        return True


class CertificateStore:
    """Manages persistent storage and retrieval of validation certificates."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            self.base_dir = Path(appdata) / "Vetro" / "certificates"
        else:
            self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _cert_path(self, vid: str, pid: str, firmware: str) -> Path:
        clean_vid = vid.replace("0x", "").upper()
        clean_pid = pid.replace("0x", "").upper()
        clean_fw = "".join(c for c in (firmware or "default") if c.isalnum() or c in ("-", "_"))
        return self.base_dir / f"cert_{clean_vid}_{clean_pid}_{clean_fw}.json"

    def save(self, cert: DeviceValidationCertificate) -> Path:
        if not cert.certificate_id:
            digest = hashlib.sha256(
                f"{cert.vid}:{cert.pid}:{cert.firmware_branch}:{cert.descriptor_hash}:{cert.created_at}".encode()
            ).hexdigest()[:12]
            cert.certificate_id = f"cert-{digest}"
        path = self._cert_path(cert.vid, cert.pid, cert.firmware_branch)
        path.write_text(json.dumps(cert.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def find_matching(
        self,
        *,
        vid: str,
        pid: str,
        descriptor_hash: str = "",
        firmware_branch: str = "",
        connection_mode: str = "",
        knowledge_revision: str = "",
    ) -> DeviceValidationCertificate | None:
        path = self._cert_path(vid, pid, firmware_branch)
        if not path.is_file():
            # Try finding any cert for this VID/PID
            clean_vid = vid.replace("0x", "").upper()
            clean_pid = pid.replace("0x", "").upper()
            candidates = list(self.base_dir.glob(f"cert_{clean_vid}_{clean_pid}_*.json"))
            if not candidates:
                return None
            path = candidates[0]

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cert = DeviceValidationCertificate.from_dict(data)
            if cert.is_valid_for(
                vid=vid,
                pid=pid,
                descriptor_hash=descriptor_hash,
                firmware_branch=firmware_branch,
                connection_mode=connection_mode,
                knowledge_revision=knowledge_revision,
            ):
                return cert
        except Exception:
            pass
        return None
