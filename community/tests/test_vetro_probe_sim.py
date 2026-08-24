"""Integration regressions for real hardware path via pdevemu simulator.

Covers step 6 of REAL HARDWARE VERTICAL SLICE: errors found on real hardware
must be regresstested against sim (which reproduces real device behaviors
like polling disconnect, echo-ACK, etc).
"""

import json
from pathlib import Path

import pytest

from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.bundle_export import export_bundle_for_uuid
from community.vetro_probe.aula_transport import AulaHidTransport
from community.vetro_probe.cli import run_headless
from community.vetro_probe.certificate import build_certificate
from community.vetro_probe.identity import ExactIdentityGate, mock_hero84_instance
from community.vetro_probe.safety import SafetyGate


def test_production_bundle_has_no_raw_and_is_from_registry():
    data = export_bundle_for_uuid()
    assert data["schema"] == "vetro.preview-bundle.v1"
    assert "knowledge_revision" in data
    assert data["product"]["uuid"] == "18691697672197"
    for op_id, op in data["operations"].items():
        assert "raw_bytes" not in op and "opcode" not in op
    b = production_bundle_for_hero84()
    assert b.product.vid == "0x372E"
    assert "he.actuation" in b.operations
    assert b.operations["keyboard.polling"].requires_reconnect is True


def test_sim_simple_reversible_actuation_pass():
    # Uses AulaHidTransport + pdevemu (no hid hardware)
    out = run_headless(operation_id="he.actuation", use_sim=True, output_path=Path("sim_actuation.vetrojson"))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS"
    assert data["baseline_restored"] is True
    assert data["tests"][0]["status"] == "PASS"
    assert "E4" in data["tests"][0]["evidence_strength"]
    assert data["knowledge_revision"]
    assert "timings" in data and "total_ms" in data["timings"]
    Path(out).unlink(missing_ok=True)


def test_sim_polling_reconnect_pass():
    out = run_headless(operation_id="keyboard.polling", use_sim=True, output_path=Path("sim_polling.vetrojson"))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS", data["tests"][0]["error"]
    t = data["tests"][0]
    assert t["operation"] == "keyboard.polling"
    assert t["status"] == "PASS"
    assert "E4" in t["evidence_strength"]
    # polling must have invalidated old session and reacquired
    assert data["baseline_restored"] is True
    Path(out).unlink(missing_ok=True)


def test_sim_certificate_contains_required_fields():
    out = run_headless(operation_id="keyboard.profile", use_sim=True, output_path=Path("sim_profile.vetrojson"))
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["schema"] == "vetro.hardware-validation.v1"
    assert data["tool_version"]
    assert data["bundle"]["id"] and data["bundle"]["hash"]
    assert data["identity"]["product"] and data["identity"]["descriptor_hash"]
    assert data["identity"]["firmware"]
    assert data["identity"]["connection"] == "wired"
    assert data["baseline_hash"] and data["final_hash"]
    assert "timings" in data
    assert "knowledge_revision" in data
    assert data["quorum"]["eligible_for"] == "none"
    Path(out).unlink(missing_ok=True)


def test_sim_final_restore_gate_catches_tamper():
    # Simulate tamper after write: directly use sim transport to change state after baseline
    from pdevemu.aula_kb_v3_sim import AulaKbV3SimDevice
    import aula_kb_v3.registry as reg

    hero = reg.resolve_by_uuid(18691697672197)
    sim = AulaKbV3SimDevice(product=hero)
    transport = AulaHidTransport.from_sim(sim)
    # Do a normal run via cli, but tamper raw after baseline? Instead we test RecoveryJournal directly
    from community.vetro_probe.baseline import BaselineCollector
    from community.vetro_probe.recovery import RecoveryJournal

    collector = BaselineCollector(transport)
    snap = collector.collect(["he.actuation"])
    journal = RecoveryJournal(snap)
    # Tamper underlying sim state without journal
    transport.raw.send(b"\x00" * 63)  # nonsense but will be ignored? Instead directly mutate
    sim.actuation_mm[30] = 99.9
    final = collector.collect(["he.actuation"])
    assert not journal.final_matches_initial(final)
    diff = journal.final_diff(final)
    assert "he.actuation" in diff


def test_sim_perspective_no_raw_bundle_can_reach_hardware():
    # Even a malicious bundle that tries to inject raw_bytes must be rejected before transport
    from community.vetro_probe.bundle import parse_bundle

    with pytest.raises(ValueError, match="forbidden raw"):
        parse_bundle({
            "schema": "vetro.preview-bundle.v1",
            "id": "evil",
            "version": 1,
            "product": {"vid": "0x372E", "pid": "0x103E", "name": "evil", "uuid": "1"},
            "family": "aula_kb_v3_wired",
            "connection": {"mode": "wired"},
            "firmware": {"branch": "unknown"},
            "capabilities": {},
            "bounds": {},
            "operations": {"evil": {"id": "evil", "kind": "set", "reversible": True, "readback": True, "raw_bytes": "00"}},
        })


def test_sim_stale_session_is_blocked():
    from community.vetro_probe.bundle import production_bundle_for_hero84
    from community.vetro_probe.safety import SafetyGate
    from community.vetro_probe.baseline import BaselineCollector
    from community.vetro_probe.recovery import RecoveryJournal
    from community.vetro_probe.executor import ExecutorContext, execute_single
    from community.vetro_probe.identity import ExactIdentityGate, mock_hero84_instance

    b = production_bundle_for_hero84()
    # Use sim transport then invalidate
    out_path = Path("sim_stale.vetrojson")
    # Create sim transport and immediately invalidate
    from community.vetro_probe.aula_transport import AulaHidTransport
    from pdevemu.aula_kb_v3_sim import AulaKbV3SimDevice
    import aula_kb_v3.registry as reg

    hero = reg.resolve_by_uuid(18691697672197)
    sim = AulaKbV3SimDevice(product=hero)
    transport = AulaHidTransport.from_sim(sim)
    transport.invalidate()
    collector = BaselineCollector(transport)
    snap = collector.collect(["he.actuation"])
    # snap will be empty due to stale session, so gate will block
    assert "he.actuation" not in snap.values
    assert "stale session" in snap.errors.get("he.actuation", "").lower() or "stale" in str(snap.errors)
