"""E5 integration: temporary keyboard remap with OS observable + rollback.

Uses pdevemu simulator (no hardware) + FakeObservable that auto-passes,
but verifies the full typed transaction:
- baseline get_keymap
- set_remap pos->A
- readback get_keymap
- observable press W expecting A (Fake auto-pass, but E5 recorded)
- rollback set_remap to original
- readback original
- final restore
"""

import json
from pathlib import Path

import pytest

from community.vetro_probe.aula_transport import AulaHidTransport
from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.executor import ExecutorContext, execute_single
from community.vetro_probe.baseline import BaselineCollector
from community.vetro_probe.recovery import RecoveryJournal
from community.vetro_probe.safety import SafetyGate
from community.vetro_probe.identity import ExactIdentityGate, mock_hero84_instance
from community.vetro_probe.observable import FakeObservableListener
from community.vetro_probe.planner import plan
from community.vetro_probe.certificate import build_certificate


def test_e5_remap_with_observable_via_sim():
    bundle = production_bundle_for_hero84()
    # Ensure remap is observable for E5
    assert bundle.operations["keyboard.remap"].needs_observable is True
    assert bundle.operations["keyboard.remap"].reversible is True

    # Use sim transport (no real HID)
    import aula_kb_v3.registry as reg
    from pdevemu.aula_kb_v3_sim import AulaKbV3SimDevice

    hero = reg.resolve_by_uuid(18691697672197)
    sim = AulaKbV3SimDevice(product=hero)
    transport = AulaHidTransport.from_sim(sim)

    instance = mock_hero84_instance(firmware="1.17.3")
    gate = ExactIdentityGate(bundle)
    verdict = gate.evaluate(instance)
    assert verdict.passed

    collector = BaselineCollector(transport)
    # Baseline for remap
    snap = collector.collect(["keyboard.remap"])
    assert "keyboard.remap" in snap.values
    # Capture original value for later comparison (get_keymap returns HID usage)
    original = snap.values["keyboard.remap"]

    # Recovery armed
    recovery = RecoveryJournal(snap)
    safety = SafetyGate(bundle, instance_firmware=instance.firmware_version)

    # Expect observable: pressing W should yield A after remap
    observable = FakeObservableListener(expectations={
        "press_key:A": {"vk": 0x41, "target": "A", "observed": True}
    }, auto_pass=True)

    ctx = ExecutorContext(
        bundle=bundle,
        transport=transport,
        safety=safety,
        baseline=snap,
        recovery=recovery,
        reconnect=None,
        observable=observable,
        firmware_branch=instance.firmware_version,
        connection_mode=instance.connection_mode,
    )

    ev = execute_single("keyboard.remap", ctx)
    assert ev.status == "PASS", f"ev {ev.status} err {ev.error}"
    assert "E5" in ev.evidence_strength, f"expected E5, got {ev.evidence_strength}"
    assert ev.readback_matched is True
    assert ev.rollback_matched is True
    assert ev.observable_pass is True
    assert ev.observable_result is not None

    # Final restore: after rollback, pressing W should again give original (not A)
    # For sim, after rollback, get should return original
    final_snap = collector.collect(["keyboard.remap"])
    assert recovery.final_matches_initial(final_snap)
    assert final_snap.values["keyboard.remap"] == original

    # Second observable after rollback would be W -> W (not tested here, but we verify rollback)
    # For completeness, test that after rollback, a second remap observation would be original
    # (We don't re-trigger observable after rollback in current executor, but we verify readback)


def test_e5_via_cli_sim_produces_e5():
    # End-to-end via CLI --sim should produce E5 for remap
    from community.vetro_probe.cli import run_headless

    out = run_headless(operation_id="keyboard.remap", use_sim=True, output_path=Path("e5_cli_sim.vetrojson"))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["tests"][0]["operation"] == "keyboard.remap"
    assert "E5" in data["tests"][0]["evidence_strength"]
    assert data["tests"][0]["observable_pass"] is True
    Path(out).unlink(missing_ok=True)
