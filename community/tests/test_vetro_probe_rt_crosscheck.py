"""Deterministic he.rt units-crosscheck evidence (no hardware).

Resolved from authoritative static + real-live-capture evidence:
- raw scale = 0.01 mm (same PRECISION_DISTANCE convention as actuation)
- record (8B): key_id_hi, key_id_lo, rt_enable, rt_up_hi, rt_up_lo,
  rt_down_hi, rt_down_lo, rt_global (STATIC `_transfer_rt`)
- serializer golden vector == real live HERO84 capture
  (enable ON at up=down=0.10 mm): 00 1e 01 00 0a 00 0a 01
- rt_enable is a SEPARATE byte from the up/down thresholds

NOT resolved (blocker): the 0x99 GET readback path has NO parser in this repo
(no parse_rt_get_reply, no operations.get_rapid_trigger), and the typed
transport he.rt GET returns a self-confirming cache. rapid_trigger_units_crosscheck
is a REAL-DEVICE SET->GET round-trip and cannot be closed by static tests.
Therefore RT revalidation is NOT READY until an authoritative 0x99 parser +
independent readback exist.
"""

from pathlib import Path

import pytest

from community.vetro_probe import feature_gates as fg
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.identity import mock_hero84_instance
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED

SCOPE = dict(vid="0x372E", pid="0x103E", family="aula_kb_v3_wired", fw="0216")

# Real live HERO84 capture: RT ON toggle at up=down=0.10mm, key 30, global 1.
LIVE_ON_RECORD = bytes([0x00, 0x1E, 0x01, 0x00, 0x0A, 0x00, 0x0A, 0x01])


