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

# Helper to map operation -> observable request
OPERATION_OBSERVABLES: dict[str, ObservableRequest] = {
    "keyboard.remap": ObservableRequest("press_key", "PrtSc", "Нажмите подсвеченную клавишу PrtSc один раз", "Press highlighted key PrtSc once"),
    "he.macro": ObservableRequest("press_key", "PrtSc", "Нажмите PrtSc один раз", "Press PrtSc once"),
    "he.analog_w": ObservableRequest("he_press", "W", "Нажмите W полностью один раз", "Press W fully once"),
    "mouse.remap": ObservableRequest("press_button", "MouseButton4", "Нажмите боковую кнопку мыши один раз", "Press Mouse Button 4 once"),
    "mouse.move": ObservableRequest("move_mouse", "move", "Подвигайте мышью влево-вправо", "Move the mouse left and right"),
}
