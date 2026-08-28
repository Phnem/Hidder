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
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ObservableRequest:
    kind: str  # "press_key" | "press_button" | "move_mouse" | "he_press" | "visual_confirm"
    target: str  # e.g. "PrtSc", "MouseButton4", "W", "move", "green_color"
    prompt_ru: str = ""
    prompt_en: str = ""
    timeout_ms: int = 15000
    options: list[str] = field(default_factory=lambda: ["yes", "no"])


@dataclass
class ObservableResult:
    ok: bool
    observed: dict[str, Any] | None = None
    error: str = ""
    latency_ms: int = 0
    # Classification for evidence strength (per spec):
    # - "simulated": FakeObservable auto-pass (not hardware)
    # - "uncorrelated_os": GetAsyncKeyState/GetCursorPos (auxiliary, not strong)
    # - "device_correlated": WM_INPUT hDevice matches PhysicalInstance (strong E5)
    # - "human_physical_observable": Human confirmation (explicitly marked, never pretending machine-observed)
    source: str = "simulated"  # simulated | uncorrelated_os | device_correlated | human_physical_observable | prototype


class ObservableListener(ABC):
    @abstractmethod
    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        ...


class HumanConfirmationListener(ObservableListener):
    """Explicit human physical observable (e.g. lighting color confirmation).
    
    Never pretends to be machine-observed: source is strictly 'human_physical_observable'.
    """

    def __init__(self, callback: Any = None, auto_response: str | None = None) -> None:
        self.callback = callback
        self.auto_response = auto_response

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        start = time.time()
        if self.auto_response is not None:
            ans = self.auto_response.lower()
            ok = ans in ("yes", "y", "true", "ok", "да")
            latency = int((time.time() - start) * 1000)
            return ObservableResult(
                ok=ok,
                observed={"human_response": self.auto_response, "prompt": req.prompt_en or req.prompt_ru},
                error="" if ok else "User reported visual check failed (answered No)",
                latency_ms=latency,
                source="human_physical_observable",
            )
        if self.callback is not None:
            res = self.callback(req)
            latency = int((time.time() - start) * 1000)
            if isinstance(res, bool):
                return ObservableResult(
                    ok=res,
                    observed={"human_response": "yes" if res else "no"},
                    error="" if res else "User reported visual check failed",
                    latency_ms=latency,
                    source="human_physical_observable",
                )
            if isinstance(res, dict):
                ok = bool(res.get("ok", False))
                return ObservableResult(
                    ok=ok,
                    observed=res,
                    error="" if ok else res.get("error", "Human confirmation failed"),
                    latency_ms=latency,
                    source="human_physical_observable",
                )
        return ObservableResult(
            False,
            error="No interactive confirmation handler configured",
            source="human_physical_observable",
        )


