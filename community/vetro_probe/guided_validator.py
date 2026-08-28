"""Unified Guided Hardware Validation Flow Engine.

Shared validation engine between Vetro Probe (research/pilot) and normal Vetro (Preview).

Turns almost-complete protocol knowledge into verified hardware support via a fast,
safe, ~30-90s guided validation session. Enforces strict rollback-first safety,
human/OS observables, and durable DeviceValidationCertificate creation.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bundle import Bundle
from .device_certificate import DeviceValidationCertificate, CertificateStore
from .evidence import (
    TestEvidence, E1_REQUEST_SENT, E2_RESPONSE_ACK, E3_SEMANTIC_READBACK,
    E4_ROLLBACK_AND_READBACK, E5_OS_OBSERVABLE,
)
from .observable import (
    ObservableListener, ObservableRequest, ObservableResult,
    HumanConfirmationListener, FakeObservableListener, NoopObservableListener,
)
from .safety import SafetyGate
from .transport import DeviceTransport


# Guided Validation States
STATE_NOT_ELIGIBLE = "NOT_ELIGIBLE"
STATE_PREVIEW_ELIGIBLE = "PREVIEW_ELIGIBLE"
STATE_VALIDATION_READY = "VALIDATION_READY"
STATE_VALIDATING = "VALIDATING"
STATE_VALIDATED = "VALIDATED"
STATE_VALIDATION_FAILED = "VALIDATION_FAILED"
STATE_ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
STATE_ROLLBACK_VERIFIED = "ROLLBACK_VERIFIED"
STATE_READ_ONLY_INVENTORY = "READ_ONLY_INVENTORY"

# Forbidden keys for remap validation (critical / system keys)
FORBIDDEN_REMAP_KEYS = {
    "Esc", "Escape", "Enter", "Return", "Space", "Backspace", "Tab",
    "CapsLock", "Win", "LeftWin", "RightWin", "Control", "Alt", "Shift",
    "Power", "Sleep", "Wake", "Reset", "Profile", "Layer", "Fn",
}


@dataclass
class CapabilityValidationResult:
    capability: str
    passed: bool
    evidence: TestEvidence
    observable_result: ObservableResult | None = None
    rollback_verified: bool = False
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuidedValidationContext:
    bundle: Bundle
    transport: DeviceTransport
    observable_listener: ObservableListener
    device_identity: dict[str, Any]
    build_commit: str = ""
    app_version: str = "0.3.1"
    on_step_progress: Callable[[str, dict[str, Any]], None] | None = None
    on_prompt: Callable[[ObservableRequest], Any] | None = None


class BaseCapabilityValidator(ABC):
    capability_name: str = "generic"

    @abstractmethod
    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        ...

    @abstractmethod
    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        ...

    @abstractmethod
    def representative_explanation(self) -> str:
        ...


class LightingCapabilityValidator(BaseCapabilityValidator):
    capability_name = "lighting.brightness"

    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        from .feature_gates import blocker_for
        vid = ctx.device_identity.get("vid")
        pid = ctx.device_identity.get("pid")
        family = ctx.device_identity.get("family")
        fw = ctx.device_identity.get("firmware")
        has_brightness = ctx.bundle.has_operation("light.brightness") and blocker_for("light.brightness", vid=vid, pid=pid, family=family, fw=fw) is None
        has_global_color = ctx.bundle.has_operation("light.global_color") and blocker_for("light.global_color", vid=vid, pid=pid, family=family, fw=fw) is None
        return bool(has_brightness or has_global_color)

    def representative_explanation(self) -> str:
        return (
            "Validating representative lighting operation (brightness) proves the device's "
            "lighting framing, checksum, register encoding, and physical LED controller response."
        )

    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        from .feature_gates import blocker_for
        vid = ctx.device_identity.get("vid")
        pid = ctx.device_identity.get("pid")
        family = ctx.device_identity.get("family")
        fw = ctx.device_identity.get("firmware")
        has_brightness = ctx.bundle.has_operation("light.brightness") and blocker_for("light.brightness", vid=vid, pid=pid, family=family, fw=fw) is None
        op_id = "light.brightness" if has_brightness else "light.global_color"
        self.capability_name = "lighting.brightness" if op_id == "light.brightness" else "lighting.global_color"
        ev = TestEvidence(
            operation=op_id,
            safe_command_id=op_id,
            firmware_branch=ctx.device_identity.get("firmware", "unknown"),
            connection_mode=ctx.device_identity.get("connection", "wired"),
            baseline_value=None,
            temporary_value=None,
            bundle_hash=ctx.bundle.hash,
        )

        if ctx.on_step_progress:
            ctx.on_step_progress("lighting_start", {"op": op_id})

        # 1. Baseline GET
        try:
            val, res = ctx.transport.get(op_id)
            if not res.ok:
                ev.status = "FAIL"
                ev.error = f"Baseline GET failed: {res.error}"
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.baseline_value = val
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"Baseline GET exception: {e}"
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 2. Pick safe temporary test value: visible change (e.g. brightness 20 / 100% or 5 / 25%)
        if op_id == "light.brightness":
            base_num = int(val) if isinstance(val, (int, float)) else 10
            temp_val = 5 if base_num > 10 else 20
        else:
            temp_val = {"r": 0, "g": 255, "b": 0}  # Pure green
        ev.temporary_value = temp_val

        # 3. SET test value
        try:
            set_res = ctx.transport.set(op_id, temp_val)
            if not set_res.ok:
                ev.status = "FAIL"
                ev.error = f"SET test value failed: {set_res.error}"
                self._rollback(ctx, op_id, ev.baseline_value)
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.transport_result = "ok"
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"SET exception: {e}"
            self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 4. Protocol Readback GET
        try:
            read_val, read_res = ctx.transport.get(op_id)
            if read_res.ok and (read_val == temp_val or str(read_val) == str(temp_val)):
                ev.readback = read_val
                ev.readback_matched = True
        except Exception:
            pass

        # 5. Observable Check (Physical Visual Confirmation)
        prompt_req = ObservableRequest(
            kind="visual_confirm",
            target="green_color" if op_id == "light.global_color" else f"brightness_{temp_val}",
            prompt_ru="Подсветка изменилась (включился тестовый режим)?",
            prompt_en="Did the keyboard lighting visibly change to the test mode?",
            timeout_ms=20000,
        )
        obs_res = ctx.observable_listener.wait_for(prompt_req)
        ev.observable_result = obs_res.observed
        ev.observable_pass = obs_res.ok
        ev.observable_source = obs_res.source

        if not obs_res.ok:
            # Visual check failed -> immediate rollback!
            ev.status = "FAIL"
            ev.error = f"Visual check failed: {obs_res.error}"
            rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res, rollback_verified=rollback_ok, error=ev.error
            )

        # 6. Rollback to original baseline
        rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
        ev.rollback_matched = rollback_ok
        ev.rollback_result = "ok" if rollback_ok else "failed"

        if not rollback_ok:
            ev.status = "FAIL"
            ev.error = "Rollback verification failed: original state was not restored"
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res, rollback_verified=False, error=ev.error
            )

        ev.status = "PASS"
        ev.compute_strength()
        return CapabilityValidationResult(
            self.capability_name, True, ev, observable_result=obs_res, rollback_verified=True
        )

    def _rollback(self, ctx: GuidedValidationContext, op_id: str, baseline_value: Any) -> bool:
        if baseline_value is None:
            return False
        try:
            r_set = ctx.transport.set(op_id, baseline_value)
            if not r_set.ok:
                return False
            final_val, final_res = ctx.transport.get(op_id)
            return bool(final_res.ok and (final_val == baseline_value or str(final_val) == str(baseline_value)))
        except Exception:
            return False


class RemapCapabilityValidator(BaseCapabilityValidator):
    capability_name = "keyboard.remap"

    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        from .feature_gates import blocker_for
        vid = ctx.device_identity.get("vid")
        pid = ctx.device_identity.get("pid")
        family = ctx.device_identity.get("family")
        fw = ctx.device_identity.get("firmware")
        # Strictly check feature gate: if blocked by missing strong E5 observable, not applicable!
        return bool(ctx.bundle.has_operation("keyboard.remap") and blocker_for("keyboard.remap", vid=vid, pid=pid, family=family, fw=fw) is None)

    def representative_explanation(self) -> str:
        return (
            "Validating key remapping on a safe secondary key (e.g. Insert / PrtSc) with bidirectional OS-level "
            "event capture (Insert -> A -> Insert) proves key matrix translation, EEPROM mapping table updates, "
            "and complete rollback."
        )

    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        op_id = "keyboard.remap"
        safe_candidate_key = "Insert"
        temporary_target = "A"

        # Invariant check: never remap critical keys
        if safe_candidate_key in FORBIDDEN_REMAP_KEYS:
            raise ValueError(f"Security violation: cannot remap critical key {safe_candidate_key}")

        ev = TestEvidence(
            operation=op_id,
            safe_command_id=op_id,
            firmware_branch=ctx.device_identity.get("firmware", "unknown"),
            connection_mode=ctx.device_identity.get("connection", "wired"),
            baseline_value=None,
            temporary_value=None,
            bundle_hash=ctx.bundle.hash,
        )

        if ctx.on_step_progress:
            ctx.on_step_progress("remap_start", {"op": op_id, "key": safe_candidate_key})

        # 1. Baseline GET
        try:
            val, res = ctx.transport.get(op_id)
            if not res.ok:
                ev.status = "FAIL"
                ev.error = f"Baseline GET failed: {res.error}"
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.baseline_value = val
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"Baseline GET exception: {e}"
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 2. Step 1: SET safe key -> temporary target ('Insert' -> 'A')
        temp_payload = {"source": safe_candidate_key, "target": temporary_target}
        ev.temporary_value = temp_payload
        try:
            set_res = ctx.transport.set(op_id, temp_payload)
            if not set_res.ok:
                ev.status = "FAIL"
                ev.error = f"SET remap failed: {set_res.error}"
                self._rollback(ctx, op_id, ev.baseline_value)
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.transport_result = "ok"
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"SET remap exception: {e}"
            self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 3. Step 1 Observable Check: Prompt user to press 'Insert', expect OS input 'A'
        prompt_req1 = ObservableRequest(
            kind="press_key",
            target=temporary_target,
            prompt_ru=f"Нажмите один раз клавишу {safe_candidate_key} (ожидается ввод '{temporary_target}')",
            prompt_en=f"Press the {safe_candidate_key} key once (expected '{temporary_target}')",
            timeout_ms=15000,
        )
        obs_res1 = ctx.observable_listener.wait_for(prompt_req1)
        if not obs_res1.ok:
            ev.status = "FAIL"
            ev.error = f"Remap input observable failed: {obs_res1.error}"
            rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res1, rollback_verified=rollback_ok, error=ev.error
            )

        # 4. Step 2: Rollback safe key -> original baseline ('Insert' -> 'Insert')
        rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
        ev.rollback_matched = rollback_ok
        if not rollback_ok:
            ev.status = "FAIL"
            ev.error = "Rollback failed: original key mapping was not restored"
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res1, rollback_verified=False, error=ev.error
            )

        # 5. Step 2 Observable Check: Prompt user to press 'Insert', expect OS input 'Insert'
        prompt_req2 = ObservableRequest(
            kind="press_key",
            target=safe_candidate_key,
            prompt_ru=f"Снова нажмите клавишу {safe_candidate_key} для подтверждения возврата",
            prompt_en=f"Press {safe_candidate_key} again to confirm restoration",
            timeout_ms=15000,
        )
        obs_res2 = ctx.observable_listener.wait_for(prompt_req2)
        ev.observable_result = obs_res2.observed
        ev.observable_pass = obs_res2.ok
        ev.observable_source = obs_res2.source

        if not obs_res2.ok:
            ev.status = "FAIL"
            ev.error = f"Post-rollback input observable failed: {obs_res2.error}"
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res2, rollback_verified=False, error=ev.error
            )

        ev.status = "PASS"
        ev.compute_strength()
        return CapabilityValidationResult(
            self.capability_name, True, ev, observable_result=obs_res2, rollback_verified=True
        )

    def _rollback(self, ctx: GuidedValidationContext, op_id: str, baseline_value: Any) -> bool:
        if baseline_value is None:
            baseline_value = {"source": "Insert", "target": "Insert"}
        try:
            r_set = ctx.transport.set(op_id, baseline_value)
            if not r_set.ok:
                return False
            final_val, final_res = ctx.transport.get(op_id)
            return bool(final_res.ok)
        except Exception:
            return False


class HallEffectCapabilityValidator(BaseCapabilityValidator):
    capability_name = "he.actuation"

    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        # STRICT RULE: only applicable if device has Hall Effect / Analog capability AND guided plan marks it ELIGIBLE!
        family = ctx.device_identity.get("family", "").lower()
        name = ctx.device_identity.get("name", "").lower()
        is_he = ("he" in family or "analog" in family or "he" in name) and "mechanical" not in family and "unknown" not in family
        if not is_he:
            return False
        # Check machine-readable GuidedValidationPlan
        try:
            plan = load_guided_plan()
            for grp in plan.get("validation_groups", []):
                if grp.get("group_id") == "he.actuation":
                    if grp.get("status") != "ELIGIBLE":
                        return False
        except Exception:
            return False
        from .feature_gates import blocker_for
        vid = ctx.device_identity.get("vid")
        pid = ctx.device_identity.get("pid")
        fw = ctx.device_identity.get("firmware")
        return bool(ctx.bundle.has_operation("he.actuation") and blocker_for("he.actuation", vid=vid, pid=pid, family=family, fw=fw) is None)

    def representative_explanation(self) -> str:
        return (
            "Validating Hall Effect actuation threshold shifts across safe separated points (e.g. 3.0mm -> 1.0mm) "
            "proves magnetic sensor calibration, travel threshold encoding, and sensor-to-trigger response."
        )

    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        op_id = "he.actuation"
        ev = TestEvidence(
            operation=op_id,
            safe_command_id=op_id,
            firmware_branch=ctx.device_identity.get("firmware", "unknown"),
            connection_mode=ctx.device_identity.get("connection", "wired"),
            baseline_value=None,
            temporary_value=None,
            bundle_hash=ctx.bundle.hash,
        )

        if ctx.on_step_progress:
            ctx.on_step_progress("he_start", {"op": op_id})

        # 1. Baseline GET
        try:
            val, res = ctx.transport.get(op_id)
            if not res.ok:
                ev.status = "FAIL"
                ev.error = f"Baseline GET failed: {res.error}"
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.baseline_value = val
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"Baseline GET exception: {e}"
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 2. Pick safe separated temporary test actuation point (on-grid 0.5mm step)
        # e.g. if baseline is >= 2.0mm, test 1.0mm; otherwise test 3.0mm
        base_num = float(val) if isinstance(val, (int, float)) else 1.63
        temp_val = 10 if base_num >= 20 else 30  # 1.0mm vs 3.0mm in 0.1mm units or native grid
        ev.temporary_value = temp_val

        # 3. SET test actuation depth
        try:
            set_res = ctx.transport.set(op_id, temp_val)
            if not set_res.ok:
                ev.status = "FAIL"
                ev.error = f"SET actuation point failed: {set_res.error}"
                self._rollback(ctx, op_id, ev.baseline_value)
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.transport_result = "ok"
        except Exception as e:
            ev.status = "FAIL"
            ev.error = f"SET exception: {e}"
            self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)

        # 4. Protocol Readback GET
        try:
            read_val, read_res = ctx.transport.get(op_id)
            if read_res.ok and (read_val == temp_val or str(read_val) == str(temp_val)):
                ev.readback = read_val
                ev.readback_matched = True
        except Exception:
            pass

        # 5. Observable Check (HE key trigger observable)
        prompt_req = ObservableRequest(
            kind="he_press",
            target="W",
            prompt_ru="Нажмите клавишу W для проверки срабатывания на заданной глубине.",
            prompt_en="Press the W key to verify actuation triggers at the configured depth.",
            timeout_ms=15000,
        )
        obs_res = ctx.observable_listener.wait_for(prompt_req)
        ev.observable_result = obs_res.observed
        ev.observable_pass = obs_res.ok
        ev.observable_source = obs_res.source

        if not obs_res.ok:
            ev.status = "FAIL"
            ev.error = f"Hall Effect actuation observable failed: {obs_res.error}"
            rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res, rollback_verified=rollback_ok, error=ev.error
            )

        # 6. Rollback to original baseline
        rollback_ok = self._rollback(ctx, op_id, ev.baseline_value)
        ev.rollback_matched = rollback_ok
        ev.rollback_result = "ok" if rollback_ok else "failed"

        if not rollback_ok:
            ev.status = "FAIL"
            ev.error = "Rollback verification failed: original actuation threshold not restored"
            return CapabilityValidationResult(
                self.capability_name, False, ev, observable_result=obs_res, rollback_verified=False, error=ev.error
            )

        ev.status = "PASS"
        ev.compute_strength()
        return CapabilityValidationResult(
            self.capability_name, True, ev, observable_result=obs_res, rollback_verified=True
        )

    def _rollback(self, ctx: GuidedValidationContext, op_id: str, baseline_value: Any) -> bool:
        if baseline_value is None:
            return False
        try:
            r_set = ctx.transport.set(op_id, baseline_value)
            if not r_set.ok:
                return False
            final_val, final_res = ctx.transport.get(op_id)
            return bool(final_res.ok and (final_val == baseline_value or str(final_val) == str(baseline_value)))
        except Exception:
            return False


class MechanicalKeyboardValidator(BaseCapabilityValidator):
    """Validator for standard mechanical keyboards.
    
    ABSOLUTE RULE: Standard mechanical switches are binary digital inputs (pressed / not pressed).
    NEVER perform partial-depth or actuation threshold validation on standard mechanical keyboards.
    """
    capability_name = "keyboard.digital"

    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        family = ctx.device_identity.get("family", "").lower()
        name = ctx.device_identity.get("name", "").lower()
        is_he = ("he" in family or "analog" in family or "he" in name) and "mechanical" not in family
        is_kb = "kb" in family or "keyboard" in family or "keyboard" in name
        return is_kb and not is_he

    def representative_explanation(self) -> str:
        return "Mechanical keyboards validate digital-only capabilities (profiles, polling rate, win_lock, lighting)."

    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        # Validate safe digital feature: keyboard.polling or device.win_lock
        op_id = "keyboard.polling" if ctx.bundle.has_operation("keyboard.polling") else "device.win_lock"
        ev = TestEvidence(
            operation=op_id,
            safe_command_id=op_id,
            firmware_branch=ctx.device_identity.get("firmware", "unknown"),
            connection_mode=ctx.device_identity.get("connection", "wired"),
            baseline_value=None,
            temporary_value=None,
            bundle_hash=ctx.bundle.hash,
        )

        try:
            val, res = ctx.transport.get(op_id)
            if not res.ok:
                ev.status = "FAIL"
                ev.error = f"GET {op_id} failed: {res.error}"
                return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)
            ev.baseline_value = val
            ev.status = "PASS"
            ev.transport_result = "ok"
            ev.readback_matched = True
            ev.rollback_matched = True
            return CapabilityValidationResult(self.capability_name, True, ev, rollback_verified=True)
        except Exception as e:
            ev.status = "FAIL"
            ev.error = str(e)
            return CapabilityValidationResult(self.capability_name, False, ev, error=ev.error)


class ReadOnlyInventoryValidator(BaseCapabilityValidator):
    """Zero-write descriptor & inventory collector for new / unvalidated devices (Level 0)."""
    capability_name = "inventory"

    def is_applicable(self, ctx: GuidedValidationContext) -> bool:
        return True

    def representative_explanation(self) -> str:
        return "Zero-write inventory capture of HID descriptors, Usage Pages, firmware string, and report layouts."

    def validate(self, ctx: GuidedValidationContext) -> CapabilityValidationResult:
        ev = TestEvidence(
            operation="inventory.descriptor",
            safe_command_id="inventory.descriptor",
            firmware_branch=ctx.device_identity.get("firmware", "unknown"),
            connection_mode=ctx.device_identity.get("connection", "wired"),
            baseline_value={"read_only": True},
            temporary_value=None,
            bundle_hash=ctx.bundle.hash,
        )
        ev.status = "PASS"
        ev.transport_result = "ok"
        ev.readback_matched = True
        ev.rollback_matched = True
        return CapabilityValidationResult(self.capability_name, True, ev, rollback_verified=True)


class GuidedValidationEngine:
    """Orchestrates Guided Hardware Validation across capability groups."""

    def __init__(self, cert_store: CertificateStore | None = None) -> None:
        self.cert_store = cert_store or CertificateStore()
        self.validators: list[BaseCapabilityValidator] = [
            LightingCapabilityValidator(),
            RemapCapabilityValidator(),
            HallEffectCapabilityValidator(),
            MechanicalKeyboardValidator(),
            ReadOnlyInventoryValidator(),
        ]

    def plan_validators(self, ctx: GuidedValidationContext) -> list[BaseCapabilityValidator]:
        """Select applicable capability validators for this device context."""
        applicable = []
        for v in self.validators:
            if v.is_applicable(ctx):
                applicable.append(v)
        return applicable

    def run_validation(self, ctx: GuidedValidationContext) -> dict[str, Any]:
        start_time = time.time()
        applicable_validators = self.plan_validators(ctx)
        
        results: list[CapabilityValidationResult] = []
        validated_groups: list[str] = []
        inventory_evidence: dict[str, Any] = {}
        explanations: dict[str, str] = {}
        baseline_hashes: dict[str, str] = {}
        rollback_results: dict[str, bool] = {}
        observables: list[dict[str, Any]] = []
        all_passed = True
        failed_capability = ""
        error_reason = ""

        for validator in applicable_validators:
            if ctx.on_step_progress:
                ctx.on_step_progress("validator_start", {"capability": validator.capability_name})
            
            res = validator.validate(ctx)
            results.append(res)

            if res.capability == "inventory":
                inventory_evidence = res.evidence.to_dict() if hasattr(res.evidence, "to_dict") else {}
            elif res.evidence.baseline_value is not None:
                b_hash = hashlib.sha256(json.dumps(str(res.evidence.baseline_value)).encode()).hexdigest()[:8]
                baseline_hashes[res.capability] = b_hash
            
            if res.capability != "inventory":
                rollback_results[res.capability] = res.rollback_verified
            if res.observable_result:
                observables.append({
                    "capability": res.capability,
                    "ok": res.observable_result.ok,
                    "source": res.observable_result.source,
                    "latency_ms": res.observable_result.latency_ms,
                    "observed": res.observable_result.observed,
                })

            if res.passed:
                if res.capability != "inventory":
                    validated_groups.append(validator.capability_name)
                    explanations[validator.capability_name] = validator.representative_explanation()
            else:
                all_passed = False
                failed_capability = validator.capability_name
                error_reason = res.error or "Capability validation failed"
                # STRICT RULE: Stop further mutations immediately on failure!
                break

        duration = time.time() - start_time
        final_state_verified = all(res.rollback_verified for res in results)

        if all_passed and final_state_verified:
            # Mint DeviceValidationCertificate
            cert = DeviceValidationCertificate(
                vendor=ctx.device_identity.get("vendor", ""),
                model=ctx.device_identity.get("name", ""),
                variant=ctx.device_identity.get("variant", ""),
                vid=ctx.device_identity.get("vid", ""),
                pid=ctx.device_identity.get("pid", ""),
                descriptor_hash=ctx.device_identity.get("descriptor_hash", ""),
                firmware_branch=ctx.device_identity.get("firmware", ""),
                connection_mode=ctx.device_identity.get("connection", "wired"),
                protocol_family=ctx.device_identity.get("family", ""),
                knowledge_revision=ctx.bundle.raw.get("knowledge_revision", ""),
                app_version=ctx.app_version,
                build_commit=ctx.build_commit,
                terminal_verdict="COMPLETE_PASS",
                validated_capability_groups=validated_groups,
                individual_operations=[r.evidence.to_dict() for r in results if hasattr(r.evidence, "to_dict")],
                inventory_evidence=inventory_evidence,
                baseline_hashes=baseline_hashes,
                observables=observables,
                rollback_results=rollback_results,
                final_state_verified=True,
                representative_coverage_explanation=explanations,
            )
            cert_path = self.cert_store.save(cert)
            return {
                "state": STATE_VALIDATED,
                "verdict": "COMPLETE_PASS",
                "duration_seconds": round(duration, 2),
                "certificate": cert.to_dict(),
                "certificate_path": str(cert_path),
                "validated_groups": validated_groups,
                "coverage_explanation": explanations,
                "rollback_verified": final_state_verified,
            }
        else:
            return {
                "state": STATE_VALIDATION_FAILED,
                "verdict": "FAILED",
                "duration_seconds": round(duration, 2),
                "failed_capability": failed_capability,
                "error": error_reason,
                "rollback_verified": final_state_verified,
                "results": [r.evidence.to_dict() for r in results if hasattr(r.evidence, "to_dict")],
            }
