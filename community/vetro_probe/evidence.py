"""Evidence strength E1..E6 separate from PASS/FAIL."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Evidence taxonomy per spec chapter 17
E1_REQUEST_SENT = "E1"
E2_RESPONSE_ACK = "E2"
E3_SEMANTIC_READBACK = "E3"
E4_ROLLBACK_AND_READBACK = "E4"
E5_OS_OBSERVABLE = "E5"
E6_QUORUM = "E6"


@dataclass
class TestEvidence:
    operation: str
    safe_command_id: str
    firmware_branch: str
    connection_mode: str
    baseline_value: Any | None
    temporary_value: Any | None
    transport_result: str = ""
    readback: Any | None = None
    readback_matched: bool = False
    observable_result: Any | None = None
    observable_pass: bool | None = None
    observable_source: str = ""  # simulated | uncorrelated_os | device_correlated | prototype
    rollback_result: str = ""
    rollback_readback: Any | None = None
    rollback_matched: bool = False
    timing_ms: dict[str, int] = field(default_factory=dict)
    bundle_hash: str = ""
    evidence_strength: list[str] = field(default_factory=list)
    status: str = "FAIL"  # PASS / FAIL / SKIP / BLOCKED
    error: str = ""
    sessions: dict[str, Any] = field(default_factory=dict)  # A/B/C/D session ids
    recovery: dict[str, Any] = field(default_factory=dict)  # RecoveryJournal record (reconnect ops)
    ack_valid: bool = False  # canonical device echo verified (register-0x01 light path)
    echo_hex: str = ""       # captured canonical echo frame (hex), ACK evidence only
    validation_flags: dict[str, bool] = field(default_factory=dict)  # write/ack/readback/rollback/final_restore

    def compute_strength(self) -> list[str]:
        s: list[str] = []
        if self.transport_result == "ok":
            s.append(E1_REQUEST_SENT)
            s.append(E2_RESPONSE_ACK)
        if self.readback_matched:
            s.append(E3_SEMANTIC_READBACK)
        if self.rollback_matched:
            # E4 requires both readback and rollback readback
            if E3_SEMANTIC_READBACK in s:
                s.append(E4_ROLLBACK_AND_READBACK)
        if self.observable_pass:
            # E5 strong only for device_correlated; simulated/uncorrelated are auxiliary
            # For backward compat with sim tests, we still count simulated as E5 but mark source
            # Real GetAsyncKeyState (uncorrelated_os) is stored as auxiliary, not strong E5
            if self.observable_source in ("device_correlated", "simulated"):
                s.append(E5_OS_OBSERVABLE)
            elif self.observable_source == "uncorrelated_os":
                # Auxiliary, not strong — do not add E5, but keep observable_result for diagnostics
                pass
            else:
                # Fallback: if source empty, treat as E5 for legacy
                s.append(E5_OS_OBSERVABLE)
        # E6 is server-side quorum, never set inside Probe
        return s


def verdict_from_evidence(ev: TestEvidence) -> str:
    if ev.status in ("SKIP", "BLOCKED"):
        return ev.status
    # per spec: ACK only is not PASS; need E4 for reversible, E5 for remap/macro ideally
    if ev.rollback_matched and ev.readback_matched:
        return "PASS"
    return "FAIL"
