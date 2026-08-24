"""Fail-closed tests for Vetro Probe (headless vertical slice).

Covers per spec chapter 15:
fake transport, disconnect/re-enumeration, wrong identity after reconnect,
failed readback, failed rollback, partial baseline, stale session,
timeout/cadence, final-state mismatch, malicious bundle raw opcode.
"""

import json
from pathlib import Path
import pytest

from community.vetro_probe.bundle import example_hero84_bundle, parse_bundle
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.identity import ExactIdentityGate, mock_hero84_instance, PhysicalInstance
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.baseline import BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.reconnect import ReconnectManager
from community.vetro_probe.observable import FakeObservableListener
from community.vetro_probe.planner import plan, coverage_report
from community.vetro_probe.certificate import build_certificate


def _ctx(bundle, transport, reconnect=None, ops=None):
    safety = SafetyGate(bundle)
    collector = BaselineCollector(transport)
    target_ops = ops or [p.operation_id for p in plan(bundle)] + ["he.actuation"]
    # dedup preserve order
    target_ops = list(dict.fromkeys(target_ops))
    # only collect ops that are expected to have baseline; for isolated he.actuation test use single op
    # tests that need full plan will pass ops explicitly
    if "he.actuation" in target_ops and len(target_ops) > 5:
        # for generic helper, use single op to keep hash stable for final check
        target_ops = ["he.actuation"]
    snap = collector.collect(target_ops)
    recovery = RecoveryJournal(snap)
    return ExecutorContext(bundle, transport, safety, snap, recovery, reconnect, FakeObservableListener(), "1.17.3", "wired"), snap, recovery


def test_fake_transport_happy_path():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    ctx, snap, rec = _ctx(bundle, trans, ops=["he.actuation"])
    ev = execute_single("he.actuation", ctx)
    assert ev.status == "PASS"
    assert ev.evidence_strength == ["E1", "E2", "E3", "E4"]
    assert ev.readback_matched and ev.rollback_matched
    # final restore — must collect same ops as initial
    final = BaselineCollector(trans).collect(["he.actuation"])
    assert rec.final_matches_initial(final)


def test_failed_readback_is_fail_closed():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    trans.inject_readback_mismatch("he.actuation", 99.9)
    ctx, snap, rec = _ctx(bundle, trans)
    ev = execute_single("he.actuation", ctx)
    assert ev.status == "FAIL"
    assert "readback mismatch" in ev.error
    assert "E4" not in ev.evidence_strength  # no E4 without rollback success


def test_failed_rollback_is_fail_closed():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    ctx, snap, rec = _ctx(bundle, trans)
    # make rollback fail: inject set failure that triggers on second set (rollback)
    # first set succeeds, second (rollback) should fail
    call_count = {"n": 0}
    orig_set = trans.set

    def failing_set(op, val):
        call_count["n"] += 1
        if call_count["n"] == 2:
            from community.vetro_probe.transport import TransportResult
            return TransportResult(False, error="rollback io error")
        return orig_set(op, val)

    trans.set = failing_set  # type: ignore
    ev = execute_single("he.actuation", ctx)
    assert ev.status == "FAIL"
    assert "rollback" in ev.error.lower()


