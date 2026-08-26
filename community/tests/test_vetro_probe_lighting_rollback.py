"""Deterministic tests for the guarded K14 lighting rollback flow (no hardware)."""

import json
from pathlib import Path

import pytest

from community.vetro_probe.lighting_probe import (
    plan_brightness_temporary, is_valid_baseline, verify_echo,
    decode_echo, normalize_echo, _canonical_set_frame,
    run_rollback_flow, _recovery_required, recovery_action,
    recover_pending_checkpoint, UNCERTAIN_STATES, _checkpoint_matches_device,
    _rollback,
)
from community.vetro_probe.runstate import RunCheckpoint, RunStateStore

A = bytes([1, 0, 255, 0, 0, 10, 2])  # mode1, res0, R255, G0, B0, br10, sp2

# Real captured device echo for SET B = [1,0,255,0,0,5,2] on fw 0216 (K14 run #2).
# Canonical 63-byte frame: header 04 01 00 01 00 07, state at 6..12, checksum e2.
REAL_ECHO_B = "0401000100070100ff0000050200000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000e2"


def _frame(reg7: bytes) -> bytes:
    return _canonical_set_frame(reg7)


class FakeSession:
    def __init__(self, env):
        self.env = env

    def get_light(self):
        if self.env.raise_on_get:
            self.env.raise_on_get = False  # one-shot (only the immediate readback after write_B)
            raise RuntimeError("get failed")
        if self.env.readback_override is not None:
            ov = self.env.readback_override
            self.env.readback_override = None  # one-shot (only the immediate readback after write_B)
            return bytes(ov)
        return bytes(self.env.state)

    def set_light(self, reg7):
        if self.env.raise_on_set:
            raise RuntimeError("set failed")
        self.env.write_log.append(bytes(reg7))
        self.env.state = bytearray(reg7)
        if self.env.echo is None:
            return None  # recv timeout: write may have applied, no echo frame
        if self.env.echo == "CANONICAL":
            return _frame(reg7)  # device echoes the canonical serialized frame
        return bytes(self.env.echo)  # test override (full frame)

    def close(self):
        pass


class FakeEnv:
    def __init__(self, state, echo="CANONICAL", readback_override=None, raise_on_get=False, raise_on_set=False, raise_on_open=False):
        self.state = bytearray(state)
        self.echo = echo
        self.readback_override = readback_override
        self.raise_on_get = raise_on_get
        self.raise_on_set = raise_on_set
        self.raise_on_open = raise_on_open
        self.write_log = []

    def make(self):
        if self.raise_on_open:
            raise RuntimeError("open failed")
        return FakeSession(self)


def test_plan_brightness_only_field():
    B, changed = plan_brightness_temporary(A)
    assert len(B) == 7
    assert B[5] == 5  # 25%
    assert changed == [5]
    for i in range(7):
        if i != 5:
            assert B[i] == A[i]
    # if already 5 -> choose 10
    A5 = bytearray(A); A5[5] = 5
    B2, _ = plan_brightness_temporary(bytes(A5))
    assert B2[5] == 10


def test_is_valid_baseline():
    assert is_valid_baseline(A) is True
    assert is_valid_baseline(b"\x01\x00\xff\x00\x00\x00\x00") is True  # br 0
    assert is_valid_baseline(b"\x01\x00\xff\x00\x00\x15\x02") is False  # br 21 out of range
    assert is_valid_baseline(b"\x01\x00\xff\x00\x00") is False           # len 5
    assert is_valid_baseline(b"\x01\x00\xff\x00\x00\x21\x02") is False   # br 33 out of range


def test_verify_echo_canonical():
    B, _ = plan_brightness_temporary(A)
    assert verify_echo(B, _frame(B)) is True   # canonical serialized echo -> PASS
    assert verify_echo(B, None) is False        # no echo
    assert verify_echo(B, B) is False           # 7-byte state NEVER equals 63-byte echo
    wrong = bytearray(_frame(B)); wrong[0] = 0x05  # wrong group/header
    assert verify_echo(B, bytes(wrong)) is False


def test_run_rollback_flow_happy():
    env = FakeEnv(A)
    B, _ = plan_brightness_temporary(A)
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    assert res["ok"] is True
    assert res["recovered"] is True
    assert env.write_log == [B, A]  # temp write then immutable baseline rollback
    assert bytes(env.state) == A
    stages = [s["stage"] for s in res["stages"]]
    assert stages == ["write_B", "readback_B", "rollback_A"]


