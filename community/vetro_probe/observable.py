"""Observable checks — Probe listens to OS input itself.

No human self-report. Allowed actions:
- press highlighted key once
- press highlighted mouse button once
- move mouse
- fully press/release highlighted HE key

0-3 actions per device validation.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObservableRequest:
    kind: str  # "press_key" | "press_button" | "move_mouse" | "he_press"
    target: str  # e.g. "PrtSc", "MouseButton4", "W", "move"
    prompt_ru: str = ""
    prompt_en: str = ""
    timeout_ms: int = 15000


@dataclass
class ObservableResult:
    ok: bool
    observed: dict[str, Any] | None = None
    error: str = ""
    latency_ms: int = 0


class ObservableListener(ABC):
    @abstractmethod
    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        ...


class FakeObservableListener(ObservableListener):
    """Deterministic fake: pre-program expected observations."""

    def __init__(self, expectations: dict[str, dict[str, Any]] | None = None, auto_pass: bool = True) -> None:
        # key: "press_key:PrtSc" etc
        self.expectations = expectations or {}
        self.auto_pass = auto_pass

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        key = f"{req.kind}:{req.target}"
        if key in self.expectations:
            exp = self.expectations[key]
            if exp.get("fail"):
                return ObservableResult(False, error=exp.get("error", "expected observable not seen"))
            return ObservableResult(True, observed=exp, latency_ms=120)
        if self.auto_pass:
            return ObservableResult(True, observed={"kind": req.kind, "target": req.target, "auto": True}, latency_ms=80)
        return ObservableResult(False, error="timeout — no OS event")


class NoopObservableListener(ObservableListener):
    """When observable not required, or headless without OS hook."""
    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        return ObservableResult(True, observed={"skipped": True})


# --- Real OS listeners (Windows) ---

VK_MAP: dict[str, int] = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46, "G": 0x47, "H": 0x48,
    "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C, "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50,
    "Q": 0x51, "R": 0x52, "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "PrtSc": 0x2C, "F7": 0x76, "F8": 0x77,
    "MouseButton4": 0x05,  # VK_XBUTTON1
    "MouseButton5": 0x06,  # VK_XBUTTON2
}


def _vk_for_target(target: str) -> int | None:
    # target may be HID usage like "A" or pos name; try map, fallback to first char
    if target in VK_MAP:
        return VK_MAP[target]
    if len(target) == 1 and target.upper() in VK_MAP:
        return VK_MAP[target.upper()]
    # For remap W->A, target is expected output "A", not physical W
    return None


class WinRealKeyboardListener(ObservableListener):
    """Polls GetAsyncKeyState for the expected VK. Shows prompt, waits for key press."""

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        import ctypes

        vk = _vk_for_target(req.target)
        if vk is None:
            # Unknown target, fall back to generic: wait for any key (user must press highlighted)
            vk = 0x41  # A as default
        prompt = req.prompt_en or f"Press {req.target} once"
        print(f"\n[Observable] {prompt} (waiting {req.timeout_ms//1000}s)...")
        # Flush
        try:
            ctypes.windll.user32.GetAsyncKeyState(vk)
        except Exception:
            pass
        start = time.time()
        deadline = start + req.timeout_ms / 1000.0
        # Also handle generic "any key" if vk is None: poll all A-Z
        poll_vks = [vk] if vk else list(VK_MAP.values())[:10]
        while time.time() < deadline:
            for code in poll_vks:
                try:
                    state = ctypes.windll.user32.GetAsyncKeyState(code)
                    if state & 0x8000:
                        latency = int((time.time() - start) * 1000)
                        print(f"[Observable] Detected VK {code:#x} ({req.target}) after {latency}ms")
                        return ObservableResult(True, observed={"vk": code, "target": req.target}, latency_ms=latency)
                except Exception:
                    break
            time.sleep(0.02)
        return ObservableResult(False, error=f"timeout waiting for {req.target} (VK {vk:#x})")


class WinRealMouseMoveListener(ObservableListener):
    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        try:
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            x0, y0 = pt.x, pt.y
        except Exception:
            return ObservableResult(False, error="GetCursorPos failed")
        print(f"\n[Observable] {req.prompt_en or 'Move mouse'} (waiting {req.timeout_ms//1000}s)...")
        start = time.time()
        deadline = start + req.timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                if abs(pt.x - x0) > 30 or abs(pt.y - y0) > 30:
                    latency = int((time.time() - start) * 1000)
                    return ObservableResult(True, observed={"dx": pt.x - x0, "dy": pt.y - y0}, latency_ms=latency)
            except Exception:
                break
            time.sleep(0.05)
        return ObservableResult(False, error="timeout waiting for mouse move")


class WinRealHEAnalogListener(ObservableListener):
    """For HE analog press: observes that device sends input report with travel.
    Currently stub that falls back to keyboard poll (since analog stream needs HID).
    For real HE, we could open HID input report stream, but for now prompt and fake PASS.
    """

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        print(f"\n[Observable] {req.prompt_en or req.prompt_ru} (HE analog — press W fully)")
        # In real implementation, open HID input report and wait for travel > 90%
        # For now, treat as keyboard W press as proxy
        kb = WinRealKeyboardListener()
        # Map HE W to VK_W
        he_req = ObservableRequest("press_key", "W", req.prompt_ru, req.prompt_en, timeout_ms=req.timeout_ms)
        return kb.wait_for(he_req)


def real_listener_for(req: ObservableRequest) -> ObservableListener:
    if req.kind == "move_mouse":
        return WinRealMouseMoveListener()
    if req.kind == "he_press":
        return WinRealHEAnalogListener()
    # press_key / press_button
    return WinRealKeyboardListener()


class RealCompositeListener(ObservableListener):
    """Delegates to the appropriate WinReal* listener per request kind."""

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        return real_listener_for(req).wait_for(req)


# Helper to map operation -> observable request
OPERATION_OBSERVABLES: dict[str, ObservableRequest] = {
    # For HERO84, remap test uses pos 30 (W) -> A (0x04). Highlighted physical is W, expected OS is A.
    "keyboard.remap": ObservableRequest("press_key", "A", "Нажмите подсвеченную клавишу W один раз", "Press highlighted key W once (expect A)"),
    "he.macro": ObservableRequest("press_key", "PrtSc", "Нажмите PrtSc один раз", "Press PrtSc once"),
    "he.analog_w": ObservableRequest("he_press", "W", "Нажмите W полностью один раз", "Press W fully once"),
    "mouse.remap": ObservableRequest("press_button", "MouseButton4", "Нажмите боковую кнопку мыши один раз", "Press Mouse Button 4 once"),
    "mouse.move": ObservableRequest("move_mouse", "move", "Подвигайте мышью влево-вправо", "Move the mouse left and right"),
}
