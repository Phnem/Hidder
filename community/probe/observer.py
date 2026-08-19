"""Unified passive transport observer supporting WebHID and Native Desktop backends.

SAFETY INVARIANT:
This module only OBSERVES vendor software traffic and records marker windows.
It contains NO functions to transmit raw HID writes to devices.
Official vendor software is the writer; PeripheralResearch is purely the observer.
"""

from __future__ import annotations

import atexit
import sys
import time
from pathlib import Path
from typing import Any

try:
    from probe.native_observer import NativeVendorObserver
    from probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransportObservation,
    )
    from probe.webhid_observer import WebHidObserver
except ImportError:
    from community.probe.native_observer import NativeVendorObserver
    from community.probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransportObservation,
    )
    from community.probe.webhid_observer import WebHidObserver


class PassiveTransportObserver:
    """Unified observer managing WebHID and Native backends with pairwise A -> B -> A action correlation."""

    def __init__(self, target_vid: str = "", target_pid: str = "") -> None:
        self.target_vid = target_vid.upper()
        self.target_pid = target_pid.upper()
        self.active_action_id: str | None = None
        self.observations: list[TransportObservation] = []
        self.idle_baseline_events: list[dict[str, Any]] = []
        self.is_capturing_idle: bool = False
        self._last_event_signature: str | None = None
        self._last_event_ref: TransportObservation | None = None
        
        self.capture_metadata = CaptureMetadata()
        self.backend_type: str = "none"
        
        self.webhid_backend: WebHidObserver | None = None
        self.native_backend: NativeVendorObserver | None = None
        
        atexit.register(self.detach)

    def attach_webhid(self, target_url: str = "about:blank") -> bool:
        """Launch controlled browser and attach WebHID instrumentation."""
        self.backend_type = "webhid"
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
        """Attach MinHook DLL injector to native desktop application."""
        self.backend_type = "native"
        self.capture_metadata.mechanism = "win32_user_mode_api_hook"
        self.capture_metadata.target_process = process_basename
        self.capture_metadata.target_pid = pid
        self.capture_metadata.hooks_requested = [
            "WriteFile",
            "HidD_SetFeature",
            "HidD_GetFeature",
            "HidD_SetOutputReport",
            "HidD_GetInputReport",
        ]
        self.capture_metadata.hooks_installed = []
        self.capture_metadata.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.native_backend = NativeVendorObserver(on_event_callback=self._handle_raw_event)
        try:
            ok = self.native_backend.attach_to_process(pid, process_basename)
            if ok:
                self.capture_metadata.observer_attached = True
                self.capture_metadata.device_handle_bound = True
                self.capture_metadata.hooks_installed = list(self.capture_metadata.hooks_requested)
                return True
            else:
                self.capture_metadata.observer_attached = False
                self.capture_metadata.hooks_installed = []
                self.capture_metadata.observer_errors.append("CreateRemoteThread returned false")
                return False
        except PermissionError as pe:
            self.capture_metadata.observer_attached = False
            self.capture_metadata.hooks_installed = []
            self.capture_metadata.observer_errors.append(str(pe))
            raise
        except Exception as exc:
            self.capture_metadata.observer_attached = False
            self.capture_metadata.hooks_installed = []
            self.capture_metadata.observer_errors.append(str(exc))
            return False

    def _handle_raw_event(self, ev: dict[str, Any]) -> None:
        api = ev.get("api", "unknown")
        direction = ev.get("direction", "out")
        report_id = int(ev.get("report_id", 0))
        bytes_hex = ev.get("bytes_hex", "")
        ts = float(ev.get("timestamp", time.time()))
        
        proc_name = self.capture_metadata.target_process or "VendorSoftware"
        source = "webhid_api_observer" if self.backend_type == "webhid" else "win32_api_hook"
        
        if ev.get("page_origin"):
            self.capture_metadata.page_origin = ev.get("page_origin")

        self.record_event(
            api=api,
            direction=direction,
            report_id=report_id,
            bytes_hex=bytes_hex,
            process_basename=proc_name,
            timestamp=ts,
            capture_source=source,
        )

    def detach(self) -> None:
        if self.webhid_backend:
            self.webhid_backend.close()
            self.webhid_backend = None
        if self.native_backend:
            self.native_backend.close()
            self.native_backend = None

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
        process_basename: str = "VendorApp.exe",
        device_id: str | None = None,
        timestamp: float | None = None,
        capture_source: str = "win32_api_hook",
    ) -> None:
        ts = timestamp or time.time()
        byte_len = len(bytes_hex) // 2
        
        sig = f"{self.active_action_id}|{process_basename}|{api}|{report_id}|{bytes_hex}"
        
        if sig == self._last_event_signature and self._last_event_ref is not None:
            self._last_event_ref.repeat_count += 1
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
            device_id=device_id,
            repeat_count=1,
            capture_source=capture_source,
        )
        
        self.observations.append(obs)
        self._last_event_signature = sig
        self._last_event_ref = obs
        
        if self.is_capturing_idle:
            self.idle_baseline_events.append({
                "report_id": report_id,
                "bytes_hex": bytes_hex,
                "api": api,
            })

    def correlate_actions(
        self,
        guided_actions: list[Any],
    ) -> list[CorrelationCandidate]:
        """Correlate actions with pairwise A -> B -> A change/restore comparison."""
        if not self.observations:
            return []

        candidates: list[CorrelationCandidate] = []
        idle_hexes = {e["bytes_hex"] for e in self.idle_baseline_events}
        
        action_obs_map: dict[str, list[TransportObservation]] = {}
        for o in self.observations:
            if o.action_id:
                action_obs_map.setdefault(o.action_id, []).append(o)

        # 1. Group paired change/restore actions by base_id
        pairs: dict[str, dict[str, Any]] = {}
        single_actions: list[Any] = []

        for action in guided_actions:
            aid = getattr(action, "action_id", "")
            if aid.endswith("_change"):
                base_id = aid[:-7]
                pairs.setdefault(base_id, {})["change"] = action
            elif aid.endswith("_restore"):
                base_id = aid[:-8]
                pairs.setdefault(base_id, {})["restore"] = action
            else:
                single_actions.append(action)

        # 2. Process paired change/restore actions (True A -> B -> A logic)
        for base_id, pair in pairs.items():
            change_act = pair.get("change")
            restore_act = pair.get("restore")
            if not change_act:
                continue

            change_aid = change_act.action_id
            restore_aid = restore_act.action_id if restore_act else None

            semantic_dict = getattr(change_act, "expected_semantic", {})
            semantic_name = semantic_dict.get("setting") or base_id

            change_events = action_obs_map.get(change_aid, [])
            restore_events = action_obs_map.get(restore_aid, []) if restore_aid else []

            # Filter out idle background noise if novel events exist
            novel_change = [e for e in change_events if e.bytes_hex not in idle_hexes]
            target_change = novel_change if novel_change else change_events

            novel_restore = [e for e in restore_events if e.bytes_hex not in idle_hexes]
            target_restore = novel_restore if novel_restore else restore_events

            if not target_change and not target_restore:
                continue

            candidate_reports = list({e.report_id for e in (target_change + target_restore)})
            after_values = [e.bytes_hex for e in target_change]
            restored_values = [e.bytes_hex for e in target_restore]
            
            # Baseline A: use idle hexes if available, or restored values as reference
            before_values = list(idle_hexes)[:3] if idle_hexes else (restored_values[:1] if restored_values else ["00" * (len(after_values[0]) // 2 if after_values else 8)])

            changed_offsets: set[int] = set()

            # Compare change (B) vs restore (A') and baseline (A)
            for aft in after_values:
                for rst in restored_values:
                    if len(aft) == len(rst):
                        for byte_idx in range(len(aft) // 2):
                            if aft[byte_idx*2:(byte_idx+1)*2] != rst[byte_idx*2:(byte_idx+1)*2]:
                                changed_offsets.add(byte_idx)
                for bef in before_values:
                    if len(aft) == len(bef):
                        for byte_idx in range(len(aft) // 2):
                            if aft[byte_idx*2:(byte_idx+1)*2] != bef[byte_idx*2:(byte_idx+1)*2]:
                                changed_offsets.add(byte_idx)

            # Disambiguate RT Press & RT Release when transmitted as composite tables
            if semantic_name in ("he.rt.press", "he.rt.release"):
                if len(changed_offsets) > 2 or any(len(v) >= 32 for v in after_values):
                    semantic_name = f"{semantic_name}_or_rt_table"

            candidates.append(CorrelationCandidate(
                semantic=semantic_name,
                action_id=base_id,
                candidate_reports=candidate_reports,
                changed_offsets=sorted(changed_offsets),
                before_values=before_values,
                after_values=after_values,
                restored_values=restored_values,
                confidence="CommunityGuidedObservation",
            ))

        # 3. Process single / analog baseline actions (e.g. he_w_light, he_w_half, etc.)
        for action in single_actions:
            aid = getattr(action, "action_id", "")
            semantic_dict = getattr(action, "expected_semantic", {})
            semantic_name = semantic_dict.get("setting") or aid

            action_events = action_obs_map.get(aid, [])
            if not action_events:
                continue

            candidate_reports = list({e.report_id for e in action_events})
            after_values = [e.bytes_hex for e in action_events]
            before_values = list(idle_hexes)[:3] if idle_hexes else ["00" * (len(after_values[0]) // 2 if after_values else 8)]

            changed_offsets = set()
            for aft in after_values:
                for bef in before_values:
                    if len(aft) == len(bef):
                        for byte_idx in range(len(aft) // 2):
                            if aft[byte_idx*2:(byte_idx+1)*2] != bef[byte_idx*2:(byte_idx+1)*2]:
                                changed_offsets.add(byte_idx)

            candidates.append(CorrelationCandidate(
                semantic=semantic_name,
                action_id=aid,
                candidate_reports=candidate_reports,
                changed_offsets=sorted(changed_offsets),
                before_values=before_values,
                after_values=after_values,
                restored_values=[],
                confidence="CommunityGuidedObservation",
            ))

        return candidates
