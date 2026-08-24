"""Typed write transaction executor.

Enforces per spec chapter 7:
READ baseline -> choose safe temp -> record recovery -> WRITE -> cadence -> READBACK -> compare -> ROLLBACK -> READBACK -> compare

ACK alone is not PASS. Fail-closed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .bundle import Bundle
from .safety import SafetyGate
from .transport import DeviceTransport
from .baseline import BaselineSnapshot
from .recovery import RecoveryJournal
from .evidence import TestEvidence
from .reconnect import ReconnectManager
from .observable import ObservableListener, NoopObservableListener, OPERATION_OBSERVABLES


@dataclass
class ExecutorContext:
    bundle: Bundle
    transport: DeviceTransport
    safety: SafetyGate
    baseline: BaselineSnapshot
    recovery: RecoveryJournal
    reconnect: ReconnectManager | None = None
    observable: ObservableListener | None = None
    firmware_branch: str = ""
    connection_mode: str = ""


def execute_single(operation_id: str, ctx: ExecutorContext) -> TestEvidence:
    bundle = ctx.bundle
    op = bundle.get_operation(operation_id)
    ev = TestEvidence(
        operation=operation_id,
        safe_command_id=operation_id,
        firmware_branch=ctx.firmware_branch or bundle.firmware_branch,
        connection_mode=ctx.connection_mode or bundle.connection_mode,
        baseline_value=ctx.baseline.values.get(operation_id),
        temporary_value=None,
        bundle_hash=bundle.hash,
    )

    # ---- preconditions fail-closed ----
    if op is None:
        ev.status = "BLOCKED"
        ev.error = f"unknown operation {operation_id}"
        return ev
    if operation_id not in ctx.baseline.values:
        # baseline required for reversible writes
        if op.reversible:
            ev.status = "BLOCKED"
            ev.error = "baseline unavailable for rollback-required operation"
            return ev
    # safety authorize with baseline
    baseline_val = ctx.baseline.values.get(operation_id)
    dec = ctx.safety.authorize_with_baseline(operation_id, baseline_val) if op.reversible else ctx.safety.authorize(operation_id)
    if not dec.allowed:
        ev.status = "BLOCKED"
        ev.error = dec.reason
        return ev
    temp_value = dec.safe_value
    ev.temporary_value = temp_value
    if op.reversible and temp_value == baseline_val:
        ev.status = "BLOCKED"
        ev.error = "safe temporary equals baseline — cannot test"
        return ev

    # record recovery attempt
    ctx.recovery.record_attempt(operation_id)

    # ---- WRITE ----
    t_write_start = time.time()
    set_res = ctx.transport.set(operation_id, temp_value)
    t_write_ms = int((time.time() - t_write_start) * 1000)
    ev.timing_ms["write_ms"] = t_write_ms
    ev.transport_result = "ok" if set_res.ok else f"fail: {set_res.error}"
    if not set_res.ok:
        ev.status = "FAIL"
        ev.error = set_res.error
        ev.evidence_strength = ev.compute_strength()
        return ev

    # Reconnect handling: invalidate immediately and wait
    if op.requires_reconnect:
        if ctx.reconnect is None:
            ev.status = "FAIL"
            ev.error = "reconnect required but no ReconnectManager"
            ev.evidence_strength = ev.compute_strength()
            return ev
        ctx.reconnect.begin_reconnect_write()
        # For fake transports caller must simulate reconnect externally;
        # executor will still wait via manager if provided
        rr = ctx.reconnect.wait_for_reconnect()
        if not rr.ok:
            ev.status = "FAIL"
            ev.error = f"reconnect failed: {rr.error}"
            ev.evidence_strength = ev.compute_strength()
            return ev
        # old session already invalidated; new session assumed ready

    # cadence
    cadence = op.cadence_ms or 120
    time.sleep(cadence / 1000)

    # ---- READBACK ----
    t_rb = time.time()
    rb_val, rb_res = ctx.transport.get(operation_id)
    ev.timing_ms["readback_ms"] = int((time.time() - t_rb) * 1000)
    ev.readback = rb_val
    if not rb_res.ok:
        ev.status = "FAIL"
        ev.error = f"readback failed: {rb_res.error}"
        ev.evidence_strength = ev.compute_strength()
        # attempt rollback even if readback failed
    else:
        ev.readback_matched = (rb_val == temp_value)
        if not ev.readback_matched:
            ev.status = "FAIL"
            ev.error = f"readback mismatch: got {rb_val!r} expected {temp_value!r}"
            # still attempt rollback

    # ---- OBSERVABLE if needed ----
    if op.needs_observable:
        listener = ctx.observable or NoopObservableListener()
        req = OPERATION_OBSERVABLES.get(operation_id)
        if req is None:
            # generic observable for this op
            from .observable import ObservableRequest
            req = ObservableRequest("press_key", "PrtSc", "Нажмите подсвеченную клавишу", "Press highlighted key")
        obs = listener.wait_for(req)
        ev.observable_result = obs.observed
        ev.observable_pass = obs.ok
        if not obs.ok:
            ev.status = "FAIL"
            ev.error = (ev.error + "; " if ev.error else "") + f"observable failed: {obs.error}"
            ev.evidence_strength = ev.compute_strength()

    # ---- ROLLBACK ----
    if op.reversible:
        t_rb2 = time.time()
        rbk_res = ctx.transport.set(operation_id, baseline_val)
        ev.timing_ms["rollback_ms"] = int((time.time() - t_rb2) * 1000)
        ev.rollback_result = "ok" if rbk_res.ok else f"fail: {rbk_res.error}"
        if not rbk_res.ok:
            ev.status = "FAIL"
            ev.error = (ev.error + "; " if ev.error else "") + f"rollback failed: {rbk_res.error}"
            ev.evidence_strength = ev.compute_strength()
            return ev

        if op.requires_reconnect:
            if ctx.reconnect is not None:
                ctx.reconnect.begin_reconnect_write()
                rr2 = ctx.reconnect.wait_for_reconnect()
                if not rr2.ok:
                    ev.status = "FAIL"
                    ev.error = (ev.error + "; " if ev.error else "") + f"rollback reconnect failed: {rr2.error}"
                    ev.evidence_strength = ev.compute_strength()
                    return ev
            time.sleep(cadence / 1000)

        # rollback readback
        t_rb3 = time.time()
        rb2_val, rb2_res = ctx.transport.get(operation_id)
        ev.timing_ms["rollback_readback_ms"] = int((time.time() - t_rb3) * 1000)
        ev.rollback_readback = rb2_val
        if not rb2_res.ok:
            ev.status = "FAIL"
            ev.error = (ev.error + "; " if ev.error else "") + f"rollback readback failed: {rb2_res.error}"
            ev.evidence_strength = ev.compute_strength()
            return ev
        ev.rollback_matched = (rb2_val == baseline_val)
        if not ev.rollback_matched:
            ev.status = "FAIL"
            ev.error = (ev.error + "; " if ev.error else "") + f"rollback readback mismatch: got {rb2_val!r} expected {baseline_val!r}"
            ev.evidence_strength = ev.compute_strength()
            return ev
        ctx.recovery.record_restored(operation_id, True)

    # ---- final verdict ----
    ev.evidence_strength = ev.compute_strength()
    if ev.status == "FAIL" and ev.error:
        # already failed
        pass
    elif ev.readback_matched and (not op.reversible or ev.rollback_matched):
        # observable optional for non-remap ops; for needs_observable must also pass
        if op.needs_observable and not ev.observable_pass:
            ev.status = "FAIL"
        else:
            ev.status = "PASS"
    else:
        if ev.status not in ("FAIL", "BLOCKED", "SKIP"):
            ev.status = "FAIL"
            if not ev.error:
                ev.error = "readback/rollback not matched"
    return ev
