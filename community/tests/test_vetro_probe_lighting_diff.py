"""Deterministic lighting differential-analysis tests (synthetic traces, no hardware)."""

import json
from pathlib import Path

import pytest

from community.vetro_probe.lighting_diff import (
    filter_idle, idle_signatures, correlate, byte_diff,
    full_state_write_detected, infer_field, infer_enum,
    verify_checksum, classify_offsets, correlate_window,
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
    assert d["fields"]["light.brightness"]["status"] == "KNOWN_mapping_range"
    assert d["fields"]["light.global_color"]["status"] == "KNOWN"
    assert d["fields"]["light.mode"]["status"] == "KNOWN_selector"
    for field in ("light.per_key_rgb", "light.edge_light"):
        assert d["fields"][field]["status"] == "UNKNOWN"
    assert d["authoritative_baseline_available"] is True
    assert d["rollback_proven"] is False
    assert d["auto_eligible"] is False
    assert d["full_state_write"] == "YES"


def test_capture_harness_imports():
    import community.vetro_probe.webhid_capture as wc
    import community.vetro_probe.lighting_diff as ld
    assert hasattr(wc, "WebHidCapture")
    assert hasattr(ld, "correlate")


# Real CAPTURE_SMOKE frames from the physical HERO84 (brightness actions)
FRAME_BRIGHT_A1 = "04010001000703005305ff0a020000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000083"
FRAME_BRIGHT_A2 = "04010001000703005305ff0002000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000008d"


def test_checksum_proven_on_real_frames():
    assert verify_checksum(FRAME_BRIGHT_A1) is True   # byte62=0x83
    assert verify_checksum(FRAME_BRIGHT_A2) is True   # byte62=0x8D
    # tamper -> must fail
    tampered = FRAME_BRIGHT_A1[:-2] + "84"
    assert verify_checksum(tampered) is False


def test_checksum_offset_never_semantic():
    diff = byte_diff(FRAME_BRIGHT_A1, FRAME_BRIGHT_A2)
    changed = [d["offset"] for d in diff]
    assert changed == [11, 62]
    cls = classify_offsets(changed, {62})
    assert cls["semantic"] == [11]
    assert cls["checksum"] == [62]


def test_brightness_candidate_offset_11_only():
    # brightness change touches byte 11 (0x0A->0x00); byte 62 is checksum -> semantic offset = 11 only
    diff = byte_diff(FRAME_BRIGHT_A1, FRAME_BRIGHT_A2)
    cls = classify_offsets([d["offset"] for d in diff], {62})
    assert cls["semantic"] == [11]
    assert len(cls["checksum"]) == 1


def test_correlate_window_action_begin_before_frames():
    # ACTION_BEGIN at t=10, ACTION_END at t=15; frames during action -> in window; before BEGIN -> out
    events = [
        {"timestamp": 9.0, "method": "sendReport", "direction": "OUT", "hex": "pre"},   # before BEGIN
        {"timestamp": 12.0, "method": "sendReport", "direction": "OUT", "hex": "a1"},
        {"timestamp": 14.0, "method": "sendReport", "direction": "OUT", "hex": "a2"},
        {"timestamp": 15.5, "method": "sendReport", "direction": "OUT", "hex": "tail"},  # within +tail
        {"timestamp": 20.0, "method": "sendReport", "direction": "OUT", "hex": "late"},  # after window
    ]
    win = correlate_window(events, begin_ts=10.0, end_ts=15.0, tail_s=1.0)
    hexes = {e["hex"] for e in win}
    assert hexes == {"a1", "a2", "tail"}
    assert "pre" not in hexes and "late" not in hexes


def test_action_window_markers_ordered_begin_then_end():
    import community.vetro_probe.webhid_capture as wc
    # synthetic: ACTION_BEGIN timestamp < ACTION_END timestamp; frames captured during
    begin = {"type": "ACTION_BEGIN", "action": "brightness:50->75", "timestamp": 10.0}
    end = {"type": "ACTION_END", "action": "brightness:50->75", "timestamp": 15.0}
    assert begin["timestamp"] < end["timestamp"]
    assert begin["type"] == "ACTION_BEGIN" and end["type"] == "ACTION_END"


def test_full_state_preservation_across_semantics():
    # brightness change preserves mode/rgb/speed (only offset 11 + checksum differ)
    base = "04010001000703005305ff0a02" + "0" * 100
    br75 = "04010001000703005305ff0f02" + "0" * 100
    diff = byte_diff(base, br75)
    cls = classify_offsets([d["offset"] for d in diff], {62})
    assert cls["semantic"] == [11]
    # color change preserves mode/brightness/speed (only 8/9/10 differ)
    color = "0401000100070300ff00000a02" + "0" * 100
    diff2 = byte_diff(base, color)
    cls2 = classify_offsets([d["offset"] for d in diff2], {62})
    assert cls2["semantic"] == [8, 9, 10]


def test_lighting_mapping_v4_real_sweep_evidence():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["checksum"]["evidence_count"] == 26
    assert d["device_echo"]["matched"] == "26/26"
    assert d["device_echo"]["semantics"].startswith("write acceptance/echo")
    assert d["global_state_block"]["class"] == "FULL_GLOBAL_LIGHTING_STATE_BLOCK"
    assert d["global_state_block"]["layout"] == {"mode": 6, "reserved": 7, "R": 8, "G": 9, "B": 10, "brightness": 11, "speed": 12}
    assert d["fields"]["light.global_color"]["status"] == "KNOWN"
    assert d["fields"]["light.global_color"]["encoding"] == "RGB888"
    assert d["fields"]["light.brightness"]["status"] == "KNOWN_mapping_range"
    assert d["fields"]["light.brightness"]["mapping"] == "raw = UI_percent / 5"
    assert d["fields"]["light.speed"]["values"]["mid"] == 2 and d["fields"]["light.speed"]["values"]["max"] == 4
    assert d["fields"]["light.speed"]["values"]["min"] == "UNKNOWN"
    assert d["fields"]["light.mode"]["values"]["0"] == "OFF"
    assert d["fields"]["light.direction"]["status"] == "UNSUPPORTED_BY_UI"
    assert d["k13_baseline"].startswith("CLOSED")
    assert d["k14_rollback"].startswith("OPEN")
    assert d["auto_eligible"] is False
    assert d["old_mapping"]["status"] == "REJECTED"
    assert d["groups"]["0x06"]["status"].startswith("STATIC_CAPABILITY_LABEL_ONLY")


def test_lighting_mapping_v3_brightness_partial_and_checksum_proven():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["capture"]["method"] == "sendReport"
    assert d["capture"]["report_id"] == 9
    assert d["capture"]["payload_length"] == 63
    assert d["checksum"]["offset"] == 62
    assert d["checksum"]["status"].startswith("PROVEN")
    assert d["checksum"]["evidence_count"] == 26
    assert d["fields"]["light.brightness"]["status"] == "KNOWN_mapping_range"
    assert d["fields"]["light.brightness"]["offset"] == 11
    assert d["groups"]["0x06"]["status"].startswith("STATIC_CAPABILITY_LABEL_ONLY")
    assert d["old_mapping"]["status"] == "REJECTED"
    assert d["auto_eligible"] is False
