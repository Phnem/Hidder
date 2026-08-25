"""DEFECT #1 reconnect/recovery regression — A→B→C→D lifecycle.

Reproduces the physical HERO84 polling failure:
  baseline 3 -> A WRITE 2 physically applies -> reconnect invalidates A
  -> B fresh readback null -> B SUSPECT, ZERO writes via B
  -> C fresh GET current 2 -> C WRITE baseline 3 -> invalidate C
  -> D fresh GET 3 -> baseline_restored=true

Negative gates (identity/firmware/ambiguous/C GET fail) must yield ZERO recovery writes,
operation verdict RECOVERY_BLOCKED, baseline_restored=false, final_state UNKNOWN,
manual_restore_required=true.
"""

import pytest

from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.identity import ExactIdentityGate, mock_hero84_instance, PhysicalInstance
from community.vetro_probe.transport import FakeTransport, TransportResult
from community.vetro_probe.baseline import BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.reconnect import ReconnectManager


def _make_env():
    bundle = production_bundle_for_hero84()
    trans = FakeTransport({"keyboard.polling": 3}, reconnect_ops={"keyboard.polling"})
    gate = ExactIdentityGate(bundle)
    inst = mock_hero84_instance()  # firmware 0216
    return bundle, trans, gate, inst


def _make_ctx(bundle, trans, gate, inst, cfg):
    collector = BaselineCollector(trans)
    snap = collector.collect(["keyboard.polling"])
    assert snap.values.get("keyboard.polling") == 3
    journal = RecoveryJournal(snap)

    def enumerate_fn():
        if cfg.get("ambiguous"):
            from community.vetro_probe.reconnect import AMBIGUOUS
            return AMBIGUOUS
        if not cfg.get("identity_ok", True):
            return PhysicalInstance(
                vid="0x1234", pid="0x5678", descriptor_hash="bad",
                firmware_version="0216", connection_mode="wired", interfaces=[], report_ids=[9],
            )
        return inst

    def fw_check(i):
        if cfg.get("firmware_ok", True):
            return True, "0216"
        return False, cfg.get("firmware_reason", "firmware check failed")

    rm = ReconnectManager(trans, gate, enumerate_fn, timeout_ms=2000, poll_ms=10, firmware_check=fw_check)
    safety = SafetyGate(bundle, instance_firmware="0216")
    ctx = ExecutorContext(
        bundle=bundle, transport=trans, safety=safety, baseline=snap, recovery=journal,
        reconnect=rm, observable=None, firmware_branch="0216", connection_mode="wired",
    )
    return ctx


# ---------------------------------------------------------------- happy path

def test_main_recovery_regression_proves_defect_not_masked():
    bundle, trans, gate, inst = _make_env()
    # B (session 2) readback must return null — the exact physical defect
    trans.device.readback_fail_sessions.add(2)
    ctx = _make_ctx(bundle, trans, gate, inst, {"firmware_ok": True})
    ev = execute_single("keyboard.polling", ctx)

    d = trans.device
    # Without recovery, the write 2 physically applied and device would stay 2:
    assert d.session_write_counts.get(1) == 1  # A wrote 2
    # A separate proof: a bare write leaves state 2
    ref = FakeTransport({"keyboard.polling": 3}, reconnect_ops={"keyboard.polling"})
    ref.set("keyboard.polling", 2)
    assert ref.device.state["keyboard.polling"] == 2

    # Recovery: C (session 3) wrote baseline 3, D (session 4) verified
    assert d.session_write_counts.get(3) == 1  # C recovery write
    assert d.session_write_counts.get(2, 0) == 0  # B NEVER wrote (suspect)
    assert d.session_write_counts.get(4, 0) == 0  # D only reads
    assert d.write_count == 2  # A write + C recovery write
    assert d.state["keyboard.polling"] == 3

    j = ctx.recovery.recovery_record
    # A != B != C != D
    sessions = (j.session_a, j.session_b, j.session_c, j.session_d)
    assert sessions == (1, 2, 3, 4)
    assert len(set(sessions)) == 4
    assert j.observed_current == 2
    assert j.final_get == 3
    assert j.baseline_restored is True
    assert j.recovery_blocked is False
    assert j.final_state == 3
    assert j.manual_restore_required is False
    assert j.recovery_write_attempted is True
    assert j.initial_readback_result == "fail: simulated readback failure (null)"
    assert ev.sessions == {"A": 1, "B": 2, "C": 3, "D": 4}
    assert ev.rollback_matched is True


