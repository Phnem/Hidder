"""E2E physical-auto preparation: deterministic preflight for the controlled
five-op HERO84/FW0216 run. NO hardware. Proves planner<->executor consistency,
defense-in-depth stale-plan rejection, failure policy (stop-on-failure, restore
before next), aggregate final verification, recovery-first startup, and the
exact identity/FW zero-write gate."""

from pathlib import Path

import pytest

from community.vetro_probe.automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.identity import mock_hero84_instance, PhysicalInstance, descriptor_hash_from_bytes
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.baseline import BaselineSnapshot, BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.runstate import RunCheckpoint
from community.vetro_probe import feature_gates as fg

EXPECTED = {"keyboard.profile", "keyboard.polling", "device.win_lock", "he.deadzone", "light.brightness"}
BLOCKED_OPS = ["keyboard.remap", "he.actuation", "he.rt", "light.rgb_core",
               "light.global_color", "light.effect", "light.speed", "light.direction",
               "custom.per_key", "light.edge_light"]


def _baseline_state(bundle):
    from community.vetro_probe.planner import plan
    state = {}
    defaults = {
        "he.actuation": 1.0, "he.rt": 1, "he.deadzone": 0.5,
        "keyboard.remap": 0x46, "keyboard.profile": 0,
        "light.rgb_core": "00ff0000000000", "device.win_lock": False,
        "keyboard.polling": 3, "light.brightness": 10,
    }
    for p in plan(bundle):
        if p.operation_id in defaults:
            state[p.operation_id] = defaults[p.operation_id]
        elif p.operation_id in bundle.operations and bundle.operations[p.operation_id].kind != "observable":
            state[p.operation_id] = 0
    return state


def _make_run(tmp_path, *, state=None, instance=None, allowed_ops=None, pending=None,
              mismatch_op=None, readback_fail=None, get_fail=None):
    bundle = production_bundle_for_hero84()
    state = state or _baseline_state(bundle)
    trans = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    if mismatch_op:
        trans.inject_readback_mismatch(mismatch_op, 999)
    if readback_fail:
        trans.device.readback_fail_sessions.update(readback_fail)
    if get_fail:
        trans.device.get_fail_sessions.update(get_fail)
    inst = instance if instance is not None else mock_hero84_instance()
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=2000,
                       allowed_ops=allowed_ops)
    if pending:
        store = run.store
        cp = RunCheckpoint(**pending)
        cp.run_id = "crash-run"
        cp.save(store.checkpoint_path)
        run.cp = store.load()
    return run, trans


def _wrong_vid_instance():
    return PhysicalInstance(vid="0x1234", pid="0x103E",
                            descriptor_hash=descriptor_hash_from_bytes(b"hero84-descriptor-v1"),
                            firmware_version="0216", connection_mode="wired",
                            interfaces=[0, 1, 2], report_ids=[0, 1, 8, 9],
                            product_string="AULA HERO84 HE", manufacturer="AULA")


# 1. exact HERO84/FW0216 real-auto executable set is exactly the five ops
def test_executable_set_is_exactly_five(tmp_path):
    run, trans = _make_run(tmp_path)
    run._plan()
    executable = {e["operation"] for e in run.plan
                  if e["classification"] == CLS_AUTO_REVERSIBLE and not e.get("informational")}
    assert executable == EXPECTED
    for op in BLOCKED_OPS:
        entry = next(e for e in run.plan if e["operation"] == op)
        assert entry["classification"] == CLS_BLOCKED, op


# 2. unexpected sixth mutable op -> abort before any write
def test_unexpected_sixth_mutable_op_aborts_before_write(tmp_path, monkeypatch):
    monkeypatch.setitem(fg.CLOSED_EVIDENCE, "rapid_trigger_units_crosscheck", "simulated")
    run, trans = _make_run(tmp_path)
    run.run()
    assert run.verdict == "BLOCKED"
    assert trans.device.write_count == 0
    assert any("ABORT BEFORE FIRST WRITE" in t.reason for t in run.transitions)


