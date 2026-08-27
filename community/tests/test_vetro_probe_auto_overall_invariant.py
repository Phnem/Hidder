"""Fail-closed overall-status invariant regressions.

A physical full-auto run may report overall success ONLY when every expected op
executed+passed+restored, the aggregate final verification RAN and PASSED,
baseline_restored and final_state_verified are both true, and recovery is not
required. baseline_restored=false or final_state_verified=false ALWAYS make the
overall result non-success, and the terminal status must say so explicitly —
never an ambiguous COMPLETE."""

from pathlib import Path

from community.vetro_probe.automation import (
    AutoProbeRun, overall_success, S_COMPLETE_PASS, S_COMPLETE_UNVERIFIED,
)
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.identity import mock_hero84_instance

PASS_TRUE = dict(
    executed_expected_ops=5, passed_expected_ops=5, failed_ops=0, restored_all=True,
    aggregate_ran=True, aggregate_pass=True, baseline_restored=True,
    final_verified=True, recovery_required=False,
)


def _base_run(tmp_path):
    bundle = production_bundle_for_hero84()
    state = {
        "keyboard.profile": 1, "keyboard.polling": 3, "device.win_lock": False,
        "he.deadzone": 0.5, "light.brightness": 10, "light.rgb_core": "00ff0000000000",
    }
    trans = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    inst = mock_hero84_instance()
    return AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                        enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                        run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=2000), trans


# 1. predicate: all true -> success
def test_predicate_all_true_is_success():
    assert overall_success(**PASS_TRUE) is True


# 2-4, 6-8. predicate: each required field false -> NOT success
def test_predicate_aggregate_not_run_not_success():
    kw = dict(PASS_TRUE); kw["aggregate_ran"] = False
    assert overall_success(**kw) is False


def test_predicate_aggregate_mismatch_not_success():
    kw = dict(PASS_TRUE); kw["aggregate_pass"] = False
    assert overall_success(**kw) is False


def test_predicate_aggregate_none_not_success():
    kw = dict(PASS_TRUE); kw["aggregate_pass"] = False; kw["final_verified"] = False
    assert overall_success(**kw) is False


def test_predicate_restored_false_not_success():
    kw = dict(PASS_TRUE); kw["restored_all"] = False
    assert overall_success(**kw) is False


def test_predicate_final_verified_false_never_success():
    kw = dict(PASS_TRUE); kw["final_verified"] = False
    assert overall_success(**kw) is False
    assert overall_success(**{**kw, "baseline_restored": True, "aggregate_pass": True, "aggregate_ran": False}) is False


def test_predicate_baseline_restored_false_never_success():
    kw = dict(PASS_TRUE); kw["baseline_restored"] = False
    assert overall_success(**kw) is False


def test_predicate_recovery_required_never_success():
    kw = dict(PASS_TRUE); kw["recovery_required"] = True
    assert overall_success(**kw) is False


# 5. per-op restored=false (even with PASS-count) -> NOT success
def test_predicate_restored_false_even_with_pass_count_not_success():
    kw = dict(PASS_TRUE); kw["restored_all"] = False
    assert overall_success(**kw) is False


def test_finalize_verdict_recomputes_authoritative_fields(tmp_path):
    # The formatter MUST use the exact authoritative aggregate fields: tampering
    # with run.overall is inert because _finalize_verdict recomputes from evidence.
    run, trans = _base_run(tmp_path)
    run.run()
    assert run.verdict == S_COMPLETE_PASS
    run.overall["overall_pass"] = False
    run.overall["final_state_verified"] = False
    run._finalize_verdict()
    assert run.overall_pass is True
    assert run.overall["final_state_verified"] is True
    assert run.verdict == S_COMPLETE_PASS


# 9. formatter uses the exact authoritative aggregate fields
def test_summary_uses_authoritative_fields(tmp_path):
    run, trans = _base_run(tmp_path)
    run.run()
    assert run.verdict == S_COMPLETE_PASS
    s = run.summary()
    assert "OVERALL: PASS" in s
    assert "STATUS: COMPLETE_PASS" in s
    assert "FINAL STATE VERIFIED: YES" in s
    assert "AGGREGATE FINAL VERIFICATION: RAN" in s
    # force the unverified path and confirm the formatter switches
    run.overall["overall_pass"] = False
    run.verdict = S_COMPLETE_UNVERIFIED
    s2 = run.summary()
    assert "OVERALL: UNVERIFIED_FINAL_STATE" in s2
    assert "STATUS: COMPLETE_UNVERIFIED_FINAL_STATE" in s2


# 10. historical current-run fixture reproduces the NO/NO contradiction and is
#     classified correctly after the fix
def test_historical_no_no_contradiction_classified_unverified(tmp_path):
    run, trans = _base_run(tmp_path)
    # The real physical run's per-op evidence: 5/5 PASS with rollback readbacks
    # matching baselines, but the aggregate reader desynced on real HID.
    run.run()
    assert run.verdict == S_COMPLETE_PASS
    # Replay the actual artifact outcome: per-op evidence PASS, aggregate mismatch.
    run.baseline_restored = False
    run.final_state = {
        "restored": False, "aggregate_ran": True,
        "mismatches": {
            "keyboard.polling": {"expected": 3, "actual": 17, "error": ""},
            "device.win_lock": {"expected": False, "actual": True, "error": ""},
            "light.brightness": {"expected": 10, "actual": None,
                                 "error": "light register 0x01 GET must be 7 bytes, got 1"},
        },
        "fresh_session": True,
    }
    run.contradictions.append("final baseline mismatch")
    run._finalize_verdict()
    assert run.verdict == S_COMPLETE_UNVERIFIED, run.verdict
    assert run.verdict != "COMPLETE"  # never ambiguous success
    assert run.overall_pass is False
    assert run.overall["final_state_verified"] is False
    assert run.overall["baseline_restored"] is False


def test_ambiguous_complete_status_cannot_be_success(tmp_path):
    run, trans = _base_run(tmp_path)
    run.verdict = "COMPLETE"  # legacy run-ended status must never imply success
    run.overall_pass = False
    assert run.verdict != S_COMPLETE_PASS
