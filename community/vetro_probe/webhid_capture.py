"""Passive WebHID outgoing-write capture v2 — main-world, cross-frame, health-gated.

Problems fixed vs v1 (legacy observer produced 0 frames):
- multi-target: attaches to EVERY page target (top page + OOPIF/cross-origin iframes), not just the first target
- main-world injection via Page.addScriptToEvaluateOnNewDocument + Runtime.addBinding on each page target
- cross-frame relay: the hook script runs in every frame and forwards events to window.top.postMessage;
  the top frame relays them to the CDP binding, so iframe-owned HID calls are observed
- already-granted devices are instrumented (getDevices() re-hooks HIDDevice objects)
- hard CAPTURE_HEALTH gate: sweep is refused until the harness observes real WebHID activity
  (requestDevice/getDevices/open/sendReport/sendFeatureReport/receiveFeatureReport/inputreport)
- self-test panel with live counts

Probe never writes — it only observes the official vendor app.
"""

from __future__ import annotations

import argparse
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

from community.probe.webhid_observer import SimpleWebSocketClient  # noqa: F401 (reused, unchanged)

CAPTURE_HEALTH_SCRIPT = r"""
(() => {
  // ---- cross-frame HID hook (runs in EVERY frame main world) ----
  const FRAME_ID = window.frameElement ? (window.frameElement.getAttribute('id') || 'subframe') : 'top';
  function toHex(buf) {
    if (!buf) return "";
    let u8 = (buf instanceof Uint8Array) ? buf : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    let s = "";
    for (let i = 0; i < u8.length; i++) s += u8[i].toString(16).padStart(2, "0");
    return s;
  }
  let __vetroSeq = 0;
  function emit(api, direction, reportId, data, device) {
    __vetroSeq += 1;
    const payload = JSON.stringify({
      api, direction, report_id: reportId, length: data ? data.byteLength || data.length : 0,
      bytes_hex: toHex(data),
      vendor_id: device ? device.vendorId : 0, product_id: device ? device.productId : 0,
      product_name: device ? (device.productName || "") : "",
      origin: window.location.origin, frame: FRAME_ID, seq: __vetroSeq,
      timestamp: Date.now() / 1000.0
    });
    // ONE explicit route per frame (no double capture):
    // top frame -> direct CDP binding only; child frame -> postMessage relay to top only.
    if (window === window.top) {
      try { window.__peripheral_webhid_event__(payload); } catch (e) {}
    } else {
      try { window.top.postMessage({__vetroHidCapture: true, payload}, "*"); } catch (e) {}
    }
  }
  function attach(device) {
    if (!device || device.__vetroObserved) return;
    device.__vetroObserved = true;
    try { device.addEventListener("inputreport", (ev) => { try { emit("inputreport", "in", ev.reportId, ev.data, device); } catch(e){} }); } catch(e){}
  }
  if (typeof window.HIDDevice !== "undefined" && window.HIDDevice.prototype) {
    for (const [name, dir_] of [["sendReport","out"], ["sendFeatureReport","feature_out"]]) {
      const orig = window.HIDDevice.prototype[name];
      if (!orig) continue;
      window.HIDDevice.prototype[name] = function(reportId, data) {
        try { attach(this); emit(name, dir_, reportId, data, this); } catch(e){}
        return orig.apply(this, arguments);
      };
      window.HIDDevice.prototype[name].__vetroHooked = true;
    }
    const oRecv = window.HIDDevice.prototype.receiveFeatureReport;
    if (oRecv) window.HIDDevice.prototype.receiveFeatureReport = function(reportId) {
      attach(this);
      const p = oRecv.apply(this, arguments);
      if (p && typeof p.then === "function") p.then((dv) => { try { emit("receiveFeatureReport", "feature_in", reportId, dv, this); } catch(e){} }).catch(()=>{});
      return p;
    };
    for (const m of ["open", "close"]) {
      const orig = window.HIDDevice.prototype[m];
      if (!orig) continue;
      window.HIDDevice.prototype[m] = function() {
        try { attach(this); emit(m, "out", -1, null, this); } catch(e){}
        const p = orig.apply(this, arguments);
        if (p && typeof p.then === "function") p.then(() => { attach(this); }).catch(()=>{});
        return p;
      };
    }
  }
  if (typeof navigator !== "undefined" && navigator.hid) {
    for (const m of ["requestDevice", "getDevices"]) {
      const orig = navigator.hid[m];
      if (!orig) continue;
      navigator.hid[m] = function() {
        try { emit("hid." + m, "out", -1, null, null); } catch(e){}
        const p = orig.apply(this, arguments);
        if (p && typeof p.then === "function") p.then((devs) => { if (Array.isArray(devs)) devs.forEach(attach); }).catch(()=>{});
        return p;
      };
    }
  }
  // ---- top-frame relay: forward postMessage from subframes to the CDP binding ----
  if (window === window.top) {
    window.addEventListener("message", (ev) => {
      const d = ev.data;
      if (d && d.__vetroHidCapture) {
        try { if (typeof window.__peripheral_webhid_event__ === "function") window.__peripheral_webhid_event__(d.payload); } catch(e){}
      }
    });
  }
  window.__vetroInjected__ = true;
})();
"""


