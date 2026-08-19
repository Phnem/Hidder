"""Playwright-driven Fake-WebHID / Fake-WebUSB dynamic browser producer.

Runs official vendor configurators inside controlled Chromium with injected
fake navigator.hid / navigator.usb. Captures all transport activity as immutable
trace events without physical HID access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

_RUNTIME_JS_PATH = Path(__file__).parent / "fake_browser" / "runtime.js"


class PlaywrightUnavailableError(RuntimeError):
    """Raised when Playwright or required browser binaries are unavailable."""


class PlaywrightWebHIDRunner:
    """Manages headless browser sessions with injected Fake-WebHID/WebUSB mocks."""

    def __init__(
        self,
        device_config: dict[str, Any] | None = None,
        canned_responses: dict[str, str] | None = None,
        headless: bool = True,
        timeout_ms: int = 15000,
    ) -> None:
        self.device_config = device_config or {
            "vendorId": 0x1234,
            "productId": 0x5678,
            "productName": "Simulated Protocol Miner HID Device",
            "collections": [
                {
                    "usagePage": 0xFF00,
                    "usage": 0x01,
                    "inputReports": [{"reportId": 0}],
                    "outputReports": [{"reportId": 0}],
                    "featureReports": [{"reportId": 0}],
                }
            ],
        }
        self.canned_responses = canned_responses or {}
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._runtime_js = _RUNTIME_JS_PATH.read_text(encoding="utf-8")

    def run_session(
        self,
        target_url: str,
        actions_callback: Callable[[Any], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Run Chromium, inject fake WebHID runtime, navigate to target, execute actions, and return traces."""
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise PlaywrightUnavailableError("playwright Python package is not installed") from exc

        recorded_traces: list[dict[str, Any]] = []

        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(
                        headless=self.headless,
                        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                    )
                except Exception as exc:
                    raise PlaywrightUnavailableError(f"Chromium failed to launch: {exc}") from exc

                try:
                    context = browser.new_context(
                        ignore_https_errors=True,
                    )
                    # Expose binding before adding init script
                    def bridge_record(trace_json: str) -> None:
                        try:
                            item = json.loads(trace_json)
                            recorded_traces.append(item)
                        except json.JSONDecodeError:
                            pass

                    context.expose_binding(
                        "__protocolMinerBridgeRecord",
                        lambda source, trace_json: bridge_record(trace_json),
                    )

                    # Prepare init script with configuration
                    config_script = (
                        f"window.__protocolMinerDeviceConfig = {json.dumps(self.device_config)};\n"
                        f"window.__protocolMinerCannedResponses = {json.dumps(self.canned_responses)};\n"
                    )
                    context.add_init_script(config_script + "\n" + self._runtime_js)

                    page = context.new_page()

                    def handle_console(msg: Any) -> None:
                        text = msg.text
                        if text.startswith("__PM_TRACE__:"):
                            try:
                                item = json.loads(text[len("__PM_TRACE__:") :])
                                if item not in recorded_traces:
                                    recorded_traces.append(item)
                            except json.JSONDecodeError:
                                pass

                    page.on("console", handle_console)

                    # Navigate to target
                    page.goto(target_url, wait_until="domcontentloaded", timeout=self.timeout_ms)

                    # Execute optional action script
                    if actions_callback is not None:
                        actions_callback(page)

                    page.wait_for_timeout(500)
                finally:
                    browser.close()
        except PlaywrightUnavailableError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Playwright Fake-WebHID session failed: {exc}") from exc

        return recorded_traces

    def run_and_save_trace(
        self,
        target_url: str,
        output_trace_path: Path,
        actions_callback: Callable[[Any], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Run session and write immutable JSONL trace."""
        traces = self.run_session(target_url, actions_callback)
        output_trace_path.parent.mkdir(parents=True, exist_ok=True)
        with output_trace_path.open("w", encoding="utf-8") as f:
            for item in traces:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return traces
