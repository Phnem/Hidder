"""light.brightness AUTO_REVERSIBLE integration (deterministic, no hardware).

Proves: exact HERO84/FW0216 scope gates brightness to AUTO_REVERSIBLE only;
runtime gates block everything else; the executor runs the proven K14 lifecycle
through the shared lighting_core primitives (canonical echo != readback, final
GET hard invariant, immutable-A rollback); global color / effect / per-key stay
blocked; the operation certificate carries per-step physical-validation claims;
K20 is never promoted.
"""

import json
from pathlib import Path

import pytest

from community.vetro_probe.automation import AutoProbeRun, CLS_AUTO_REVERSIBLE, CLS_BLOCKED
from community.vetro_probe.bundle import production_bundle_for_hero84, parse_bundle
from community.vetro_probe.bundle_export import export_bundle_for_uuid
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.identity import mock_hero84_instance, PhysicalInstance, descriptor_hash_from_bytes
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.baseline import BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.certificate import build_certificate
from community.vetro_probe.lighting_core import plan_brightness_temporary, read_light_state
from community.vetro_probe.lighting_probe import recover_pending_checkpoint, _rollback
from community.vetro_probe.runstate import RunCheckpoint

A = bytes([1, 0, 255, 0, 0, 10, 2])  # [mode,res,R,G,B,brightness,speed] brightness=10


# --------------------------------------------------------------- helpers
def _raw_bundle_dict():
    return export_bundle_for_uuid()


def _bundle_with_family(family: str):
    d = _raw_bundle_dict()
    d["family"] = family
    d.pop("hash", None)
    return parse_bundle(d)


def _bundle_with_fw(fw: str):
    d = _raw_bundle_dict()
    d["firmware"]["branch"] = fw
    d.pop("hash", None)
    return parse_bundle(d)


def _inst(vid="0x372E", pid="0x103E", fw="0216", family_hint=True):
    return PhysicalInstance(
        vid=vid, pid=pid,
        descriptor_hash=descriptor_hash_from_bytes(b"hero84-descriptor-v1"),
        firmware_version=fw, connection_mode="wired",
        interfaces=[0, 1, 2], report_ids=[0, 1, 8, 9],
        product_string="AULA HERO84 HE", manufacturer="AULA",
    )


def _plan_for(bundle, instance):
    trans = FakeTransport(initial_state={})
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=instance,
                       enumerate_fn=lambda: instance, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(".") / "_ltmp", reconnect_timeout_ms=200)
    run._plan()
    return run


def _entry(run, op):
    return next(e for e in run.plan if e["operation"] == op)


# ------------------------------------------------ 1..3 planner scope gates
def test_exact_hero84_fw0216_brightness_auto_reversible():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    assert _entry(run, "light.brightness")["classification"] == CLS_AUTO_REVERSIBLE
    assert "PHYSICALLY CLOSED" in _entry(run, "light.brightness")["why_safe"]


def test_wrong_firmware_brightness_blocked_zero_writes(tmp_path):
    bundle = _bundle_with_fw("9999")
    inst = _inst(fw="9999")
    trans = FakeTransport(initial_state={"light.brightness": 10})
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=200,
                       allowed_ops=["light.brightness"])
    run.run()
    assert trans.device.write_count == 0
    entry = _entry(run, "light.brightness")
    assert entry["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_LIGHT_BRIGHTNESS_SCOPE" in entry["why_safe"]


def test_wrong_identity_and_family_blocked():
    # wrong VID/PID
    run = _plan_for(production_bundle_for_hero84(), _inst(vid="0x1234"))
    assert _entry(run, "light.brightness")["classification"] == CLS_BLOCKED
    # wrong family
    run2 = _plan_for(_bundle_with_family("kb_by_v3_wired"), _inst())
    assert _entry(run2, "light.brightness")["classification"] == CLS_BLOCKED


# ------------------------------------------------------- 4..6 runtime gates
def test_baseline_get_failure_blocks_brightness(tmp_path):
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={"light.brightness": 10})
    trans.device.get_fail_sessions.update({1})  # initial session GET fails
    inst = _inst()
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=200,
                       allowed_ops=["light.brightness"])
    run._plan()
    run._baseline()
    entry = _entry(run, "light.brightness")
    assert entry["classification"] == CLS_BLOCKED
    assert "baseline unavailable" in entry["why_safe"]
    assert trans.device.write_count == 0


