"""Deterministic lighting differential-analysis tests (synthetic traces, no hardware)."""

import json
from pathlib import Path

import pytest

from community.vetro_probe.lighting_diff import (
    filter_idle, idle_signatures, correlate, byte_diff,
    full_state_write_detected, infer_field, infer_enum,
)

HEX = "06000001"
# idle background frames (heartbeat) that must be filtered


def _mk(ts, method, direction, hex_, action=None, annotation=None):
    return {"timestamp": ts, "method": method, "direction": direction,
            "hex": hex_, "action": action, "annotation": annotation}


def test_idle_filtering():
    idle = [_mk(0.0, "sendReport", "OUT", "06000001", action="idle"),
            _mk(0.1, "sendReport", "OUT", "06000002", action="idle"),
            _mk(0.2, "inputreport", "IN", "06000001", action="idle")]
    events = idle + [_mk(1.0, "sendReport", "OUT", "06010001", action="on")]  # not in idle
    sig = idle_signatures(idle)
    kept = filter_idle(events, sig)
    assert len(kept) == 1 and kept[0]["hex"] == "06010001"


def test_user_action_correlation():
    events = [
        {"type": "USER_ACTION", "action": "brightness", "timestamp": 0.0},
        _mk(0.1, "sendReport", "OUT", "06010001", action="brightness"),
        _mk(0.5, "sendReport", "OUT", "06010002", action="brightness"),
        {"type": "USER_ACTION", "action": "effect", "timestamp": 1.0},
        _mk(1.2, "sendReport", "OUT", "06020001", action="effect"),
    ]
    actions = [e for e in events if e.get("type") == "USER_ACTION"]
    correlated = correlate(events, actions, window_s=2.0)
    assert "brightness" in correlated and len(correlated["brightness"]) == 2
    assert "effect" in correlated and len(correlated["effect"]) == 1


def test_byte_diff():
    diffs = byte_diff("06010001", "06010002")
    assert diffs == [{"offset": 3, "a": "01", "b": "02"}]
    diffs_len = byte_diff("06010001", "060100")
    assert diffs_len[0]["note"] == "length differs"


def test_full_state_write_detected():
    # Different UI actions, same method+length, mostly invariant -> full-state write
    frames = [_mk(0.1, "sendReport", "OUT", "06010001", action="a"),
              _mk(0.2, "sendReport", "OUT", "06010002", action="b"),
              _mk(0.3, "sendReport", "OUT", "06010003", action="c"),
              _mk(0.4, "sendReport", "OUT", "06010004", action="d")]
    res = full_state_write_detected({"a": [frames[0]], "b": [frames[1]], "c": [frames[2]], "d": [frames[3]]})
    assert res["detected"] is True
    assert res["method"] == "sendReport"
    # Varying lengths -> changed-field (not full-state)
    var = [_mk(0.1, "sendReport", "OUT", "060100", action="a"),
           _mk(0.2, "sendReport", "OUT", "0601000102", action="b")]
    res2 = full_state_write_detected({"a": [var[0]], "b": [var[1]]})
    assert res2["detected"] is False


def test_field_inference_requires_multiple_samples():
    # One sample at an offset -> UNKNOWN (never claim from a single frame)
    one = [_mk(0.1, "sendReport", "OUT", "06010001", action="red")]
    fi = infer_field(one, 3, 1, min_samples=2)
    assert fi.status == "UNKNOWN"
    # Two distinct values at a stable offset -> KNOWN (RGB inference only after multiple samples)
    two = [_mk(0.1, "sendReport", "OUT", "06010001", action="red"),
           _mk(0.2, "sendReport", "OUT", "06010002", action="green")]
    fi2 = infer_field(two, 3, 1, min_samples=2)
    assert fi2.status == "KNOWN" and fi2.offset == 3 and fi2.evidence_count == 2


def test_enum_inference():
    frames = [_mk(0.1, "sendReport", "OUT", "06ff0001", action="static"),
              _mk(0.2, "sendReport", "OUT", "06ff0002", action="breathing"),
              _mk(0.3, "sendReport", "OUT", "06ff0003", action="animated")]
    en = infer_enum(frames, 3, 1, min_samples=3)
    assert en.status == "KNOWN"
    assert en.values == {"static": "01", "breathing": "02", "animated": "03"}


def test_lighting_mapping_schema_and_old_mapping_rejected():
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["old_mapping"]["status"] == "REJECTED"
    for field in ("light.enable", "light.brightness", "light.global_color", "light.effect",
                  "light.speed", "light.direction", "light.per_key_rgb", "light.edge_light"):
        rec = d["fields"][field]
        assert rec["status"] == "UNKNOWN"
        assert rec["evidence_count"] == 0 and rec["hardware_verified"] is False
    assert d["authoritative_baseline_available"] is False
    assert d["rollback_proven"] is False
    assert d["auto_eligible"] is False
    assert d["full_state_write"] == "UNKNOWN"


def test_capture_harness_imports():
    import community.vetro_probe.webhid_capture as wc
    import community.vetro_probe.lighting_diff as ld
    assert hasattr(wc, "WebHidCapture")
    assert hasattr(ld, "correlate")
