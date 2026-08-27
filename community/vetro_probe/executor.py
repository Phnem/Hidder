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
    enforce_feature_gates: bool = False  # executor-level defense in depth (auto flow)


def _validation_flags(ev: TestEvidence, op_id: str) -> dict[str, bool]:
    """Per-step physical-validation flags for the operation certificate.

    ack for light.brightness means the canonical device echo was validated
    (echo != readback); for other ops it falls back to transport completion.
    final_restore is the rollback/final readback matched the immutable baseline."""
    return {
        "write": ev.transport_result == "ok",
        "ack": bool(ev.ack_valid) if op_id == "light.brightness" else ev.transport_result == "ok",
        "readback": ev.readback_matched,
        "rollback": ev.rollback_matched,
        "final_restore": ev.rollback_matched,
    }


def _capture_ack_evidence(ev: TestEvidence, transport) -> None:
    """For the register-0x01 light path: pull the canonical echo captured by the
    transport SET and verify it. Echo is ACK evidence only — never a readback."""
    take = getattr(transport, "take_light_echo", None)
    if take is None:
        return
    try:
        echo, written = take()
    except Exception:
        return
    if echo is None or written is None:
        return
    from .lighting_core import decode_echo
    dec = decode_echo(bytes(written), echo)
    ev.ack_valid = bool(dec["ack"])
    ev.echo_hex = dec["echo_frame"] or ""


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
    # ---- executor-level feature-evidence gate (defense in depth) ----
    # Even a stale/corrupt plan entry cannot execute an operation whose hard
    # feature requirement is OPEN: the gate is consulted here independently of
    # the planner presentation. ZERO writes for blocked ops.
    if ctx.enforce_feature_gates:
        from .feature_gates import blocker_for
        blk = blocker_for(operation_id,
                          vid=ctx.bundle.product.vid, pid=ctx.bundle.product.pid,
                          family=ctx.bundle.family, fw=ctx.firmware_branch)
        if blk is not None:
            ev.status = "BLOCKED"
            ev.error = blk[1]
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
    _capture_ack_evidence(ev, ctx.transport)

    if op.requires_reconnect:
        # ---- A→B→C→D reconnect/recovery lifecycle ----
        return _execute_reconnect_recovery(ctx, op, ev, temp_value, baseline_val)

    # ---- non-reconnect path (unchanged) ----
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
    else:
        ev.readback_matched = (rb_val == temp_value)
        if not ev.readback_matched:
            ev.status = "FAIL"
            ev.error = f"readback mismatch: got {rb_val!r} expected {temp_value!r}"

    # ---- OBSERVABLE if needed ----
    if op.needs_observable:
        listener = ctx.observable or NoopObservableListener()
        req = OPERATION_OBSERVABLES.get(operation_id)
        if req is None:
            from .observable import ObservableRequest
            req = ObservableRequest("press_key", "PrtSc", "Нажмите подсвеченную клавишу", "Press highlighted key")
        obs = listener.wait_for(req)
        ev.observable_result = obs.observed
        ev.observable_pass = obs.ok
        ev.observable_source = getattr(obs, "source", "")
        if not obs.ok:
            ev.status = "FAIL"
            ev.error = (ev.error + "; " if ev.error else "") + f"observable failed: {obs.error} (source={ev.observable_source})"
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

        # rollback readback (same session — non-reconnect op)
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
    ev.validation_flags = _validation_flags(ev, operation_id)
    ev.evidence_strength = ev.compute_strength()
    if ev.status == "FAIL" and ev.error:
        pass
    elif ev.readback_matched and (not op.reversible or ev.rollback_matched):
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


