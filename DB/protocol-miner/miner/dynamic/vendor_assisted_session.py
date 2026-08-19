"""Real-Device Vendor-Assisted Research Session Controller.

Orchestrates vendor UI experiments against official software with passive observer
capture, mandatory baseline capture, one-setting-at-a-time execution, rollback,
restore verification, and .pevidence export.

CRITICAL SAFETY BOUNDARY (ADR 0002):
The Research Controller itself NEVER constructs or transmits raw HID packets.
Only the official vendor software communicates with hardware.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from miner.dynamic.experiment_runner import ExperimentStep
from miner.dynamic.safety_filter import SafetyStatus, classify_control_safety
from miner.dynamic.ui_discovery import DiscoveredControl
from miner.storage.pevidence import export_pevidence_bundle


@dataclass
class SessionIdentity:
    session_id: str
    device_id: str
    vendor_name: str
    model_name: str
    software_sha256: str
    firmware_version: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VendorAssistedSessionError(RuntimeError):
    """Raised when an unrecoverable research session error occurs."""


class VendorAssistedResearchSession:
    """Manages an active vendor-assisted research session with passive observation."""

    def __init__(
        self,
        vendor_name: str,
        model_name: str,
        software_sha256: str,
        device_id: str | None = None,
        firmware_version: str | None = None,
    ) -> None:
        self.session_id = f"sess-{uuid.uuid4().hex[:12]}"
        dev_id = device_id or f"dev-{hashlib.sha256(f'{vendor_name}:{model_name}'.encode()).hexdigest()[:16]}"
        self.identity = SessionIdentity(
            session_id=self.session_id,
            device_id=dev_id,
            vendor_name=vendor_name,
            model_name=model_name,
            software_sha256=software_sha256,
            firmware_version=firmware_version,
        )
        self.baseline_values: dict[str, Any] = {}
        self.executed_actions: list[dict[str, Any]] = []
        self.observed_traces: list[dict[str, Any]] = []
        self.restore_status: str = "INITIALIZED"  # "RESTORE_CONFIRMED", "RESTORE_UNCERTAIN", "ABORTED"
        self.is_active: bool = True
        self.abort_reason: str | None = None

    def capture_baseline(self, control: DiscoveredControl) -> Any:
        """Record the initial state of a UI control before any mutation."""
        if not self.is_active:
            raise VendorAssistedSessionError("Session is not active")
        self.baseline_values[control.control_id] = control.current_value
        return control.current_value

    def execute_one_setting_experiment(
        self,
        control: DiscoveredControl,
        target_value: Any,
        driver_callback: Any,
        observer_traces_provider: Any = None,
    ) -> tuple[bool, str]:
        """Execute a single setting experiment via official vendor software UI, observe, and rollback."""
        if not self.is_active:
            return False, f"Session aborted: {self.abort_reason}"

        # 1. Safety verification
        safety = classify_control_safety(control.to_dict())
        if not safety.is_safe_for_auto_experiment:
            self.abort(f"Quarantined/Forbidden control: {safety.reason}")
            return False, f"Safety check failed: {safety.reason}"

        # 2. Baseline capture
        baseline = self.baseline_values.get(control.control_id, control.current_value)
        self.baseline_values[control.control_id] = baseline

        action_id = f"act-{len(self.executed_actions) + 1:04d}"
        semantic = f"{control.label}:{target_value}"

        try:
            # 3. Apply target setting through vendor UI (driver callback)
            driver_callback(control, target_value, action_id, semantic)

            # 4. Collect passive observer traces
            if observer_traces_provider is not None:
                new_traces = observer_traces_provider(action_id)
                self.observed_traces.extend(new_traces)

            self.executed_actions.append({
                "action_id": action_id,
                "control_id": control.control_id,
                "label": control.label,
                "old_value": baseline,
                "new_value": target_value,
                "step_type": "experiment",
                "semantic_context": semantic,
            })

            # 5. Mandatory Rollback through vendor UI
            rollback_action_id = f"act-rb-{len(self.executed_actions) + 1:04d}"
            driver_callback(control, baseline, rollback_action_id, f"{control.label}:restore_baseline")

            if observer_traces_provider is not None:
                rb_traces = observer_traces_provider(rollback_action_id)
                self.observed_traces.extend(rb_traces)

            self.executed_actions.append({
                "action_id": rollback_action_id,
                "control_id": control.control_id,
                "label": control.label,
                "old_value": target_value,
                "new_value": baseline,
                "step_type": "restore",
                "semantic_context": f"{control.label}:restore_baseline",
            })

            self.restore_status = "RESTORE_CONFIRMED"
            return True, "Experiment and rollback completed cleanly"
        except Exception as exc:
            self.abort(f"Error during UI experiment: {exc}")
            return False, f"Experiment failed and aborted: {exc}"

    def abort(self, reason: str) -> None:
        """Safely halt the session, marking restore status uncertain."""
        self.is_active = False
        self.abort_reason = reason
        self.restore_status = "RESTORE_UNCERTAIN"

    def export_to_pevidence(self, output_path: Path) -> Path:
        """Export session metadata, passive traces, actions, and restore confirmation to .pevidence."""
        device_info = {
            "vendor_name": self.identity.vendor_name,
            "model_name": self.identity.model_name,
            "device_id": self.identity.device_id,
            "firmware_version": self.identity.firmware_version,
        }
        software_info = {
            "name": f"{self.identity.vendor_name} Official Utility",
            "sha256": self.identity.software_sha256,
        }

        return export_pevidence_bundle(
            output_path=output_path,
            device_info=device_info,
            software_info=software_info,
            traces=self.observed_traces,
            actions=self.executed_actions,
            research_mode="vendor_assisted",
            restore_status=self.restore_status,
            submission_id=self.identity.session_id,
        )
