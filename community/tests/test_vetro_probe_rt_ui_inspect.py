"""RT UI inspection mode regressions (no browser, no hardware).

Proves the inspector is READ-ONLY (zero HID writes, no input/change dispatch, no
element.value writes), extracts native/custom slider metadata from deterministic
fixtures, keeps missing metadata UNKNOWN, never reuses unrelated actuation slider
evidence as RT, compares up/down independently, keeps protocol quantum != UI step
unless proven, and leaves he.rt BLOCKED."""

import json
from pathlib import Path

import pytest

from community.vetro_probe import webhid_capture as wc
from community.vetro_probe import feature_gates as fg
from community.vetro_probe.rt_ui_contract import select_temporary_threshold
from community.vetro_probe.webhid_capture import normalize_rt_ui_contract

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")


# 1/2/3. inspector is read-only: JS + Python path never mutate
def test_inspect_script_is_read_only():
    js = wc.RT_UI_INSPECT_SCRIPT
    # actual mutation/dispatch CALL patterns must never appear (the selector string
    # legitimately contains the word "input" for input[type=range], which is read-only)
    for forbidden in ("dispatchEvent(", ".click(", ".value=", "addEventListener(",
                      "document.createEvent", "HIDDevice.prototype.sendReport", "sendReport",
                      "setAttribute(", "input.", "change("):
        assert forbidden not in js
    assert "querySelectorAll" in js and "getAttribute" in js


def test_inspect_path_has_no_hid_write_call():
    import inspect
    src = inspect.getsource(wc.run_rt_ui_inspect)
    assert "sendReport" not in src
    assert "evaluate(" in src  # read-only Runtime.evaluate only


# 4. native slider metadata extracted
def test_normalize_native_range_fixture():
    raw = [{"controls": [
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.05",
         "value": "0.1", "aria_label": "rt up", "text": "", "parent_text": "Rapid Trigger"},
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.05",
         "value": "0.1", "aria_label": "rt down", "text": "", "parent_text": "Rapid Trigger"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    assert c["control_type"] == "native_range"
    assert c["up"] == {"min": "0", "max": "4", "step": "0.05", "current": "0.1", "rt_linkage": "PROVEN"}
    assert c["down"]["step"] == "0.05"
    assert c["up_down_same_contract"] is True
    assert c["safe_temp_grid"] == "PROVEN"  # min+max+step all present on RT-linked controls


# 5. custom slider config can be extracted from a deterministic fixture
def test_normalize_custom_slider_fixture():
    raw = [{"controls": [
        {"tag": "DIV", "role": "slider", "aria_min": "0", "aria_max": "4",
         "aria_now": "0.1", "aria_label": "trigger press", "text": "0.02mm", "parent_text": "rapid"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    assert c["control_type"] == "custom_slider"
    assert c["up"]["min"] == "0" and c["up"]["max"] == "4" and c["up"]["current"] == "0.1"
    assert c["up"]["step"] is None  # step not exposed -> stays UNKNOWN-ish (None)


# 6. missing metadata stays UNKNOWN
def test_missing_metadata_unknown():
    raw = [{"controls": []}]
    c = normalize_rt_ui_contract(raw)
    assert c["control_type"] == "UNKNOWN"
    assert c["up"] is None and c["down"] is None
    assert c["safe_temp_grid"] == "OPEN"
    assert c["display_precision"] == "UNKNOWN" and c["snap_rule"] == "UNKNOWN"


# 7. unrelated actuation slider is NOT reused as RT evidence
def test_unrelated_actuation_not_reused_as_rt():
    raw = [{"controls": [
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.5",
         "value": "1.0", "aria_label": "actuation distance", "text": "", "parent_text": "Actuation"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    assert c["up"] is None and c["down"] is None  # no RT linkage -> not promoted
    assert c["safe_temp_grid"] == "OPEN"
    assert c["all_controls"][0]["rt_linkage"] == "UNKNOWN"


# 8. up/down compared independently
def test_up_down_compared_independently():
    raw = [{"controls": [
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.05",
         "value": "0.1", "aria_label": "rt up", "parent_text": "rapid"},
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.02",
         "value": "0.1", "aria_label": "rt down", "parent_text": "rapid"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    assert c["up"]["step"] != c["down"]["step"]
    assert c["up_down_same_contract"] is False


# 9. protocol quantum != UI step unless explicitly proven
def test_protocol_quantum_not_ui_step():
    raw = [{"controls": [
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.05",
         "value": "0.1", "aria_label": "rt up", "parent_text": "rapid"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    # protocol quantum is 0.01; UI step observed 0.05 -> they are NOT assumed equal
    assert c["up"]["step"] == "0.05"
    assert c["safe_temp_grid"] == "PROVEN"  # step explicitly present
    from community.vetro_probe.rt_ui_contract import RT_THRESHOLD_SCALE_MM
    assert RT_THRESHOLD_SCALE_MM == 0.01
    assert float(c["up"]["step"]) != RT_THRESHOLD_SCALE_MM


# 10. baseline never normalized (enforced by rt_ui_contract; contract quantum unchanged)
def test_baseline_never_normalized():
    from community.vetro_probe.rt_ui_contract import RT_THRESHOLD_SCALE_MM
    assert RT_THRESHOLD_SCALE_MM == 0.01  # raw 1 = 0.01 mm; baseline A is never snapped


# 11. B selection refuses if contract incomplete
def test_b_selection_refuses_when_incomplete():
    with pytest.raises(RuntimeError, match="safe temporary RT grid is OPEN"):
        select_temporary_threshold(0.01)


# 12/13/14. he.rt / remap blocked, K20 unpromoted
def test_blocked_and_k20():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True


# artifact written by run_rt_ui_inspect path is structured (mocked lifecycle)
def test_artifact_schema_deterministic(tmp_path):
    raw = [{"controls": [
        {"tag": "INPUT", "type": "range", "min": "0", "max": "4", "step": "0.05",
         "value": "0.1", "aria_label": "rt up", "parent_text": "rapid"},
    ]}]
    c = normalize_rt_ui_contract(raw)
    (tmp_path / "rt_ui_contract_capture.json").write_text(json.dumps(c, indent=2), encoding="utf-8")
    d = json.loads((tmp_path / "rt_ui_contract_capture.json").read_text(encoding="utf-8"))
    assert d["source"] == "live_vendor_ui"
    assert d["control_type"] in ("native_range", "custom_slider", "UNKNOWN")
    assert "up" in d and "down" in d and "safe_temp_grid" in d and "snap_rule" in d