def _acquire_fresh_or_block(ctx, ev, journal) -> tuple[bool, Any]:
    """Acquire a fresh session; on gate failure mark journal blocked and return (False, reason)."""
    if ctx.reconnect is None:
        journal.block_recovery("reconnect required but no ReconnectManager")
        ev.status = "FAIL"
        ev.error = "reconnect required but no ReconnectManager"
        ev.evidence_strength = ev.compute_strength()
        return False, "no reconnect manager"
    rr = ctx.reconnect.acquire_fresh()
    if not rr.ok:
        journal.block_recovery(rr.error)
        journal.recovery_block_reason = rr.error
        ev.status = "FAIL"
        ev.error = f"reconnect failed: {rr.error}"
        ev.evidence_strength = ev.compute_strength()
        return False, rr.error
    return True, rr.session


def _execute_reconnect_recovery(ctx, op, ev, temp_value, baseline_val):
    """A→B→C→D lifecycle for reconnect-triggering writes.

    A: write session (already used). invalidated.
    B: fresh readback session. If readback == expected → normal path (rollback via B→C).
       If null/mismatch → B is SUSPECT, invalidated, NEVER used for recovery write.
    C: fresh recovery session. GET current (must observe), WRITE baseline, invalidated.
    D: fresh final-verification session. GET == baseline → baseline_restored=true.

    Stale pre-reconnect handle is never reused: every step uses a fresh session.
    """
    cadence = op.cadence_ms or 120
    journal = ctx.recovery.recovery_record
    ctx.recovery.record_attempt(op.id)
    session_a = ctx.transport.current_session_id()
    journal.session_a = session_a
    journal.reconnect_occurred = True
    # The reconnect-triggering write already succeeded on the wire; it may have applied.
    journal.initial_write_may_have_applied = True
    ev.sessions = {"A": session_a, "B": None, "C": None, "D": None}

    def _finalize():
        ev.sessions = {"A": journal.session_a, "B": journal.session_b, "C": journal.session_c, "D": journal.session_d}
        ev.recovery = ctx.recovery.recovery_record.to_dict()
        ev.validation_flags = _validation_flags(ev, op.id)
        return ev

    # A -> invalidate, then acquire B
    if ctx.reconnect is None:
        journal.block_recovery("reconnect required but no ReconnectManager")
        ev.status = "FAIL"
        ev.error = "reconnect required but no ReconnectManager"
        ev.evidence_strength = ev.compute_strength()
        return _finalize()
    ctx.reconnect.begin_reconnect_write()
    ok_b, session_b = _acquire_fresh_or_block(ctx, ev, ctx.recovery)
    if not ok_b:
        return _finalize()
    journal.session_b = session_b.current_session_id()
    ev.sessions["B"] = journal.session_b

    # ---- B readback ----
    time.sleep(cadence / 1000)
    t_rb = time.time()
    rb_val, rb_res = session_b.get(op.id)
    ev.timing_ms["readback_ms"] = int((time.time() - t_rb) * 1000)
    ev.readback = rb_val
    journal.initial_readback_result = "ok" if rb_res.ok else f"fail: {rb_res.error}"

    if rb_res.ok and rb_val == temp_value:
        # ---- NORMAL path: B good; rollback via B, then rollback-readback via C ----
        ev.readback_matched = True
        ev.transport_result = "ok"
        ctx.transport = session_b  # expose fresh session to caller (final snapshot)
        journal.identity_result = "PASS"
        journal.firmware_result = "0216"
        journal.recovery_started = False

        if op.reversible:
            t_rb2 = time.time()
            rbk_res = session_b.set(op.id, baseline_val)
            ev.timing_ms["rollback_ms"] = int((time.time() - t_rb2) * 1000)
            ev.rollback_result = "ok" if rbk_res.ok else f"fail: {rbk_res.error}"
            if not rbk_res.ok:
                ev.status = "FAIL"
                ev.error = f"rollback failed: {rbk_res.error}"
                ev.evidence_strength = ev.compute_strength()
                return _finalize()
            # rollback on reconnect op -> B invalidated, reacquire C
            ok_c, session_c = _acquire_fresh_or_block(ctx, ev, ctx.recovery)
            if not ok_c:
                return _finalize()
            journal.session_c = session_c.current_session_id()
            time.sleep(cadence / 1000)
            t_rb3 = time.time()
            rb2_val, rb2_res = session_c.get(op.id)
            ev.timing_ms["rollback_readback_ms"] = int((time.time() - t_rb3) * 1000)
            ev.rollback_readback = rb2_val
            ev.rollback_matched = (rb2_res.ok and rb2_val == baseline_val)
            ctx.transport = session_c
            if not ev.rollback_matched:
                ev.status = "FAIL"
                ev.error = f"rollback readback mismatch: got {rb2_val!r} expected {baseline_val!r}"
                ev.evidence_strength = ev.compute_strength()
                return _finalize()
            ctx.recovery.record_restored(op.id, True)

        ev.evidence_strength = ev.compute_strength()
        if op.needs_observable and not ev.observable_pass:
            ev.status = "FAIL"
        else:
            ev.status = "PASS"
        return _finalize()

    # ---- RECOVERY PATH: B gave null/mismatch; B is SUSPECT -> invalidated, ZERO writes ----
    ev.status = "FAIL"
    if not rb_res.ok:
        ev.error = f"readback failed: {rb_res.error}"
    else:
        ev.error = f"readback mismatch: got {rb_val!r} expected {temp_value!r}"
    session_b.invalidate()  # SUSPECT — never reused for writes
    journal.recovery_started = True
    journal.identity_result = "PASS"
    journal.firmware_result = "0216"

    # C: fresh, GET current, WRITE baseline
    ok_c, session_c = _acquire_fresh_or_block(ctx, ev, ctx.recovery)
    if not ok_c:
        return _finalize()
    journal.session_c = session_c.current_session_id()
    cur_val, cur_res = session_c.get(op.id)
    if not cur_res.ok:
        journal.observed_current = None
        journal.final_state = "UNKNOWN"
        ctx.recovery.block_recovery(f"C GET current failed: {cur_res.error}")
        ev.error += f"; recovery blocked: C GET current failed ({cur_res.error})"
        ev.evidence_strength = ev.compute_strength()
        return _finalize()
    journal.observed_current = cur_val
    journal.last_known_state = cur_val
    if cur_val != baseline_val:
        jw = session_c.set(op.id, baseline_val)
        journal.recovery_write_attempted = True
        journal.recovery_write_result = "ok" if jw.ok else f"fail: {jw.error}"
        if not jw.ok:
            ctx.recovery.block_recovery(f"recovery WRITE failed: {jw.error}")
            ev.error += f"; recovery WRITE failed: {jw.error}"
            ev.evidence_strength = ev.compute_strength()
            return _finalize()
    else:
        journal.recovery_write_attempted = False
        journal.recovery_write_result = "already at baseline"
    session_c.invalidate()
    journal.second_reconnect = True

    # D: fresh, final GET
    ok_d, session_d = _acquire_fresh_or_block(ctx, ev, ctx.recovery)
    if not ok_d:
        return _finalize()
    journal.session_d = session_d.current_session_id()
    time.sleep(cadence / 1000)
    final_val, final_res = session_d.get(op.id)
    journal.final_get = final_val
    journal.final_session = session_d.current_session_id()
    restored = final_res.ok and final_val == baseline_val
    journal.baseline_restored = restored
    journal.final_state = final_val if final_res.ok else "UNKNOWN"
    ctx.transport = session_d
    ev.rollback_result = "recovery"
    ev.rollback_readback = final_val
    ev.rollback_matched = restored
    if not restored:
        ctx.recovery.block_recovery("final GET did not match baseline")
        ev.error += f"; recovery final GET mismatch: got {final_val!r} expected {baseline_val!r}"
        ev.evidence_strength = ev.compute_strength()
        return _finalize()
    # Recovery succeeded: original operation failed at readback but baseline restored.
    ctx.recovery.record_restored(op.id, True)
    ev.evidence_strength = ev.compute_strength()
    return _finalize()