def test_baseline_brightness_out_of_range_blocks():
    # Out-of-range brightness surfaces as a GET failure in the real transport path
    # (AulaHidTransport._typed_get gate), so BaselineCollector marks the baseline
    # unavailable -> the planner blocks the operation -> ZERO writes.
    from aula_kb_v3.registry import resolve_by_uuid
    prod = resolve_by_uuid(18691697672197)
    raw = _LightRegisterRaw(bytes([1, 0, 255, 0, 0, 21, 2]))  # brightness 21 > 20
    from community.vetro_probe.aula_transport import AulaHidTransport
    t = AulaHidTransport(raw=raw, product=prod)
    val, res = t.get("light.brightness")
    assert res.ok is False
    assert "out of safe range" in res.error
    assert val is None


def test_baseline_register_wrong_length_raises():
    from aula_kb_v3.registry import resolve_by_uuid  # type: ignore
    raw = _LightRegisterRaw(b"")
    # GET reply declares len 3 -> parser returns 3 bytes -> read_light_state fails closed
    raw.bad_get = b"\x84\x01\x00\x01\x00\x03\x01\x02\x03" + b"\x00" * 54
    from community.vetro_probe.lighting_core import read_light_state
    with pytest.raises(RuntimeError, match="must be 7 bytes"):
        read_light_state(raw, resolve_by_uuid(18691697672197))


# ------------------------------------------------- 7..10 temporary planning
def test_B_differs_only_at_brightness_byte():
    B, changed = plan_brightness_temporary(A)
    assert len(B) == 7
    assert changed == [5]
    for i in range(7):
        if i != 5:
            assert B[i] == A[i]
    assert B[5] != A[5]


def test_baseline_10_produces_B_5():
    B, _ = plan_brightness_temporary(bytes([1, 0, 255, 0, 0, 10, 2]))
    assert B[5] == 5


def test_baseline_5_produces_B_10():
    B, _ = plan_brightness_temporary(bytes([1, 0, 255, 0, 0, 5, 2]))
    assert B[5] == 10


def test_mode_rgb_reserved_speed_preserved_byte_for_byte():
    B, _ = plan_brightness_temporary(bytes([3, 0, 0x12, 0x34, 0x56, 10, 4]))
    assert B[:5] == bytes([3, 0, 0x12, 0x34, 0x56])
    assert B[6] == 4


# ----------------------------------------------- real-transport stub
def _feature_get_reply(state7):
    import aula_kb_v3.protocol as prot  # type: ignore
    fr = bytearray(63)
    fr[0] = 0x84
    fr[1] = 0x01
    fr[5] = 7
    fr[6:13] = bytes(state7)
    fr[62] = prot.checksum(bytes(fr[:62]))
    return bytes(fr)


class _LightRegisterRaw:
    """Minimal register-0x01 emulator: canonical echo on SET, GET readback."""

    def __init__(self, state7):
        self.state = bytearray(state7)
        self._orig_br = state7[5] if len(state7) > 5 else None
        self.sent = []
        self.readback_override = None  # one-shot: reported brightness once a SET changed it
        self.bad_get = None
        self.get_count = 0

    def send(self, frame):
        self.sent.append(bytes(frame))

    def recv(self, timeout_ms=1000):
        if self.bad_get is not None:
            b = self.bad_get
            self.bad_get = None
            return b
        f = self.sent[-1]
        if f[0] == 0x04:  # feature SET -> canonical echo
            self.state = bytearray(f[6:13])  # the 7-byte register state within the frame
            return f
        self.get_count += 1
        st = bytearray(self.state)
        # readback_override applies only AFTER a SET changed the brightness (i.e. the
        # executor's post-write readback B), never to baseline or RMW pre-reads.
        if self.readback_override is not None and st[5] != self._orig_br:
            st[5] = self.readback_override
            self.readback_override = None
        return _feature_get_reply(bytes(st))

    def close(self):
        pass

    def is_connected(self):
        return True