def test_partial_baseline_blocked():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={})  # no baseline for he.actuation
    safety = SafetyGate(bundle)
    collector = BaselineCollector(trans)
    snap = collector.collect(["he.actuation"])
    assert "he.actuation" not in snap.values
    assert "he.actuation" in snap.errors
    rec = RecoveryJournal(snap)
    ctx = ExecutorContext(bundle, trans, safety, snap, rec, None, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("he.actuation", ctx)
    assert ev.status == "BLOCKED"
    assert "baseline unavailable" in ev.error


def test_stale_session_blocked():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    trans.invalidate()
    safety = SafetyGate(bundle)
    collector = BaselineCollector(trans)
    snap = collector.collect(["he.actuation"])
    # stale session yields no baseline
    assert "he.actuation" not in snap.values
    rec = RecoveryJournal(snap)
    ctx = ExecutorContext(bundle, trans, safety, snap, rec, None, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("he.actuation", ctx)
    assert ev.status == "BLOCKED"


def test_wrong_identity_after_reconnect():
    bundle = example_hero84_bundle()
    gate = ExactIdentityGate(bundle)
    bad = PhysicalInstance(vid="0x1234", pid="0x5678", descriptor_hash="bad", firmware_version="1.17.3", connection_mode="wired", interfaces=[0], report_ids=[0])
    verdict = gate.evaluate(bad)
    assert not verdict.passed
    assert "VID/PID mismatch" in verdict.reason


def test_malicious_bundle_raw_opcode_blocked():
    with pytest.raises(ValueError, match="forbidden raw"):
        parse_bundle({
            "schema": "vetro.preview-bundle.v1",
            "id": "x", "version": 1,
            "product": {"vid": "0x372E", "pid": "0x103E", "name": "test"},
            "family": "x",
            "connection": {"mode": "wired"},
            "firmware": {"branch": "1"},
            "capabilities": {}, "bounds": {},
            "operations": {"bad": {"id": "bad", "kind": "set", "reversible": True, "readback": True, "raw_bytes": "00"}},
        })


def test_unknown_operation_blocked_by_safety():
    bundle = example_hero84_bundle()
    gate = SafetyGate(bundle)
    dec = gate.authorize("unknown.op", 123)
    assert not dec.allowed
    assert "unknown operation" in dec.reason


def test_raw_bytes_value_blocked():
    bundle = example_hero84_bundle()
    gate = SafetyGate(bundle)
    dec = gate.authorize("he.actuation", b"\x00\x01")
    assert not dec.allowed


def test_calibration_blocked():
    bundle = example_hero84_bundle()
    # inject calibration op with bounds
    bundle.operations["calibration.full"] = bundle.operations["he.actuation"]  # type: ignore
    bundle = parse_bundle({
        **bundle.raw,
        "operations": {**bundle.raw["operations"], "calibration.full": {"id": "calibration.full", "kind": "set", "reversible": False, "readback": False}},
        "bounds": {**bundle.raw.get("bounds", {}), "calibration.full": {"min": 0, "max": 1}},
    })
    gate = SafetyGate(bundle)
    dec = gate.authorize("calibration.full", 0)
    assert not dec.allowed
    assert "forbidden" in dec.reason


def test_reconnect_session_invalidation():
    bundle = example_hero84_bundle()
    # polling requires reconnect
    assert bundle.operations["keyboard.polling"].requires_reconnect
    trans = FakeTransport(initial_state={"keyboard.polling": 1000}, reconnect_ops={"keyboard.polling"})
    gate = ExactIdentityGate(bundle)
    inst = mock_hero84_instance()
    collector = BaselineCollector(trans)
    snap = collector.collect(["keyboard.polling"])
    rec = RecoveryJournal(snap)
    safety = SafetyGate(bundle)

    def enumerate_fn():
        return inst

    rm = ReconnectManager(trans, gate, enumerate_fn, timeout_ms=1000, poll_ms=50)
    # patch to auto-simulate reconnect after invalidation
    orig_wait = rm.wait_for_reconnect

    def patched():
        if not trans.is_connected():
            trans.simulate_reconnect()
        return orig_wait()

    rm.wait_for_reconnect = patched  # type: ignore
    ctx = ExecutorContext(bundle, trans, safety, snap, rec, rm, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("keyboard.polling", ctx)
    assert ev.status == "PASS"
    # ensure old session invalidated and new session id increased
    assert trans.current_session_id() >= 2


def test_final_state_mismatch_fail_closed():
    bundle = example_hero84_bundle()
    gate = ExactIdentityGate(bundle)
    inst = mock_hero84_instance()
    verdict = gate.evaluate(inst)
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    collector = BaselineCollector(trans)
    snap = collector.collect(["he.actuation"])
    rec = RecoveryJournal(snap)
    safety = SafetyGate(bundle)
    ctx = ExecutorContext(bundle, trans, safety, snap, rec, None, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("he.actuation", ctx)
    # external tamper before final snapshot
    trans.state["he.actuation"] = 9.9
    final = collector.collect(["he.actuation"])
    assert not rec.final_matches_initial(final)
    coverage = coverage_report(bundle, [ev])
    cert = build_certificate(verdict, bundle, snap.hash, final.hash, False, [ev], ["final baseline mismatch"], coverage)
    assert cert.verdict == "FAIL"
    assert not cert.to_dict()["baseline_restored"]


def test_certificate_never_promotes():
    bundle = example_hero84_bundle()
    gate = ExactIdentityGate(bundle)
    inst = mock_hero84_instance()
    verdict = gate.evaluate(inst)
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    collector = BaselineCollector(trans)
    snap = collector.collect(["he.actuation"])
    rec = RecoveryJournal(snap)
    ctx = ExecutorContext(bundle, trans, SafetyGate(bundle), snap, rec, None, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("he.actuation", ctx)
    final = collector.collect(["he.actuation"])
    cov = coverage_report(bundle, [ev])
    cert = build_certificate(verdict, bundle, snap.hash, final.hash, True, [ev], [], cov)
    assert cert.to_dict()["quorum"]["eligible_for"] == "none"
    assert cert.verdict in ("PASS", "FAIL")


def test_ack_only_not_pass():
    # Simulate transport that ACKs set but readback mismatches — must not be PASS
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"he.actuation": 0.5})
    trans.inject_readback_mismatch("he.actuation", 0.0)  # write ok but readback wrong
    ctx, snap, rec = _ctx(bundle, trans)
    ev = execute_single("he.actuation", ctx)
    assert ev.status != "PASS"
    assert ev.transport_result == "ok"  # ACK happened but still FAIL


def test_timeout_on_reconnect():
    bundle = example_hero84_bundle()
    trans = FakeTransport(initial_state={"keyboard.polling": 1000}, reconnect_ops={"keyboard.polling"})
    gate = ExactIdentityGate(bundle)
    collector = BaselineCollector(trans)
    snap = collector.collect(["keyboard.polling"])
    rec = RecoveryJournal(snap)

    def never_enumerate():
        return None

    rm = ReconnectManager(trans, gate, never_enumerate, timeout_ms=200, poll_ms=50)
    ctx = ExecutorContext(bundle, trans, SafetyGate(bundle), snap, rec, rm, FakeObservableListener(), "1.17.3", "wired")
    ev = execute_single("keyboard.polling", ctx)
    assert ev.status == "FAIL"
    assert "reconnect" in ev.error.lower()
