"""Deterministic tests for the dedicated he.actuation physical revalidation
(prep only — no hardware). Covers grid selection, immutable-baseline restore,
write-ahead/recovery, exact gate, and cross-feature non-promotion."""

from pathlib import Path

import pytest

from community.vetro_probe.actuation_revalidation import (
    GRID, OPERATION, plan_actuation_temporary, run_actuation_revalidation,
    _restore_actuation, revalidation_identity_ok,
)
from community.vetro_probe import feature_gates as fg
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.identity import mock_hero84_instance, PhysicalInstance, descriptor_hash_from_bytes
from community.vetro_probe.transport import TransportResult
from community.vetro_probe.runstate import RunCheckpoint
from community.vetro_probe.automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED
from community.vetro_probe.transport import FakeTransport

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")


class _ActuationDevice:
    def __init__(self, state):
        self.state = float(state)
        self.write_count = 0
        self.one_shot_mismatch = None  # value for the next get (once)
        self.fail_get_at_write_count = None  # fail the next get once write_count reaches this


class _FakeActuationTransport:
    def __init__(self, dev):
        self.dev = dev
        self._valid = True
        self._sid = id(self)

    def get(self, op):
        if self.dev.one_shot_mismatch is not None:
            v = self.dev.one_shot_mismatch
            self.dev.one_shot_mismatch = None
            return v, TransportResult(True)
        if self.dev.fail_get_at_write_count is not None and self.dev.write_count >= self.dev.fail_get_at_write_count:
            self.dev.fail_get_at_write_count = None
            return None, TransportResult(False, error="injected final GET failure")
        return self.dev.state, TransportResult(True)

    def set(self, op, v):
        self.dev.write_count += 1
        self.dev.state = float(v)
        return TransportResult(True)

    def invalidate(self):
        self._valid = False

    def close(self):
        self._valid = False

    def current_session_id(self):
        return self._sid

    def is_connected(self):
        return self._valid


def _make(dev):
    def factory():
        return _FakeActuationTransport(dev)
    return factory


# 1. actuation remains BLOCKED without physical post-fix closure
def test_actuation_blocked_without_physical_closure():
    blk = fg.blocker_for("he.actuation", **SCOPE)
    assert blk is not None
    assert blk[0] == "BLOCKED_PENDING_PHYSICAL_REVALIDATION"
    assert "prior real Probe run FAILED" in blk[1]


# 2. temporary B always belongs to the proven grid
@pytest.mark.parametrize("A", [0.0, 0.4, 0.5, 0.9, 1.0, 1.63, 1.9, 2.0, 3.0])
def test_temporary_B_always_on_grid(A):
    B, plan = plan_actuation_temporary(A)
    assert B in GRID, B
    assert plan["chosen_mm"] == B


# 3. temporary B != baseline
@pytest.mark.parametrize("A", [0.4, 0.5, 1.0, 1.63, 1.9, 2.0])
def test_temporary_B_differs_from_baseline(A):
    B, _ = plan_actuation_temporary(A)
    assert B != A


# 4/5. baseline never rounded to grid; 1.63 preserved exactly
def test_baseline_1_63_preserved_exactly():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore
    assert mm_to_raw(1.63) == 163
    assert raw_to_mm(163) == 1.63
    dev = _ActuationDevice(1.63)
    rec = _restore_actuation(1.63, _make(dev))
    assert rec["ok"] is True
    assert dev.state == 1.63  # restored EXACTLY, not snapped to 1.5/2.0
    assert rec["written"] == 1.63


# 6. write B + fresh readback B + full lifecycle pass
def test_full_lifecycle_pass():
    dev = _ActuationDevice(1.63)
    res = run_actuation_revalidation(_make(dev), 1.63)
    assert res["ok"] is True and res["recovered"] is True
    assert res["temporary_B"] == 1.0  # nearest clearly-different central grid value
    rb = [s for s in res["stages"] if s["stage"] == "readback_B"][0]
    assert rb["match"] is True and rb["got"] == 1.0
    fin = [s for s in res["stages"] if s["stage"] == "restore_A"][0]
    assert fin["final_get_equals_A"] is True and fin["final_get"] == 1.63
    assert dev.state == 1.63
    assert dev.write_count == 2  # temp write + restore write