def _exec_ctx(bundle, transport, baseline_ops=("light.brightness",)):
    safety = SafetyGate(bundle, instance_firmware="0216")
    collector = BaselineCollector(transport)
    snap = collector.collect(list(baseline_ops))
    recovery = RecoveryJournal(snap)
    return ExecutorContext(bundle=bundle, transport=transport, safety=safety,
                           baseline=snap, recovery=recovery, reconnect=None,
                           firmware_branch="0216", connection_mode="wired")


def _real_transport(state7):
    from aula_kb_v3.registry import resolve_by_uuid  # type: ignore
    from community.vetro_probe.aula_transport import AulaHidTransport
    prod = resolve_by_uuid(18691697672197)
    raw = _LightRegisterRaw(state7)
    return AulaHidTransport(raw=raw, product=prod), raw


# ------------------------------------------------------- 11 executor PASS
def test_executor_full_lifecycle_get_set_echo_get_rollback_final_get():
    bundle = production_bundle_for_hero84()
    transport, raw = _real_transport(A)
    ctx = _exec_ctx(bundle, transport)
    ev = execute_single("light.brightness", ctx)
    assert ev.status == "PASS", ev.error
    assert ev.readback_matched and ev.rollback_matched
    assert ev.ack_valid is True  # canonical echo validated (ACK != readback)
    assert ev.transport_result == "ok"
    # final restore verified: device returned to immutable A byte-for-byte
    assert bytes(raw.state) == A
    assert ev.validation_flags == {
        "write": True, "ack": True, "readback": True, "rollback": True, "final_restore": True,
    }


# --------------------------------------------------- 12 echo ok, GET B mismatch
def test_echo_ok_get_B_mismatch_recovers_A():
    bundle = production_bundle_for_hero84()
    transport, raw = _real_transport(A)
    ctx = _exec_ctx(bundle, transport)
    raw.readback_override = 0  # only the post-write GET B reports wrong brightness
    ev = execute_single("light.brightness", ctx)
    assert ev.status == "FAIL"
    assert ev.ack_valid is True  # echo B was canonical/valid
    assert ev.readback_matched is False
    assert ev.rollback_matched is True  # rollback restored A
    assert bytes(raw.state) == A
    assert ev.validation_flags["final_restore"] is True


# ---------------------------------------------- 13 crash-before-persistence
def test_pending_applied_checkpoint_recovery_restores_A():
    # A pending TEMP_WRITE_APPLIED checkpoint (SET B reached hardware, crash
    # before applied-state persistence) must trigger recovery that restores A.
    from community.vetro_probe.lighting_core import _rollback
    env = _EnvSession(A)
    cp = RunCheckpoint(run_id="crash", operation="lighting.rollback_validation",
                       baseline=A.hex(), attempted=plan_brightness_temporary(A)[0].hex(),
                       phase="TEMP_WRITE_APPLIED", closed=False)
    rec = recover_pending_checkpoint(cp, env.make)
    assert rec["ok"] is True
    assert env.write_log == [A]  # only immutable A written, never B-derived
    assert bytes(env.state) == A


class _EnvSession:
    """Canonical-echo session model (device echoes exact canonical frame)."""

    def __init__(self, state):
        self.state = bytearray(state)
        self.write_log = []

    def make(self):
        return self

    def set_light(self, reg7):
        self.write_log.append(bytes(reg7))
        self.state = bytearray(reg7)
        from community.vetro_probe.lighting_core import canonical_set_frame
        return canonical_set_frame(reg7)

    def get_light(self):
        if getattr(self, "get_none", False):
            return None
        if getattr(self, "get_override", None) is not None:
            return bytes(self.get_override)
        return bytes(self.state)

    def close(self):
        pass


# ------------------------------------------- 14/15 rollback final-GET invariant
def test_rollback_echo_valid_final_get_mismatch_fails():
    wrong = bytearray(A); wrong[5] = 15
    env = _EnvSession(A)
    env.get_override = bytes(wrong)  # SET A echo valid, final GET != A
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "FINAL_STATE_MISMATCH"
    assert rec["echo_ack"] is True
    assert rec["final_get"] == wrong.hex()