def test_readback_mismatch_recovers_from_immutable_A():
    wrong = bytearray(A); wrong[5] = 20
    env = FakeEnv(A, readback_override=bytes(wrong))
    B, _ = plan_brightness_temporary(A)
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    assert res["ok"] is False
    assert res["recovered"] is True
    # recovery rollback writes baseline A (never B-derived)
    assert env.write_log == [B, A]
    assert bytes(env.state) == A


def test_echo_missing_fails_closed():
    env = FakeEnv(A, echo=None)  # recv timeout: SET B issued but no echo frame
    B, _ = plan_brightness_temporary(A)
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    assert res["ok"] is False
    assert res["error_code"] == "ECHO_B_MISMATCH"
    assert "echo ACK" in res.get("error", "")
    assert env.write_log == [B]  # no rollback attempted before ACK
    wb = [s for s in res["stages"] if s["stage"] == "write_B"][0]
    assert wb["set_B_issued"] is True and wb["set_B_completed"] is False
    assert wb["echo_B_observed"] is False and wb["echo_B_state_match"] is False


def test_set_write_completion_independent_of_echo_validation():
    # wrong 63-byte echo frame: the write completed (transport returned a frame)
    # and an echo was observed, but frame/state validation fails -> SET_B must be
    # reported ISSUED/COMPLETED while ACK stays FAIL.
    wrong = bytearray(_frame(B := plan_brightness_temporary(A)[0])); wrong[6] = 0x00  # corrupt state[0]
    env = FakeEnv(A, echo=bytes(wrong))
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    wb = [s for s in res["stages"] if s["stage"] == "write_B"][0]
    assert wb["set_B_issued"] is True and wb["set_B_completed"] is True
    assert wb["echo_B_observed"] is True
    assert wb["echo_B_state_match"] is False
    assert wb["ack"] is False
    assert res["ok"] is False
    assert env.write_log == [B]


def test_real_captured_echo_b_verifies():
    # The exact physical echo captured from K14 run #2 on fw 0216.
    B, _ = plan_brightness_temporary(A)
    fr = decode_echo(B, bytes.fromhex(REAL_ECHO_B))
    assert fr["ack"] is True
    assert fr["state_match"] is True
    assert fr["checksum_valid"] is True
    assert fr["frame_valid"] is True
    assert fr["report_header_valid"] is True
    assert fr["decoded_state"] == B.hex()
    assert fr["expected_frame"] == REAL_ECHO_B
    assert bytes.fromhex(REAL_ECHO_B) == _frame(B)
    assert verify_echo(B, bytes.fromhex(REAL_ECHO_B)) is True


def test_verify_echo_fails_wrong_header():
    B, _ = plan_brightness_temporary(A)
    bad = bytearray(_frame(B)); bad[3] = 0x00  # reserved3 header byte wrong
    assert verify_echo(B, bytes(bad)) is False


def test_verify_echo_fails_wrong_report_id_prefix():
    B, _ = plan_brightness_temporary(A)
    bad = bytes([8]) + _frame(B)  # 64-byte form, report id 8 (must be 9)
    assert verify_echo(B, bad) is False
    good = bytes([9]) + _frame(B)  # 64-byte form with correct report id -> normalize
    assert verify_echo(B, good) is True


def test_verify_echo_fails_wrong_state_bytes():
    B, _ = plan_brightness_temporary(A)
    bad = bytearray(_frame(B)); bad[8] = 0xAA  # state byte corrupt
    assert verify_echo(B, bytes(bad)) is False


def test_verify_echo_fails_wrong_checksum():
    B, _ = plan_brightness_temporary(A)
    bad = bytearray(_frame(B)); bad[62] ^= 0xFF
    assert verify_echo(B, bytes(bad)) is False


def test_verify_echo_correct_state_but_malformed_frame():
    # Declared length 6 while carrying 7 state bytes + recomputed valid checksum:
    # correct state alone must NOT be accepted; header/length inconsistency fails.
    import aula_kb_v3.protocol as prot  # type: ignore
    B, _ = plan_brightness_temporary(A)
    bad = bytearray(_frame(B)); bad[5] = 0x06
    bad[62] = prot.checksum(bytes(bad[:62]))
    assert verify_echo(B, bytes(bad)) is False
    # truncated frame (62 bytes) is not a valid echo
    assert verify_echo(B, _frame(B)[:62]) is False


