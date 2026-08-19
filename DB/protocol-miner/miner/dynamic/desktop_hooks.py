"""Desktop dynamic interception template generator and trace normalizer (Frida / Win32)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from miner import __version__
from miner.schemas.models import ConfidenceClass, Observation

_FRIDA_WIN32_HID_HOOK_SCRIPT = r"""
// Frida interception script for Windows HID APIs (Peripheral Protocol Miner)
(() => {
  function logApi(name, handle, bufferPtr, length) {
    if (!bufferPtr || length <= 0) return;
    try {
      const bytes = Java ? [] : Memory.readByteArray(bufferPtr, Math.min(length, 256));
      const u8 = new Uint8Array(bytes);
      let hex = '';
      for (let i = 0; i < u8.length; i++) {
        hex += u8[i].toString(16).padStart(2, '0');
      }
      send({
        api: name,
        timestamp: new Date().toISOString(),
        length: length,
        buffer_hex: hex,
        process: Process.id
      });
    } catch (e) {}
  }

  const hid = Module.findExportByName('hid.dll', 'HidD_SetFeature');
  if (hid) {
    Interceptor.attach(hid, {
      onEnter(args) {
        logApi('HidD_SetFeature', args[0], args[1], args[2].toInt32());
      }
    });
  }

  const hidGet = Module.findExportByName('hid.dll', 'HidD_GetFeature');
  if (hidGet) {
    Interceptor.attach(hidGet, {
      onEnter(args) {
        this.buf = args[1];
        this.len = args[2].toInt32();
      },
      onLeave(retval) {
        if (retval.toInt32() !== 0) {
          logApi('HidD_GetFeature', null, this.buf, this.len);
        }
      }
    });
  }

  const writeFile = Module.findExportByName('kernel32.dll', 'WriteFile');
  if (writeFile) {
    Interceptor.attach(writeFile, {
      onEnter(args) {
        logApi('WriteFile', args[0], args[1], args[2].toInt32());
      }
    });
  }
})();
"""


def generate_frida_script() -> str:
    """Return reusable Frida instrumentation script for Windows HID interception."""
    return _FRIDA_WIN32_HID_HOOK_SCRIPT


def normalize_desktop_trace_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    """Convert raw Frida / desktop hook payload into standard trace format."""
    api_name = raw_event.get("api", "unknown_api")
    buf_hex = raw_event.get("buffer_hex", "")
    length = raw_event.get("length", len(buf_hex) // 2 if buf_hex else 0)

    report_id = None
    if buf_hex and len(buf_hex) >= 2:
        try:
            report_id = int(buf_hex[:2], 16)
        except ValueError:
            pass

    return {
        "transport": "win32_hid",
        "method": api_name,
        "report_id": report_id,
        "bytes_hex": buf_hex,
        "length": length,
        "timestamp": raw_event.get("timestamp"),
        "process": raw_event.get("process"),
        "stack": raw_event.get("stack", []),
    }


def load_desktop_trace(path: Path, artifact_sha256: str) -> list[Observation]:
    """Ingest JSONL file of desktop intercepted events into normalized Observations."""
    observations: list[Observation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        norm = normalize_desktop_trace_event(item)
        canonical = json.dumps(norm, sort_keys=True, separators=(",", ":"))
        identifier = f"obs-native-{hashlib.sha256(f'{artifact_sha256}|{path.name}|{line_number}|{canonical}'.encode()).hexdigest()[:20]}"
        observations.append(
            Observation(
                identifier,
                artifact_sha256,
                "dynamic.desktop_frida_trace",
                __version__,
                "dynamic.native_hid_call",
                norm,
                f"native_trace/{path.name}:line={line_number}",
                ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE,
            )
        )
    return observations
