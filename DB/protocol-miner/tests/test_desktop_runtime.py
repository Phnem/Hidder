import json
from pathlib import Path

from miner.config import default_settings
from miner.dynamic.desktop_runner import DesktopDynamicRunner
from miner.orchestrator.analyze import analyze_artifact
from miner.orchestrator.ingest import ingest_file


def test_desktop_dynamic_runtime_flow_and_evidence_graph(tmp_path: Path) -> None:
    # 1. Create a dummy CAS artifact
    fake_exe = tmp_path / "VendorKeyboardUtility.exe"
    fake_exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 200)

    settings = default_settings(root=tmp_path, cas_root=tmp_path / "cas")
    settings.ensure_directories()

    ingest_res = ingest_file(settings, fake_exe, vendor="VendorCorp")
    artifact_sha = ingest_res["sha256"]

    # 2. Run Desktop Dynamic Runner with safe simulated native HID events
    runner = DesktopDynamicRunner()
    trace_path = tmp_path / "desktop_trace.jsonl"

    simulated_native_events = [
        {
            "api": "HidD_SetFeature",
            "timestamp": "2026-08-19T14:00:00Z",
            "buffer_hex": "09130064",
            "length": 4,
            "process": 4321,
        },
        {
            "api": "HidD_GetFeature",
            "timestamp": "2026-08-19T14:00:01Z",
            "buffer_hex": "09130064",
            "length": 4,
            "process": 4321,
        },
    ]

    traces = runner.run_and_save_trace(
        target="VendorKeyboardUtility.exe",
        output_trace_path=trace_path,
        simulated_events=simulated_native_events,
    )

    assert trace_path.is_file()
    assert len(traces) == 2
    assert traces[0]["transport"] == "win32_hid"
    assert traces[0]["method"] == "HidD_SetFeature"
    assert traces[0]["report_id"] == 9

    # 3. Feed trace into analyzer
    analysis_res = analyze_artifact(settings, artifact_sha, trace_path=trace_path)
    run_id = analysis_res["run_id"]

    # 4. Verify evidence graph and confidence class
    evidence_json = settings.workspace_dir / "runs" / run_id / "evidence.json"
    assert evidence_json.is_file()
    ev_data = json.loads(evidence_json.read_text(encoding="utf-8"))
    obs_list = ev_data["observations"]

    # Check that native trace calls became VerifiedDynamicVendorSoftware
    dyn_obs = [o for o in obs_list if o["kind"] == "dynamic.webhid_call" or o["extractor"] == "dynamic.fake_webhid_trace"]
    assert len(dyn_obs) == 2
    for o in dyn_obs:
        assert o["confidence"] == "VerifiedDynamicVendorSoftware"
        assert o["confidence"] != "HardwareVerifiedExchange"