def test_verify_echo_representation_mismatch_cannot_recur():
    # 7-byte register state vs 63-byte echo: the historical FALSE mismatch.
    B, _ = plan_brightness_temporary(A)
    assert verify_echo(B, B) is False           # 7B against 63B never matches
    assert verify_echo(B, bytes(B)) is False
    # canonical frame for a DIFFERENT value than the 7-byte B must not pass either
    other = bytearray(B); other[5] = 9
    assert verify_echo(B, _frame(bytes(other))) is False
    assert verify_echo(B, _frame(B)) is True


def test_rollback_uses_same_canonical_echo_verifier():
    # Recovery SET A echoes the same 63-byte family -> same verifier must pass.
    env = FakeEnv(A)
    rec = _rollback(A, env.make)
    assert rec["ok"] is True
    assert rec["echo_ack"] is True
    assert rec["echo_frame_valid"] is True
    assert rec["echo_checksum_valid"] is True
    assert rec["echo_state_match"] is True
    assert rec["decoded_state"] == A.hex()
    assert rec["fresh_get_equals_A"] is True


def test_rollback_echo_mismatch_reports_frame_diagnostics():
    import aula_kb_v3.protocol as prot  # type: ignore
    bad = bytearray(_frame(A)); bad[8] = 0xAA  # wrong state byte, recompute checksum
    bad[62] = prot.checksum(bytes(bad[:62]))
    env = FakeEnv(A, echo=bytes(bad))
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "ECHO_A_MISMATCH"
    assert rec["echo_ack"] is False
    assert rec["echo_frame_valid"] is True
    assert rec["echo_state_match"] is False
    assert rec["restore_write_issued"] is True
    assert rec["fresh_get_equals_A"] is True  # hardware actually restored A


def test_recovery_required_detects_pending_checkpoint(tmp_path):
    store = RunStateStore(tmp_path)
    cp = store.new_run()
    cp.operation = "lighting.rollback_validation"
    cp.baseline = A.hex()
    cp.attempted = "x"
    cp.phase = "TEMP_WRITE_APPLIED"
    cp.closed = False
    store.save(cp)
    assert _recovery_required(tmp_path) is not None
    # a RESTORED/closed checkpoint -> no recovery required
    cp.phase = "RESTORED"
    cp.closed = True
    store.save(cp)
    assert _recovery_required(tmp_path) is None


def _cp(phase, identity=None, fw="0216"):
    cp = RunCheckpoint(run_id="crash", operation="lighting.rollback_validation",
                       baseline=A.hex(), attempted="", phase=phase, closed=False)
    cp.device = identity or {"vid": "0x372E", "pid": "0x103E", "family": "aula_kb_v3_wired", "firmware": fw}
    return cp


def _inst(vid="0x372E", pid="0x103E", fw="0216"):
    from types import SimpleNamespace
    return SimpleNamespace(vid=vid, pid=pid, firmware_version=fw, family="aula_kb_v3_wired")


def test_recovery_action_per_boundary():
    # crash after BASELINE_SAVED, before TEMP_WRITE_INTENT -> zero recovery write
    assert recovery_action(_cp("BASELINE_SAVED")) == "NONE"
    # crash after TEMP_WRITE_INTENT, before SET B -> restore A
    assert recovery_action(_cp("TEMP_WRITE_INTENT")) == "RESTORE_A"
    # SET B succeeded then crash before TEMP_WRITE_APPLIED -> restore A
    assert recovery_action(_cp("TEMP_WRITE_APPLIED")) == "RESTORE_A"
    # crash after RESTORE_INTENT, before SET A -> restore A
    assert recovery_action(_cp("RESTORE_INTENT")) == "RESTORE_A"
    # RESTORED -> none
    assert recovery_action(_cp("RESTORED")) == "NONE"
    # non-lighting checkpoint -> none
    cp = RunCheckpoint(run_id="x", operation="other", phase="TEMP_WRITE_INTENT")
    assert recovery_action(cp) == "NONE"


def test_recovery_restores_immutable_A_for_uncertain_states():
    for phase in UNCERTAIN_STATES:
        env = FakeEnv(bytes([1, 0, 255, 0, 0, 20, 2]))  # device left at some B-ish state
        rec = recover_pending_checkpoint(_cp(phase), env.make)
        assert rec["action"] == "RESTORE_A"
        assert rec["writes"] == 1
        assert rec["ok"] is True
        assert bytes(env.state) == A  # restored to immutable baseline
        assert env.write_log == [A]  # only baseline rollback write; never B-derived


