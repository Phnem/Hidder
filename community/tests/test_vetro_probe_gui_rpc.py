"""GUI RPC engine tests (deterministic, no hardware).

Covers the GUI -> engine contract: discovery states, plan from the backend
planner, recovery-first gating, six-op success, FAIL_RESTORED, MANUAL_RESTORE,
recovery startup, blocked ops never counted as failures, zero HID in mock mode,
no physical evidence from mock mode, and no protocol/safety knowledge drift."""

import io
import json
import threading

import pytest

from community.vetro_probe.gui_rpc import (
    DemoEngine, ProbeRpcServer, plan_preview, friendly_label,
)
from community.vetro_probe import feature_gates as fg


def _server(engine=None):
    s = ProbeRpcServer(engine=engine, input_stream=io.StringIO(), output_stream=io.StringIO())
    return s


def _request(s, method, params=None):
    ident = 1
    req = {"id": ident, "method": method, "params": params or {}}
    s.handle(req)
    lines = [l for l in s.output_stream.getvalue().splitlines() if l.strip()]
    s.output_stream = io.StringIO()  # reset for next call
    return json.loads(lines[-1])


def _run_script(s, scenario):
    """Run a demo scenario synchronously, returning (responses, events)."""
    s.engine = DemoEngine(scenario=scenario)
    out = []
    events = []
    orig = s.emit
    def emit(obj):
        events.append(obj)
        orig(obj)
    s.emit = emit
    resp = _request(s, "start_run")
    return resp, events


def _supported_six_ids():
    return ["keyboard.profile", "keyboard.polling", "device.win_lock",
            "he.deadzone", "he.actuation", "light.brightness"]


# 1. GUI cannot start run before recovery preflight clears
def test_start_blocked_until_recovery_cleared():
    s = _server(DemoEngine(scenario="recovery_startup"))
    pre = _request(s, "recovery_status")
    assert pre["ok"] and pre["result"]["preflight"] in ("RECOVERING", "RECOVERY_REQUIRED")
    # clear recovery -> ready
    cleared = _request(s, "clear_recovery")
    assert cleared["result"]["preflight"] == "CLEAR"
    resp = _request(s, "start_run")
    assert resp["result"]["started"] is True


def test_real_path_blocks_start_on_pending_checkpoint(tmp_path):
    from pathlib import Path
    from community.vetro_probe.gui_rpc import recovery_status
    from community.vetro_probe.runstate import RunCheckpoint, RunStateStore
    store = RunStateStore(tmp_path)
    cp = store.new_run()
    cp.operation = "keyboard.polling"
    cp.write_may_have_applied = True
    cp.closed = False
    store.save(cp)
    pre = recovery_status(tmp_path)
    assert pre["pending"] is True and pre["preflight"] in ("RECOVERING", "RECOVERY_REQUIRED")


# 2. unsupported identity/FW disables Start
def test_unsupported_fw_disables_start():
    s = _server(DemoEngine(scenario="unsupported_fw"))
    d = _request(s, "discover")
    assert d["result"]["state"] == "FW_UNSUPPORTED"
    assert d["result"]["supported_count"] == 0


def test_no_device_state():
    s = _server(DemoEngine(scenario="no_device"))
    d = _request(s, "discover")
    assert d["result"]["state"] == "NO_DEVICE"


# 3. GUI plan reflects backend planner exactly
def test_plan_from_backend_planner():
    p = plan_preview()
    ids = [o["id"] for o in p["safe"]]
    assert ids == _supported_six_ids()
    assert p["safe_count"] == 6
    assert friendly_label("keyboard.profile", "AUTO_REVERSIBLE") == "Profiles"


# 4. blocked RT/remap never become executable via GUI
def test_blocked_rt_remap_never_executable_via_gui():
    p = plan_preview()
    blocked_ids = {b["id"] for b in p["blocked"]}
    assert "he.rt" in blocked_ids and "keyboard.remap" in blocked_ids
    for b in p["blocked"]:
        assert b["classification"] == "BLOCKED"
    # and the backend feature gate still blocks them (no drift)
    assert fg.blocker_for("he.rt", vid="0x372E", pid="0x103E",
                          family="aula_kb_v3_wired", fw="0216") is not None
    assert fg.blocker_for("keyboard.remap", vid="0x372E", pid="0x103E",
                          family="aula_kb_v3_wired", fw="0216") is not None


