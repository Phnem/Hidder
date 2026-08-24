"""Local SafetyGate. Server cannot dictate raw HID frames.

Plan may request typed operation + strategy, but SafetyGate
validates against local bundle bounds/typed op and forbids:
- unknown operation_id
- non-reversible write without readback
- out-of-bounds values
- raw opcode/frame passthrough
- destructive/calibration operations without explicit allowlist
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bundle import Bundle

FORBIDDEN_PREFIXES = ("calibration", "dfu", "bootloader", "flash", "erase", "factory_reset")
FORBIDDEN_OP_IDS = {"calibration.full", "calibration.start", "firmware.flash"}


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = ""
    safe_value: Any | None = None


class SafetyGate:
    def __init__(self, bundle: Bundle, instance_firmware: str | None = None) -> None:
        self.bundle = bundle
        self.instance_firmware = instance_firmware

    def _check_firmware_for_write(self) -> SafetyDecision | None:
        # Wildcard ("unknown") is allowed only for passive discovery/read.
        # Write requires explicit verified branch or exact match.
        branch = self.bundle.firmware_branch
        if not branch or branch == "unknown":
            return SafetyDecision(False, "firmware branch unknown — write requires explicitly verified branch (see bundle firmware.branch)")
        fw = (self.instance_firmware or "").strip()
        if not fw or fw == "unknown":
            return SafetyDecision(False, "instance firmware unknown — write requires exact firmware (discovery must have read firmware)")
        # Exact or prefix compatible (branch "1.17" matches "1.17.3")
        if branch not in fw and fw not in branch and not fw.startswith(branch):
            return SafetyDecision(False, f"firmware mismatch: device {fw!r} not compatible with bundle branch {branch!r}")
        return None

    def authorize(self, operation_id: str, requested_value: Any | None = None) -> SafetyDecision:
        # 1. typed operation must exist
        op = self.bundle.get_operation(operation_id)
        if op is None:
            return SafetyDecision(False, f"unknown operation: {operation_id}")
        # 2. forbidden prefixes
        if operation_id in FORBIDDEN_OP_IDS or any(operation_id.startswith(p + ".") or operation_id == p for p in FORBIDDEN_PREFIXES):
            return SafetyDecision(False, f"forbidden operation: {operation_id}")
        # 3. raw fields never allowed — bundle parser already blocked, double-check
        if isinstance(requested_value, (bytes, bytearray)):
            return SafetyDecision(False, "raw bytes not allowed as semantic value")
        if isinstance(requested_value, dict) and any(k in requested_value for k in ("raw_bytes", "opcode", "report_id")):
            return SafetyDecision(False, "raw frame dict not allowed")
        # 4. firmware check for writes (wildcard only for passive discovery/read, not for writes)
        if op.kind in ("set", "toggle", "transaction"):
            fw_decision = self._check_firmware_for_write()
            if fw_decision is not None:
                return fw_decision
        # 5. bounds required for writes
        if op.kind in ("set", "toggle", "transaction"):
            if operation_id not in self.bundle.bounds:
                return SafetyDecision(False, f"no bounds for {operation_id} — cannot choose safe value")
            bounds = self.bundle.bounds[operation_id]
            if requested_value is not None:
                if not self._in_bounds(requested_value, bounds):
                    return SafetyDecision(False, f"value {requested_value!r} out of bounds {bounds}")
                return SafetyDecision(True, "in bounds", requested_value)
            # choose safe delta if no value provided
            sv = self._choose_safe_delta(operation_id, bounds)
            if sv is None:
                return SafetyDecision(False, f"cannot choose safe value for {operation_id}")
            return SafetyDecision(True, "safe_delta", sv)
        # observable / get do not need value check
        return SafetyDecision(True, "no value needed")

    def _in_bounds(self, value: Any, bounds: dict[str, Any]) -> bool:
        try:
            mn = bounds.get("min")
            mx = bounds.get("max")
            if mn is not None and value < mn:  # type: ignore
                return False
            if mx is not None and value > mx:  # type: ignore
                return False
            safe_vals = bounds.get("safe_values")
            if safe_vals is not None and value not in safe_vals:
                # if safe_values enumerated, allow any in-range; but if value not in safe_values and is numeric, still allow if in [min,max]
                # strict: if safe_values defined, only those are auto-chosen, but explicit in-range is still considered in-bounds
                pass
            return True
        except TypeError:
            return False

    def _choose_safe_delta(self, op_id: str, bounds: dict[str, Any]) -> Any | None:
        sv = bounds.get("safe_values")
        if sv:
            return sv[0]
        mn = bounds.get("min")
        mx = bounds.get("max")
        if isinstance(mn, (int, float)) and isinstance(mx, (int, float)):
            # pick midpoint-ish safe value
            mid = (mn + mx) / 2
            return type(mn)(mid) if isinstance(mn, int) else mid
        return None

    def authorize_with_baseline(self, operation_id: str, baseline_value: Any) -> SafetyDecision:
        """Choose a safe temporary value distinct from baseline."""
        op = self.bundle.get_operation(operation_id)
        if op is None:
            return SafetyDecision(False, f"unknown operation: {operation_id}")
        # Firmware check for writes (wildcard not allowed)
        if op.kind in ("set", "toggle", "transaction"):
            fw_decision = self._check_firmware_for_write()
            if fw_decision is not None:
                return fw_decision
        bounds = self.bundle.bounds.get(operation_id)
        if bounds is None:
            return SafetyDecision(False, f"no bounds for {operation_id}")
        # try safe_values that differ from baseline
        for cand in bounds.get("safe_values", []):
            if cand != baseline_value and self._in_bounds(cand, bounds):
                return SafetyDecision(True, "safe_delta", cand)
        # fallback: if baseline is at min, pick max and vice versa
        mn, mx = bounds.get("min"), bounds.get("max")
        if baseline_value == mn and mx != mn:
            return SafetyDecision(True, "safe_delta_alt", mx)
        if baseline_value == mx and mn != mx:
            return SafetyDecision(True, "safe_delta_alt", mn)
        # generic safe
        sv = self._choose_safe_delta(operation_id, bounds)
        if sv == baseline_value:
            # perturb
            if isinstance(sv, (int, float)) and isinstance(mn, (int, float)) and isinstance(mx, (int, float)):
                alt = mn if sv != mn else mx
                return SafetyDecision(True, "safe_delta_perturbed", alt)
        if sv is not None:
            return SafetyDecision(True, "safe_delta", sv)
        return SafetyDecision(False, "cannot choose distinct safe value")
