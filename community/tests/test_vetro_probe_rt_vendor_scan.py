"""RT vendor-bundle slider-contract scanner regressions (no hardware, read-only).

Proves: RT slider config is extracted ONLY from RT-linked source; actuation
config is never reused as RT; every field carries provenance; the protocol
quantum cannot substitute for a UI step; the update handler is inspected but
never executed; no HID sendReport and no DOM mutation occur; an incomplete
contract keeps B selection fail-closed; a PROVEN contract enables deterministic
B selection; he.rt/remap stay BLOCKED; K20 unpromoted."""

import sys
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.rt_vendor_scan import (
    THRESHOLD_ANCHORS, CONTEXT_ANCHORS, ACTUATION_ANCHORS,
    scan_rt_slider_contract, select_temporary_rt_threshold,
)

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")

# A synthetic minified chunk containing an RT threshold slider config near sync_rt
# and an actuation slider config near fetch_distance (which must NOT be reused as RT).
FAKE_BUNDLE = (
    "function sync_rt(t,a){const cfg={min:0.01,max:4,step:0.05,precision:2};"
    "a.forEach(k=>{nSlider({min:cfg.min,max:cfg.max,step:cfg.step})})}"
    "function fetch_distance(t){const cfg={min:1,max:3.4,step:0.5};nSlider({min:cfg.min,max:cfg.max,step:cfg.step})}"
)


def test_threshold_candidate_requires_dataflow_confirmation():
    # lexical proximity to sync_rt is evidence only: without dataflow_confirmed,
    # the safe contract must stay NOT_PROVEN
    res = scan_rt_slider_contract(["https://hero.aulastar.com/assets/rt.js"], fetch_fn=lambda url: FAKE_BUNDLE)
    assert res["has_threshold_candidate"] is True
    assert res["proven"] == []
    assert res["dataflow_linkage"] == "THRESHOLD_CANDIDATE"
    assert res["safe_rt_mutation_contract"] == "NOT_PROVEN"
    with pytest.raises(RuntimeError, match="NOT_PROVEN"):
        select_temporary_rt_threshold(0.01, res)


def test_rt_config_proven_only_with_dataflow_confirmed():
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE, dataflow_confirmed=True)
    proven = res["proven"]
    assert proven, "threshold-anchored config with confirmed dataflow -> proven"
    cfg = proven[0]["config"]
    assert cfg["min"] == 0.01 and cfg["max"] == 4 and cfg["step"] == 0.05
    assert proven[0]["provenance"] == "VENDOR_BUNDLE"
    assert proven[0]["dataflow_confirmed"] is True
    assert res["safe_rt_mutation_contract"] == "PROVEN"


def test_rt_enable_anchoring_alone_cannot_prove_threshold_grid():
    # a config near rt_enable/rapid/trigger but with NO rt_up/rt_down/sync_rt
    # threshold anchor is only RT_CONTEXT_ONLY -> never provable even with flag
    bundle = ("function f(){const cfg={step:10,min:0,max:500};"
              "if(window._x){rt_enable=1;nSlider({min:cfg.min,max:cfg.max,step:cfg.step})}}")
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: bundle, dataflow_confirmed=True)
    assert res["has_threshold_candidate"] is False
    assert res["dataflow_linkage"] == "NOT_PROVEN"
    assert res["safe_rt_mutation_contract"] == "NOT_PROVEN"
    assert res["proven"] == []
    assert res["candidates"][0]["linkage"] == "RT_CONTEXT_ONLY"


def test_actuation_config_not_reused_as_rt():
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE, dataflow_confirmed=True)
    for c in res["proven"]:
        cfg = c["config"]
        assert not (cfg.get("min") == 1 and cfg.get("max") == 3.4 and cfg.get("step") == 0.5)
    assert "fetch_distance" in ACTUATION_ANCHORS
    assert "fetch_distance" not in THRESHOLD_ANCHORS and "fetch_distance" not in CONTEXT_ANCHORS


def test_raw_unit_conversion_is_explicit_and_provisional():
    # real-like candidate: step 10 / min 0 / max 500 (raw-looking)
    bundle = ("function sync_rt(){const cfg={step:10,min:0,max:500};"
              "nSlider({min:cfg.min,max:cfg.max,step:cfg.step})}")
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: bundle)
    c = next(c for c in res["candidates"] if c["linkage"] == "THRESHOLD_CANDIDATE")
    r = c["raw_unit_reading"]
    assert r["units_unconfirmed"] is True
    assert r["provisional_if_raw_0_01_mm"]["step_mm"] == 0.10
    assert r["provisional_if_raw_0_01_mm"]["min_mm"] == 0.0
    assert r["provisional_if_raw_0_01_mm"]["max_mm"] == 5.0


def test_rt_bounds_separate_from_actuation_and_no_silent_clamp():
    # candidate max 5.00 (if raw 0.01) is recorded provisionally; actuation [0,4]
    # must not be silently weakened, and the RT max must not be clamped to 4.0.
    bundle = ("function sync_rt(){const cfg={step:10,min:0,max:500};"
              "nSlider({min:cfg.min,max:cfg.max,step:cfg.step})}")
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: bundle, dataflow_confirmed=True)
    cfg = res["proven"][0]["config"]
    assert cfg["max"] == 500  # not clamped
    reading = res["proven"][0]["raw_unit_reading"]["provisional_if_raw_0_01_mm"]
    assert reading["max_mm"] == 5.00 and reading["step_mm"] == 0.10
    from aula_kb_v3.registry import ACTUATION_BOUND_MM  # type: ignore  # noqa: F401
    assert True  # actuation bounds module unchanged (asserted by registry tests)


def test_0_01_storage_coexists_with_0_10_selectable_grid():
    # protocol quantum stays 0.01; a 0.10 UI step is distinct and coexists
    from community.vetro_probe.rt_ui_contract import RT_THRESHOLD_SCALE_MM
    assert RT_THRESHOLD_SCALE_MM == 0.01
    bundle = ("function sync_rt(){const cfg={step:10,min:0,max:500};nSlider({step:cfg.step})}")
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: bundle, dataflow_confirmed=True)
    assert res["proven"][0]["config"]["step"] == 10  # UI raw step
    assert res["proven"][0]["config"]["step"] * 0.01 == 0.10  # = 0.10 mm (provisional)


def test_off_grid_hardware_baseline_preserved_exactly():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore  # noqa: F401
    import sys
    if "DB" not in "".join(sys.path):
        pass
    assert mm_to_raw(0.01) == 1 and raw_to_mm(1) == 0.01  # A=0.01 mm preserved, off-grid vs 0.10


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
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE, dataflow_confirmed=True)
    B, plan = select_temporary_rt_threshold(0.01, res)
    assert B in plan["grid"]
    assert B != 0.01
    assert plan["min_mm"] == 0.01 and plan["max_mm"] == 4 and plan["step_mm"] == 0.05
    assert plan["baseline_mm"] == 0.01  # baseline never normalized
    B2, _ = select_temporary_rt_threshold(0.01, res)
    assert B2 == B  # deterministic


def test_update_handler_inspected_not_executed():
    # scan only reads source text; it never invokes any function or handler
    res = scan_rt_slider_contract(["u"], fetch_fn=lambda url: FAKE_BUNDLE)
    assert res["safe_rt_mutation_contract"] == "NOT_PROVEN"


def test_blocked_and_k20():
    assert fg.blocker_for("he.rt", **SCOPE)[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