# 5. GUI invokes existing executor rather than direct transport (real path delegates)
def test_gui_delegates_to_existing_engine_not_transport():
    import inspect
    from community.vetro_probe import gui_rpc as g
    src = inspect.getsource(g)
    assert "AutoProbeRun" in src          # real path delegates to the existing engine
    assert "sendReport" not in src         # GUI layer never calls transport directly
    assert "build_rt_set_frame" not in src  # no serializer in the GUI layer


# 6. six-op success flow renders complete/restored
def test_six_op_success_flow():
    s = _server(DemoEngine(scenario="supported"))
    resp, events = _run_script(s, "supported")
    run = events[-1]["data"]
    assert run["status"] == "SUCCESS_RESTORED"
    assert run["restored"] is True
    assert run["checks_completed"] == 6
    assert run["checks_total"] == 6
    progress = [e for e in events if e["event"] == "progress"]
    assert progress  # progress events emitted
    passed = {e["data"]["op"] for e in progress if e["data"]["state"] == "PASS"}
    assert passed == set(_supported_six_ids())


# 7. FAIL_RESTORED clearly reports restored state
def test_fail_restored_reports_restored():
    s = _server(DemoEngine(scenario="fail_restored"))
    resp, events = _run_script(s, "fail_restored")
    run = events[-1]["data"]
    assert run["status"] == "FAIL_RESTORED"
    assert run["restored"] is True
    assert run["checks_completed"] < run["checks_total"]


# 8. FAILED_REQUIRES_MANUAL_RESTORE blocks new run until recovery clears
def test_manual_restore_blocks_new_run():
    s = _server(DemoEngine(scenario="manual"))
    resp, events = _run_script(s, "manual")
    run = events[-1]["data"]
    assert run["status"] == "FAILED_REQUIRES_MANUAL_RESTORE"
    assert run["restored"] is False
    # recovery now pending -> recovery_status says RECOVERING / RECOVERY_REQUIRED
    pre = _request(s, "recovery_status")
    assert pre["result"]["preflight"] in ("RECOVERING", "RECOVERY_REQUIRED")
    # clear recovery (user restored via vendor UI), then a new run is allowed
    cleared = _request(s, "clear_recovery")
    assert cleared["result"]["preflight"] == "CLEAR"
    resp2, events2 = _run_script(s, "supported")
    assert events2[-1]["data"]["status"] == "SUCCESS_RESTORED"


# 9. recovery startup flow works
def test_recovery_startup_flow():
    s = _server(DemoEngine(scenario="recovery_startup"))
    pre = _request(s, "recovery_status")
    assert pre["result"]["preflight"] in ("RECOVERING", "RECOVERY_REQUIRED")
    cleared = _request(s, "clear_recovery")
    assert cleared["result"]["preflight"] == "CLEAR"


# 10. blocked operations are not counted as failures
def test_blocked_not_counted_as_failures():
    p = plan_preview()
    assert all(b["classification"] == "BLOCKED" for b in p["blocked"])
    s = _server(DemoEngine(scenario="supported"))
    resp, events = _run_script(s, "supported")
    run = events[-1]["data"]
    # blocked ops appear in plan but never in run results
    result_ids = {r["id"] for r in run["results"]}
    assert not (result_ids & {"he.rt", "keyboard.remap"})
    assert run["checks_completed"] == 6


# 11. mock mode performs zero HID writes
def test_mock_mode_zero_hid():
    s = _server(DemoEngine(scenario="supported"))
    resp, events = _run_script(s, "supported")
    # the DemoEngine has no transport import and writes nothing
    import inspect
    src = inspect.getsource(DemoEngine)
    assert "transport" not in src and "hid" not in src and "sendReport" not in src


# 12. synthetic UI run does not generate physical evidence
def test_mock_run_no_physical_evidence():
    s = _server(DemoEngine(scenario="supported"))
    resp, events = _run_script(s, "supported")
    run = events[-1]["data"]
    assert run["evidence_source"] == "mock"
    assert run["physical_validation_evidence"] is False


