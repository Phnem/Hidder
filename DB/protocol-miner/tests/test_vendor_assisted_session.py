from pathlib import Path

from miner.dynamic.ui_discovery import DiscoveredControl
from miner.dynamic.vendor_assisted_session import VendorAssistedResearchSession
from miner.storage.pevidence import validate_pevidence_bundle


def test_vendor_assisted_session_full_lifecycle(tmp_path: Path) -> None:
    session = VendorAssistedResearchSession(
        vendor_name="AULA",
        model_name="F75",
        software_sha256="c" * 64,
        firmware_version="1.0.4",
    )

    control = DiscoveredControl(
        control_id="actuation_slider",
        selector="#actuation",
        label="Actuation Point",
        control_type="numeric_slider",
        current_value=1.0,
        min_value=0.2,
        max_value=3.8,
        step=0.1,
        is_safe_for_auto_experiment=True,
    )

    # 1. Baseline capture
    baseline = session.capture_baseline(control)
    assert baseline == 1.0

    # 2. Driver callback simulating vendor UI mutation
    ui_state = {"current_value": 1.0}

    def vendor_ui_driver(ctrl, target_val, act_id, semantic):
        ui_state["current_value"] = target_val

    def passive_observer_trace_provider(act_id):
        return [
            {
                "method": "sendReport",
                "report_id": 9,
                "bytes_hex": "09130064",
                "ui_action_id": act_id,
            }
        ]

    # 3. Execute experiment and rollback
    ok, msg = session.execute_one_setting_experiment(
        control=control,
        target_value=2.0,
        driver_callback=vendor_ui_driver,
        observer_traces_provider=passive_observer_trace_provider,
    )

    assert ok
    # State should have rolled back to baseline 1.0
    assert ui_state["current_value"] == 1.0
    assert session.restore_status == "RESTORE_CONFIRMED"
    assert len(session.executed_actions) == 2
    assert session.executed_actions[0]["step_type"] == "experiment"
    assert session.executed_actions[1]["step_type"] == "restore"

    # 4. Export to .pevidence bundle
    bundle_path = tmp_path / "aula_session.pevidence"
    session.export_to_pevidence(bundle_path)
    assert bundle_path.is_file()

    # 5. Validate exported bundle
    validation = validate_pevidence_bundle(bundle_path)
    assert validation["valid"]
    assert validation["manifest"]["research_mode"] == "vendor_assisted"
    assert validation["manifest"]["restore_status"] == "RESTORE_CONFIRMED"


def test_vendor_assisted_session_blocks_forbidden_control(tmp_path: Path) -> None:
    session = VendorAssistedResearchSession(
        vendor_name="AULA",
        model_name="F75",
        software_sha256="d" * 64,
    )

    forbidden_control = DiscoveredControl(
        control_id="flash_btn",
        selector="#flash",
        label="Flash Firmware",
        control_type="button_action",
        current_value=None,
        is_safe_for_auto_experiment=False,
    )

    ok, msg = session.execute_one_setting_experiment(
        control=forbidden_control,
        target_value=None,
        driver_callback=lambda *args: None,
    )

    assert not ok
    assert "Safety check failed" in msg
    assert session.restore_status == "RESTORE_UNCERTAIN"
    assert not session.is_active
