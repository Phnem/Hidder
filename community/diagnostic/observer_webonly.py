"""Web-only diagnostic observer without any native Win32 injection code."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from community.probe.schema import (
    CaptureMetadata,
    CorrelationCandidate,
    TransitionDelta,
    TransportObservation,
)
from community.probe.webhid_observer import WebHidObserver


class WebOnlyTransportObserver:
    """Pure WebHID observer with zero native injection APIs or helper DLLs."""

    def __init__(self, target_vid: str = "", target_pid: str = "") -> None:
        self.target_vid = target_vid.upper().replace("0X", "")
        self.target_pid = target_pid.upper().replace("0X", "")
        self.active_action_id: str | None = None
        self.observations: list[TransportObservation] = []
        self.idle_baseline_events: list[dict[str, Any]] = []
        self.is_capturing_idle: bool = False
        self._last_event_signature: str | None = None
        self._last_event_ref: TransportObservation | None = None
        
        self.capture_metadata = CaptureMetadata()
        self.webhid_backend: WebHidObserver | None = None

    @property
    def formatted_device_id(self) -> str:
        if self.target_vid and self.target_pid:
            return f"{self.target_vid}:{self.target_pid}"
        return ""

    def attach_webhid(self, target_url: str = "about:blank") -> bool:
        self.capture_metadata.mechanism = "browser_webhid_api_observer"
        self.capture_metadata.hooks_requested = [
            "HIDDevice.prototype.sendReport",
            "HIDDevice.prototype.sendFeatureReport",
            "HIDDevice.prototype.receiveFeatureReport",
            "HIDDevice.addEventListener('inputreport')",
        ]
        self.capture_metadata.hooks_installed = []
        self.capture_metadata.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.webhid_backend = WebHidObserver(on_event_callback=self._handle_raw_event)
        ok = self.webhid_backend.launch_and_attach(target_url)
        
        if ok:
            self.capture_metadata.observer_attached = True
            self.capture_metadata.device_handle_bound = True
            self.capture_metadata.browser = self.webhid_backend.browser_name
            self.capture_metadata.target_process = f"{self.webhid_backend.browser_name}.exe"
            self.capture_metadata.hooks_installed = list(self.capture_metadata.hooks_requested)
            return True
        else:
            self.capture_metadata.observer_attached = False
            self.capture_metadata.hooks_installed = []
            self.capture_metadata.observer_errors.append("Browser launch or CDP attachment failed")
            return False

    def attach_native(self, pid: int, process_basename: str) -> bool:
        print("[!] Native observation is disabled in this web-only build.")
        return False

    def _handle_raw_event(self, ev: dict[str, Any]) -> None:
        api = ev.get("api", "unknown")
        direction = ev.get("direction", "out")
        report_id = int(ev.get("report_id", 0))
        bytes_hex = ev.get("bytes_hex", "")
        ts = float(ev.get("timestamp", time.time()))
        proc_name = self.capture_metadata.target_process or "browser.exe"

        if api == "inputreport" or direction == "in":
            if self.active_action_id not in (
                "phys_keys_wasd", "he_w_light", "he_w_half", "he_w_full", "he_w_slow", "mouse_phys_buttons"
            ):
                if len(bytes_hex) == 16 and report_id in (0, 1):
                    return

        dev_id = self.formatted_device_id or "UNKNOWN_DEVICE"
        source = "webhid_inputreport" if api == "inputreport" else "webhid_api_observer"

        self.record_event(
            api=api,
            direction=direction,
            report_id=report_id,
            bytes_hex=bytes_hex,
            process_basename=proc_name,
            device_id=dev_id,
            timestamp=ts,
            capture_source=source,
        )

    def detach(self) -> None:
        if self.webhid_backend:
            self.webhid_backend.close()
            self.webhid_backend = None

    def set_active_action(self, action_id: str | None) -> None:
        self.active_action_id = action_id
        self._last_event_signature = None
        self._last_event_ref = None

    def start_idle_baseline(self) -> None:
        self.is_capturing_idle = True
        self.set_active_action("vendor_idle_baseline")

    def stop_idle_baseline(self) -> None:
        self.is_capturing_idle = False
        self.set_active_action(None)

    def record_event(
        self,
        api: str,
        direction: str,
        report_id: int,
        bytes_hex: str,
        process_basename: str = "browser.exe",
        device_id: str | None = None,
        timestamp: float | None = None,
        capture_source: str = "webhid_api_observer",
    ) -> None:
        ts = timestamp or time.time()
        byte_len = len(bytes_hex) // 2
        dev_id = device_id or self.formatted_device_id or "UNKNOWN_DEVICE"
        
        sig = f"{self.active_action_id}|{process_basename}|{api}|{direction}|{report_id}|{bytes_hex}"
        
        if sig == self._last_event_signature and self._last_event_ref is not None:
            self._last_event_ref.repeat_count += 1
            self._last_event_ref.last_seen = ts
            return

        obs = TransportObservation(
            timestamp=ts,
            process_basename=process_basename,
            api=api,
            direction=direction,
            report_id=report_id,
            bytes_hex=bytes_hex,
            byte_length=byte_len,
            action_id=self.active_action_id,
            device_id=dev_id,
            repeat_count=1,
            first_seen=ts,
            last_seen=ts,
            capture_source=capture_source,
        )
        self.observations.append(obs)
        self._last_event_signature = sig
        self._last_event_ref = obs

    def correlate_actions(self, guided_actions: list[Any]) -> list[CorrelationCandidate]:
        from community.probe.observer import PassiveTransportObserver
        # Reuse pure mathematical correlation algorithm
        dummy = PassiveTransportObserver()
        dummy.observations = list(self.observations)
        dummy.idle_baseline_events = list(self.idle_baseline_events)
        return dummy.correlate_actions(guided_actions)