def test_rollback_final_get_none_fails_closed():
    env = _EnvSession(A)
    env.get_none = True
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "GET_A_FAILED"
    assert rec["final_get"] is None


# ---------------------------------------------------------- 16..18 blocked
def test_rgb_not_auto_despite_shared_register():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    assert _entry(run, "light.rgb_core")["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_UNRESOLVED_LIGHTING_REGISTER" in _entry(run, "light.rgb_core")["why_safe"]


def test_effects_and_perkey_and_custom_blocked():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    for op in ("light.global_color", "light.effect", "light.speed", "light.direction",
               "custom.per_key", "light.edge_light"):
        entry = _entry(run, op)
        assert entry["classification"] == CLS_BLOCKED
        assert entry["informational"] is True


# ------------------------------------------------------------- 19 certificate
def test_certificate_carries_physical_rollback_and_readback_evidence():
    bundle = production_bundle_for_hero84()
    transport, raw = _real_transport(A)
    ctx = _exec_ctx(bundle, transport)
    ev = execute_single("light.brightness", ctx)
    assert ev.status == "PASS"

    gate = __import__("community.vetro_probe.identity", fromlist=["ExactIdentityGate"]).ExactIdentityGate(bundle)
    verdict = gate.evaluate(_inst())
    snap = ctx.baseline
    from community.vetro_probe.planner import coverage_report
    cert = build_certificate(verdict, bundle, snap.hash, snap.hash, True, [ev], [],
                             coverage_report(bundle, [ev]), knowledge_revision="test")
    d = cert.to_dict()
    assert d["verdict"] == "PASS"
    claims = d["physical_validation"]["light.brightness @ 0x372E:0x103E/0216"]
    assert claims["PHYSICAL_WRITE_VALIDATED"] is True
    assert claims["PHYSICAL_ACK_VALIDATED"] is True
    assert claims["PHYSICAL_READBACK_VALIDATED"] is True
    assert claims["PHYSICAL_ROLLBACK_VALIDATED"] is True
    assert claims["PHYSICAL_FINAL_RESTORE_VALIDATED"] is True
    # ACK is never reported as readback: distinct claims exist.
    assert d["tests"][0]["ack_valid"] is True
    assert d["tests"][0]["readback_matched"] is True
    assert d["quorum"]["eligible_for"] == "none"  # Probe never promotes


# -------------------------------------------------------------- 20 no K20
def test_k20_not_promoted():
    from community.vetro_probe.knowledge_rank import load_registry
    reg = load_registry()
    aula = next(g for g in reg["groups"] if g.get("group") == "aula")
    assert aula["lighting_feature_scope"]["k20_not_promoted"] is True
    p = Path(__file__).resolve().parents[1] / "vetro_probe" / "knowledge" / "lighting_mapping.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert "K20" in d["physically_closed"]["not_generalized"]


# ===================================================== planner evidence gates
# Generic reversible metadata can never override a hard feature-level OPEN
# requirement. Precedence: feature blocker > generic metadata > family knowledge.
from community.vetro_probe import feature_gates as fg  # noqa: E402


def test_generic_metadata_cannot_override_hard_blocker():
    # he.rt is reversible + has bounds in the bundle, yet must stay BLOCKED.
    run = _plan_for(production_bundle_for_hero84(), _inst())
    e = _entry(run, "he.rt")
    assert e["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_KNOWLEDGE_HOLE" in e["why_safe"]


def test_remap_missing_e5_blocked():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    e = _entry(run, "keyboard.remap")
    assert e["classification"] == CLS_BLOCKED
    assert "BLOCKED_BY_MISSING_STRONG_E5" in e["why_safe"]


def test_rt_open_units_crosscheck_blocked():
    assert fg.missing_evidence("he.rt", "0x372E", "0x103E", "aula_kb_v3_wired", "0216") == [
        "rapid_trigger_units_crosscheck"]
    assert fg.blocker_for("he.rt", "0x372E", "0x103E", "aula_kb_v3_wired", "0216")[0] == "BLOCKED_BY_KNOWLEDGE_HOLE"


def test_closing_exact_evidence_allows_promotion():
    # Closing the EXACT required evidence (and only that) allows promotion.
    closed = dict(fg.CLOSED_EVIDENCE)
    closed["rapid_trigger_units_crosscheck"] = "simulated closure"
    assert fg.missing_evidence("he.rt", "0x372E", "0x103E", "aula_kb_v3_wired", "0216", closed=closed) == []
    assert fg.blocker_for("he.rt", "0x372E", "0x103E", "aula_kb_v3_wired", "0216", closed=closed) is None
    # unrelated gates unaffected
    assert fg.missing_evidence("keyboard.remap", "0x372E", "0x103E", "aula_kb_v3_wired", "0216", closed=closed) != []


def test_brightness_k_closure_does_not_leak_into_rt_remap():
    # light.brightness K13/K14/K18/K19 closure must NOT leak into RT/remap gates.
    assert fg.missing_evidence("light.brightness", "0x372E", "0x103E", "aula_kb_v3_wired", "0216") == []
    assert fg.missing_evidence("he.rt", "0x372E", "0x103E", "aula_kb_v3_wired", "0216") != []
    assert fg.missing_evidence("keyboard.remap", "0x372E", "0x103E", "aula_kb_v3_wired", "0216") != []


def test_family_full_k_status_cannot_override_feature_open():
    # AULA brand k_matrix is FULL for K13/K14/K18/K19, but feature-level OPEN
    # gates for RT/remap still win.
    run = _plan_for(production_bundle_for_hero84(), _inst())
    assert _entry(run, "he.rt")["classification"] == CLS_BLOCKED
    assert _entry(run, "keyboard.remap")["classification"] == CLS_BLOCKED


def test_actuation_promoted_after_post_fix_revalidation():
    # he.actuation's post-fix physical revalidation PASS closed its blocker
    # (BLOCKED_PENDING_PHYSICAL_REVALIDATION -> AUTO_REVERSIBLE), while
    # RT/remap remain BLOCKED (no cross-feature inference).
    run = _plan_for(production_bundle_for_hero84(), _inst())
    e = _entry(run, "he.actuation")
    assert e["classification"] == CLS_AUTO_REVERSIBLE
    assert "PHYSICAL" in e["why_safe"] or "AUTO_REVERSIBLE" == e["classification"]
    for op in ("he.rt", "keyboard.remap"):
        assert _entry(run, op)["classification"] == CLS_BLOCKED


def test_polling_winlock_deadzone_remain_eligible():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    for op in ("keyboard.polling", "device.win_lock", "he.deadzone"):
        e = _entry(run, op)
        assert e["classification"] == CLS_AUTO_REVERSIBLE
        assert "OPEN required evidence" not in e.get("evidence_closure", "")


def test_profile_evidence_supports_auto():
    e = _entry(_plan_for(production_bundle_for_hero84(), _inst()), "keyboard.profile")
    assert e["classification"] == CLS_AUTO_REVERSIBLE
    assert "PHYSICAL_VALIDATION_PASS G" in e["evidence_closure"]


def test_brightness_remains_auto_reversible():
    assert _entry(_plan_for(production_bundle_for_hero84(), _inst()), "light.brightness")["classification"] == CLS_AUTO_REVERSIBLE


def test_rgb_effect_custom_remain_blocked():
    run = _plan_for(production_bundle_for_hero84(), _inst())
    for op in ("light.rgb_core", "light.global_color", "light.effect", "custom.per_key"):
        assert _entry(run, op)["classification"] == CLS_BLOCKED


def test_dry_plan_performs_zero_writes(tmp_path):
    bundle = production_bundle_for_hero84()
    trans = FakeTransport(initial_state={})
    inst = _inst()
    run = AutoProbeRun(bundle=bundle, transport=trans, instance=inst,
                       enumerate_fn=lambda: inst, make_transport=lambda: trans.fresh_session(),
                       run_dir=Path(tmp_path) / "dry", reconnect_timeout_ms=200)
    run.plan_only()
    assert trans.device.write_count == 0
