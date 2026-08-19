from pathlib import Path
import pytest

from miner.dynamic.safety_filter import SafetyStatus, classify_control_safety
from miner.dynamic.ui_discovery import discover_controls_in_page
from miner.dynamic.experiment_runner import generate_experiment_plan, run_control_experiments
from miner.dynamic.playwright_webhid import PlaywrightWebHIDRunner, PlaywrightUnavailableError


def test_dangerous_controls_are_quarantined() -> None:
    dangerous_samples = [
        {"label": "Firmware Update (v1.2)", "control_type": "button_action"},
        {"label": "Flash Device", "control_type": "button_action"},
        {"id": "btn-bootloader", "label": "Enter DFU Bootloader", "control_type": "button_action"},
        {"label": "Factory Reset All Settings", "control_type": "button_action"},
        {"aria_label": "EEPROM Clear and Erase", "control_type": "button_action"},
        {"label": "Device Recovery Mode", "control_type": "button_action"},
    ]
    for sample in dangerous_samples:
        decision = classify_control_safety(sample)
        assert decision.status == SafetyStatus.FORBIDDEN
        assert not decision.is_safe_for_auto_experiment


def test_review_only_controls_are_skipped() -> None:
    review_samples = [
        {"label": "Hall Effect Sensor Calibration", "control_type": "button_action"},
        {"label": "SOCD Mode Selection", "control_type": "enum"},
        {"label": "DKS 4-point trigger setting", "control_type": "enum"},
        {"label": "Record Custom Macro", "control_type": "button_action"},
        {"label": "Fn Key Remap Layer 1", "control_type": "button_action"},
    ]
    for sample in review_samples:
        decision = classify_control_safety(sample)
        assert decision.status == SafetyStatus.REVIEW_ONLY
        assert not decision.is_safe_for_auto_experiment


def test_safe_controls_generate_valid_plans() -> None:
    from miner.dynamic.ui_discovery import DiscoveredControl

    slider = DiscoveredControl(
        control_id="actuation_slider",
        selector="#actuation",
        label="Actuation Distance",
        control_type="numeric_slider",
        current_value=1.0,
        min_value=0.1,
        max_value=4.0,
        step=0.1,
        is_safe_for_auto_experiment=True,
    )
    plan = generate_experiment_plan(slider)
    assert len(plan) >= 3
    # Last step must be restore
    assert plan[-1][1] == "restore"
    assert plan[-1][0] == 1.0


def test_browser_ui_discovery_and_experiments_with_quarantine(tmp_path: Path) -> None:
    html_file = tmp_path / "configurator.html"
    html_file.write_text(
        """<!DOCTYPE html>
<html>
<body>
  <button id="connect">Connect Device</button>
  <div>
    <label for="actuation">Actuation Point (mm)</label>
    <input type="range" id="actuation" min="0.2" max="3.8" step="0.1" value="1.0">
  </div>
  <div>
    <label for="rgb-toggle">RGB Backlight</label>
    <input type="checkbox" id="rgb-toggle" checked>
  </div>
  <div>
    <label for="polling">Polling Rate</label>
    <select id="polling">
      <option value="1000">1000 Hz</option>
      <option value="8000">8000 Hz</option>
    </select>
  </div>
  <div>
    <button id="flash-btn">Flash Firmware</button>
  </div>
  <script>
    let dev = null;
    document.getElementById('connect').addEventListener('click', async () => {
      const devs = await navigator.hid.requestDevice({ filters: [] });
      dev = devs[0];
      await dev.open();
    });
    document.getElementById('actuation').addEventListener('change', (e) => {
      if (!dev) return;
      const val = Math.round(parseFloat(e.target.value) * 100);
      dev.sendReport(9, new Uint8Array([0x09, 0x13, (val >> 8) & 0xff, val & 0xff]));
    });
    document.getElementById('rgb-toggle').addEventListener('change', (e) => {
      if (!dev) return;
      dev.sendReport(8, new Uint8Array([0x08, e.target.checked ? 0x01 : 0x00]));
    });
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    runner = PlaywrightWebHIDRunner(headless=True)
    discovered_list = []
    experiment_results = []

    def session_actions(page):
        page.click("#connect")
        page.wait_for_timeout(100)
        controls = discover_controls_in_page(page)
        discovered_list.extend(controls)
        res = run_control_experiments(page, controls)
        experiment_results.extend(res)

    trace_file = tmp_path / "experiment_trace.jsonl"
    try:
        traces = runner.run_and_save_trace(
            html_file.as_uri(),
            trace_file,
            actions_callback=session_actions,
        )
    except PlaywrightUnavailableError:
        pytest.skip("Playwright Chromium unavailable in environment")

    # Verify discovered controls
    labels = {c.label: c for c in discovered_list}
    assert "Actuation Point (mm)" in labels
    assert "RGB Backlight" in labels
    assert "Flash Firmware" in labels

    # Verify Flash Firmware was quarantined and skipped
    flash_ctrl = labels["Flash Firmware"]
    assert flash_ctrl.safety_status == "FORBIDDEN"
    assert not flash_ctrl.is_safe_for_auto_experiment

    # Verify executed experiments
    executed_labels = [r.label for r in experiment_results if r.executed]
    assert "Actuation Point (mm)" in executed_labels
    assert "Flash Firmware" not in executed_labels

    # Verify restore status
    for res in experiment_results:
        if res.executed:
            assert res.restore_status == "RESTORE_CONFIRMED"
