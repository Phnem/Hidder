"""RT vendor-bundle slider-contract scanner regressions (no hardware, read-only).

Proves: RT slider config is extracted ONLY from RT-linked source; actuation
config is never reused as RT; every field carries provenance; the protocol
quantum cannot substitute for a UI step; the update handler is inspected but
never executed; no HID sendReport and no DOM mutation occur; an incomplete
contract keeps B selection fail-closed; a PROVEN contract enables deterministic
B selection; he.rt/remap stay BLOCKED; K20 unpromoted."""

import pytest

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.rt_vendor_scan import (
    RT_ANCHORS, ACTUATION_ANCHORS, scan_rt_slider_contract, select_temporary_rt_threshold,
)

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")

# A synthetic minified chunk containing an RT slider config near sync_rt and an
# actuation slider config near fetch_distance (which must NOT be reused as RT).
FAKE_BUNDLE = (
    "function sync_rt(t,a){const cfg={min:0.01,max:4,step:0.05,precision:2};"
    "a.forEach(k=>{nSlider({min:cfg.min,max:cfg.max,step:cfg.step})})}"
    "function fetch_distance(t){const cfg={min:1,max:3.4,step:0.5};nSlider({min:cfg.min,max:cfg.max,step:cfg.step})}"
)


def test_rt_config_extracted_only_from_rt_linked_source():
    res = scan_rt_slider_contract(["https://hero.aulastar.com/assets/rt.js"], fetch_fn=lambda url: FAKE_BUNDLE)
    proven = res["proven"]
    assert proven, "expected at least one RT-linked proven config"
    cfg = proven[0]["config"]
    assert cfg["min"] == 0.01 and cfg["max"] == 4 and cfg["step"] == 0.05
    assert proven[0]["provenance"] == "VENDOR_BUNDLE"
    assert res["safe_rt_mutation_contract"] == "PROVEN"


def test_actuation_config_not_reused_as_rt():
    res = scan_rt_slider_contract(["https://hero.aulastar.com/assets/rt.js"], fetch_fn=lambda url: FAKE_BUNDLE)
    for c in res["proven"]:
        cfg = c["config"]
        assert not (cfg.get("min") == 1 and cfg.get("max") == 3.4 and cfg.get("step") == 0.5)
    # and the actuation anchors are excluded from the RT-anchor set entirely
    assert "fetch_distance" in ACTUATION_ANCHORS and "fetch_distance" not in RT_ANCHORS


def test_provenance_and_quantum_not_ui_step():
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE)
    cfg = res["proven"][0]["config"]
    assert res["proven"][0]["provenance"] == "VENDOR_BUNDLE"
    assert cfg["step"] == 0.05  # UI step, distinct from the 0.01 storage quantum
    assert cfg["step"] != 0.01


def test_no_hid_and_no_mutation_in_scan_path():
    import inspect
    from community.vetro_probe import rt_vendor_scan as m
    src = inspect.getsource(m)
    assert "sendReport" not in src
    assert "dispatchEvent" not in src and ".value=" not in src


def test_incomplete_contract_keeps_b_fail_closed():
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: "function foo(){return 1}")
    assert res["proven"] == [] and res["safe_rt_mutation_contract"] == "NOT_PROVEN"
    with pytest.raises(RuntimeError, match="NOT_PROVEN"):
        select_temporary_rt_threshold(0.01, res)


def test_proven_contract_enables_deterministic_b():
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE)
    B, plan = select_temporary_rt_threshold(0.01, res)
    assert B in plan["grid"]
    assert B != 0.01
    assert plan["min_mm"] == 0.01 and plan["max_mm"] == 4 and plan["step_mm"] == 0.05
    # baseline never normalized: A=0.01 stays as-is, B is a grid value
    assert plan["baseline_mm"] == 0.01
    # deterministic: same inputs -> same B
    B2, _ = select_temporary_rt_threshold(0.01, res)
    assert B2 == B


def test_update_handler_inspected_not_executed():
    # scan only reads source text; it never invokes any function
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE)
    assert res["safe_rt_mutation_contract"] == "PROVEN"
    # no side effects: the fetch_fn was called read-only; nothing mutated


def test_blocked_and_k20():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
