"""Deterministic tests for the guarded K14 lighting rollback flow (no hardware)."""

import json
from pathlib import Path

import pytest

from community.vetro_probe.lighting_probe import (
    plan_brightness_temporary, is_valid_baseline, verify_echo,
    run_rollback_flow, _recovery_required, recovery_action,
    recover_pending_checkpoint, UNCERTAIN_STATES, _checkpoint_matches_device,
)
from community.vetro_probe.runstate import RunCheckpoint, RunStateStore

A = bytes([1, 0, 255, 0, 0, 10, 2])  # mode1, res0, R255, G0, B0, br10, sp2


class FakeSession:
    def __init__(self, env):
        self.env = env

    def get_light(self):
        if self.env.readback_override is not None:
            ov = self.env.readback_override
            self.env.readback_override = None  # one-shot (only the immediate readback after write_B)
            return bytes(ov)
        return bytes(self.env.state)

    def set_light(self, reg7):
        self.env.write_log.append(bytes(reg7))
        self.env.state = bytearray(reg7)
        echo = self.env.echo if self.env.echo is not None else bytes(reg7)
        return echo

    def close(self):
        pass


class FakeEnv:
    def __init__(self, state, echo=None, readback_override=None):
        self.state = bytearray(state)
        self.echo = echo
        self.readback_override = readback_override
        self.write_log = []

    def make(self):
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


def test_verify_echo():
    assert verify_echo(b"abc", b"abc") is True
    assert verify_echo(b"abc", b"abd") is False
    assert verify_echo(b"abc", None) is False


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
    env = FakeEnv(A, echo=None)
    B, _ = plan_brightness_temporary(A)
    # force echo mismatch
    env.echo = bytes(b"\x00" * 7)
    res = run_rollback_flow(env.make, A, B, delay_s=0)
    assert res["ok"] is False
    assert "echo ACK" in res.get("error", "")
    assert env.write_log == [B]  # no rollback attempted before ACK


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
