"""The gate must be able to fail, and the freeze must be able to refuse.

A green gate proves nothing unless a red one is reachable, and a manifest check
proves nothing unless it actually fires.  Both halves are tested here against
synthetic inputs; neither test opens ground_truth/truth.json.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from .. import score_v3


PASSING = {
    "FIELD_OFFSET_ACCURACY": 1.0,
    "ENDIANNESS_ACCURACY": 1.0,
    "CHECKSUM_RECOVERY": True,
    "EXACT_PACKET_MATCH": "18/18",
    "BYTE_ACCURACY": 1.0,
    "HIGH_CONFIDENCE_WRONG": 0,
}

MODES = ["A_RAW_ONLY", "B_CONTROLLED_ACTIONS"]


def _scores(**overrides):
    return {m: dict(PASSING, **overrides) for m in MODES}


def test_gate_is_green_when_every_criterion_holds():
    assert score_v3.gate_verdict(_scores(), MODES)["verdict"] == "GREEN"


@pytest.mark.parametrize("metric,bad", [
    ("HIGH_CONFIDENCE_WRONG", 1),
    ("EXACT_PACKET_MATCH", "16/18"),
    ("BYTE_ACCURACY", 0.9648),
    ("FIELD_OFFSET_ACCURACY", 0.75),
    ("ENDIANNESS_ACCURACY", 0.75),
    ("CHECKSUM_RECOVERY", False),
])
def test_gate_is_red_when_any_single_criterion_fails(metric, bad):
    v = score_v3.gate_verdict(_scores(**{metric: bad}), MODES)
    assert v["verdict"] == "RED"
    assert {f["metric"] for f in v["failures"]} == {metric}


def test_one_high_confidence_wrong_is_enough_to_go_red():
    """The criterion that cannot be relaxed, stated as a test."""
    v = score_v3.gate_verdict(_scores(HIGH_CONFIDENCE_WRONG=1), MODES)
    assert v["verdict"] == "RED"


def test_a_failure_in_one_mode_alone_still_goes_red():
    s = _scores()
    s["B_CONTROLLED_ACTIONS"]["HIGH_CONFIDENCE_WRONG"] = 2
    v = score_v3.gate_verdict(s, MODES)
    assert v["verdict"] == "RED"
    assert v["failures"][0]["mode"] == "B_CONTROLLED_ACTIONS"


def test_mode_c_cannot_rescue_a_failing_gate_mode():
    """Mode C is handed the schema; it is never allowed into the verdict."""
    s = _scores(HIGH_CONFIDENCE_WRONG=3)
    s["C_PARTIAL_PROTOCOL"] = dict(PASSING)
    assert score_v3.gate_verdict(s, MODES)["verdict"] == "RED"


# --------------------------------------------------------------------------
# freeze verification
# --------------------------------------------------------------------------

def _fake_freeze(tmp_path):
    frozen = tmp_path / "frozen_fake"
    frozen.mkdir()
    (frozen / "predictions_FAKE.json").write_text('{"mode": "FAKE"}', encoding="utf-8")
    manifest = {
        "label": "fake",
        "engine_module": "engine_v3",
        "gate_modes": ["FAKE"],
        "engine_files": {
            f: hashlib.sha256((score_v3.HERE / f).read_bytes()).hexdigest()
            for f in ("engine_v3.py", "score_v3.py")},
        "datasets": {},
        "predictions": {
            "FAKE": hashlib.sha256(
                (frozen / "predictions_FAKE.json").read_bytes()).hexdigest()},
    }
    m = frozen / "MANIFEST.sha256.json"
    m.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (frozen / "MANIFEST.digest.txt").write_text(
        hashlib.sha256(m.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    return frozen


def test_intact_freeze_verifies(tmp_path):
    assert score_v3.verify_freeze(_fake_freeze(tmp_path))["label"] == "fake"


def test_edited_prediction_is_refused(tmp_path):
    frozen = _fake_freeze(tmp_path)
    (frozen / "predictions_FAKE.json").write_text('{"mode": "FAKE!"}', encoding="utf-8")
    with pytest.raises(SystemExit, match="FREEZE VIOLATION"):
        score_v3.verify_freeze(frozen)


def test_edited_manifest_is_refused_by_its_own_digest(tmp_path):
    frozen = _fake_freeze(tmp_path)
    m = frozen / "MANIFEST.sha256.json"
    d = json.loads(m.read_text(encoding="utf-8"))
    d["engine_files"]["engine_v3.py"] = "0" * 64
    m.write_text(json.dumps(d, indent=1), encoding="utf-8")
    with pytest.raises(SystemExit, match="own digest"):
        score_v3.verify_freeze(frozen)


def test_changed_engine_source_is_refused(tmp_path):
    frozen = _fake_freeze(tmp_path)
    m = frozen / "MANIFEST.sha256.json"
    d = json.loads(m.read_text(encoding="utf-8"))
    d["engine_files"]["engine_v3.py"] = "0" * 64
    m.write_text(json.dumps(d, indent=1), encoding="utf-8")
    (frozen / "MANIFEST.digest.txt").write_text(
        hashlib.sha256(m.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since the freeze"):
        score_v3.verify_freeze(frozen)


def test_missing_digest_is_refused(tmp_path):
    frozen = _fake_freeze(tmp_path)
    (frozen / "MANIFEST.digest.txt").unlink()
    with pytest.raises(SystemExit, match="digest"):
        score_v3.verify_freeze(frozen)


def test_scorer_has_no_drift_bypass(tmp_path):
    """score.py takes `strict=False` and `--allow-drift`.  v3 takes neither.

    A gate with a documented bypass is not a gate, so the refusal has to be
    unconditional rather than merely on by default.
    """
    import inspect

    from .. import score as score_v1
    assert "strict" in inspect.signature(score_v1.verify_freeze).parameters
    assert list(inspect.signature(score_v3.verify_freeze).parameters) == ["frozen"]

    frozen = _fake_freeze(tmp_path)
    (frozen / "predictions_FAKE.json").write_text("{}", encoding="utf-8")
    for kwargs in ({"strict": False}, {"allow_drift": True}):
        with pytest.raises(TypeError):
            score_v3.verify_freeze(frozen, **kwargs)
    with pytest.raises(SystemExit, match="FREEZE VIOLATION"):
        score_v3.verify_freeze(frozen)