# 7. readback mismatch -> recovery A
def test_readback_mismatch_recovers_A():
    dev = _ActuationDevice(1.63)
    dev.one_shot_mismatch = 0.0  # GET B reports wrong value once
    res = run_actuation_revalidation(_make(dev), 1.63)
    assert res["ok"] is False
    assert res["recovered"] is True
    assert res["error_code"] == "READBACK_B_MISMATCH"
    assert dev.state == 1.63  # immutable A restored


# 8. crash after TEMP_WRITE_INTENT -> recovery A
def test_crash_after_temp_write_intent_recovers_A():
    # Pending checkpoint: TEMP_WRITE_INTENT persisted, process crashed.
    cp = RunCheckpoint(run_id="crash", operation=OPERATION, baseline=1.63,
                       attempted="", phase="TEMP_WRITE_INTENT", closed=False)
    dev = _ActuationDevice(1.63)  # device may or may not have received B; restore A regardless
    rec = _restore_actuation(float(cp.baseline), _make(dev))
    assert rec["ok"] is True
    assert dev.state == 1.63
    assert rec["final_get_equals_A"] is True


# 9. restore writes immutable original A, not normalized
def test_restore_writes_immutable_A_not_normalized():
    dev = _ActuationDevice(1.63)
    rec = _restore_actuation(1.63, _make(dev))
    assert rec["ok"] is True
    assert dev.state != 1.5 and dev.state != 2.0
    assert dev.state == 1.63


# 10. final GET must equal original baseline
def test_final_get_equals_original_baseline():
    dev = _ActuationDevice(1.63)
    res = run_actuation_revalidation(_make(dev), 1.63)
    fin = [s for s in res["stages"] if s["stage"] == "restore_A"][0]
    assert fin["final_get"] == 1.63 and fin["final_get_equals_A"] is True


# 11. final GET None -> FAIL
def test_final_get_none_fails_closed():
    dev = _ActuationDevice(1.63)
    dev.fail_get_at_write_count = 2  # fail the GET that follows the restore SET
    res = run_actuation_revalidation(_make(dev), 1.63)
    assert res["ok"] is False
    fin = [s for s in res["stages"] if s["stage"] == "restore_A"][0]
    assert fin["error_code"] == "GET_A_FAILED"
    assert fin["final_get_observed"] is False


# 12. identity/FW mismatch -> zero writes (gate)
def test_identity_fw_mismatch_gate_zero_writes():
    bundle = production_bundle_for_hero84()
    ok, _ = revalidation_identity_ok(bundle, mock_hero84_instance())
    assert ok is True
    bad = PhysicalInstance(vid="0x1234", pid="0x103E",
                           descriptor_hash=descriptor_hash_from_bytes(b"x"),
                           firmware_version="0216", connection_mode="wired",
                           interfaces=[0, 1, 2], report_ids=[0, 1, 8, 9],
                           product_string="x", manufacturer="x")
    ok2, reason = revalidation_identity_ok(bundle, bad)
    assert ok2 is False and "VID/PID mismatch" in reason
    ok3, _ = revalidation_identity_ok(bundle, mock_hero84_instance(firmware="unknown"))
    assert ok3 is False


# 13/14. successful physical evidence closes ONLY he.actuation; RT/remap stay blocked
def test_closure_promotes_only_actuation(monkeypatch):
    monkeypatch.setitem(fg.CLOSED_EVIDENCE, "physical Probe PASS after 0.5mm-grid fix", "real PASS recorded")
    assert fg.blocker_for("he.actuation", **SCOPE) is None
    # RT and remap are NOT affected (no cross-feature inference)
    assert fg.blocker_for("he.rt", **SCOPE) is not None
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE) is not None
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"


def test_plan_classifies_actuation_after_closure(monkeypatch):
    from community.vetro_probe.automation import AutoProbeRun
    monkeypatch.setitem(fg.CLOSED_EVIDENCE, "physical Probe PASS after 0.5mm-grid fix", "real PASS recorded")
    bundle = production_bundle_for_hero84()
    inst = mock_hero84_instance()
    trans = FakeTransport(initial_state={})
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(".") / "_ltmp", reconnect_timeout_ms=200)
    run._plan()
    entry = next(e for e in run.plan if e["operation"] == "he.actuation")
    assert entry["classification"] == CLS_AUTO_REVERSIBLE
    for op, expect_blocked in (("he.rt", CLS_BLOCKED), ("keyboard.remap", CLS_BLOCKED), ("light.rgb_core", CLS_BLOCKED)):
        e = next(x for x in run.plan if x["operation"] == op)
        assert e["classification"] == expect_blocked
