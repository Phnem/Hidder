"""Lighting mapping regression (v5): old 0x01 mapping REJECTED; light.brightness
physically closed for exact HERO84/FW0216; per-operation eligibility only — no
single lighting_auto_eligible flag that would unlock RGB/effect/custom."""

import json
from pathlib import Path

from community.vetro_probe.brand_router import resolve


def _mapping():
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_lighting_old_mapping_rejected():
    d = _mapping()
    assert d["schema"] == "vetro.lighting-mapping.v5"
    assert d["old_mapping"]["status"] == "REJECTED"
    assert "contradicted_by_real_vendor_app_capture" in d["old_mapping"]["reason"]
    assert d["authoritative_baseline_available"] is True
    # K14 rollback is closed for brightness only (per-operation), never global.
    assert d["rollback_proven"] is True
    assert "light.brightness" in d["k14_rollback"]
    # NO single global lighting auto flag: eligibility is per-feature.
    assert "auto_eligible" not in d
    assert d["auto_eligibility"]["note"].startswith("per-operation")


def test_lighting_brightness_physically_validated():
    d = _mapping()
    br = d["features"]["light.brightness"]
    assert br["status"] == "KNOWN_PHYSICALLY_VALIDATED"
    assert br["auto_reversible"] is True
    assert br["register_offset"] == 5
    assert br["frame_offset"] == 11
    assert br["observed_range"] == "0x00..0x14 (0..20)"
    assert "fresh final GET == immutable A" in br["final_restore_verification"]
    for claim in ("K13_global_lighting_baseline", "K14_light_brightness_rollback",
                  "K18_light_brightness_observable_readback", "K19_light_brightness_hardware_validation"):
        assert d["physically_closed"][claim] == "PHYSICALLY_CLOSED"
    assert d["physically_closed"]["not_generalized"]


def test_lighting_other_features_not_auto():
    d = _mapping()
    assert d["features"]["light.global_color"]["auto_reversible"] is False
    assert d["features"]["light.global_color"]["auto_reason"]
    assert d["features"]["light.effect"]["status"] == "UNRESOLVED_ENUM"
    assert d["features"]["light.effect"]["auto_reversible"] is False
    assert d["features"]["light.speed"]["auto_reversible"] is False
    assert d["features"]["light.direction"]["auto_reversible"] is False
    assert d["features"]["custom.per_key"]["auto_reversible"] is False
    assert d["features"]["light.edge_light"]["auto_reversible"] is False
    # eligibility map matches per-feature flags
    assert d["auto_eligibility"]["light.brightness"] == "AUTO_REVERSIBLE"
    assert d["auto_eligibility"]["light.global_color"] == "NOT_AUTO_VALIDATED"
    assert d["auto_eligibility"]["light.effect"] == "BLOCKED"
    assert d["auto_eligibility"]["custom.per_key"] == "BLOCKED"


def test_lighting_probe_harness_imports_and_snapshot_shape():
    import community.vetro_probe.lighting_probe as lp

    probe = lp.LightingProbe.__new__(lp.LightingProbe)
    # snapshot shape without touching hardware
    assert "snapshot" in lp.LightingProbe.__dict__
    assert "read_register" in lp.LightingProbe.__dict__
    assert "read_group" in lp.LightingProbe.__dict__


def test_light_rgb_core_stays_blocked_and_brightness_auto():
    from community.vetro_probe.automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED
    from community.vetro_probe.bundle import production_bundle_for_hero84
    from community.vetro_probe.transport import FakeTransport
    from community.vetro_probe.identity import mock_hero84_instance

    bundle = production_bundle_for_hero84()
    assert "light.brightness" in bundle.operations  # production bundle now exposes it
    state = {p.operation_id: 1.0 for p in __import__("community.vetro_probe.planner", fromlist=["plan"]).plan(bundle)}
    state["keyboard.polling"] = 3
    state["light.brightness"] = 10
    trans = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    inst = mock_hero84_instance()

    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(".") / "_ltmp", reconnect_timeout_ms=200,
                       block_knowledge_holes=True, block_missing_strong_e5=True)
    run._plan()
    rgb = next(e for e in run.plan if e["operation"] == "light.rgb_core")
    assert rgb["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_UNRESOLVED_LIGHTING_REGISTER" in rgb["why_safe"]
    br = next(e for e in run.plan if e["operation"] == "light.brightness")
    assert br["classification"] == CLS_AUTO_REVERSIBLE
    assert "PHYSICALLY CLOSED" in br["why_safe"]
    # informational non-auto lighting features stay blocked
    feats = {e["operation"]: e for e in run.plan}
    assert feats["light.global_color"]["classification"] == CLS_BLOCKED
    assert feats["light.effect"]["classification"] == CLS_BLOCKED
    assert feats["custom.per_key"]["classification"] == CLS_BLOCKED


def test_resolution_feature_scope_present_in_registry():
    # Brand-level registry carries the feature-scope note so the plan knows the
    # brightness path is closed while global_color/effect/custom stay blocked.
    res = resolve(brand="AULA", family="aula_kb_v3_wired", model="HERO 84 HE", firmware="0216")
    reg = __import__("community.vetro_probe.knowledge_rank", fromlist=["load_registry"]).load_registry()
    aula = next(g for g in reg["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["light.brightness"]["auto"] == "AUTO_REVERSIBLE"
    assert aula["lighting_feature_scope"]["light.global_color"]["auto"] == "NOT_AUTO_VALIDATED"
    assert aula["lighting_feature_scope"]["light.effect"]["auto"] == "BLOCKED"
    assert aula["lighting_feature_scope"]["custom.per_key"]["auto"] == "BLOCKED"
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
    assert res.brand == "AULA"
