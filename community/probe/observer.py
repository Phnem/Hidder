"""Passive HID transport observer and window correlation engine.

SAFETY INVARIANT:
This module only OBSERVES vendor software traffic and records marker windows.
It contains NO functions to transmit raw HID writes to devices.
Official vendor software is the writer; PeripheralResearch is purely the observer.
"""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransportObservation,
    )
except ImportError:
    from community.probe.schema import (
        CaptureMetadata,
        CorrelationCandidate,
        TransportObservation,
    )

PIPE_ACCESS_INBOUND = 0x00000001
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
INVALID_HANDLE_VALUE = -1
PIPE_NAME = r"\\.\pipe\PeripheralResearch_Observer"


class PassiveTransportObserver:
    """Collects, deduplicates, and correlates passive transport events with guided action windows."""

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
        self.attached_pid: int | None = None
        self.target_process_name: str = ""
        
        self._temp_dir: Path | None = None
        self._pipe_stop_event = threading.Event()
        self._pipe_thread: threading.Thread | None = None
        self._pipe_handle: int | None = None
        
        # Start IPC pipe server
        if sys.platform == "win32":
            self._start_pipe_server()
            atexit.register(self.detach)

    def _start_pipe_server(self) -> None:
        """Start Windows Named Pipe listener in background thread."""
        self._pipe_stop_event.clear()
        self._pipe_thread = threading.Thread(target=self._pipe_worker, daemon=True)
        self._pipe_thread.start()

    def _pipe_worker(self) -> None:
        kernel32 = ctypes.windll.kernel32
        while not self._pipe_stop_event.is_set():
            h_pipe = kernel32.CreateNamedPipeW(
                PIPE_NAME,
                PIPE_ACCESS_INBOUND,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                2,
                65536,
                65536,
                0,
                None
            )
            if h_pipe == INVALID_HANDLE_VALUE:
                time.sleep(0.1)
                continue
                
            self._pipe_handle = h_pipe
            connected = kernel32.ConnectNamedPipe(h_pipe, None)
            if not connected and kernel32.GetLastError() != 535: # ERROR_PIPE_CONNECTED
                kernel32.CloseHandle(h_pipe)
                continue

            buf = ctypes.create_string_buffer(65536)
            bytes_read = wintypes.DWORD(0)

            while not self._pipe_stop_event.is_set():
                success = kernel32.ReadFile(h_pipe, buf, 65536, ctypes.byref(bytes_read), None)
                if success and bytes_read.value > 0:
                    msg = buf.raw[:bytes_read.value].decode("utf-8", errors="replace").strip()
                    for line in msg.splitlines():
                        if line:
                            self._handle_raw_pipe_message(line)
                else:
                    break

            try:
                kernel32.DisconnectNamedPipe(h_pipe)
                kernel32.CloseHandle(h_pipe)
            except Exception:
                pass

    def _handle_raw_pipe_message(self, line: str) -> None:
        try:
            ev = json.loads(line)
            api = ev.get("api", "unknown")
            direction = ev.get("direction", "out")
            report_id = int(ev.get("report_id", 0))
            bytes_hex = ev.get("bytes_hex", "")
            ts = float(ev.get("timestamp", time.time()))
            
            # Record into observer
            self.record_event(
                api=api,
                direction=direction,
                report_id=report_id,
                bytes_hex=bytes_hex,
                process_basename=self.target_process_name or "VendorApp.exe",
                timestamp=ts,
                capture_source="win32_api_hook"
            )
        except Exception:
            pass

    def attach_to_process(self, pid: int, process_basename: str) -> bool:
        """Inject observer hook into the target vendor process."""
        if sys.platform != "win32":
            return False
            
        self.attached_pid = pid
        self.target_process_name = process_basename
        self.capture_metadata.target_process = process_basename
        self.capture_metadata.target_pid = pid
        self.capture_metadata.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Locate or unpack hook DLL
        dll_path = self._get_hook_dll_path()
        if not dll_path or not dll_path.is_file():
            err = "Hook DLL not found or failed to extract"
            self.capture_metadata.observer_errors.append(err)
            return False

        # Perform User-Mode DLL Injection
        try:
            success = self._inject_dll(pid, dll_path)
            if success:
                self.capture_metadata.observer_attached = True
                self.capture_metadata.device_handle_bound = True
                return True
            else:
                self.capture_metadata.observer_errors.append("CreateRemoteThread returned false")
                return False
        except PermissionError as pe:
            self.capture_metadata.observer_errors.append(str(pe))
            raise
        except Exception as exc:
            self.capture_metadata.observer_errors.append(str(exc))
            return False

    def _get_hook_dll_path(self) -> Path | None:
        """Find hook DLL in package or extract from embedded assets to clean temp dir."""
        # 1. Check local assets directory
        module_dir = Path(__file__).resolve().parent
        local_asset = module_dir / "assets" / "probe_hook_x64.dll"
        if local_asset.is_file():
            return local_asset
            
        # 2. Check cargo build output
        cargo_asset = module_dir.parent / "probe_hook" / "target" / "release" / "probe_hook.dll"
        if cargo_asset.is_file():
            return cargo_asset

        # 3. Extract embedded bytes if bundled inside PyInstaller
        try:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                meipass_asset = Path(sys._MEIPASS) / "probe" / "assets" / "probe_hook_x64.dll"
                if meipass_asset.is_file():
                    return meipass_asset
        except Exception:
            pass

        return None

    def _inject_dll(self, pid: int, dll_path: Path) -> bool:
        """Win32 user-mode LoadLibraryW remote thread injection."""
        kernel32 = ctypes.windll.kernel32
        
        PROCESS_CREATE_THREAD = 0x0002
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_OPERATION = 0x0008
        PROCESS_VM_WRITE = 0x0020
        PROCESS_VM_READ = 0x0010
        
        desired_access = (
            PROCESS_CREATE_THREAD
            | PROCESS_QUERY_INFORMATION
            | PROCESS_VM_OPERATION
            | PROCESS_VM_WRITE
            | PROCESS_VM_READ
        )
        
        h_proc = kernel32.OpenProcess(desired_access, False, pid)
        if not h_proc or h_proc == INVALID_HANDLE_VALUE:
            err = kernel32.GetLastError()
            if err == 5:  # ERROR_ACCESS_DENIED
                raise PermissionError(
                    "Target vendor process is running with Administrator privileges. "
                    "Please restart PeripheralResearch as Administrator."
                )
            return False

        try:
            dll_str = str(dll_path.resolve())
            dll_bytes = dll_str.encode("utf-16le") + b"\x00\x00"
            path_len = len(dll_bytes)

            remote_mem = kernel32.VirtualAllocEx(
                h_proc,
                None,
                path_len,
                0x3000,  # MEM_COMMIT | MEM_RESERVE
                0x04     # PAGE_READWRITE
            )
            if not remote_mem:
                return False

            written = wintypes.DWORD(0)
            write_ok = kernel32.WriteProcessMemory(
                h_proc,
                remote_mem,
                dll_bytes,
                path_len,
                ctypes.byref(written)
            )
            if not write_ok:
                return False

            h_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
            p_load_lib = kernel32.GetProcAddress(h_kernel32, b"LoadLibraryW")

            thread_id = wintypes.DWORD(0)
            h_thread = kernel32.CreateRemoteThread(
                h_proc,
                None,
                0,
                p_load_lib,
                remote_mem,
                0,
                ctypes.byref(thread_id)
            )

            if h_thread:
                kernel32.WaitForSingleObject(h_thread, 3000)
                kernel32.CloseHandle(h_thread)
                return True
            return False
        finally:
            kernel32.CloseHandle(h_proc)

    def detach(self) -> None:
        """Unhook and clean up temporary resources."""
        self._pipe_stop_event.set()
        if self._pipe_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._pipe_handle)
            except Exception:
                pass
        if self._temp_dir and self._temp_dir.is_dir():
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def set_active_action(self, action_id: str | None) -> None:
        """Set the active marker window for subsequent transport events."""
        self.active_action_id = action_id
        self._last_event_signature = None
        self._last_event_ref = None

    def start_idle_baseline(self) -> None:
        """Start marking events as idle baseline traffic."""
        self.is_capturing_idle = True
        self.set_active_action("vendor_idle_baseline")

    def stop_idle_baseline(self) -> None:
        """Finish idle baseline capture."""
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
        """Record an observed transport event with automatic idle deduplication."""
        ts = timestamp or time.time()
        byte_len = len(bytes_hex) // 2
        
        # Deduplication signature: same action, same process, same api, same bytes
        sig = f"{self.active_action_id}|{process_basename}|{api}|{report_id}|{bytes_hex}"
        
        if sig == self._last_event_signature and self._last_event_ref is not None:
            # Increment repeat count on existing observation instead of bloating JSON
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
        """Correlate change and restore action windows to find changed byte offsets."""
        if not self.observations:
            return []

        candidates: list[CorrelationCandidate] = []
        idle_hexes = {e["bytes_hex"] for e in self.idle_baseline_events}
        
        action_obs_map: dict[str, list[TransportObservation]] = {}
        for o in self.observations:
            if o.action_id:
                action_obs_map.setdefault(o.action_id, []).append(o)

        for action in guided_actions:
            aid = getattr(action, "action_id", "")
            semantic_dict = getattr(action, "expected_semantic", {})
            semantic_name = semantic_dict.get("setting") or aid
            
            action_events = action_obs_map.get(aid, [])
            novel_events = [e for e in action_events if e.bytes_hex not in idle_hexes]
            target_events = novel_events if novel_events else action_events
            
            if not target_events:
                continue

            candidate_reports = list({e.report_id for e in target_events})
            after_values = [e.bytes_hex for e in target_events]
            before_values = list(idle_hexes)[:3] if idle_hexes else ["00" * target_events[0].byte_length]
            
            changed_offsets: set[int] = set()
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