class PageTarget:
    def __init__(self, target: dict[str, Any]) -> None:
        self.id = target.get("id")
        self.url = target.get("url", "")
        self.ws_url = target.get("webSocketDebuggerUrl")
        self.ws = SimpleWebSocketClient(self.ws_url)
        self.bind_name = "__peripheral_webhid_event__"
        self._cmd_id = 100
        self.health: dict[str, Any] = {}
        self._last_seq = 0
        self.target_type = target.get("type", "page")
        self._init()

    def _send(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._cmd_id += 1
        self.ws.send_json({"id": self._cmd_id, "method": method, "params": params or {}})

    def _init(self) -> None:
        self._send("Runtime.enable")
        self._send("Page.enable")
        self._send("Runtime.addBinding", {"name": self.bind_name})
        self._send("Page.addScriptToEvaluateOnNewDocument", {"source": CAPTURE_HEALTH_SCRIPT})
        # addScriptToEvaluateOnNewDocument only applies to SUBSEQUENT document loads.
        # Force one clean controlled reload so the vendor app obtains the instrumented
        # methods from startup (spec: reload after early injection, no permission revoke).
        if self.url and "about:blank" not in self.url:
            self._send("Page.reload", {"ignoreCache": True})

    def _alive(self) -> bool:
        try:
            self.ws.recv_json(timeout=0.0)
        except Exception:
            return False
        return True

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        try:
            self._cmd_id += 1
            req_id = self._cmd_id
            self.ws.send_json({"id": req_id, "method": "Runtime.evaluate",
                               "params": {"expression": expression, "returnByValue": True, "awaitPromise": await_promise}})
            deadline = time.time() + 4
            while time.time() < deadline:
                msg = self.ws.recv_json(timeout=0.5)
                if msg is None:
                    continue
                if msg.get("id") == req_id:
                    return msg.get("result", {}).get("result", {}).get("value")
                if msg.get("method") == "Runtime.bindingCalled":
                    self._binding(msg.get("params"))
        except Exception:
            pass
        return None

    def read_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            try:
                msg = self.ws.recv_json(timeout=0.05)
            except Exception:
                break
            if not msg:
                break
            if msg.get("method") == "Runtime.bindingCalled":
                ev = self._binding(msg.get("params"))
                if ev is not None:
                    out.append(ev)
        return out

    def _binding(self, params: dict[str, Any] | None) -> dict[str, Any] | None:
        """Parse ONE Runtime.bindingCalled into ONE canonical event.

        Returns the canonical event (also stored in self.health['events']), or
        None for a duplicate seq or malformed payload. Exactly one physical
        WebHID call -> exactly one canonical Python event.
        """
        if not params:
            return None
        if params.get("name") != self.bind_name:
            return None
        try:
            raw = json.loads(params.get("payload", "{}"))
        except Exception:
            return None
        seq = int(raw.get("seq", 0) or 0)
        if seq and seq <= self._last_seq:
            return None  # duplicate (relay + direct) — drop
        self._last_seq = max(self._last_seq, seq)
        # canonical schema; accepts legacy aliases (api->method, bytes_hex->hex)
        ev: dict[str, Any] = {
            "timestamp": raw.get("timestamp", time.time()),
            "method": raw.get("method") or raw.get("api", ""),
            "direction": "OUT" if raw.get("direction") in ("out", "feature_out") else "IN",
            "report_id": raw.get("report_id"),
            "length": raw.get("length", 0),
            "hex": raw.get("hex") or raw.get("bytes_hex", ""),
            "vendor_id": raw.get("vendor_id", 0),
            "product_id": raw.get("product_id", 0),
            "product_name": raw.get("product_name", ""),
            "origin": raw.get("origin") or params.get("executionContextOrigin") or "",
            "frame": raw.get("frame", ""),
            "target_id": self.id,
            "target_url": self.url,
            "target_type": self.target_type,
        }
        self.health.setdefault("events", []).append(ev)
        return ev


class WebHidCapture:
    """Multi-target passive WebHID capture with a hard health gate."""

    def __init__(self, trace_path: Path, target_url: str = "https://hero.aulastar.com", health_mode: bool = False) -> None:
        self.trace_path = Path(trace_path)
        self.target_url = target_url
        self.health_mode = health_mode
        self.targets: dict[str, PageTarget] = {}
        self.browser_proc: subprocess.Popen | None = None
        self.temp_profile_dir: Path | None = None
        self.port = 0
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.poll_thread: threading.Thread | None = None

    # ------------------------------------------------------------ browser
    def _profile_dir(self) -> Path:
        """Dedicated PERSISTENT capture profile (WebHID grants persist across runs).
        Lives under the repo but is gitignored; contains only Vetro capture state."""
        return Path(__file__).resolve().parent / ".capture_profile"

    def _find_browser(self) -> tuple[str, Path] | None:
        cands = [
            ("chrome", Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")),
            ("chrome", Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")),
            ("chrome", Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe"),
            ("msedge", Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")),
            ("msedge", Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")),
            ("msedge", Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe"),
        ]
        for name, p in cands:
            if p.is_file():
                return name, p
        return None

    def _kill_stale_profile_browsers(self) -> int:
        """Kill any browser process already bound to the dedicated capture profile
        (prevents a handoff to a stale isolated instance). Never touches the personal profile."""
        killed = 0
        try:
            import psutil  # type: ignore

            profile = str(self._profile_dir()).lower()
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    if proc.info["name"] in ("chrome.exe", "msedge.exe", "chrome", "msedge"):
                        cmd = " ".join(proc.info["cmdline"] or []).lower()
                        if profile in cmd:
                            proc.terminate()
                            killed += 1
                except Exception:
                    continue
            if killed:
                time.sleep(1.0)
        except Exception:
            pass
        return killed

    def launch(self) -> bool:
        bi = self._find_browser()
        if not bi:
            return False
        name, exe = bi
        self._kill_stale_profile_browsers()
        self.temp_profile_dir = self._profile_dir()
        self.temp_profile_dir.mkdir(parents=True, exist_ok=True)
        with socket.socket() as s:
            s.bind(("", 0))
            self.port = s.getsockname()[1]
        cmd = [str(exe), f"--remote-debugging-port={self.port}", f"--user-data-dir={self.temp_profile_dir}",
               "--no-first-run", "--no-default-browser-check", "--disable-sync",
               "--disable-extensions", "--disable-component-extensions-with-background-pages",
               "--disable-default-apps", "--disable-popup-blocking",
               "--disable-features=msEdgeFirstRunExperience,msEdgeWelcomePage,msEdgeFre,msEdgeSyncPromo,msEdgeSidebarV2",
               self.target_url]
        self.browser_proc = subprocess.Popen(cmd)
        time.sleep(2.5)
        return True

    def isolation_panel(self) -> dict[str, Any]:
        return {
            "dedicated_user_data_dir": "YES",
            "existing_personal_profile": "NO",
            "third_party_extensions": "NONE",
            "first_run_sync_modal": "NO",
            "startup_url": self.target_url,
            "capture_profile": str(self._profile_dir()),
        }

    def _fetch_targets(self) -> list[dict[str, Any]]:
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5)
            targets = json.loads(req.read().decode())
            # attach to every render target that can own a WebHID execution context:
            # top pages AND OOPIF/cross-origin iframes. Skip service-worker/background-only
            # targets unless they expose a debugger URL and a page-ish origin.
            relevant = [t for t in targets
                        if t.get("type") in ("page", "iframe", "other")
                        and t.get("webSocketDebuggerUrl")]
            return relevant
        except Exception:
            return []

    def _poll_targets(self) -> None:
        while not self.stop.is_set():
            try:
                for t in self._fetch_targets():
                    tid = t.get("id")
                    with self._lock:
                        if tid and tid not in self.targets and t.get("webSocketDebuggerUrl"):
                            try:
                                self.targets[tid] = PageTarget(t)
                            except Exception:
                                pass
                        if tid in self.targets:
                            self.targets[tid].url = t.get("url", self.targets[tid].url)
            except Exception:
                pass
            time.sleep(1.0)

    def start(self) -> None:
        self.poll_thread = threading.Thread(target=self._poll_targets, daemon=True)
        self.poll_thread.start()

    def collect(self, seconds: float, annotation: str | None = None) -> int:
        deadline = time.time() + seconds
        count = 0
        while time.time() < deadline:
            for t in list(self.targets.values()):
                for ev in t.read_events():
                    count += 1
                    if annotation:
                        ev["annotation"] = annotation
                    self._write(ev)
            time.sleep(0.02)
        return count

    def _write(self, ev: dict[str, Any]) -> None:
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def write_marker(self, action: str, frm: str = "", to: str = "", mtype: str = "USER_ACTION") -> None:
        rec = {"type": mtype, "action": action, "from": frm, "to": to, "timestamp": time.time()}
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def action_window(self, label: str, frm: str, to: str, prompt: str, tail_s: float = 1.0) -> list[dict[str, Any]]:
        """Correct action-window semantics: ACTION_BEGIN BEFORE the user acts, so real HID
        frame timestamps can never precede the marker; ACTION_END after Enter; collect tail.

        Returns the frames captured during [BEGIN, END + tail], annotated with the label."""
        self.write_marker(f"{label}:{frm}->{to}", frm, to, mtype="ACTION_BEGIN")
        input(prompt)
        self.write_marker(f"{label}:{frm}->{to}", frm, to, mtype="ACTION_END")
        return self.collect_frames(tail_s, annotation=label)

    def collect_frames(self, seconds: float, annotation: str | None = None) -> list[dict[str, Any]]:
        deadline = time.time() + seconds
        frames: list[dict[str, Any]] = []
        while time.time() < deadline:
            for t in list(self.targets.values()):
                for ev in t.read_events():
                    if annotation:
                        ev["annotation"] = annotation
                    self._write(ev)
                    frames.append(ev)
            time.sleep(0.02)
        return frames

    # ------------------------------------------------------------ health
    def health_status(self) -> dict[str, Any]:
        per_target: list[dict[str, Any]] = []
        for t in self.targets.values():
            hid = t.evaluate("typeof navigator !== 'undefined' && !!navigator.hid")
            granted = t.evaluate("navigator.hid ? navigator.hid.getDevices().then(d => d.length) : Promise.resolve(-1)", await_promise=True)
            events = t.health.get("events", [])
            per_target.append({
                "url": t.url, "target_id": t.id, "target_type": getattr(t, "target_type", "page"),
                "navigator_hid": bool(hid),
                "granted_devices": granted,
                "observed_calls": len(events),
                "out_frames": sum(1 for e in events if e.get("direction") == "OUT"),
                "events": list(events),
            })
        return compute_health(per_target)

    def print_panel(self, h: dict[str, Any]) -> None:
        print("\nWebHID capture:")
        for k, v in h.items():
            if k != "targets":
                print(f"  {k}: {v}")
        for p in h.get("targets", []):
            print(f"  target {p['target_id'][:8]} url={p['url'][:60]} hid={p['navigator_hid']} granted={p['granted_devices']} calls={p['observed_calls']} out={p['out_frames']}")
        if h["capture_health"] == "PASS":
            print("\nReady for lighting sweep.")
        else:
            print("\nCAPTURE_HEALTH = FAIL — sweep blocked. Connect the device and grant WebHID permission in the browser.")

    def close(self) -> None:
        self.stop.set()
        for t in self.targets.values():
            try:
                t.ws.close()
            except Exception:
                pass
        if self.browser_proc:
            try:
                self.browser_proc.terminate()
                self.browser_proc.wait(timeout=2)
            except Exception:
                try:
                    self.browser_proc.kill()
                except Exception:
                    pass
        if self.temp_profile_dir and self.temp_profile_dir.is_dir():
            shutil.rmtree(self.temp_profile_dir, ignore_errors=True)


def compute_health(per_target: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure health computation (unit-testable, no browser)."""
    total_events = sum(p.get("observed_calls", 0) for p in per_target)
    any_hid = any(p.get("navigator_hid") for p in per_target)
    any_granted = any((p.get("granted_devices") or 0) > 0 for p in per_target)
    detected = any(
        e.get("product_name") or (e.get("vendor_id") == 14126 and e.get("product_id") == 4158)
        for p in per_target for e in p.get("events", [])
    )
    opened = any(e.get("method") == "open" for p in per_target for e in p.get("events", []))
    return {
        "capture_health": "PASS" if (any_hid and (total_events > 0 or any_granted)) else "FAIL",
        "page_attached": bool(per_target),
        "transport_context_found": any_hid,
        "navigator_hid": any_hid,
        "granted_devices": sum(p.get("granted_devices") or 0 for p in per_target),
        "hero84_detected": detected,
        "device_opened": opened,
        "observed_webhid_calls": total_events,
        "observed_out_frames": sum(p.get("out_frames", 0) for p in per_target),
        "targets": [{k: v for k, v in p.items() if k != "events"} for p in per_target],
    }



RT_UI_INSPECT_SCRIPT = r"""(() => {
  // READ-ONLY RT UI contract extraction v2: DOM metadata + ancestor chain +
  // Naive-UI dual-handle detection + safe Vue prop reads. Never dispatches
  // events, never writes values, never clicks, never calls HID, never mutates
  // state. All reads only.
  const out = { controls: [], script_urls: [] };
  const CONFIG_KEYS = ["min","max","step","precision","marks","showStops","range",
                       "disabled","modelValue","value","rtUp","rtDown","rt_up","rt_down","current"];
  const vueProps = (el) => {
    let node = el, depth = 0, collected = {};
    while (node && depth < 6) {
      const inst = node.__vueParentComponent || node.__vue__;
      if (inst) {
        for (const src of [inst.props, inst.attrs, inst.setupState]) {
          if (src) for (const k in src) {
            if (CONFIG_KEYS.includes(k) && !(k in collected)) collected[k] = src[k];
          }
        }
        if (inst.props && "min" in inst.props) collected["_vue_min"] = inst.props["min"];
        if (inst.props && "max" in inst.props) collected["_vue_max"] = inst.props["max"];
        if (inst.props && "step" in inst.props) collected["_vue_step"] = inst.props["step"];
      }
      node = node.parentElement;
      depth++;
    }
    return collected;
  };
  const snap = (el) => {
    const g = (n) => el.getAttribute ? el.getAttribute(n) : null;
    let anc = [], n = el.parentElement, d = 0;
    while (n && d < 6) { anc.push(n.tagName + "." + String(n.className || "").split(" ")[0]); n = n.parentElement; d++; }
    let cont = null, handles = 0;
    try { cont = el.closest ? el.closest('[class*="n-slider"]') : null; } catch (e) {}
    if (cont) { try { handles = cont.querySelectorAll('[role="slider"]').length; } catch (e) {} }
    return {
      tag: el.tagName, role: g("role"), type: g("type"),
      cls: String(el.className || ""),
      min: g("min"), max: g("max"), step: g("step"), value: g("value"),
      aria_min: g("aria-valuemin"), aria_max: g("aria-valuemax"),
      aria_now: g("aria-valuenow"), aria_label: g("aria-label"),
      aria_valuetext: g("aria-valuetext"),
      text: (el.innerText || el.textContent || "").trim().slice(0, 80),
      parent_text: (el.parentElement ? (el.parentElement.innerText || "").trim().slice(0, 120) : ""),
      ancestor_chain: anc,
      slider_container_handles: handles,
      vue_props: vueProps(el),
    };
  };
  const sel = 'input[type="range"], [role="slider"], [aria-valuemin], [aria-valuenow]';
  try { document.querySelectorAll(sel).forEach((el) => out.controls.push(snap(el))); } catch (e) {}
  try {
    performance.getEntriesByType("resource").forEach((e) => {
      if (e.name && /\.js($|\?)/.test(e.name)) out.script_urls.push(e.name);
    });
  } catch (e) {}
  return JSON.stringify(out);
})()"""


def normalize_rt_ui_contract(raw_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify live DOM slider readings into a UI contract. PURE / unit-testable.

    A control is RT-linked ONLY when its own label/text/aria carries an RT marker
    (rapid/trigger/rt/触发/distance). Unrelated actuation sliders are never reused
    as RT evidence. min/max/step must come from the SAME control. Anything absent
    stays UNKNOWN. The safe grid is PROVEN only when >=1 RT-linked control exposes
    explicit min/max/step; otherwise it stays OPEN (protocol quantum != UI step).
    """
    RT_MARKERS = ("rapid", "trigger", "0.02mm", "0.02 mm", "rt", "触发")
    controls: list[dict[str, Any]] = []
    script_urls: list[str] = []
    for r in raw_results or []:
        controls.extend(r.get("controls") or [])
        script_urls.extend(r.get("script_urls") or [])

    def prov(c: dict[str, Any], key: str, vue_key: str) -> str:
        vp = (c.get("vue_props") or {})
        if vp.get(vue_key) is not None:
            return "VUE_PROP"
        if c.get(key) is not None:
            return "DOM" if c.get("tag") == "INPUT" else "ARIA"
        return "UNKNOWN"

    labeled: list[dict[str, Any]] = []
    for c in controls:
        blob = " ".join(str(c.get(k) or "") for k in
                        ("aria_label", "aria_valuetext", "text", "parent_text")).lower()
        c["rt_linkage"] = "PROVEN" if any(m in blob for m in RT_MARKERS) else "UNKNOWN"
        vp = c.get("vue_props") or {}
        c["_value"] = {"min": c.get("min") or c.get("aria_min") or vp.get("min"),
                       "max": c.get("max") or c.get("aria_max") or vp.get("max"),
                       "step": c.get("step") or vp.get("step"),
                       "current": c.get("value") or c.get("aria_now") or vp.get("modelValue") or vp.get("value")}
        c["_prov"] = {"min": prov(c, "min", "_vue_min"), "max": prov(c, "max", "_vue_max"),
                      "step": prov(c, "step", "_vue_step"), "current": "DOM/ARIA/VUE_PROP"}
        labeled.append(c)

    def ctl(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "min": c["_value"]["min"], "max": c["_value"]["max"], "step": c["_value"]["step"],
            "current": c["_value"]["current"], "rt_linkage": c["rt_linkage"],
            "source": dict(c["_prov"]),
        }

    rt_controls = [c for c in labeled if c["rt_linkage"] == "PROVEN"]
    total_handles = sum(int(c.get("slider_container_handles") or 1) for c in labeled)
    contract: dict[str, Any] = {
        "source": "live_vendor_ui",
        "control_count": len(labeled),
        "semantic_handles": total_handles,
        "dual_handle_component": (total_handles >= 2) or any(c.get("slider_container_handles", 0) >= 2 for c in labeled),
        "control_type": ("native_range" if any(c.get("type") == "range" for c in labeled)
                         else ("custom_slider" if any(c.get("role") == "slider" for c in labeled)
                               else "UNKNOWN")),
        "script_urls": sorted(set(script_urls)),
        "up": None, "down": None,
        "up_down_same_contract": "UNKNOWN",
        "display_precision": "UNKNOWN",
        "snap_rule": "UNKNOWN",
        "rt_enable_current": "UNKNOWN",
        "all_controls": labeled,
    }
    if len(rt_controls) >= 2:
        contract["up"] = ctl(rt_controls[0])
        contract["down"] = ctl(rt_controls[1])
        su, sd = contract["up"]["step"], contract["down"]["step"]
        contract["up_down_same_contract"] = (su == sd) if (su is not None and sd is not None) else "UNKNOWN"
    elif len(rt_controls) == 1:
        contract["up"] = ctl(rt_controls[0])
    complete = [c for c in rt_controls if c["_value"]["min"] is not None
                and c["_value"]["max"] is not None and c["_value"]["step"] is not None]
    contract["safe_temp_grid"] = "PROVEN" if complete else "OPEN"
    return contract


def run_rt_ui_inspect(trace_path: Path, url: str, idle_seconds: int) -> int:
    """READ-ONLY RT UI contract extraction from the live AULA page (ZERO HID writes).

    Reuses the canonical lifecycle (launch -> start -> health gate -> evaluate ->
    close). Only Runtime.evaluate reads the DOM/component metadata; it NEVER
    dispatches input/change, writes element.value, clicks, drags, or calls HID.
    Persists rt_ui_contract_capture.json next to the trace. he.rt stays BLOCKED —
    this pass only discovers the UI contract."""
    cap = WebHidCapture(trace_path, target_url=url)
    closed = False

    def _cleanup() -> None:
        nonlocal closed
        if not closed:
            cap.close()
            closed = True

    try:
        if not cap.launch():
            print("FAIL: browser not found", file=sys.stderr)
            return 1
        cap.start()
        print("BROWSER ISOLATION:")
        for k, v in cap.isolation_panel().items():
            print(f"  {k} = {v}")
        print("RT UI CONTRACT INSPECTION — READ-ONLY (ZERO HID writes):")
        print("  1. In the opened AULA app, click Connect and pick the HERO 84 HE device.")
        input("Press Enter AFTER the app recognizes HERO84: ")
        time.sleep(1.0)
        h = cap.health_status()
        cap.print_panel(h)
        if h["capture_health"] != "PASS":
            print("\nCAPTURE_HEALTH = FAIL — inspection aborted. "
                  "Connect the device and grant WebHID permission in the browser.")
            _cleanup()
            return 2
        print("  2. Now navigate to the HE / Rapid Trigger (PERFORMANCE) page.")
        print("     Do NOT change ANY value. Do NOT drag or click RT controls.")
        input("Press Enter AFTER the RT page has hydrated: ")
        time.sleep(1.0)
        raw_results: list[dict[str, Any]] = []
        for t in list(cap.targets.values()):
            raw = t.evaluate(RT_UI_INSPECT_SCRIPT)
            if raw:
                try:
                    parsed = json.loads(raw)
                    parsed["target_url"] = t.url
                    raw_results.append(parsed)
                except Exception:
                    pass
        contract = normalize_rt_ui_contract(raw_results)
        artifact = trace_path.parent / "rt_ui_contract_capture.json"
        artifact.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nRT UI INSPECTION SUMMARY (read-only):")
        print(f"  control/component count = {contract['control_count']}")
        print(f"  semantic handle count = {contract['semantic_handles']}")
        print(f"  dual-handle component = {contract['dual_handle_component']}")
        print(f"  control_type = {contract['control_type']}")
        for i, c in enumerate(contract["all_controls"]):
            print(f"  control#{i}: tag={c.get('tag')} cls={c.get('cls')} role={c.get('role')} "
                  f"aria=[{c.get('aria_min')},{c.get('aria_max')}] now={c.get('aria_now')} "
                  f"text={c.get('text')!r} rt_linkage={c.get('rt_linkage')} "
                  f"handles_in_component={c.get('slider_container_handles')} "
                  f"vue_props={c.get('vue_props')}")
        for side in ("up", "down"):
            c = contract.get(side)
            if c:
                print(f"  {side}: min={c['min']} ({c['source']['min']}) max={c['max']} ({c['source']['max']}) "
                      f"step={c['step']} ({c['source']['step']}) current={c['current']}")
        print(f"  up/down same contract = {contract['up_down_same_contract']}")
        print(f"  safe_temp_grid = {contract['safe_temp_grid']}")
        print(f"  loaded script resources = {len(contract['script_urls'])}")
        print(f"  evidence artifact = {artifact}")
        print("  NOTE: he.rt stays BLOCKED until the real Probe SET->GET round-trip is performed.")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: RT UI inspection error: {exc}", file=sys.stderr)
        _cleanup()
        return 1
    _cleanup()
    return 0


def run_passive_rt_get_capture(trace_path: Path, url: str, idle_seconds: int) -> int:
    """Passive-only capture for RT GET discovery (ZERO Probe HID writes).

    Uses the SAME canonical lifecycle as every working capture mode:
    launch() -> start() -> health gate -> collect_frames() -> close().
    The harness only hooks navigator.hid and observes traffic; the vendor app is
    the only writer. The user opens the official AULA app, connects, and navigates
    to the HE / Rapid Trigger page WITHOUT changing any RT value, so the vendor
    software itself issues the current-state GET (0x99) whose reply we need to
    implement parse_rt_get_reply. If no real 0x99 request/reply is observed, this
    reports it honestly — it never fabricates a parser or reply layout.
    """
    cap = WebHidCapture(trace_path, target_url=url)
    closed = False

    def _cleanup() -> None:
        nonlocal closed
        if not closed:
            cap.close()
            closed = True

    try:
        if not cap.launch():
            print("FAIL: browser not found", file=sys.stderr)
            return 1
        cap.start()
        print("BROWSER ISOLATION:")
        for k, v in cap.isolation_panel().items():
            print(f"  {k} = {v}")
        print("PASSIVE RT GET DISCOVERY — instructions (do NOT change any RT value):")
        print("  1. In the opened AULA app, click Connect and pick the HERO 84 HE device.")
        input("Press Enter AFTER the app recognizes HERO84 (WebHID permission granted): ")
        time.sleep(1.0)
        h = cap.health_status()
        cap.print_panel(h)
        if h["capture_health"] != "PASS":
            print("\nCAPTURE_HEALTH = FAIL — RT discovery aborted. "
                  "Connect the device and grant WebHID permission in the browser.")
            _cleanup()
            return 2
        print("  2. Now navigate to the HE / Rapid Trigger (PERFORMANCE) page and wait for")
        print("     the RT sliders/toggle to hydrate. The vendor app issues its own GETs.")
        print(f"     Capture runs for ~{idle_seconds}s. Do NOT change any RT value.\n")
        frames = cap.collect_frames(float(idle_seconds), annotation="rt_get_discovery")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: capture bootstrap/interaction error: {exc}", file=sys.stderr)
        _cleanup()
        return 1
    _cleanup()

    out99 = [f for f in frames if f.get("hex", "").startswith("99") and f.get("direction") == "OUT"]
    in99 = [f for f in frames if f.get("hex", "").startswith("99") and f.get("direction") == "IN"]
    print(f"\nRT GET DISCOVERY: frames captured = {len(frames)}, "
          f"OUT 0x99 = {len(out99)}, IN 0x99 = {len(in99)}")
    if in99:
        r = in99[0]
        print(f"  candidate real 0x99 reply: report_id={r.get('report_id')} "
              f"len={r.get('length')} hex={r.get('hex')}")
    else:
        print("  NO real 0x99 request/reply observed in this window.")
        print("  Honest 'not observed' result — no reply parser was fabricated.")
    print(f"trace path = {trace_path}")
    return 0


def run_health_and_sweep(trace_path: Path, url: str, idle_seconds: int) -> int:
    cap = WebHidCapture(trace_path, target_url=url)
    if not cap.launch():
        print("FAIL: browser not found", file=sys.stderr)
        return 1
    cap.start()
    print("BROWSER ISOLATION:")
    for k, v in cap.isolation_panel().items():
        print(f"  {k} = {v}")
    print("Browser launched (dedicated isolated profile). Connect the HERO84 and open the Lighting tab.")
    input("Press Enter AFTER the app recognizes HERO84: ")
    time.sleep(1.0)
    h = cap.health_status()
    cap.print_panel(h)
    if h["capture_health"] != "PASS":
        print("\nCAPTURE_HEALTH = FAIL. Do NOT sweep. Diagnose injection/targets first.")
        cap.close()
        return 2

    print("\n[health PASS] Capturing idle baseline (do not touch settings)...")
    cap.collect(idle_seconds, annotation="idle")

    smoke = [("brightness_change", "Brightness", "current", "different"),
             ("brightness_back", "Brightness", "different", "original")]
    smoke_counts = []
    for action, ui, frm, to in smoke:
        print(f"\n>>> [{action}] set {ui} {frm} -> {to}")
        input("Press Enter after applying in the app: ")
        cap.write_marker(action, frm, to)
        n = cap.collect(3.0, annotation=action)
        smoke_counts.append(n)
        print(f"[capture] {n} frames")
    cap.close()
    print(f"\nsmoke frames: {smoke_counts}")
    return 0 if any(n > 0 for n in smoke_counts) else 3


def run_smoke_brightness(trace_path: Path, url: str, idle_seconds: int) -> int:
    """Minimal capture smoke: prove brightness changes produce observed outbound frames."""
    from .lighting_diff import idle_signatures, filter_idle, byte_diff

    cap = WebHidCapture(trace_path, target_url=url)
    if not cap.launch():
        print("FAIL: browser not found", file=sys.stderr)
        return 1
    cap.start()
    print("BROWSER ISOLATION:")
    for k, v in cap.isolation_panel().items():
        print(f"  {k} = {v}")
    print("Connect the HERO84 and open the Lighting tab in the isolated browser.")
    input("Press Enter AFTER the app recognizes HERO84: ")
    time.sleep(1.0)
    h = cap.health_status()
    cap.print_panel(h)
    if h["capture_health"] != "PASS":
        print("\nCAPTURE_HEALTH = FAIL — smoke aborted. Do not change settings.")
        cap.close()
        return 2
    if not h["hero84_detected"] or not h["device_opened"]:
        print("\nWARN: HERO84 not detected/opened — continuing smoke but may not capture device frames.")
    print(f"\n[smoke] idle baseline {idle_seconds}s — DO NOT touch settings...")
    idle = cap.collect_frames(float(idle_seconds), annotation="idle")
    idle_sig = idle_signatures(idle)
    print(f"[smoke] idle frames captured: {len(idle)} (signatures: {len(idle_sig)})")

    results = []
    for label, frm, to, action in (("brightness_1", "current", "different", "brightness change"),
                                   ("brightness_2", "different", "original", "brightness restore")):
        print(f"\n>>> [{label}] set BRIGHTNESS {frm} -> {to} in the app")
        frames = cap.action_window(label, frm, to, "Press Enter AFTER applying: ", tail_s=5.0)
        out = [f for f in frames if f.get("direction") in ("OUT", "feature_out", "out", "feature_out")]
        novel = filter_idle(out, idle_sig)
        results.append({"label": label, "frames": frames, "out": out, "novel": novel})
        print(f"[smoke] {label}: total={len(frames)} out={len(out)} novel(non-idle)={len(novel)}")
    cap.close()

    a1 = results[0]
    a2 = results[1]
    n1 = a1["novel"]
    n2 = a2["novel"]
    smoke_pass = (len(n1) > 0 or len(n2) > 0)
    print("\n=== BRIGHTNESS SMOKE RESULT ===")
    for r in results:
        n = r["novel"]
        print(f"{r['label']}: correlated OUT frames = {len(n)}")
        if n:
            f = n[0]
            print(f"  report_id={f.get('report_id')} method={f.get('method')} length={f.get('length')} hex={f.get('hex')}")
    print(f"IDLE FRAMES FILTERED = {len(idle)}")
    if n1 and n2:
        diff = byte_diff(n1[0].get("hex", ""), n2[0].get("hex", ""))
        print(f"ACTION1 <-> ACTION2 BYTE DIFF (first novel out frames): {diff}")
    elif n1 and not n2:
        print("ACTION1 <-> ACTION2 BYTE DIFF: only action1 produced novel out frames")
    elif n2 and not n1:
        print("ACTION1 <-> ACTION2 BYTE DIFF: only action2 produced novel out frames")
    else:
        print("ACTION1 <-> ACTION2 BYTE DIFF: none (no novel out frames)")
    print(f"CAPTURE_SMOKE = {'PASS' if smoke_pass else 'FAIL'}")
    print(f"trace path = {trace_path}")
    return 0 if smoke_pass else 3


def run_sweep_full(trace_path: Path, url: str, idle_seconds: int) -> int:
    """Full controlled lighting sweep with EXACT UI value markers.

    Official AULA app is the only writer. All frames persisted before analysis.
    """
    cap = WebHidCapture(trace_path, target_url=url)
    if not cap.launch():
        print("FAIL: browser not found", file=sys.stderr)
        return 1
    cap.start()
    print("BROWSER ISOLATION:")
    for k, v in cap.isolation_panel().items():
        print(f"  {k} = {v}")
    print("Connect the HERO84 and open the Lighting tab in the isolated browser.")
    input("Press Enter AFTER the app recognizes HERO84: ")
    time.sleep(1.0)
    h = cap.health_status()
    cap.print_panel(h)
    if h["capture_health"] != "PASS":
        print("\nCAPTURE_HEALTH = FAIL — sweep aborted.")
        cap.close()
        return 2

    print(f"\n[sweep] idle baseline {idle_seconds}s — DO NOT touch settings...")
    idle = cap.collect_frames(float(idle_seconds), annotation="idle")

    baseline = input("\nWhat is the CURRENT brightness % shown in the app (0-100)? ").strip() or "?"
    steps = [
        ("brightness", "Brightness", baseline, "0"),
        ("brightness", "Brightness", "0", "25"),
        ("brightness", "Brightness", "25", "50"),
        ("brightness", "Brightness", "50", "75"),
        ("brightness", "Brightness", "75", "100"),
        ("brightness", "Brightness", "100", baseline),
        ("enable", "Lighting", "ON", "OFF"),
        ("enable", "Lighting", "OFF", "ON"),
        ("color", "Static single color", "current", "#FF0000 RED"),
        ("color", "Static single color", "RED", "#00FF00 GREEN"),
        ("color", "Static single color", "GREEN", "#0000FF BLUE"),
        ("color", "Static single color", "BLUE", "#FFFFFF WHITE"),
        ("color", "Static single color", "WHITE", "#FF0000 RED"),
        ("effect", "Effect", "current", "STATIC"),
        ("effect", "Effect", "STATIC", "BREATHING"),
        ("effect", "Effect", "BREATHING", "ANIMATED_1"),
        ("effect", "Effect", "ANIMATED_1", "ANIMATED_2"),
        ("effect", "Effect", "ANIMATED_2", "STATIC"),
        ("speed", "Speed (if UI exposes it)", "min", "mid"),
        ("speed", "Speed (if UI exposes it)", "mid", "max"),
        ("speed", "Speed (if UI exposes it)", "max", "mid"),
        ("direction", "Direction (if UI exposes it)", "left", "right"),
        ("direction", "Direction (if UI exposes it)", "right", "left"),
    ]
    for kind, ui, frm, to in steps:
        note = "" if kind != "speed" or ui.split(" ")[0] != "Speed" else ""
        print(f"\n>>> [{kind}] {ui}: {frm} -> {to}")
        extra = input(f"Apply this in the app, then press Enter (or 'skip' if UI has no such control): ")
        if extra.strip().lower() == "skip":
            cap.write_marker(f"{kind}_SKIPPED", frm, to)
            continue
        frames = cap.action_window(kind, frm, to, "", tail_s=3.0)
        print(f"[capture] {len(frames)} frames")
    cap.close()
    print(f"\nFull sweep done. trace = {trace_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetro.webhid_capture", description="Passive WebHID capture v2 (health-gated)")
    parser.add_argument("--trace", type=Path, default=Path("lighting_trace.jsonl"))
    parser.add_argument("--url", type=str, default="https://hero.aulastar.com")
    parser.add_argument("--health", action="store_true", help="Only show the capture health panel, then exit")
    parser.add_argument("--idle", type=int, default=40)
    parser.add_argument("--self-check", action="store_true", help="Launch browser, verify main-world injection + navigator.hid, exit (no interaction)")
    parser.add_argument("--smoke", type=str, default=None, help="Minimal capture smoke: 'brightness' (2 brightness actions only)")
    parser.add_argument("--sweep", action="store_true", help="Full controlled lighting sweep (exact UI value markers)")
    parser.add_argument("--rt-get-capture", action="store_true", help="PASSIVE-only RT GET discovery: open AULA app, connect, navigate to HE/Rapid Trigger page WITHOUT changing values; harness issues ZERO HID writes. Captures the vendor's own 0x99 GET+reply.")
    parser.add_argument("--rt-ui-inspect", action="store_true", help="READ-ONLY RT UI contract extraction from the live AULA page (ZERO HID writes, no input/change dispatch, no element.value writes). Persists rt_ui_contract_capture.json. he.rt stays BLOCKED.")
    args = parser.parse_args(argv)

    if args.rt_ui_inspect:
        return run_rt_ui_inspect(args.trace, args.url, args.idle)

    if args.rt_get_capture:
        return run_passive_rt_get_capture(args.trace, args.url, args.idle)

    if args.self_check:
        return _self_check(args.url)

    if args.smoke:
        if args.smoke != "brightness":
            print(f"unknown smoke target: {args.smoke}", file=sys.stderr)
            return 2
        return run_smoke_brightness(args.trace, args.url, args.idle)

    if args.sweep:
        return run_sweep_full(args.trace, args.url, args.idle)

    cap = WebHidCapture(args.trace, target_url=args.url)
    if not cap.launch():
        print("FAIL: browser not found", file=sys.stderr)
        return 1
    cap.start()
    if args.health:
        print("BROWSER ISOLATION:")
        for k, v in cap.isolation_panel().items():
            print(f"  {k} = {v}")
        print("Browser launched (dedicated isolated profile). Connect HERO84 and grant WebHID permission, then press Enter.")
        input("Press Enter: ")
        time.sleep(1.0)
        h = cap.health_status()
        cap.print_panel(h)
        cap.close()
        return 0 if h["capture_health"] == "PASS" else 2
    return run_health_and_sweep(args.trace, args.url, args.idle)


def _self_check(url: str) -> int:
    """Non-interactive: launch browser, wait for targets, verify injection + navigator.hid in main world."""
    cap = WebHidCapture(Path("self_check_trace.jsonl"), target_url=url)
    if not cap.launch():
        print("FAIL: browser not found", file=sys.stderr)
        return 1
    cap.start()
    print("BROWSER ISOLATION:")
    for k, v in cap.isolation_panel().items():
        print(f"  {k} = {v}")
    time.sleep(8)  # allow targets to attach + controlled reload to settle
    print("TARGETS:")
    for t in cap.targets.values():
        injected = t.evaluate("!!window.__vetroInjected__")
        hid = t.evaluate("typeof navigator !== 'undefined' && !!navigator.hid")
        hooked = t.evaluate("typeof window.HIDDevice !== 'undefined' && !!window.HIDDevice.prototype.sendReport.__vetroHooked")
        print(f"  {t.id[:8]} url={t.url[:70]} injected={injected} navigator.hid={hid} sendReport_hooked={hooked}")
    cap.close()
    print("SELF-CHECK done. injected=YES on a hero.aulastar.com page proves main-world injection in the isolated profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