# 3-5. stale plan containing blocked ops -> executor refuses (defense in depth)
def _exec_ctx(bundle, transport, enforce=True):
    safety = SafetyGate(bundle, instance_firmware="0216")
    snap = BaselineSnapshot(values={})
    return ExecutorContext(bundle=bundle, transport=transport, safety=safety,
                           baseline=snap, recovery=RecoveryJournal(snap),
                           reconnect=None, firmware_branch="0216",
                           connection_mode="wired", enforce_feature_gates=enforce)


def test_stale_plan_remap_executor_refuses_zero_writes():
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={"keyboard.remap": 0x46})
    ev = execute_single("keyboard.remap", _exec_ctx(bundle, trans))
    assert ev.status == "BLOCKED"
    assert "BLOCKED_BY_MISSING_STRONG_E5" in ev.error
    assert trans.device.write_count == 0


def test_stale_plan_rt_executor_refuses():
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={"he.rt": 1})
    ev = execute_single("he.rt", _exec_ctx(bundle, trans))
    assert ev.status == "BLOCKED"
    assert "BLOCKED_BY_KNOWLEDGE_HOLE" in ev.error
    assert trans.device.write_count == 0


def test_stale_plan_actuation_executor_refuses():
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    ev = execute_single("he.actuation", _exec_ctx(bundle, trans))
    assert ev.status == "BLOCKED"
    assert "BLOCKED_PENDING_PHYSICAL_REVALIDATION" in ev.error
    assert trans.device.write_count == 0


# 6. each successful op restores before next begins
def test_each_successful_op_restores_before_next(tmp_path):
    bundle = production_bundle_for_hero84()
    state = _baseline_state(bundle)
    run, trans = _make_run(tmp_path, state=dict(state))
    run.run()
    assert run.verdict == "COMPLETE_PASS", run.verdict
    assert run.overall_pass is True
    assert run.baseline_restored is True
    for op in EXPECTED:
        assert trans.device.state[op] == state[op], op


# 7-8. failure of op N prevents write for op N+1; FAIL_RESTORED on verified restore
def test_failure_of_op_n_prevents_write_for_op_nplus1(tmp_path):
    # polling (op 2) fails at readback; recovery restores it -> FAIL_RESTORED.
    # win_lock/deadzone/brightness (ops 3-5) must NOT be scheduled/written.
    run, trans = _make_run(tmp_path, readback_fail={2})
    run.run()
    assert run.verdict == "FAIL_RESTORED", run.verdict
    assert run.baseline_restored is True
    assert trans.device.state["keyboard.polling"] == 3  # restored to baseline
    # profile(2 writes) + polling(write + recovery write) = 4; no op 3-5 write
    assert trans.device.write_count == 4
    executed = {e.operation for e in run.results}
    assert "win_lock" not in executed and "he.deadzone" not in executed and "light.brightness" not in executed


# 9. failure + unverified recovery -> FAILED_REQUIRES_MANUAL_RESTORE
def test_failure_unverified_recovery_manual_restore(tmp_path):
    # profile readback mismatch persists into its rollback write -> final state
    # not verified == unverified recovery -> MANUAL.
    run, trans = _make_run(tmp_path, mismatch_op="keyboard.profile")
    run.run()
    assert run.verdict == "FAILED_REQUIRES_MANUAL_RESTORE", run.verdict
    assert run.cp is not None and run.cp.closed is False and run.cp.recovery_required is True
    assert trans.device.write_count == 2  # temp write + failed-restore write; nothing speculative


def test_failure_unverified_recovery_manual_restore_reconnect(tmp_path):
    run, trans = _make_run(tmp_path, readback_fail={2}, get_fail={3}, allowed_ops=["keyboard.polling"])
    run.run()
    assert run.verdict == "FAILED_REQUIRES_MANUAL_RESTORE", run.verdict
    assert run.cp is not None and run.cp.closed is False and run.cp.recovery_required is True


# 10. aggregate final verification compares against original baselines
def test_aggregate_final_verification_against_original_baselines(tmp_path):
    run, trans = _make_run(tmp_path)
    run.run()
    assert run.verdict == "COMPLETE_PASS", run.verdict
    assert run.overall["aggregate_final_verification_ran"] is True
    assert run.final_state["restored"] is True
    assert run.final_state["mismatches"] == {}


