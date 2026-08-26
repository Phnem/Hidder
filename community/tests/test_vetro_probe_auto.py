"""End-to-end auto flow regression (scenarios A-G) — simulation only, no hardware.

A known HERO84 → COMPLETE + package
B unknown firmware → writes BLOCKED, package still created
C ambiguous identity → ZERO writes, BLOCKED
D reconnect transient → retry → COMPLETE
E readback null after write → recovery → baseline restored
F recovery impossible → FAILED_REQUIRES_MANUAL_RESTORE + persisted journal + ZERO speculative writes
G simulated crash after write → restart detects journal → recovery before new run → baseline restored
"""

import json
from pathlib import Path

import pytest

from community.vetro_probe.automation import AutoProbeRun
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.transport import FakeTransport
from community.vetro_probe.identity import mock_hero84_instance, PhysicalInstance
from community.vetro_probe.runstate import RunCheckpoint, RunStateStore


def _baseline_state(bundle) -> dict:
    from community.vetro_probe.planner import plan

    state = {}
    defaults = {
        "he.actuation": 1.0, "he.rt": 1, "he.deadzone": 0.5,
        "keyboard.remap": 0x46, "keyboard.profile": 0,
        "light.rgb_core": 0x00FF00, "device.win_lock": False, "keyboard.polling": 3,
    }
    for p in plan(bundle):
        op_id = p.operation_id
        if op_id in defaults:
            state[op_id] = defaults[op_id]
        elif p.operation_id in bundle.operations and bundle.operations[p.operation_id].kind != "observable":
            state[op_id] = 0
    return state


def _make_run(tmp_path, *, bundle=None, state=None, flaky=False, instance=None,
              enumerate_val="instance", get_fail=None, readback_fail=None, pending=None,
              allowed_ops=None):
    bundle = bundle or production_bundle_for_hero84()
    state = state or _baseline_state(bundle)
    if flaky:
        from community.vetro_probe.transport import FakeTransport as _FT
        class _Flaky(_FT):
            def __init__(self):
                super().__init__(initial_state=state, reconnect_ops={"keyboard.polling"})
                self._fails = 1
            def fresh_session(self):
                if self._fails > 0:
                    self._fails -= 1
                    raise RuntimeError("transient enumerate empty")
                return super().fresh_session()
        transport = _Flaky()
    else:
        transport = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    if get_fail:
        transport.device.get_fail_sessions.update(get_fail)
    if readback_fail:
        transport.device.readback_fail_sessions.update(readback_fail)
    inst = instance if instance is not None else mock_hero84_instance()

    def enumerate_fn():
        if enumerate_val == "ambiguous":
            from community.vetro_probe.reconnect import AMBIGUOUS
            return AMBIGUOUS
        if enumerate_val == "instance":
            return inst
        if enumerate_val == "none":
            return None
        return enumerate_val

    def make_transport():
        return transport.fresh_session()

    run = AutoProbeRun(
        bundle=bundle, transport=transport, instance=inst,
        enumerate_fn=enumerate_fn, make_transport=make_transport,
        run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=2000,
        allowed_ops=allowed_ops,
    )
    if pending:
        store = run.store
        cp = RunCheckpoint(**pending)
        cp.run_id = "crash-run"
        cp.save(store.checkpoint_path)
        run.cp = store.load()
    return run, transport


def _plan_ops(run):
    return [e["operation"] for e in run.plan]


def test_scenario_a_known_device_complete(tmp_path):
    run, trans = _make_run(tmp_path)
    run.run()
    assert run.verdict == "COMPLETE", run.verdict
    assert run.baseline_restored is True
    assert all(ev.status == "PASS" for ev in run.results), [(e.operation, e.status, e.error) for e in run.results]
    assert trans.device.write_count == 16  # 8 reversible ops x (write + rollback)
    pkg = run.package_dir
    assert pkg is not None and (pkg / "run_manifest.json").is_file()
    assert (pkg / "miner_input" / "observations.json").is_file()
    assert any((pkg / "certificates").glob("*.vetrojson"))
    assert (pkg / "summary.json").is_file()
    assert run.summary()