class FakeObservableListener(ObservableListener):
    """Deterministic fake: pre-program expected observations. Always simulated, never strong E5."""

    def __init__(self, expectations: dict[str, dict[str, Any]] | None = None, auto_pass: bool = True) -> None:
        # key: "press_key:PrtSc" etc
        self.expectations = expectations or {}
        self.auto_pass = auto_pass

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        key = f"{req.kind}:{req.target}"
        if key in self.expectations:
            exp = self.expectations[key]
            if exp.get("fail"):
                return ObservableResult(False, error=exp.get("error", "expected observable not seen"), source="simulated")
            return ObservableResult(True, observed=exp, latency_ms=120, source="simulated")
        if self.auto_pass:
            return ObservableResult(True, observed={"kind": req.kind, "target": req.target, "auto": True}, latency_ms=80, source="simulated")
        return ObservableResult(False, error="timeout — no OS event", source="simulated")


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
    "Insert": 0x2D, "Ins": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "PrtSc": 0x2C, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
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
    """Polls GetAsyncKeyState — prototype fallback, NOT strong E5 (uncorrelated_os)."""

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        import ctypes

        vk = _vk_for_target(req.target)
        if vk is None:
            vk = 0x41  # A as default
        prompt = req.prompt_en or f"Press {req.target} once"
        print(f"\n[Observable] {prompt} (uncorrelated OS, prototype — waiting {req.timeout_ms//1000}s)...")
        try:
            ctypes.windll.user32.GetAsyncKeyState(vk)
        except Exception:
            pass
        start = time.time()
        deadline = start + req.timeout_ms / 1000.0
        poll_vks = [vk] if vk else list(VK_MAP.values())[:10]
        while time.time() < deadline:
            for code in poll_vks:
                try:
                    state = ctypes.windll.user32.GetAsyncKeyState(code)
                    if state & 0x8000:
                        latency = int((time.time() - start) * 1000)
                        print(f"[Observable] Detected VK {code:#x} ({req.target}) after {latency}ms (uncorrelated)")
                        return ObservableResult(True, observed={"vk": code, "target": req.target, "uncorrelated": True}, latency_ms=latency, source="uncorrelated_os")
                except Exception:
                    break
            time.sleep(0.02)
        return ObservableResult(False, error=f"timeout waiting for {req.target} (VK {vk:#x})", source="uncorrelated_os")


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
            return ObservableResult(False, error="GetCursorPos failed", source="uncorrelated_os")
        print(f"\n[Observable] {req.prompt_en or 'Move mouse'} (uncorrelated, waiting {req.timeout_ms//1000}s)...")
        start = time.time()
        deadline = start + req.timeout_ms / 1000.0
        while time.time() < deadline:
            try:
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                if abs(pt.x - x0) > 30 or abs(pt.y - y0) > 30:
                    latency = int((time.time() - start) * 1000)
                    return ObservableResult(True, observed={"dx": pt.x - x0, "dy": pt.y - y0}, latency_ms=latency, source="uncorrelated_os")
            except Exception:
                break
            time.sleep(0.05)
        return ObservableResult(False, error="timeout waiting for mouse move", source="uncorrelated_os")


class WinRealHEAnalogListener(ObservableListener):
    """HE analog — currently unsupported/stub, never PASS for strong E5."""

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        print(f"\n[Observable] {req.prompt_en or req.prompt_ru} (HE analog — press W fully) [UNSUPPORTED/STUB]")
        return ObservableResult(False, error="HE analog listener unsupported/stub — not PASS", source="prototype")


class WinRawInputListener(ObservableListener):
    """Strong E5: WM_INPUT with hDevice correlation to PhysicalInstance.

    Registers for Raw Input, waits for WM_INPUT, extracts hDevice, correlates
    with the HID device path of the PhysicalInstance, and checks scancode/usage.
    This is the only listener that yields device_correlated source and thus strong E5.
    Currently stub that explains what would happen; real implementation requires
    a message loop and RegisterRawInputDevices.
    """

    def __init__(self, physical_device_path: str | None = None, expected_vk: int | None = None):
        self.physical_device_path = physical_device_path
        self.expected_vk = expected_vk

    def wait_for(self, req: ObservableRequest) -> ObservableResult:
        print(f"\n[Observable] {req.prompt_en} — awaiting WM_INPUT from {self.physical_device_path or 'PhysicalInstance'} (strong, device-correlated)...")
        # Real implementation:
        #  1. RegisterRawInputDevices(RIDEV_INPUTSINK) for keyboard/mouse
        #  2. Create hidden window, message loop
        #  3. On WM_INPUT, GetRawInputData -> RAWINPUT.header.hDevice
        #  4. GetRawInputDeviceInfo(hDevice, RIDI_DEVICENAME) -> compare to PhysicalInstance path
        #  5. Check raw.data.keyboard.VKey / raw.data.mouse.usButtonFlags
        # For now, return prototype not PASS to avoid false E5
        return ObservableResult(
            False,
            error="WM_INPUT Raw Input listener not yet fully implemented — need hidden window + hDevice correlation",
            source="prototype",
        )


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
