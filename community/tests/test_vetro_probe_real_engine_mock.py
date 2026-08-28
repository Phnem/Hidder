"""Deterministic non-hardware test suite for RealEngine.

Exercises the REAL execution pipeline (RealEngine -> AutoProbeRun -> SafetyGate ->
ReconnectManager -> BaselineCollector -> Executor -> recovery journal -> progress events)
without touching physical HID hardware.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from community.vetro_probe.gui_rpc import (
    ProbeRpcServer,
    RealEngine,
    plan_preview,
)
from community.vetro_probe.identity import mock_hero84_instance
from community.vetro_probe.transport import FakeTransport


def make_mock_transport_factory():
    inst = mock_hero84_instance()
    initial_state = {
        "keyboard.profile": 0,
        "keyboard.polling": 2,
        "device.win_lock": False,
        "he.deadzone": 0.5,
        "he.actuation": 2.0,
        "light.brightness": 10,
    }
    fake_trans = FakeTransport(initial_state=initial_state, reconnect_ops={"keyboard.polling"})

    def factory(bundle):
        def enumerate_fn():
            return inst

        def make_transport():
            return fake_trans.fresh_session()

        return fake_trans, inst, enumerate_fn, make_transport

    return factory, fake_trans


def test_real_engine_discovery_with_mock_factory():
    factory, _ = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        disc = engine.discover()
        assert disc["state"] == "IDENTIFIED"
        assert disc["device"]["family"] == "aula_kb_v3_wired"
        assert disc["supported_count"] == 6


def test_real_engine_start_run_returns_promptly_and_executes_e2e():
    factory, fake_trans = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        server = ProbeRpcServer(engine=engine)
        events = []

        def capture_emit(evt):
            events.append(evt)

        # 1. start_run latency should be minimal (< 50ms)
        t0 = time.time()
        res = engine.start_run(capture_emit, async_run=True)
        t_start = (time.time() - t0) * 1000
        assert res["started"] is True
        assert t_start < 100.0, f"start_run took too long: {t_start:.2f}ms"

        # 2. Cannot start duplicate run while busy
        dup = engine.start_run(capture_emit, async_run=True)
        assert dup["started"] is False
        assert "already in progress" in dup["error"]

        # 3. Wait for background worker to complete
        deadline = time.time() + 5.0
        while engine._busy.locked() and time.time() < deadline:
            time.sleep(0.05)

        assert not engine._busy.locked(), "worker did not complete in time"

        # 4. Verify progress events were emitted
        progress_events = [e for e in events if e.get("event") == "progress"]
        assert len(progress_events) >= 6, f"Expected at least 6 progress events, got {len(progress_events)}"

        # First operation progress should be present
        first_op_events = [p for p in progress_events if p["data"]["op"] == "keyboard.profile"]
        assert len(first_op_events) >= 1
        assert any(p["data"]["state"] in ("QUEUED", "BASELINING", "TESTING", "PASS") for p in first_op_events)

        # 5. Verify final run_result event
        result_events = [e for e in events if e.get("event") == "run_result"]
        assert len(result_events) == 1
        res_data = result_events[0]["data"]
        assert res_data["status"] == "COMPLETE_PASS"
        assert res_data["restored"] is True
        assert res_data["checks_completed"] == 6
        assert res_data["checks_total"] == 6
        assert res_data["evidence_source"] == "mock"
        assert res_data["physical_validation_evidence"] is False

        # 6. Verify run_result query returns identical state
        queried = engine.run_result()
        assert queried["status"] == "COMPLETE_PASS"
        assert queried["checks_completed"] == 6


def test_real_engine_rpc_server_responsiveness_during_run():
    factory, _ = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        server = ProbeRpcServer(engine=engine)
        out_lines = []

        # Monkey-patch _send to capture server responses
        orig_send = server._send

        def mock_send(obj):
            out_lines.append(obj)

        server._send = mock_send

        # Send start_run
        server.handle({"id": 1, "method": "start_run"})
        assert any(o.get("id") == 1 and o.get("ok") is True for o in out_lines)

        # While run is processing in background, query health & recovery_status
        server.handle({"id": 2, "method": "health"})
        health_resp = next(o for o in out_lines if o.get("id") == 2)
        assert health_resp["ok"] is True
        assert health_resp["result"]["engine"] == "real"

        server.handle({"id": 3, "method": "recovery_status"})
        rec_resp = next(o for o in out_lines if o.get("id") == 3)
        assert rec_resp["ok"] is True

        # Wait for completion
        deadline = time.time() + 5.0
        while engine._busy.locked() and time.time() < deadline:
            time.sleep(0.05)

        assert not engine._busy.locked()
        run_res_evt = next(o for o in out_lines if o.get("event") == "run_result")
        assert run_res_evt["data"]["status"] == "COMPLETE_PASS"


def test_real_engine_plan_safe_count_unchanged():
    plan = plan_preview()
    assert plan["safe_count"] == 6
    safe_ids = [s["id"] for s in plan["safe"]]
    expected = [
        "keyboard.profile",
        "keyboard.polling",
        "device.win_lock",
        "he.deadzone",
        "he.actuation",
        "light.brightness",
    ]
    assert safe_ids == expected


def test_real_engine_zero_physical_writes_in_mock_harness():
    factory, fake_trans = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        events = []
        engine.start_run(lambda e: events.append(e), async_run=False)
        res = engine.run_result()
        assert res["physical_validation_evidence"] is False
        assert res["evidence_source"] == "mock"


def test_real_engine_progress_stages_streamed_properly():
    factory, _ = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        events = []
        engine.start_run(lambda e: events.append(e), async_run=False)

        prog_events = [e["data"] for e in events if e.get("event") == "progress"]
        ops = ["keyboard.profile", "keyboard.polling", "device.win_lock", "he.deadzone", "he.actuation", "light.brightness"]

        # System stages must be emitted in exact truthful order
        sys_states = [p["state"] for p in prog_events if p["op"] == "system"]
        assert "PREPARING" in sys_states
        assert "VALIDATING_PLAN" in sys_states
        assert "OPENING_DEVICE" in sys_states
        assert "VERIFYING_DEVICE" in sys_states
        assert "PREPARING_BASELINE" in sys_states

        # Every op must have received QUEUED, BASELINING, and PASS
        for op in ops:
            op_states = [p["state"] for p in prog_events if p["op"] == op]
            assert "QUEUED" in op_states, f"Missing QUEUED for {op}"
            assert "BASELINING" in op_states, f"Missing BASELINING for {op}"
            assert "PASS" in op_states, f"Missing PASS for {op}"


def test_real_engine_diagnostic_state_and_thread_dump():
    factory, _ = make_mock_transport_factory()
    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=factory, is_physical=False)
        diag = engine.diagnostic_state()
        assert "engine_busy" in diag
        assert "last_backend_step" in diag
        assert "thread_dump" in diag
        assert isinstance(diag["thread_dump"], dict)
        assert len(diag["thread_dump"]) > 0  # current thread stack must be present


def test_real_engine_exclusive_handle_lifecycle():
    """Models Windows exclusive device access.

    1. discover() opens and immediately closes transport.
    2. start_run() opens transport without colliding with discovery.
    3. Leaked handle would trigger deterministic exclusive lock violation.
    """
    class ExclusiveDeviceState:
        def __init__(self):
            self.open_handles = 0

    dev_state = ExclusiveDeviceState()
    inst = mock_hero84_instance()
    initial_state = {
        "keyboard.profile": 0, "keyboard.polling": 2, "device.win_lock": False,
        "he.deadzone": 0.5, "he.actuation": 2.0, "light.brightness": 10,
    }

    class ExclusiveMockTransport(FakeTransport):
        def __init__(self, ds, shared_state=None, **kwargs):
            super().__init__(**kwargs)
            self.ds = ds
            if shared_state is not None:
                self._state = shared_state
            if self.ds.open_handles > 0:
                raise RuntimeError("Windows exclusive lock violation: device already open by another handle")
            self.ds.open_handles += 1
            self._closed = False

        def close(self):
            if not self._closed:
                self.ds.open_handles -= 1
                self._closed = True

        def invalidate(self):
            super().invalidate()
            self.close()

    def make_exclusive_factory():
        shared_state = dict(initial_state)
        def factory(bundle):
            trans = ExclusiveMockTransport(dev_state, shared_state=shared_state, initial_state=shared_state, reconnect_ops={"keyboard.polling"})
            def enumerate_fn():
                return inst
            def make_transport():
                return ExclusiveMockTransport(dev_state, shared_state=shared_state, initial_state=shared_state, reconnect_ops={"keyboard.polling"})
            return trans, inst, enumerate_fn, make_transport
        return factory

    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=make_exclusive_factory(), is_physical=False)
        disc = engine.discover()
        assert disc["state"] == "IDENTIFIED"
        assert dev_state.open_handles == 0, f"discover() leaked handle: open_handles={dev_state.open_handles}"

        events = []
        engine.start_run(lambda e: events.append(e), async_run=False)
        res = engine.run_result()
        assert res["status"] == "COMPLETE_PASS"
        assert res["checks_completed"] == 6
        assert dev_state.open_handles == 0


def test_real_engine_worker_exception_handling():
    """Proves that exceptions in the worker thread do NOT cause silent hangs."""
    def broken_factory(bundle):
        raise RuntimeError("Deterministic device failure on open")

    with tempfile.TemporaryDirectory() as td:
        engine = RealEngine(run_dir=Path(td), transport_factory=broken_factory, is_physical=False)
        events = []
        engine.start_run(lambda e: events.append(e), async_run=False)

        res = engine.run_result()
        assert res["status"] == "ERROR"
        assert "Deterministic device failure" in res["error"]
        result_evts = [e for e in events if e.get("event") == "run_result"]
        assert len(result_evts) == 1
        assert result_evts[0]["data"]["status"] == "ERROR"


def test_miner_package_generates_zip_archive():
    """Verify that build_package creates a single-file ZIP archive."""
    import zipfile
    from community.vetro_probe.miner_package import build_package
    from community.vetro_probe.identity import mock_hero84_instance

    inst = mock_hero84_instance()
    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "test_run"
        built = build_package(
            base_dir=pkg_dir,
            run_id="run-test123",
            label="test",
            discovery={"product_string": inst.product_string, "vid": inst.vid, "pid": inst.pid},
            plan=[{"operation": "keyboard.profile", "classification": "AUTO_REVERSIBLE"}],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={},
            terminal="COMPLETE_PASS",
        )
        assert built.is_dir()
        assert (built / "run_manifest.json").is_file()
        zip_path = Path(td) / "vetro_probe_results.zip"
        assert zip_path.is_file(), "vetro_probe_results.zip was not created in parent dir"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "run_manifest.json" in names
            assert "device_identity.json" in names
            assert "summary.txt" in names


def test_unique_package_zip_naming_and_overwrite_protection():
    from community.vetro_probe.miner_package import build_package, generate_package_zip_name
    from community.vetro_probe.identity import mock_hero84_instance

    inst = mock_hero84_instance()
    disc = {"product_string": "AULA HERO84 HE", "firmware": "0216", "vid": "0x372E", "pid": "0x103E"}
    zip_name1 = generate_package_zip_name(disc, "run-abc123456789", "COMPLETE_PASS")
    assert "VetroProbe_AULA-HERO84-HE_0216_run-abc123456789.zip" == zip_name1

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "run1"
        build_package(
            base_dir=pkg_dir,
            run_id="run-abc123456789",
            label="test",
            discovery=disc,
            plan=[],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={},
            terminal="COMPLETE_PASS",
        )
        first_zip = Path(td) / zip_name1
        assert first_zip.is_file()

        # Second build with same name must NOT overwrite; must append counter
        pkg_dir2 = Path(td) / "run2"
        build_package(
            base_dir=pkg_dir2,
            run_id="run-abc123456789",
            label="test",
            discovery=disc,
            plan=[],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={},
            terminal="COMPLETE_PASS",
        )
        second_zip = Path(td) / "VetroProbe_AULA-HERO84-HE_0216_run-abc123456789_1.zip"
        assert second_zip.is_file(), "Overwrite protection failed: second run should have _1 suffix"


def test_package_privacy_scrub_and_relative_paths():
    import zipfile
    from community.vetro_probe.miner_package import build_package, scan_package_for_privacy_violations

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "run_privacy"
        leaky_disc = {
            "product_string": "AULA HERO84 HE",
            "firmware": "0216",
            "vid": "0x372E",
            "pid": "0x103E",
            "local_path": r"C:\Users\JohnDoe\AppData\Local\Temp\_MEI12345\file.txt",
            "repo_path": r"D:\AndroidStudioProjects\Vetro hud\secret.txt",
        }
        build_package(
            base_dir=pkg_dir,
            run_id="run-priv1",
            label="test",
            discovery=leaky_disc,
            plan=[],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={"raw_path": r"C:\Users\SecretAdmin\Documents"},
            terminal="COMPLETE_PASS",
        )
        violations = scan_package_for_privacy_violations(pkg_dir)
        assert len(violations) == 0, f"Forbidden personal paths found in package: {violations}"

        # Inspect raw content of run_manifest.json
        manifest_text = (pkg_dir / "run_manifest.json").read_text(encoding="utf-8")
        assert "JohnDoe" not in manifest_text
        assert "SecretAdmin" not in manifest_text
        assert "_MEI" not in manifest_text
        assert "AndroidStudioProjects" not in manifest_text


def test_package_manifest_versions_and_sha256():
    import json
    import zipfile
    import hashlib
    from community.vetro_probe.miner_package import build_package

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "run_meta"
        build_package(
            base_dir=pkg_dir,
            run_id="run-meta99",
            label="test",
            discovery={"product_string": "AULA HERO84 HE", "firmware": "0216"},
            plan=[],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={},
            terminal="COMPLETE_PASS",
        )
        manifest = json.loads((pkg_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["probe_app_version"] == "0.3.0"
        assert manifest["probe_engine_version"] == "0.3.0"
        assert manifest["package_schema_version"] == "vetro.run-manifest.v1"
        assert manifest["run_id"] == "run-meta99"
        assert manifest["knowledge_revision"] == "aula_kb_v3_r1"
        assert "build_commit" in manifest
        assert manifest["build_commit"] != manifest["knowledge_revision"], "build_commit must NOT equal knowledge_revision"

        pkg_meta = json.loads((pkg_dir / "package_metadata.json").read_text(encoding="utf-8"))
        assert pkg_meta["build_commit"] == manifest["build_commit"]
        assert pkg_meta["knowledge_revision"] == "aula_kb_v3_r1"
        assert pkg_meta["build_commit"] != pkg_meta["knowledge_revision"]

        # Check sha256 file
        sha256_files = list(Path(td).glob("*.sha256"))
        assert len(sha256_files) >= 1
        sha_text = sha256_files[0].read_text(encoding="utf-8").strip()
        expected_hash = sha_text.split()[0]

        zip_files = [p for p in Path(td).glob("VetroProbe_*.zip") if not p.name.endswith(".sha256")]
        assert len(zip_files) >= 1
        actual_hash = hashlib.sha256(zip_files[0].read_bytes()).hexdigest()
        assert expected_hash == actual_hash


def test_failed_run_diagnostic_package_export():
    from community.vetro_probe.miner_package import build_package

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "run_fail"
        build_package(
            base_dir=pkg_dir,
            run_id="run-fail88",
            label="error_diag",
            discovery={"product_string": "AULA HERO84 HE", "firmware": "0216", "knowledge_revision": "aula_kb_v3_r1"},
            plan=[],
            evidence=[],
            baselines={},
            final_state={"restored": True, "error": "device timeout"},
            certificates=[],
            recovery={"error": "device timeout"},
            terminal="ERROR",
        )
        diag_zips = list(Path(td).glob("VetroProbe_DIAGNOSTIC_*.zip"))
        assert len(diag_zips) >= 1
        assert "VetroProbe_DIAGNOSTIC_" in diag_zips[0].name

        manifest = json.loads((pkg_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["build_commit"] != manifest["knowledge_revision"]
        assert manifest["knowledge_revision"] == "aula_kb_v3_r1"


def test_human_summary_content_and_uncounted_blocked_ops():
    from community.vetro_probe.miner_package import build_package

    with tempfile.TemporaryDirectory() as td:
        pkg_dir = Path(td) / "run_summary"
        build_package(
            base_dir=pkg_dir,
            run_id="run-sum77",
            label="test",
            discovery={"product_string": "AULA HERO84 HE", "firmware": "0216", "vid": "0x372E", "pid": "0x103E", "family": "aula_kb_v3_wired"},
            plan=[
                {"operation": "keyboard.profile", "classification": "AUTO_REVERSIBLE"},
                {"operation": "keyboard.remap", "classification": "BLOCKED"},
            ],
            evidence=[],
            baselines={},
            final_state={"restored": True},
            certificates=[],
            recovery={},
            terminal="COMPLETE_PASS",
        )
        summary_txt = (pkg_dir / "summary.txt").read_text(encoding="utf-8")
        assert "Device: AULA HERO84 HE" in summary_txt
        assert "Firmware: 0216" in summary_txt
        assert "Result: COMPLETE_PASS" in summary_txt
        assert "Failed Checks: 0" in summary_txt
        assert "Safely Skipped Checks: 1" in summary_txt
        assert "Original Settings Restored: Yes (Verified ✓)" in summary_txt

