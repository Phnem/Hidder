"""Native Windows desktop application observer using MinHook DLL injection (English edition).

Interprets WriteFile and HidD_* calls from native desktop vendor software.
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
from pathlib import Path
from typing import Any, Callable

PIPE_ACCESS_INBOUND = 0x00000001
PIPE_TYPE_MESSAGE = 0x00000004
PIPE_READMODE_MESSAGE = 0x00000002
PIPE_WAIT = 0x00000000
INVALID_HANDLE_VALUE = -1
PIPE_NAME = r"\\.\pipe\PeripheralResearch_Observer"


class NativeVendorObserver:
    """Manages Named Pipe IPC and DLL injection for native Windows desktop software."""

    def __init__(self, on_event_callback: Callable[[dict[str, Any]], None]) -> None:
        self.on_event_callback = on_event_callback
        self.attached_pid: int | None = None
        self.target_process_name: str = ""
        self._temp_dir: Path | None = None
        self._pipe_stop_event = threading.Event()
        self._pipe_thread: threading.Thread | None = None
        self._pipe_handle: int | None = None

        if sys.platform == "win32":
            self._start_pipe_server()
            atexit.register(self.close)

    def _start_pipe_server(self) -> None:
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
            if not connected and kernel32.GetLastError() != 535:
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
                            try:
                                ev = json.loads(line)
                                self.on_event_callback(ev)
                            except Exception:
                                pass
                else:
                    break

            try:
                kernel32.DisconnectNamedPipe(h_pipe)
                kernel32.CloseHandle(h_pipe)
            except Exception:
                pass

    def attach_to_process(self, pid: int, process_basename: str) -> bool:
        if sys.platform != "win32":
            return False
            
        self.attached_pid = pid
        self.target_process_name = process_basename

        dll_path = self._get_hook_dll_path()
        if not dll_path or not dll_path.is_file():
            return False

        return self._inject_dll(pid, dll_path)

    def _get_hook_dll_path(self) -> Path | None:
        module_dir = Path(__file__).resolve().parent
        local_asset = module_dir / "assets" / "probe_hook_x64.dll"
        if local_asset.is_file():
            return local_asset
            
        cargo_asset = module_dir.parent.parent / "probe_hook" / "target" / "release" / "probe_hook.dll"
        if cargo_asset.is_file():
            return cargo_asset

        try:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                meipass_asset = Path(sys._MEIPASS) / "probe" / "assets" / "probe_hook_x64.dll"
                if meipass_asset.is_file():
                    return meipass_asset
        except Exception:
            pass

        return None

    def _inject_dll(self, pid: int, dll_path: Path) -> bool:
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
            if err == 5:
                raise PermissionError(
                    "Target vendor process is running with Administrator privileges. "
                    "Please restart PeripheralResearch as Administrator."
                )
            return False

        try:
            dll_str = str(dll_path.resolve())
            dll_bytes = dll_str.encode("utf-16le") + b"\x00\x00"
            path_len = len(dll_bytes)

            remote_mem = kernel32.VirtualAllocEx(h_proc, None, path_len, 0x3000, 0x04)
            if not remote_mem:
                return False

            written = wintypes.DWORD(0)
            write_ok = kernel32.WriteProcessMemory(h_proc, remote_mem, dll_bytes, path_len, ctypes.byref(written))
            if not write_ok:
                return False

            h_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
            p_load_lib = kernel32.GetProcAddress(h_kernel32, b"LoadLibraryW")

            thread_id = wintypes.DWORD(0)
            h_thread = kernel32.CreateRemoteThread(h_proc, None, 0, p_load_lib, remote_mem, 0, ctypes.byref(thread_id))

            if h_thread:
                kernel32.WaitForSingleObject(h_thread, 3000)
                kernel32.CloseHandle(h_thread)
                return True
            return False
        finally:
            kernel32.CloseHandle(h_proc)

    def close(self) -> None:
        self._pipe_stop_event.set()
        if self._pipe_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._pipe_handle)
            except Exception:
                pass
        if self._temp_dir and self._temp_dir.is_dir():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
