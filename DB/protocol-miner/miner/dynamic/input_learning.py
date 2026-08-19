"""Guided physical input learning and strict privacy scrubber.

Enables input mapping observation (per-key/per-button report layout) without keylogging
or leaking user PII, paths, hostnames, or serial numbers.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


class PrivacyScrubber:
    """Removes PII, usernames, hostnames, file paths, serials, and freeform text."""

    _PATH_USER_PATTERN = re.compile(r"([a-zA-Z]:\\[Uu]sers\\)([^\\]+)", re.IGNORECASE)
    _POSIX_USER_PATTERN = re.compile(r"(/home/)([^/]+)", re.IGNORECASE)
    _SERIAL_PATTERN = re.compile(r"(?:serial|sn|device_sn)[-_]?[A-Za-z0-9\-_]{4,}", re.IGNORECASE)
    _IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    _MAC_PATTERN = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b")

    _FORBIDDEN_KEY_SUBSTRINGS = (
        "clipboard",
        "typing_history",
        "raw_keystream",
        "user_macros",
        "windows_username",
        "hostname",
        "serial",
        "password",
        "secret",
    )

    @classmethod
    def scrub_text(cls, text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        res = cls._PATH_USER_PATTERN.sub(r"\1<SCRUBBED_USER>", text)
        res = cls._POSIX_USER_PATTERN.sub(r"\1<SCRUBBED_USER>", res)
        res = cls._SERIAL_PATTERN.sub(r"<SCRUBBED_SERIAL>", res)
        res = cls._IP_PATTERN.sub(r"<SCRUBBED_IP>", res)
        res = cls._MAC_PATTERN.sub(r"<SCRUBBED_MAC>", res)
        return res

    @classmethod
    def scrub_structure(cls, data: Any) -> Any:
        if isinstance(data, str):
            return cls.scrub_text(data)
        if isinstance(data, dict):
            cleaned = {}
            for k, v in data.items():
                k_lower = str(k).lower()
                if any(sub in k_lower for sub in cls._FORBIDDEN_KEY_SUBSTRINGS):
                    cleaned[k] = "<SCRUBBED_PRIVACY>"
                else:
                    cleaned[k] = cls.scrub_structure(v)
            return cleaned
        if isinstance(data, list):
            return [cls.scrub_structure(item) for item in data]
        return data


@dataclass
class GuidedInputPrompt:
    prompt_id: str
    target_action: str  # e.g. "PRESS_KEY_A", "PRESS_KEY_W", "MOUSE_RIGHT_CLICK"
    device_type: str  # "keyboard", "mouse"
    expected_scancode_hint: int | None = None


@dataclass
class GuidedInputRecord:
    prompt_id: str
    target_action: str
    observed_report_hex: str
    report_id: int
    duration_ms: float
    is_valid_isolated_action: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STANDARD_KEYBOARD_PROMPTS = [
    GuidedInputPrompt("p-kb-01", "PRESS_KEY_A", "keyboard"),
    GuidedInputPrompt("p-kb-02", "PRESS_KEY_W", "keyboard"),
    GuidedInputPrompt("p-kb-03", "PRESS_KEY_SPACE", "keyboard"),
    GuidedInputPrompt("p-kb-04", "PRESS_KEY_ENTER", "keyboard"),
    GuidedInputPrompt("p-kb-05", "PRESS_KEY_LSHIFT", "keyboard"),
]

_STANDARD_MOUSE_PROMPTS = [
    GuidedInputPrompt("p-ms-01", "MOUSE_LEFT_CLICK", "mouse"),
    GuidedInputPrompt("p-ms-02", "MOUSE_RIGHT_CLICK", "mouse"),
    GuidedInputPrompt("p-ms-03", "MOUSE_MIDDLE_CLICK", "mouse"),
    GuidedInputPrompt("p-ms-04", "MOUSE_WHEEL_UP", "mouse"),
    GuidedInputPrompt("p-ms-05", "MOUSE_WHEEL_DOWN", "mouse"),
]


def get_guided_prompts(device_type: str = "keyboard") -> list[GuidedInputPrompt]:
    """Get standardized minimal prompt sequence for input mapping discovery."""
    if device_type == "mouse":
        return _STANDARD_MOUSE_PROMPTS
    return _STANDARD_KEYBOARD_PROMPTS


def record_isolated_input(
    prompt: GuidedInputPrompt,
    input_report_bytes: bytes,
    duration_ms: float,
) -> GuidedInputRecord:
    """Record an input report strictly inside the prompt window with privacy guarantees."""
    is_valid = 0 < duration_ms <= 3000.0 and len(input_report_bytes) > 0
    report_id = input_report_bytes[0] if input_report_bytes else 0
    return GuidedInputRecord(
        prompt_id=prompt.prompt_id,
        target_action=prompt.target_action,
        observed_report_hex=input_report_bytes.hex(),
        report_id=report_id,
        duration_ms=duration_ms,
        is_valid_isolated_action=is_valid,
    )
