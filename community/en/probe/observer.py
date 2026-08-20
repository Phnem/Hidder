"""Unified passive transport observer supporting WebHID and Native Desktop backends (English edition).

SAFETY INVARIANT:
This module only OBSERVES vendor software traffic and records marker windows.
It contains NO functions to transmit raw HID writes to devices.
Official vendor software is the writer; PeripheralResearch is purely the observer.
"""

from __future__ import annotations

import atexit
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from probe.native_observer import NativeVendorObserver
    from probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransitionDelta,
        TransportObservation,
    )
    from probe.webhid_observer import WebHidObserver
except ImportError:
    from community.en.probe.native_observer import NativeVendorObserver
    from community.en.probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransitionDelta,
        TransportObservation,
    )
    from community.en.probe.webhid_observer import WebHidObserver


class PassiveTransportObserver:
    """Unified observer managing WebHID and Native backends with pairwise A -> B -> A action correlation."""

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
        self.backend_type: str = "none"
        
        self.webhid_backend: WebHidObserver | None = None
        self.native_backend: NativeVendorObserver | None = None
        
        atexit.register(self.detach)

    @property
    def formatted_device_id(self) -> str:
        if self.target_vid and self.target_pid:
            return f"{self.target_vid}:{self.target_pid}"
        return ""

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
        
        if api == "inputreport" or direction == "in":
            if self.active_action_id not in (
                "phys_keys_wasd", "he_w_light", "he_w_half", "he_w_full", "he_w_slow", "mouse_phys_buttons"
            ):
                if len(bytes_hex) == 16 and report_id in (0, 1):
                    return

        ev_vid = ev.get("vendor_id")
        ev_pid = ev.get("product_id")
        if ev_vid and ev_pid:
            dev_id = f"{ev_vid:04X}:{ev_pid:04X}"
        else:
            dev_id = self.formatted_device_id or "UNKNOWN_DEVICE"

        if ev.get("page_origin"):
            self.capture_metadata.page_origin = ev.get("page_origin")

        source = "webhid_inputreport" if api == "inputreport" else (
            "webhid_api_observer" if self.backend_type == "webhid" else "win32_api_hook"
        )

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
        
        if self.is_capturing_idle:
            self.idle_baseline_events.append({
                "report_id": report_id,
                "bytes_hex": bytes_hex,
                "api": api,
                "direction": direction,
                "timestamp": ts,
            })

    def correlate_actions(
        self,
        guided_actions: list[Any],
    ) -> list[CorrelationCandidate]:
        if not self.observations:
            return []

        candidates: list[CorrelationCandidate] = []
        idle_hexes = {e["bytes_hex"] for e in self.idle_baseline_events}
        
        action_obs_map: dict[str, list[TransportObservation]] = {}
        for o in self.observations:
            if o.action_id:
                action_obs_map.setdefault(o.action_id, []).append(o)

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

            novel_change = [e for e in change_events if e.bytes_hex not in idle_hexes]
            target_change = novel_change if novel_change else change_events

            novel_restore = [e for e in restore_events if e.bytes_hex not in idle_hexes]
            target_restore = novel_restore if novel_restore else restore_events

            if not target_change and not target_restore:
                continue

            candidate_reports = sorted(list({e.report_id for e in (target_change + target_restore)}))
            change_reports = [e.bytes_hex for e in target_change]
            restore_reports = [e.bytes_hex for e in target_restore]

            def _struct_key(hex_str: str) -> tuple[int, int, str]:
                prefix = hex_str[:4] if len(hex_str) >= 4 else hex_str
                return len(hex_str), prefix

            matched_transitions: list[TransitionDelta] = []
            all_changed_offsets: set[int] = set()
            semantic_offsets: set[int] = set()
            checksum_offsets: set[int] = set()

            baseline_reports: list[str] = []
            for b_ev in self.idle_baseline_events:
                b_hex = b_ev["bytes_hex"]
                if any(_struct_key(b_hex) == _struct_key(ch) for ch in change_reports):
                    if b_hex not in baseline_reports:
                        baseline_reports.append(b_hex)

            baseline_available = len(baseline_reports) > 0

            for ch_hex in change_reports:
                ch_key = _struct_key(ch_hex)
                matching_rst = next((rst for rst in restore_reports if _struct_key(rst) == ch_key), None)
                matching_base = next((b for b in baseline_reports if _struct_key(b) == ch_key), None)

                if matching_rst and len(ch_hex) == len(matching_rst):
                    byte_count = len(ch_hex) // 2
                    diff_offsets = []
                    for idx in range(byte_count):
                        ch_byte = ch_hex[idx*2:(idx+1)*2]
                        rst_byte = matching_rst[idx*2:(idx+1)*2]
                        if ch_byte != rst_byte:
                            diff_offsets.append(idx)

                    highest_offset = max(diff_offsets) if diff_offsets else -1

                    for idx in diff_offsets:
                        all_changed_offsets.add(idx)
                        ch_byte = ch_hex[idx*2:(idx+1)*2]
                        rst_byte = matching_rst[idx*2:(idx+1)*2]
                        base_byte = matching_base[idx*2:(idx+1)*2] if matching_base and len(matching_base) == len(ch_hex) else None

                        role = "semantic_field"
                        if idx == highest_offset and (idx in (7, 15, 31, 63) or idx == byte_count - 1):
                            role = "checksum_candidate"
                            checksum_offsets.add(idx)
                        else:
                            semantic_offsets.add(idx)

                        matched_transitions.append(TransitionDelta(
                            offset=idx,
                            before=base_byte,
                            changed=ch_byte,
                            restored=rst_byte,
                            field_role=role,
                        ))

            if baseline_available and matched_transitions:
                restore_matches_original = all(
                    t.restored == t.before for t in matched_transitions if t.before is not None
                )
            elif matched_transitions:
                restore_matches_original = all(
                    t.restored is not None and t.restored != t.changed for t in matched_transitions
                )
            else:
                restore_matches_original = False

            notes = None
            if semantic_name in ("he.rt.press", "he.rt.release"):
                if len(semantic_offsets) > 2 or any(len(v) >= 32 for v in change_reports):
                    semantic_name = f"{semantic_name}_or_rt_table"
                    notes = "Composite Rapid Trigger configuration table (press/release transmitted together)"

            candidates.append(CorrelationCandidate(
                semantic=semantic_name,
                change_action_id=change_aid,
                restore_action_id=restore_aid,
                candidate_reports=candidate_reports,
                changed_offsets=sorted(all_changed_offsets),
                semantic_offsets=sorted(semantic_offsets),
                checksum_offsets=sorted(checksum_offsets),
                baseline_reports=baseline_reports,
                baseline_available=baseline_available,
                change_reports=change_reports,
                restore_reports=restore_reports,
                transitions=matched_transitions,
                restore_matches_original=restore_matches_original,
                confidence="CommunityGuidedObservation",
                notes=notes,
            ))

        for action in single_actions:
            aid = getattr(action, "action_id", "")
            semantic_dict = getattr(action, "expected_semantic", {})
            semantic_name = semantic_dict.get("setting") or aid

            action_events = action_obs_map.get(aid, [])
            if not action_events:
                continue

            candidate_reports = sorted(list({e.report_id for e in action_events}))
            change_reports = [e.bytes_hex for e in action_events]

            baseline_reports = []
            for b_ev in self.idle_baseline_events:
                b_hex = b_ev["bytes_hex"]
                if b_hex not in baseline_reports and any(len(b_hex) == len(ch) for ch in change_reports):
                    baseline_reports.append(b_hex)

            baseline_available = len(baseline_reports) > 0

            all_changed_offsets = set()
            transitions = []

            for ch_hex in change_reports:
                if baseline_available:
                    matching_base = next((b for b in baseline_reports if len(b) == len(ch_hex)), None)
                    if matching_base:
                        for idx in range(len(ch_hex) // 2):
                            if ch_hex[idx*2:(idx+1)*2] != matching_base[idx*2:(idx+1)*2]:
                                all_changed_offsets.add(idx)
                                transitions.append(TransitionDelta(
                                    offset=idx,
                                    before=matching_base[idx*2:(idx+1)*2],
                                    changed=ch_hex[idx*2:(idx+1)*2],
                                    restored=None,
                                    field_role="semantic_field",
                                ))

            candidates.append(CorrelationCandidate(
                semantic=semantic_name,
                change_action_id=aid,
                restore_action_id=None,
                candidate_reports=candidate_reports,
                changed_offsets=sorted(all_changed_offsets),
                semantic_offsets=sorted(all_changed_offsets),
                checksum_offsets=[],
                baseline_reports=baseline_reports,
                baseline_available=baseline_available,
                change_reports=change_reports,
                restore_reports=[],
                transitions=transitions,
                restore_matches_original=False,
                confidence="CommunityGuidedObservation",
            ))

        return candidates
