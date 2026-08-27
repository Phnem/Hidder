"""Additive E2E closure-artifact regressions (no hardware).

The historical in-run aggregate desync must be preserved as-is; the independent
zero-write verification is ADDITIVE closure evidence that records the final
state and the authoritative COMPLETE_PASS verdict. Miner/package manifest must
express the distinction (initial UNRELIABLE_DESYNC vs follow-up READONLY_VERIFIED
vs final PASS), and blocked features / K20 must not be promoted."""

import json
from pathlib import Path

from community.vetro_probe.verify_final import (
    write_closure_artifact, write_final_verdict, BLOCKED_FEATURES_NOT_PROMOTED,
)
from community.vetro_probe.miner_package import build_package


def _results():
    return {
        "keyboard.profile": {"expected": 1, "actual": 1, "matched": True, "error": ""},
        "keyboard.polling": {"expected": 3, "actual": 3, "matched": True, "error": ""},
        "device.win_lock": {"expected": False, "actual": False, "matched": True, "error": ""},
        "he.deadzone": {"expected": 0.5, "actual": 0.5, "matched": True, "error": ""},
        "light.brightness": {"expected": 10, "actual": 10, "matched": True, "error": ""},
    }


def test_closure_artifact_additive_and_preserves_desync(tmp_path):
    c = write_closure_artifact(tmp_path, _results(), True)
    d = json.loads(c.read_text(encoding="utf-8"))
    assert d["schema"] == "vetro.e2e-external-closure.v1"
    assert d["verification_mode"] == "independent_read_only"
    assert d["writes"] == 0 and d["fresh_sessions"] is True
    assert d["all_expected_baselines_matched"] is True
    assert d["initial_in_run_aggregate_read"] == "UNRELIABLE_DESYNC"
    assert d["follow_up_authoritative_verification"] == "READONLY_VERIFIED"
    assert d["final_physical_verdict"] == "PASS"
    assert d["additive_to_historical_evidence"] is True
    assert d["historical_desync_preserved"] is True
    assert len(d["operations"]) == 5
    assert all(o["status"] == "PASS" for o in d["operations"].values())


def test_final_verdict_complete_pass_not_promoting_blocked(tmp_path):
    v = write_final_verdict(tmp_path, True)
    d = json.loads(v.read_text(encoding="utf-8"))
    assert d["verdict"] == "COMPLETE_PASS"
    assert d["expected_executable_ops"] == 5 and d["passed_ops"] == 5 and d["restored_ops"] == 5
    assert d["baseline_restored"] is True and d["final_state_verified"] is True
    assert d["manual_restore_required"] is False
    assert d["k20_not_promoted"] is True
    for op in ("keyboard.remap", "he.actuation", "he.rt", "light.rgb_core",
               "light.global_color", "light.effect", "custom.per_key"):
        assert op in d["blocked_features_not_promoted"]
        assert op in BLOCKED_FEATURES_NOT_PROMOTED


def test_final_verdict_unverified_when_not_ok(tmp_path):
    d = json.loads(write_final_verdict(tmp_path, False).read_text(encoding="utf-8"))
    assert d["verdict"] == "COMPLETE_UNVERIFIED_FINAL_STATE"
    assert d["baseline_restored"] is False and d["final_state_verified"] is False
    assert d["manual_restore_required"] is True


def test_manifest_surfaces_external_closure_and_final_verdict(tmp_path):
    # simulate a full run package, then an additive external closure
    from community.vetro_probe.transport import FakeTransport
    pkg = build_package(
        base_dir=tmp_path / "pkg", run_id="r1", label="test",
        discovery={"vid": "0x372E", "pid": "0x103E", "firmware": "0216"},
        plan=[], evidence=[], baselines={}, final_state={"restored": False},
        certificates=[], recovery={}, terminal="COMPLETE_UNVERIFIED_FINAL_STATE",
    )
    # no closure yet -> manifest must NOT invent one
    m0 = json.loads((pkg / "run_manifest.json").read_text(encoding="utf-8"))
    assert "external_closure" not in m0
    # write additive closure for this run, re-run package build, manifest reflects it
    write_closure_artifact(pkg, _results(), True)
    write_final_verdict(pkg, True)
    pkg2 = build_package(
        base_dir=pkg, run_id="r1", label="test",
        discovery={"vid": "0x372E", "pid": "0x103E", "firmware": "0216"},
        plan=[], evidence=[], baselines={}, final_state={"restored": False},
        certificates=[], recovery={}, terminal="COMPLETE_PASS",
    )
    m = json.loads((pkg2 / "run_manifest.json").read_text(encoding="utf-8"))
    assert m["final_physical_verdict"] == "COMPLETE_PASS"
    assert m["external_closure"]["initial_in_run_aggregate_read"] == "UNRELIABLE_DESYNC"
    assert m["external_closure"]["follow_up_authoritative_verification"] == "READONLY_VERIFIED"
    assert m["final_verdict"]["verdict"] == "COMPLETE_PASS"
