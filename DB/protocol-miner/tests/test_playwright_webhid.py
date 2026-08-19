import json
from pathlib import Path

import pytest

from miner.dynamic.playwright_webhid import PlaywrightWebHIDRunner, PlaywrightUnavailableError
from miner.dynamic.webhid_trace import load as load_trace


def test_playwright_webhid_produces_trace_and_no_real_hid(tmp_path: Path) -> None:
    html_file = tmp_path / "index.html"
    html_file.write_text(
        """<!DOCTYPE html>
<html>
<head><title>Vendor WebHID Configurator</title></head>
<body>
  <h1>Vendor Configurator</h1>
  <button id="connect">Connect</button>
  <button id="set-actuation">Set Actuation</button>
  <script>
    let dev = null;
    document.getElementById('connect').addEventListener('click', async () => {
      const devices = await navigator.hid.requestDevice({ filters: [] });
      dev = devices[0];
      await dev.open();
    });
    document.getElementById('set-actuation').addEventListener('click', async () => {
      if (!dev) return;
      window.__protocolMinerCurrentActionId = 'act-001';
      window.__protocolMinerCurrentSemanticContext = 'actuation:1.0mm';
      const data = new Uint8Array([0x09, 0x13, 0x00, 0x64]);
      await dev.sendReport(9, data);
      await dev.sendFeatureReport(9, data);
      await dev.receiveFeatureReport(9);
    });
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    runner = PlaywrightWebHIDRunner(
        device_config={
            "vendorId": 0x3434,
            "productId": 0x0121,
            "productName": "Simulated Vendor Keyboard",
        },
        canned_responses={"9": "09130064"},
        headless=True,
    )

    def actions(page):
        page.click("#connect")
        page.wait_for_timeout(100)
        page.click("#set-actuation")
        page.wait_for_timeout(100)

    trace_file = tmp_path / "webhid_trace.jsonl"
    try:
        traces = runner.run_and_save_trace(
            html_file.as_uri(),
            trace_file,
            actions_callback=actions,
        )
    except PlaywrightUnavailableError:
        pytest.skip("Playwright Chromium unavailable in environment")

    assert trace_file.is_file()
    assert len(traces) >= 4

    methods = [t.get("method") for t in traces]
    assert "requestDevice" in methods
    assert "open" in methods
    assert "sendReport" in methods
    assert "sendFeatureReport" in methods
    assert "receiveFeatureReport" in methods

    # Verify ingestion into evidence models
    observations = load_trace(trace_file, "a" * 64)
    assert len(observations) >= 4
    for obs in observations:
        assert obs.confidence.value == "VerifiedDynamicVendorSoftware"
        assert obs.confidence.value != "HardwareVerifiedExchange"


def test_unknown_canned_response_marks_unresolved(tmp_path: Path) -> None:
    html_file = tmp_path / "unknown_canned.html"
    html_file.write_text(
        """<!DOCTYPE html>
<html>
<body>
  <script>
    (async () => {
      const devices = await navigator.hid.requestDevice({ filters: [] });
      const dev = devices[0];
      await dev.open();
      // Request report 99 which has no canned response
      await dev.receiveFeatureReport(99);
    })();
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )

    runner = PlaywrightWebHIDRunner(headless=True)
    trace_file = tmp_path / "unknown_trace.jsonl"
    try:
        traces = runner.run_and_save_trace(html_file.as_uri(), trace_file)
    except PlaywrightUnavailableError:
        pytest.skip("Playwright Chromium unavailable in environment")

    # Trace should record unknown canned response note
    unknown_traces = [t for t in traces if t.get("method") == "receiveFeatureReport_unknown"]
    assert len(unknown_traces) == 1
    assert unknown_traces[0].get("note") == "unresolved_canned_response"
