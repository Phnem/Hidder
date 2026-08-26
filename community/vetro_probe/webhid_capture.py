"""Passive WebHID outgoing-write capture for the official AULA vendor app.

Reuses the legacy passive WebHID CDP observer (community.probe.webhid_observer,
imported unchanged). Probe/harness NEVER writes — only records outbound/inbound
WebHID frames while the user drives the official AULA software.

Output: JSONL trace
  {timestamp, direction, source:"vendor_app", transport:"webhid", vid, pid,
   usage_page, usage, report_id, method, length, hex, annotation}
plus USER_ACTION markers:
  {type:"USER_ACTION", action, from, to, timestamp}
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import time
from pathlib import Path
from typing import Any

from community.probe.webhid_observer import WebHidObserver  # noqa: F401 (legacy, imported unchanged)


def _norm_frame(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": ev.get("timestamp", time.time()),
        "direction": "IN" if ev.get("direction") in ("in", "feature_in") else "OUT",
        "source": "vendor_app",
        "transport": "webhid",
        "vid": f"0x{ev.get('vendor_id', 0):04X}",
        "pid": f"0x{ev.get('product_id', 0):04X}",
        "usage_page": ev.get("usage_page"),
        "usage": ev.get("usage"),
        "report_id": ev.get("report_id"),
        "method": ev.get("api", ""),
        "length": ev.get("length", 0),
        "hex": ev.get("bytes_hex", ""),
        "annotation": None,
    }


class WebHidCapture:
    def __init__(self, trace_path: Path, target_url: str = "https://hero.aulastar.com") -> None:
        self.trace_path = Path(trace_path)
        self.q: queue.Queue[dict[str, Any]] = queue.Queue()
        self.observer = WebHidObserver(on_event_callback=self.q.put)
        self.browser_launched = self.observer.launch_and_attach(target_url)
        self.trace: list[dict[str, Any]] = []

    def _collect(self, seconds: float, annotation: str | None = None) -> list[dict[str, Any]]:
        got: list[dict[str, Any]] = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                ev = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            norm = _norm_frame(ev)
            if annotation:
                norm["annotation"] = annotation
            self.trace.append(norm)
            got.append(norm)
        return got

    def write_marker(self, action: str, frm: str = "", to: str = "") -> None:
        rec = {"type": "USER_ACTION", "action": action, "from": frm, "to": to, "timestamp": time.time()}
        self.trace.append(rec)
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _append(self, frames: list[dict[str, Any]]) -> None:
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            for f in frames:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    def idle_baseline(self, seconds: int = 40) -> int:
        print(f"[idle] collecting {seconds}s of background traffic — DO NOT touch settings...")
        frames = self._collect(float(seconds), annotation="idle")
        self._append(frames)
        print(f"[idle] captured {len(frames)} background frames")
        return len(frames)

    def action(self, action: str, frm: str = "", to: str = "", pause: float = 3.0) -> list[dict[str, Any]]:
        print(f"\n>>> [{action}] {frm} -> {to}")
        input("Change ONLY this in the official AULA app, then press Enter: ")
        self.write_marker(action, frm, to)
        frames = self._collect(pause, annotation=action)
        self._append(frames)
        print(f"[capture] {len(frames)} frames for {action}")
        return frames

    def close(self) -> None:
        self.observer.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vetro.webhid_capture", description="Passive WebHID write capture for the official AULA app")
    parser.add_argument("--trace", type=Path, default=Path("lighting_trace.jsonl"), help="JSONL output")
    parser.add_argument("--url", type=str, default="https://hero.aulastar.com")
    parser.add_argument("--idle", type=int, default=40, help="Idle baseline seconds")
    args = parser.parse_args(argv)

    cap = WebHidCapture(args.trace, target_url=args.url)
    if not cap.browser_launched:
        print("FAIL: could not launch browser (Edge/Chrome not found)", file=sys.stderr)
        return 1
    print("Browser launched. In the opened window: connect the HERO84 and open the Lighting tab.")
    input("Press Enter once the device is connected and the app is on the Lighting tab: ")

    cap.idle_baseline(args.idle)

    sweep = [
        ("enable_on_off", "Lighting", "ON", "OFF"),
        ("enable_off_on", "Lighting", "OFF", "ON"),
        ("brightness_low", "Brightness", "current", "LOW"),
        ("brightness_med", "Brightness", "LOW", "MEDIUM"),
        ("brightness_high", "Brightness", "MEDIUM", "HIGH"),
        ("brightness_med_back", "Brightness", "HIGH", "MEDIUM"),
        ("color_red", "Static color", "current", "RED"),
        ("color_green", "Static color", "RED", "GREEN"),
        ("color_blue", "Static color", "GREEN", "BLUE"),
        ("color_white", "Static color", "BLUE", "WHITE"),
        ("color_red_back", "Static color", "WHITE", "RED"),
        ("effect_static", "Effect", "current", "STATIC"),
        ("effect_breathing", "Effect", "STATIC", "BREATHING"),
        ("effect_animated", "Effect", "BREATHING", "ANIMATED"),
        ("effect_static_back", "Effect", "ANIMATED", "STATIC"),
        ("speed_low", "Speed", "current", "LOW"),
        ("speed_med", "Speed", "LOW", "MEDIUM"),
        ("speed_high", "Speed", "MEDIUM", "HIGH"),
        ("speed_med_back", "Speed", "HIGH", "MEDIUM"),
    ]
    for action, ui, frm, to in sweep:
        cap.action(action, frm, to, pause=3.0)
    cap.close()
    print(f"\ntrace: {args.trace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