def test_recovery_identity_fw_gate():
    # different identity/FW -> zero writes (recovery blocked)
    cp = _cp("TEMP_WRITE_APPLIED")
    assert _checkpoint_matches_device(cp, _inst()) is True
    assert _checkpoint_matches_device(cp, _inst(vid="0x1234")) is False
    assert _checkpoint_matches_device(cp, _inst(fw="9999")) is False


def test_run_rollback_flow_write_ahead_phase_order():
    phases = []
    env = FakeEnv(A)
    B, _ = plan_brightness_temporary(A)
    res = run_rollback_flow(env.make, A, B, delay_s=0, on_phase=phases.append)
    assert phases == ["BASELINE_SAVED", "TEMP_WRITE_INTENT", "TEMP_WRITE_APPLIED", "RESTORE_INTENT", "RESTORED"]
    assert res["ok"] is True


def test_rollback_echo_mismatch_reports_specific_code():
    env = FakeEnv(A, echo=b"\x00" * 7)  # device echoes wrong frame
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "ECHO_A_MISMATCH"
    assert rec["restore_write_issued"] is True
    assert rec["restore_write_completed"] is True
    assert rec["echo_observed"] is True


def test_rollback_final_state_mismatch_reports_code():
    wrong = bytearray(A); wrong[5] = 15
    env = FakeEnv(A, readback_override=bytes(wrong))  # SET A ok+echo ok, but fresh GET != A
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "FINAL_STATE_MISMATCH"
    assert rec["restore_write_issued"] is True
    assert rec["echo_ack"] is True
    assert rec["fresh_get_equals_A"] is False


def test_rollback_set_raises_reports_code():
    env = FakeEnv(A, raise_on_set=True)
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "SET_A_FAILED"
    assert rec["restore_write_issued"] is False


def test_rollback_open_raises_reports_code():
    env = FakeEnv(A, raise_on_open=True)
    rec = _rollback(A, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "OPEN_FAILED"


def test_recover_pending_checkpoint_reports_write_vs_verified():
    wrong = bytearray(A); wrong[5] = 15
    env = FakeEnv(A, readback_override=bytes(wrong))
    cp = _cp("TEMP_WRITE_APPLIED")
    rec = recover_pending_checkpoint(cp, env.make)
    assert rec["ok"] is False
    assert rec["error_code"] == "FINAL_STATE_MISMATCH"
    # write may have reached hardware, but not verified
    assert rec["restore_write_may_have_succeeded"] is True
    assert rec["restore_verified"] is False
    # immutable A written (never B-derived)
    assert env.write_log == [A]


def test_run_rollback_flow_get_b_raises_recovers_and_reports():
    env = FakeEnv(A, raise_on_get=True)  # fresh GET B raises -> recovery A
    B, _ = plan_brightness_temporary(A)
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    assert res["ok"] is False
    assert res["error_code"] == "GET_B_FAILED"
    assert res["recovered"] is True
    assert env.write_log[-1] == A  # recovery restored immutable A


def test_persistence_failure_before_set_b_blocks_write():
    # simulate durable persist failing at TEMP_WRITE_INTENT -> no physical SET B
    env = FakeEnv(A)
    B, _ = plan_brightness_temporary(A)

    def fail_intent(state):
        if state == "TEMP_WRITE_INTENT":
            raise OSError("disk full")
    with pytest.raises(OSError):
        run_rollback_flow(env.make, A, B, delay_s=0, on_phase=fail_intent)
    assert env.write_log == []  # ZERO writes (SET B never issued)


def test_out_dir_scoped_recovery_ignores_unrelated_pending(tmp_path):
    # A pending checkpoint in another dir (archived incident) must NOT trigger
    # recovery when the new --out dir is used. Recovery is scoped to --out only.
    other = tmp_path / "lighting_rollback_failure_001"
    other.mkdir()
    cp = _cp("TEMP_WRITE_APPLIED")
    RunStateStore(str(other)).save(cp)

    fresh = tmp_path / "lighting_rollback_checkpoint_v2"
    store = RunStateStore(str(fresh))
    assert store.load() is None  # no pending recovery for the selected path
