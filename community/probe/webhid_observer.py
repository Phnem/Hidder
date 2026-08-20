"""WebHID browser observation backend using Chrome DevTools Protocol (CDP).

Launches a controlled instance of Microsoft Edge or Google Chrome with an isolated
temporary profile, installs WebHID method and event wrappers before page scripts execute,
and passively observes real WebHID calls (both outbound reports and inbound input reports)
without altering arguments, emulating devices, or capturing user keystrokes.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


class SimpleWebSocketClient:
    """Pure-Python standard-library WebSocket client for CDP."""

    def __init__(self, ws_url: str):
        clean = ws_url.replace("ws://", "")
        host_port, self.path = clean.split("/", 1)
        self.path = "/" + self.path
        if ":" in host_port:
            self.host, port_str = host_port.split(":")
            self.port = int(port_str)
        else:
            self.host = host_port
            self.port = 80
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self._handshake()

    def _handshake(self):
        sec_key = b"dGhlIHNhbXBsZSBub25jZQ=="
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key.decode()}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        res = self.sock.recv(4096).decode("utf-8", errors="replace")
        if "101" not in res:
            raise RuntimeError(f"WebSocket handshake failed: {res}")

    def send_json(self, msg_dict: dict):
        payload = json.dumps(msg_dict).encode("utf-8")
        length = len(payload)
        mask_key = os.urandom(4)
        if length <= 125:
            header = bytes([0x81, 0x80 | length]) + mask_key
        elif length <= 65535:
            header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", length) + mask_key
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", length) + mask_key
            
        masked = bytearray(length)
        for i in range(length):
            masked[i] = payload[i] ^ mask_key[i % 4]
        self.sock.sendall(header + masked)

    def recv_json(self, timeout: float = 0.5) -> dict | None:
        self.sock.settimeout(timeout)
        try:
            head = self.sock.recv(2)
            if not head or len(head) < 2:
                return None
            b1, b2 = head[0], head[1]
            opcode = b1 & 0x0F
            if opcode == 0x08:  # Close frame
                return None
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.sock.recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.sock.recv(8))[0]
            data = bytearray()
            while len(data) < length:
                chunk = self.sock.recv(min(length - len(data), 65536))
                if not chunk:
                    break
                data.extend(chunk)
            return json.loads(data.decode("utf-8", errors="replace"))
        except (socket.timeout, TimeoutError):
            return None
        except Exception:
            return None

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


WEBHID_INJECTION_SCRIPT = r"""
(() => {
  if (window.__peripheral_webhid_injected__) return;
  window.__peripheral_webhid_injected__ = true;

  function toHex(buf) {
    if (!buf) return "";
    let u8;
    if (buf instanceof Uint8Array) {
      u8 = buf;
    } else if (buf instanceof ArrayBuffer) {
      u8 = new Uint8Array(buf);
    } else if (buf instanceof DataView) {
      u8 = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    } else if (ArrayBuffer.isView(buf)) {
      u8 = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    } else {
      return "";
    }
    let hex = "";
    for (let i = 0; i < u8.length; i++) {
      hex += u8[i].toString(16).padStart(2, "0");
    }
    return hex;
  }

  function emitEvent(api, direction, reportId, dataBuf, device) {
    const hex = toHex(dataBuf);
    const payload = JSON.stringify({
      api: api,
      direction: direction,
      report_id: reportId,
      length: hex.length / 2,
      bytes_hex: hex,
      vendor_id: device ? device.vendorId : 0,
      product_id: device ? device.productId : 0,
      product_name: device ? (device.productName || "") : "",
      page_origin: window.location.origin || "",
      timestamp: Date.now() / 1000.0
    });
    if (typeof window.__peripheral_webhid_event__ === "function") {
      window.__peripheral_webhid_event__(payload);
    }
  }

  function attachDeviceListeners(device) {
    if (!device || device.__peripheral_observed__) return;
    device.__peripheral_observed__ = true;

    try {
      device.addEventListener("inputreport", (event) => {
        try {
          emitEvent("inputreport", "in", event.reportId, event.data, device);
        } catch (e) {}
      });
    } catch (e) {}
  }

  if (typeof window.HIDDevice !== "undefined" && window.HIDDevice.prototype) {
    // 1. sendReport (outbound)
    const origSendReport = window.HIDDevice.prototype.sendReport;
    window.HIDDevice.prototype.sendReport = function(reportId, data) {
      try {
        attachDeviceListeners(this);
        emitEvent("sendReport", "out", reportId, data, this);
      } catch (e) {}
      return origSendReport.apply(this, arguments);
    };

    // 2. sendFeatureReport (outbound feature)
    const origSendFeature = window.HIDDevice.prototype.sendFeatureReport;
    window.HIDDevice.prototype.sendFeatureReport = function(reportId, data) {
      try {
        attachDeviceListeners(this);
        emitEvent("sendFeatureReport", "feature_out", reportId, data, this);
      } catch (e) {}
      return origSendFeature.apply(this, arguments);
    };

    // 3. receiveFeatureReport (inbound feature)
    const origReceiveFeature = window.HIDDevice.prototype.receiveFeatureReport;
    window.HIDDevice.prototype.receiveFeatureReport = function(reportId) {
      attachDeviceListeners(this);
      const promise = origReceiveFeature.apply(this, arguments);
      if (promise && typeof promise.then === "function") {
        promise.then((dataView) => {
          try {
            emitEvent("receiveFeatureReport", "feature_in", reportId, dataView, this);
          } catch (e) {}
        }).catch(() => {});
      }
      return promise;
    };

    // 4. open (attach inputreport on open)
    const origOpen = window.HIDDevice.prototype.open;
    window.HIDDevice.prototype.open = function() {
      attachDeviceListeners(this);
      const promise = origOpen.apply(this, arguments);
      if (promise && typeof promise.then === "function") {
        promise.then(() => {
          attachDeviceListeners(this);
        }).catch(() => {});
      }
      return promise;
    };
  }

  // 5. Hook navigator.hid if available
  if (typeof navigator !== "undefined" && navigator.hid) {
    const origRequestDevice = navigator.hid.requestDevice;
    if (typeof origRequestDevice === "function") {
      navigator.hid.requestDevice = function() {
        const promise = origRequestDevice.apply(this, arguments);
        if (promise && typeof promise.then === "function") {
          promise.then((devices) => {
            if (Array.isArray(devices)) {
              devices.forEach(attachDeviceListeners);
            }
          }).catch(() => {});
        }
        return promise;
      };
    }

    const origGetDevices = navigator.hid.getDevices;
    if (typeof origGetDevices === "function") {
      navigator.hid.getDevices = function() {
        const promise = origGetDevices.apply(this, arguments);
        if (promise && typeof promise.then === "function") {
          promise.then((devices) => {
            if (Array.isArray(devices)) {
              devices.forEach(attachDeviceListeners);
            }
          }).catch(() => {});
        }
        return promise;
      };
    }
  }
})();
"""


class WebHidObserver:
    """Manages browser launch, CDP connection, and WebHID event streaming."""

    def __init__(self, on_event_callback: Callable[[dict[str, Any]], None]) -> None:
        self.on_event_callback = on_event_callback
        self.browser_proc: subprocess.Popen | None = None
        self.temp_profile_dir: Path | None = None
        self.browser_name: str = ""
        self.page_ws: SimpleWebSocketClient | None = None
        self.browser_ws: SimpleWebSocketClient | None = None
        self.is_running: bool = False
        self.listener_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.page_origin: str = ""

    @staticmethod
    def find_browser() -> tuple[str, Path] | None:
        candidates = [
            ("msedge", Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")),
            ("msedge", Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")),
            ("chrome", Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")),
            ("chrome", Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")),
            ("msedge", Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe"),
            ("chrome", Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"),
        ]
        for name, path in candidates:
            if path.is_file():
                return name, path
        return None

    def launch_and_attach(self, target_url: str = "about:blank") -> bool:
        browser_info = self.find_browser()
        if not browser_info:
            return False

        self.browser_name, browser_exe = browser_info
        self.temp_profile_dir = Path(tempfile.mkdtemp(prefix="PeripheralResearch_Browser_"))
        
        with socket.socket() as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

        cmd = [
            str(browser_exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.temp_profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-popup-blocking",
            target_url
        ]

        self.browser_proc = subprocess.Popen(cmd)
        time.sleep(1.5)

        try:
            v_req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5)
            v_data = json.loads(v_req.read().decode())
            browser_ws_url = v_data.get("webSocketDebuggerUrl")
            if not browser_ws_url:
                return False

            self.browser_ws = SimpleWebSocketClient(browser_ws_url)
            
            req = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5)
            targets = json.loads(req.read().decode())
            page_target = next((t for t in targets if t.get("type") == "page"), None)
            
            if not (page_target and page_target.get("webSocketDebuggerUrl")):
                return False

            self.page_ws = SimpleWebSocketClient(page_target["webSocketDebuggerUrl"])
            
            self.page_ws.send_json({"id": 1, "method": "Runtime.enable"})
            self.page_ws.send_json({"id": 2, "method": "Page.enable"})
            self.page_ws.send_json({
                "id": 3,
                "method": "Runtime.addBinding",
                "params": {"name": "__peripheral_webhid_event__"}
            })
            self.page_ws.send_json({
                "id": 4,
                "method": "Page.addScriptToEvaluateOnNewDocument",
                "params": {"source": WEBHID_INJECTION_SCRIPT}
            })

            self.is_running = True
            self.stop_event.clear()
            self.listener_thread = threading.Thread(target=self._event_loop, daemon=True)
            self.listener_thread.start()
            return True
        except Exception:
            self.close()
            return False

    def _event_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self.page_ws:
                break
            msg = self.page_ws.recv_json(timeout=0.2)
            if msg:
                method = msg.get("method")
                if method == "Runtime.bindingCalled":
                    params = msg.get("params", {})
                    if params.get("name") == "__peripheral_webhid_event__":
                        try:
                            payload_str = params.get("payload", "{}")
                            ev = json.loads(payload_str)
                            if ev.get("page_origin"):
                                self.page_origin = ev.get("page_origin")
                            self.on_event_callback(ev)
                        except Exception:
                            pass
            else:
                time.sleep(0.01)

    def close(self) -> None:
        self.stop_event.set()
        self.is_running = False
        
        if self.page_ws:
            self.page_ws.close()
            self.page_ws = None
        if self.browser_ws:
            self.browser_ws.close()
            self.browser_ws = None
            
        if self.browser_proc:
            try:
                self.browser_proc.terminate()
                self.browser_proc.wait(timeout=2)
            except Exception:
                try:
                    self.browser_proc.kill()
                except Exception:
                    pass
            self.browser_proc = None

        if self.temp_profile_dir and self.temp_profile_dir.is_dir():
            try:
                shutil.rmtree(self.temp_profile_dir, ignore_errors=True)
            except Exception:
                pass
            self.temp_profile_dir = None