def test_stale_pre_reconnect_handle_never_successful():
    bundle, trans, gate, inst = _make_env()
    ctx = _make_ctx(bundle, trans, gate, inst, {"firmware_ok": True})
    ev = execute_single("keyboard.polling", ctx)
    # Original session A is now stale — any GET/SET through it must fail as stale
    val, res = trans.get("keyboard.polling")
    assert res.ok is False and res.error == "stale session"
    sres = trans.set("keyboard.polling", 3)
    assert sres.ok is False and sres.error == "stale session"
    # Post-invalidate calls went through the stale path (failed), never succeeded:
    assert trans.device.stale_get_count >= 1
    assert trans.device.stale_set_count >= 1
    # A's only successful SET was the original write (2); the stale set did not apply:
    assert trans.device.session_write_counts.get(1, 0) == 1
    assert trans.device.write_count == 2  # A write + C recovery write only
    assert ev.status in ("PASS", "FAIL")
    assert ctx.transport.current_session_id() != trans.current_session_id()


# ------------------------------------------------------------ negative gates

@pytest.mark.parametrize("cfg,reason_substr", [
    ({"identity_ok": False}, "identity mismatch"),
    ({"firmware_ok": False, "firmware_reason": "firmware unknown"}, "firmware unknown"),
    ({"firmware_ok": False, "firmware_reason": "firmware 1602 != 0216"}, "1602"),
    ({"ambiguous": True}, "ambiguous"),
], ids=["identity_mismatch", "firmware_unknown", "firmware_not_0216", "ambiguous_reacquire"])
def test_negative_recovery_gates_zero_writes(cfg, reason_substr):
    bundle, trans, gate, inst = _make_env()
    ctx = _make_ctx(bundle, trans, gate, inst, cfg)
    ev = execute_single("keyboard.polling", ctx)

    d = trans.device
    # Only the original A write occurred; ZERO recovery writes
    assert d.write_count == 1
    assert d.session_write_counts == {1: 1}
    assert d.session_write_counts.get(2, 0) == 0
    assert d.session_write_counts.get(3, 0) == 0

    j = ctx.recovery.recovery_record
    assert j.recovery_blocked is True
    assert reason_substr in j.recovery_block_reason
    assert j.baseline_restored is False
    assert j.final_state == "UNKNOWN"
    assert j.manual_restore_required is True
    assert ev.status == "FAIL"


def test_negative_c_get_current_failure_blocks_baseline_write():
    bundle, trans, gate, inst = _make_env()
    # B (session 2) readback null triggers recovery; C (session 3) GET current fails
    trans.device.readback_fail_sessions.add(2)
    trans.device.get_fail_sessions.add(3)
    ctx = _make_ctx(bundle, trans, gate, inst, {"firmware_ok": True})
    ev = execute_single("keyboard.polling", ctx)

    d = trans.device
    assert d.write_count == 1  # only A write; C never wrote baseline
    assert d.session_write_counts == {1: 1}

    j = ctx.recovery.recovery_record
    assert j.recovery_blocked is True
    assert "C GET current failed" in j.recovery_block_reason
    assert j.baseline_restored is False
    assert j.final_state == "UNKNOWN"
    assert j.manual_restore_required is True
    assert j.recovery_write_attempted is False
    assert ev.status == "FAIL"


def test_journal_never_claims_restored_without_final_fresh_get():
    # Happy path journal: baseline_restored only after D GET via fresh session
    bundle, trans, gate, inst = _make_env()
    trans.device.readback_fail_sessions.add(2)
    ctx = _make_ctx(bundle, trans, gate, inst, {"firmware_ok": True})
    execute_single("keyboard.polling", ctx)
    j = ctx.recovery.recovery_record
    # final_get observed through session D (4), distinct from A(1)/B(2)/C(3)
    assert j.final_session == 4
    assert j.final_get == 3
    assert j.baseline_restored is True
    rd = j.to_dict()
    assert rd["baseline_restored"] is True
    assert rd["final_state"] == 3
    assert rd["observed_current"] == 2
