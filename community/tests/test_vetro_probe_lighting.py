"""Lighting mapping regression: old 0x01 mapping must stay REJECTED, auto-ineligible."""

import json
from pathlib import Path

from community.vetro_probe.brand_router import resolve


def test_lighting_old_mapping_rejected():
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["old_mapping"]["status"] == "REJECTED"
    assert "contradicted_by_real_vendor_app_capture" in d["old_mapping"]["reason"]
    assert d["authoritative_baseline_available"] is False
    assert d["rollback_proven"] is False
    assert d["auto_eligible"] is False
    for field in ("light.enable", "light.brightness", "light.global_color", "light.effect",
                  "light.speed", "light.direction", "light.per_key_rgb", "light.edge_light"):
        assert d["fields"][field]["status"] == "UNKNOWN"


def test_lighting_probe_harness_imports_and_snapshot_shape():
    import community.vetro_probe.lighting_probe as lp

    probe = lp.LightingProbe.__new__(lp.LightingProbe)
    # snapshot shape without touching hardware
    assert "snapshot" in lp.LightingProbe.__dict__
    assert "read_register" in lp.LightingProbe.__dict__
    assert "read_group" in lp.LightingProbe.__dict__


def test_light_rgb_core_stays_blocked_on_real_policy():
    from community.vetro_probe.automation import AutoProbeRun, CLS_BLOCKED
    from community.vetro_probe.bundle import production_bundle_for_hero84
    from community.vetro_probe.transport import FakeTransport
    from community.vetro_probe.identity import mock_hero84_instance

    bundle = production_bundle_for_hero84()
    state = {p.operation_id: 1.0 for p in __import__("community.vetro_probe.planner", fromlist=["plan"]).plan(bundle)}
    state["keyboard.polling"] = 3
    trans = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    inst = mock_hero84_instance()

    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(".") / "_ltmp", reconnect_timeout_ms=200,
                       block_knowledge_holes=True, block_missing_strong_e5=True)
    run._plan()
    entry = next(e for e in run.plan if e["operation"] == "light.rgb_core")
    assert entry["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_UNRESOLVED_LIGHTING_REGISTER" in entry["why_safe"]