def test_scenario_b_unknown_firmware_writes_blocked_package_created(tmp_path):
    inst = mock_hero84_instance(firmware="unknown")
    run, trans = _make_run(tmp_path, instance=inst)
    run.run()
    assert run.verdict == "COMPLETE", run.verdict  # read-only discovery + package exported
    assert trans.device.write_count == 0
    assert all(ev.status == "BLOCKED" for ev in run.results)
    assert run.package_dir is not None and (run.package_dir / "plan.json").is_file()


def test_scenario_c_ambiguous_identity_zero_writes_blocked(tmp_path):
    run, trans = _make_run(tmp_path, enumerate_val="ambiguous")
    run.run()
    assert run.verdict == "BLOCKED"
    assert trans.device.write_count == 0
    assert run.package_dir is not None and (run.package_dir / "run_manifest.json").is_file()


def test_scenario_d_reconnect_transient_retries_complete(tmp_path):
    run, trans = _make_run(tmp_path, flaky=True)
    run.run()
    assert run.verdict == "COMPLETE", run.verdict
    assert run.baseline_restored is True
    assert trans.device.state["keyboard.polling"] == 3


def test_scenario_e_readback_null_after_write_recovers(tmp_path):
    run, trans = _make_run(tmp_path, readback_fail={2})
    run.run()
    assert run.verdict == "COMPLETE", run.verdict
    assert run.baseline_restored is True
    assert trans.device.state["keyboard.polling"] == 3


def test_scenario_f_recovery_impossible_manual_restore(tmp_path):
    # B readback null triggers recovery, but C GET current fails -> recovery blocked
    run, trans = _make_run(tmp_path, readback_fail={2}, get_fail={3}, allowed_ops=["keyboard.polling"])
    run.run()
    assert run.verdict == "FAILED_REQUIRES_MANUAL_RESTORE"
    assert trans.device.write_count == 1  # only A write; ZERO speculative recovery writes
    cp = run.cp
    assert cp is not None
    assert cp.write_may_have_applied is True
    assert cp.closed is False
    assert cp.recovery_required is True
    # persisted journal exists and carries the blocked reason
    assert (run.run_dir / "runstate.json").is_file()
    data = json.loads((run.run_dir / "runstate.json").read_text(encoding="utf-8"))
    assert data["write_may_have_applied"] is True and data["closed"] is False


def test_scenario_g_crash_after_write_resume_recovers(tmp_path):
    bundle = production_bundle_for_hero84()
    state = _baseline_state(bundle)
    state["keyboard.polling"] = 2  # write 3->2 already applied, process crashed
    base = FakeTransport(initial_state=state, reconnect_ops={"keyboard.polling"})
    inst = mock_hero84_instance()

    def make_transport():
        return base.fresh_session()

    def enumerate_fn():
        return inst

    # pending open checkpoint: write may have applied, not closed
    pending = {
        "operation": "keyboard.polling", "baseline": 3, "attempted": 2,
        "write_may_have_applied": True, "closed": False, "phase": "EXECUTING",
    }
    run = AutoProbeRun(bundle=bundle, transport=base, instance=inst,
                       enumerate_fn=enumerate_fn, make_transport=make_transport,
                       run_dir=Path(tmp_path) / "run", reconnect_timeout_ms=2000)
    store = run.store
    cp = RunCheckpoint(**pending)
    cp.run_id = "crash-run"
    cp.save(store.checkpoint_path)
    run.cp = store.load()

    # On startup, run() must detect the open write checkpoint and recover FIRST
    run.run()
    # recovery restored baseline before any new run: final physical polling is 3
    assert base.device.state["keyboard.polling"] == 3
    assert run.verdict == "COMPLETE", run.verdict
    assert run.baseline_restored is True
    # the recovery journal is now closed
    assert run.cp is not None and run.cp.closed is True