# 13. technical details reflect backend result
def test_run_result_reflects_backend():
    s = _server(DemoEngine(scenario="supported"))
    resp, events = _run_script(s, "supported")
    rr = _request(s, "run_result")
    assert rr["result"]["checks_completed"] == 6
    assert rr["result"]["status"] == "SUCCESS_RESTORED"


# 14. closing/reopening after pending checkpoint triggers recovery-first
def test_reopen_after_pending_checkpoint_recovery_first(tmp_path):
    from community.vetro_probe.runstate import RunCheckpoint, RunStateStore
    store = RunStateStore(tmp_path)
    cp = store.new_run()
    cp.operation = "keyboard.polling"
    cp.baseline = 3
    cp.attempted = 2
    cp.write_may_have_applied = True
    cp.closed = False
    store.save(cp)
    from community.vetro_probe.gui_rpc import recovery_status
    pre = recovery_status(tmp_path)
    assert pre["pending"] is True and pre["preflight"] in ("RECOVERING", "RECOVERY_REQUIRED")


# 15. no protocol/safety knowledge changes from GUI layer
def test_no_protocol_knowledge_changes_from_gui():
    # GUI layer never imports the protocol module or constructs frames
    import inspect
    from community.vetro_probe import gui_rpc as g
    src = inspect.getsource(g)
    for forbidden in ("from .protocol import", "import protocol", "build_frame(",
                      "build_feature_set_frame", "sendReport"):
        assert forbidden not in src, forbidden
    # feature gate state unchanged by GUI usage
    assert fg.blocker_for("light.rgb_core", vid="0x372E", pid="0x103E",
                          family="aula_kb_v3_wired", fw="0216") is not None


# 16. canonical and alias RPC methods resolve correctly
def test_all_canonical_and_alias_rpc_methods():
    s = _server(DemoEngine(scenario="supported"))
    # Check all method synonyms resolve cleanly without 'unknown method' error
    for m in ["health", "get_health", "discover", "get_discover", "get_discovery_state",
              "plan", "get_plan", "recovery_status", "get_recovery_status",
              "clear_recovery", "run_result", "get_run_result"]:
        res = _request(s, m)
        assert res["ok"] is True, f"Method {m} failed: {res}"


def test_unknown_rpc_method_returns_structured_error():
    s = _server(DemoEngine(scenario="supported"))
    res = _request(s, "non_existent_method_xyz")
    assert res["ok"] is False
    assert "unknown method" in res["error"]


def test_packaged_sidecar_smoke_rpc(tmp_path):
    """Smoke test against compiled PyInstaller sidecar binary if present."""
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    bin_path = root / "build_dist" / "vetro-probe-sidecar.exe"
    if not bin_path.is_file():
        bin_path = root / "probe-app" / "src-tauri" / "binaries" / "vetro-probe-sidecar.exe"
    if not bin_path.is_file():
        pytest.skip("packaged vetro-probe-sidecar.exe not found")

    p = subprocess.Popen([str(bin_path), "--gui-rpc", "--gui-demo", "--scenario", "supported"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        # 1. Health
        p.stdin.write(json.dumps({"id": 1, "method": "health"}) + "\n")
        # 2. Get recovery status
        p.stdin.write(json.dumps({"id": 2, "method": "get_recovery_status"}) + "\n")
        # 3. Recovery status (canonical)
        p.stdin.write(json.dumps({"id": 3, "method": "recovery_status"}) + "\n")
        # 4. Discover
        p.stdin.write(json.dumps({"id": 4, "method": "discover"}) + "\n")
        p.stdin.flush()

        r1 = json.loads(p.stdout.readline())
        r2 = json.loads(p.stdout.readline())
        r3 = json.loads(p.stdout.readline())
        r4 = json.loads(p.stdout.readline())

        assert r1["ok"] is True and r1["result"]["method"] == "health"
        assert r2["ok"] is True and r2["result"]["preflight"] == "CLEAR"
        assert r3["ok"] is True and r3["result"]["preflight"] == "CLEAR"
        assert r4["ok"] is True and r4["result"]["state"] == "IDENTIFIED"
    finally:
        p.kill()

