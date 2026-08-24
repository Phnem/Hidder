"""Validation Certificate builder. Probe emits evidence; never Rank A."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import TOOL_VERSION, SCHEMA_CERTIFICATE
from .evidence import TestEvidence
from .identity import IdentityVerdict


@dataclass
class Certificate:
    schema: str = SCHEMA_CERTIFICATE
    tool_version: str = TOOL_VERSION
    identity: dict[str, Any] = field(default_factory=dict)
    bundle: dict[str, Any] = field(default_factory=dict)
    baseline_hash: str = ""
    final_hash: str = ""
    baseline_restored: bool = False
    tests: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    verdict: str = "FAIL"  # PASS / FAIL
    knowledge_revision: str = ""
    timings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tool_version": self.tool_version,
            "identity": self.identity,
            "bundle": self.bundle,
            "knowledge_revision": self.knowledge_revision,
            "timings": self.timings,
            "baseline_hash": self.baseline_hash,
            "final_hash": self.final_hash,
            "baseline_restored": self.baseline_restored,
            "tests": self.tests,
            "contradictions": self.contradictions,
            "coverage": self.coverage,
            "verdict": self.verdict,
            "quorum": {"eligible_for": "none"},  # Probe never promotes
        }

    def write(self, path: Any) -> None:
        from pathlib import Path
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_certificate(
    identity_verdict: IdentityVerdict,
    bundle: Any,
    baseline_hash: str,
    final_hash: str,
    baseline_restored: bool,
    tests: list[TestEvidence],
    contradictions: list[str],
    coverage: dict[str, Any],
    knowledge_revision: str = "",
    timings: dict[str, Any] | None = None,
) -> Certificate:
    verdict = "PASS" if (identity_verdict.passed and baseline_restored and all(t.status == "PASS" for t in tests) and not contradictions) else "FAIL"
    # If any test BLOCKED due to missing baseline etc, verdict is FAIL per Final Restore Gate spec
    # unless mandatory coverage still 1.0 — but fail-closed means not PASS
    if any(t.status in ("FAIL", "BLOCKED") for t in tests):
        verdict = "FAIL"
    if not baseline_restored:
        verdict = "FAIL"
    if not knowledge_revision:
        # try bundle's own knowledge_revision field
        try:
            knowledge_revision = bundle.raw.get("knowledge_revision", "") or ""
        except Exception:
            knowledge_revision = ""

    cert = Certificate(
        identity={
            "product": identity_verdict.product,
            "vid": bundle.product.vid,
            "pid": bundle.product.pid,
            "family": identity_verdict.family,
            "firmware": identity_verdict.firmware,
            "connection": identity_verdict.connection,
            "descriptor_hash": identity_verdict.descriptor_hash,
            "exact_identity_pass": identity_verdict.passed,
        },
        bundle={"id": bundle.id, "version": bundle.version, "hash": bundle.hash},
        knowledge_revision=knowledge_revision,
        timings=timings or {},
        baseline_hash=baseline_hash,
        final_hash=final_hash,
        baseline_restored=baseline_restored,
        tests=[_evidence_to_dict(t) for t in tests],
        contradictions=contradictions,
        coverage=coverage,
        verdict=verdict,
    )
    return cert


def _evidence_to_dict(ev: TestEvidence) -> dict[str, Any]:
    return {
        "operation": ev.operation,
        "safe_command_id": ev.safe_command_id,
        "firmware_branch": ev.firmware_branch,
        "connection_mode": ev.connection_mode,
        "baseline_value": ev.baseline_value,
        "temporary_value": ev.temporary_value,
        "transport_result": ev.transport_result,
        "readback": ev.readback,
        "readback_matched": ev.readback_matched,
        "observable_result": ev.observable_result,
        "observable_pass": ev.observable_pass,
        "rollback_result": ev.rollback_result,
        "rollback_readback": ev.rollback_readback,
        "rollback_matched": ev.rollback_matched,
        "timing_ms": ev.timing_ms,
        "bundle_hash": ev.bundle_hash,
        "evidence_strength": ev.evidence_strength,
        "status": ev.status,
        "error": ev.error,
    }