# 11. aggregate verification uses fresh/paced reads
def test_aggregate_verification_uses_fresh_session(tmp_path):
    run, trans = _make_run(tmp_path)
    run.run()
    assert run.final_state["fresh_session"] is True


# 12. brightness path uses full 7-byte state only
def _feature_get_reply(state7):
    import aula_kb_v3.protocol as prot  # type: ignore
    fr = bytearray(63)
    fr[0] = 0x84; fr[1] = 0x01; fr[5] = 7
    fr[6:13] = bytes(state7)
    fr[62] = prot.checksum(bytes(fr[:62]))
    return bytes(fr)


class _LightRegisterRaw:
    """Minimal register-0x01 emulator: canonical echo on SET, GET readback."""

    def __init__(self, state7):
        self.state = bytearray(state7)
        self.sent = []

    def send(self, frame):
        self.sent.append(bytes(frame))

    def recv(self, timeout_ms=1000):
        f = self.sent[-1]
        if f[0] == 0x04:
            self.state = bytearray(f[6:13])
            return f
        return _feature_get_reply(bytes(self.state))

    def close(self):
        pass

    def is_connected(self):
        return True


def test_brightness_full_7byte_state_only():
    from community.vetro_probe.aula_transport import AulaHidTransport
    from aula_kb_v3.registry import resolve_by_uuid  # type: ignore

    A = bytes([1, 0, 255, 0, 0, 10, 2])
    B = bytes([1, 0, 255, 0, 0, 5, 2])
    prod = resolve_by_uuid(18691697672197)
    raw = _LightRegisterRaw(A)
    transport = AulaHidTransport(raw=raw, product=prod)
    safety = SafetyGate(production_bundle_for_hero84(), instance_firmware="0216")
    collector = BaselineCollector(transport)
    snap = collector.collect(["light.brightness"])
    ctx = ExecutorContext(bundle=production_bundle_for_hero84(), transport=transport,
                          safety=safety, baseline=snap, recovery=RecoveryJournal(snap),
                          reconnect=None, firmware_branch="0216", connection_mode="wired",
                          enforce_feature_gates=True)
    ev = execute_single("light.brightness", ctx)
    assert ev.status == "PASS", ev.error
    set_frames = [f for f in raw.sent if f[0] == 0x04]
    assert set_frames, "no SET frame captured"
    assert set_frames[0][6:13] == B  # full 7-byte temporary B in the canonical frame
    assert bytes(raw.state) == A  # final register == immutable A byte-for-byte


# 13. blocked lighting ops cannot execute
def test_blocked_lighting_ops_cannot_execute():
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={"light.rgb_core": "00ff0000000000"})
    ev = execute_single("light.rgb_core", _exec_ctx(bundle, trans))
    assert ev.status == "BLOCKED"
    assert "BLOCKED_BY_UNRESOLVED_LIGHTING_REGISTER" in ev.error
    assert trans.device.write_count == 0


# 14. recovery-first preflight occurs before first new write
def test_recovery_first_preflight_before_first_write(tmp_path):
    state = _baseline_state(production_bundle_for_hero84())
    state["keyboard.polling"] = 2  # applied before crash
    pending = {
        "operation": "keyboard.polling", "baseline": 3, "attempted": 2,
        "write_may_have_applied": True, "closed": False, "phase": "EXECUTING",
    }
    run, trans = _make_run(tmp_path, state=state, pending=pending)
    run.run()
    assert run.recovery_preflight == "RECOVERED"
    assert trans.device.state["keyboard.polling"] == 3  # recovered before new run
    states = [t.state for t in run.transitions]
    assert states.index("RECOVERING") < states.index("EXECUTING")


# 15. exact identity/FW mismatch -> zero writes
def test_identity_mismatch_zero_writes(tmp_path):
    run, trans = _make_run(tmp_path, instance=_wrong_vid_instance())
    run.run()
    assert run.verdict == "BLOCKED"
    assert trans.device.write_count == 0
