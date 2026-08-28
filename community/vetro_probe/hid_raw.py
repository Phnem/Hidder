"""Low-level HID raw transport (send 63B frame / recv 63B reply).

Isolated from vetro_probe typed layer. Tries `hid` (hidapi) first; falls back to
Windows SetupAPI via ctypes if needed. Never exposed to bundle / plan.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

# Ensure DB on path
_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))


REPORT_ID = 9
FRAME_LEN = 63


class HidRawError(ConnectionError):
    pass


class HidRawTransport:
    """Implements aula_kb_v3.transport.DeviceTransport (send/recv 63B)."""

    def __init__(self, path: str | None = None, product_uuid: int | None = None):
        self.path = path
        self.product_uuid = product_uuid
        self._dev: Any | None = None
        self._session = 1
        self._valid = True
        self._open()

    def _open(self) -> None:
        import time

        attempts = 4
        last_error = "no device or hidapi error"
        for attempt in range(attempts):
            try:
                import hid  # type: ignore

                if self.path:
                    dev = hid.device()
                    dev.open_path(self.path.encode("utf-8") if isinstance(self.path, str) else self.path)
                    dev.set_nonblocking(False)
                    self._dev = dev
                    return
                # auto-discover
                candidates = self._enumerate_via_hid()
                if not candidates:
                    raise HidRawError("no AULA HID collection found (hid.enumerate returned empty)")
                # pick first vendor collection (usage_page 0xFF60)
                path = candidates[0]["path"]
                dev = hid.device()
                dev.open_path(path.encode("utf-8") if isinstance(path, str) else path)
                dev.set_nonblocking(False)
                self._dev = dev
                self.path = path
                return
            except Exception as e:
                last_error = str(e)
                if attempt < attempts - 1:
                    time.sleep(0.08)

        raise HidRawError(f"hid transport unavailable: hid.open failed after {attempts} attempts ({last_error})")

    def _enumerate_via_hid(self) -> list[dict[str, Any]]:
        try:
            import hid  # type: ignore

            devs = hid.enumerate(0x372E, 0x103E)
            # Filter to vendor collection 0xFF60:0x0061 which is the config channel
            filtered = []
            for d in devs:
                up = d.get("usage_page")
                u = d.get("usage")
                if up == 0xFF60 and u == 0x61:
                    filtered.append(d)
            return filtered or devs
        except Exception:
            return []

    # --- aula_kb_v3.transport.DeviceTransport interface (send 63B, recv 63B) ---
    def send(self, frame: bytes) -> None:
        if not self._valid:
            raise HidRawError("stale session")
        if len(frame) != FRAME_LEN:
            raise HidRawError(f"frame must be {FRAME_LEN} bytes, got {len(frame)}")
        # Verify checksum via protocol (optional)
        try:
            from DB.aula_kb_v3 import protocol as _p  # type: ignore
            _p.verify_frame(bytes(frame))
        except ImportError:
            try:
                import aula_kb_v3.protocol as _p  # type: ignore

                _p.verify_frame(bytes(frame))
            except Exception:
                pass
        # If we have a ctypes handle, use WriteFile; else hidapi
        if hasattr(self, "_ctypes_handle") and self._dev is None:
            import ctypes

            # HID WriteFile expects report_id as first byte
            buf = bytes([REPORT_ID]) + bytes(frame)
            written = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.WriteFile(self._ctypes_handle, buf, len(buf), ctypes.byref(written), None)
            if not ok:
                err = ctypes.windll.kernel32.GetLastError()
                self._valid = False
                raise HidRawError(f"WriteFile failed: Win32 {err}")
            return
        # hidapi path
        assert self._dev is not None
        # hid.device.write expects list[int] with report_id first
        payload = [REPORT_ID] + list(frame)
        try:
            n = self._dev.write(payload)  # type: ignore
            if n < 0:
                self._valid = False
                raise HidRawError("hid write failed")
        except Exception as e:
            self._valid = False
            raise HidRawError(str(e)) from e

    def recv(self, timeout_ms: int = 1000) -> bytes:
        if not self._valid:
            raise HidRawError("stale session")
    def recv(self, timeout_ms: int = 1000) -> bytes:
        if not self._valid or self._dev is None:
            raise HidRawError("stale session")
        try:
            data = self._dev.read(64, timeout_ms=timeout_ms)  # type: ignore
        except Exception as e:
            raise HidRawError(str(e)) from e
        if not data:
            raise HidRawError("read timeout")
        buf = bytes(data)
        if len(buf) == 64 and buf[0] == REPORT_ID:
            return buf[1:]
        if len(buf) == 63:
            return buf
        if buf[0] == REPORT_ID and len(buf) >= 63:
            return buf[1:64]
        return buf[:63]

    def close(self) -> None:
        self._valid = False
        if self._dev is not None:
            try:
                self._dev.close()  # type: ignore
            except Exception:
                pass
            self._dev = None
        self._session += 1

    # For polling reconnection: close and reopen
    def reacquire(self, expected_uuid: int, timeout_ms: int = 5000) -> "HidRawTransport":
        self.close()
        deadline = time.time() + timeout_ms / 1000.0
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                new = HidRawTransport(path=None, product_uuid=expected_uuid)
                # Verify identity immediately
                try:
                    import aula_kb_v3.operations as ops  # type: ignore
                    import aula_kb_v3.registry as reg  # type: ignore
                except ImportError:
                    import DB.aula_kb_v3.operations as ops  # type: ignore
                    import DB.aula_kb_v3.registry as reg  # type: ignore
                prod = ops.connect(new)  # type: ignore
                if prod.uuid != expected_uuid:
                    new.close()
                    raise HidRawError(f"re-enumerated uuid {prod.uuid:#x} != expected {expected_uuid:#x}")
                return new
            except Exception as e:
                last_err = e
                time.sleep(0.2)
        raise HidRawError(f"reacquire timeout for uuid {expected_uuid:#x}: {last_err}")

    def is_connected(self) -> bool:
        return self._valid