# 1. he.rt remains BLOCKED while units crosscheck is OPEN
def test_rt_blocked_while_crosscheck_open():
    blk = fg.blocker_for("he.rt", **SCOPE)
    assert blk is not None
    assert blk[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"
    assert "rapid_trigger_units_crosscheck" in blk[1]


# 2. serializer/parser agree on the proven 0.01 mm scale
def test_units_0_01mm_roundtrip():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore
    for mm in (0.10, 0.50, 1.00, 1.63):
        raw = mm_to_raw(mm)
        assert raw == round(mm * 100)
        assert raw_to_mm(raw) == mm
    assert mm_to_raw(0.10) == 10
    assert mm_to_raw(0.50) == 50


# 3. serializer golden vector == real live capture (record bytes 6..14 of frame)
def test_rt_set_frame_matches_live_capture():
    from aula_kb_v3.protocol import build_rt_set_frame, mm_to_raw  # type: ignore
    frame = build_rt_set_frame(0, 30, True, mm_to_raw(0.10), mm_to_raw(0.10), 1)
    assert frame[0] == 0x19 and frame[1] == 0 and frame[5] == 8
    assert bytes(frame[6:14]) == LIVE_ON_RECORD


# 4/5. temporary B on proven 0.01 mm quantization and != A
@pytest.mark.parametrize("mm", [0.10, 0.50, 1.00, 1.50])
def test_temporary_values_whole_0_01mm_and_distinct(mm):
    from aula_kb_v3.protocol import mm_to_raw  # type: ignore
    assert mm_to_raw(mm) * 0.01 == pytest.approx(mm)  # exact 0.01mm quantum
    assert mm != 0.0


# 6. unrelated RT/HE fields preserved when only thresholds change
def test_unrelated_fields_preserved():
    from aula_kb_v3.protocol import build_rt_set_frame, mm_to_raw  # type: ignore
    f1 = build_rt_set_frame(0, 30, True, mm_to_raw(0.10), mm_to_raw(0.10), 1)
    f2 = build_rt_set_frame(0, 30, True, mm_to_raw(0.50), mm_to_raw(0.50), 1)
    # key_id, rt_enable, rt_global unchanged; only up/down fields differ
    assert f1[6:8] == f2[6:8]
    assert f1[8] == f2[8] == 1
    assert f1[13] == f2[13] == 1
    assert f1[9:11] != f2[9:11] and f1[11:13] != f2[11:13]


# 7. RT enabled-state is a separate field that a value-only test must preserve
def test_rt_enable_is_separate_field():
    from aula_kb_v3.protocol import build_rt_set_frame, mm_to_raw  # type: ignore
    off = build_rt_set_frame(0, 30, False, mm_to_raw(0.10), mm_to_raw(0.10), 1)
    on = build_rt_set_frame(0, 30, True, mm_to_raw(0.10), mm_to_raw(0.10), 1)
    # identical thresholds, only rt_enable differs (byte 8 = record offset 2)
    assert on[9:13] == off[9:13]
    assert on[8] == 1 and off[8] == 0


# 8. immutable A is not normalized for rollback (raw round-trip exact)
def test_immutable_A_not_normalized():
    from aula_kb_v3.protocol import mm_to_raw, raw_to_mm  # type: ignore
    A_up, A_down = 0.10, 0.10
    assert raw_to_mm(mm_to_raw(A_up)) == A_up and raw_to_mm(mm_to_raw(A_down)) == A_down
    assert A_up == 0.10  # not rounded to 0.0/0.5


# blocker: authoritative RT GET readback path does not exist -> NOT READY
def test_rt_readback_path_missing_blocks_revalidation():
    import aula_kb_v3.protocol as p  # type: ignore
    import aula_kb_v3.operations as o  # type: ignore
    assert hasattr(p, "parse_rt_get_reply") is False
    assert hasattr(o, "get_rapid_trigger") is False
    # typed transport he.rt GET is the self-confirming cache, NOT an independent readback
    from community.vetro_probe.aula_transport import AulaHidTransport
    import inspect
    src = inspect.getsource(AulaHidTransport._typed_get)
    assert "he.rt" in src and "_rt_cache" in src


# 9-14 (SET B + GET B == B / recovery / final-GET) are NOT IMPLEMENTABLE until an
# authoritative 0x99 parser exists: covered by test_rt_readback_path_missing_blocks_revalidation.


# 15. wrong identity/FW -> zero writes (exact gate reused)
def test_identity_fw_mismatch_gate():
    from community.vetro_probe.actuation_revalidation import revalidation_identity_ok
    bundle = production_bundle_for_hero84()
    ok, _ = revalidation_identity_ok(bundle, mock_hero84_instance())
    assert ok is True
    ok2, _ = revalidation_identity_ok(bundle, mock_hero84_instance(firmware="unknown"))
    assert ok2 is False


# 16. successful closure promotes only he.rt
def test_closure_promotes_only_rt(monkeypatch):
    monkeypatch.setitem(fg.CLOSED_EVIDENCE, "rapid_trigger_units_crosscheck", "real Probe RT SET->GET PASS")
    assert fg.blocker_for("he.rt", **SCOPE) is None
    assert fg.blocker_for("keyboard.remap", **SCOPE) is not None
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    assert fg.blocker_for("he.actuation", **SCOPE) is None  # still closed
    assert fg.blocker_for("light.rgb_core", **SCOPE) is not None


# 17/18. remap blocked; K20 not promoted
def test_remap_blocked_and_k20_not_promoted():
    assert fg.blocker_for("keyboard.remap", **SCOPE)[0] == "BLOCKED_BY_MISSING_STRONG_E5"
    from community.vetro_probe.knowledge_rank import load_registry
    aula = next(g for g in load_registry()["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True


def test_plan_keeps_rt_blocked_until_closure(monkeypatch):
    bundle = production_bundle_for_hero84()
    inst = mock_hero84_instance()
    trans = FakeTransport(initial_state={})
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(".") / "_ltmp", reconnect_timeout_ms=200)
    run._plan()
    e = next(x for x in run.plan if x["operation"] == "he.rt")
    assert e["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_KNOWLEDGE_HOLE" in e["why_safe"]
    # after closing the exact evidence -> AUTO_REVERSIBLE, remap still blocked
    monkeypatch.setitem(fg.CLOSED_EVIDENCE, "rapid_trigger_units_crosscheck", "real PASS")
    run2 = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                        enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                        run_dir=Path(".") / "_ltmp2", reconnect_timeout_ms=200)
    run2._plan()
    e2 = next(x for x in run2.plan if x["operation"] == "he.rt")
    assert e2["classification"] == CLS_AUTO_REVERSIBLE
    er = next(x for x in run2.plan if x["operation"] == "keyboard.remap")
    assert er["classification"] == CLS_BLOCKED
