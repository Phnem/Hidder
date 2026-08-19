"""Safety classification for vendor UI controls and actions.

Ensures dangerous actions (firmware flash, reset, DFU, EEPROM clear) are quarantined,
and risky features (SOCD, calibration, macros) are kept review-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SafetyStatus(StrEnum):
    SAFE = "SAFE"
    REVIEW_ONLY = "REVIEW_ONLY"
    FORBIDDEN = "FORBIDDEN"


_FORBIDDEN_PATTERNS = [
    r"firmware\s*update",
    r"\bfirmware\b",
    r"\bflash\b",
    r"\bbootloader\b",
    r"\bdfu\b",
    r"\berase\b",
    r"factory\s*reset",
    r"reset\s*all",
    r"restore\s*firmware",
    r"device\s*recovery",
    r"pairing\s*reset",
    r"eeprom\s*clear",
    r"clear\s*eeprom",
    r"recovery\s*mode",
    r"clear\s*all",
]

_REVIEW_ONLY_PATTERNS = [
    r"\bcalibration\b",
    r"\bsocd\b",
    r"\bdks\b",
    r"\bmacro\b",
    r"system[\s\-_]*key",
    r"\bfn\b[\s\-_]*remap",
    r"profile\s*erase",
    r"wipe\s*profile",
    r"raw\s*command",
    r"expert\s*mode",
]


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyStatus
    reason: str
    is_safe_for_auto_experiment: bool


def classify_control_safety(control_info: dict[str, Any]) -> SafetyDecision:
    """Evaluate text, attributes, and actions of a UI control against safety policies."""
    text_corpus = " ".join(
        str(control_info.get(field, ""))
        for field in ("label", "name", "id", "aria_label", "title", "action", "tab_section", "class_name")
    ).lower()

    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, text_corpus):
            return SafetyDecision(
                status=SafetyStatus.FORBIDDEN,
                reason=f"Matches forbidden dangerous pattern: '{pattern}'",
                is_safe_for_auto_experiment=False,
            )

    for pattern in _REVIEW_ONLY_PATTERNS:
        if re.search(pattern, text_corpus):
            return SafetyDecision(
                status=SafetyStatus.REVIEW_ONLY,
                reason=f"Matches review-only pattern: '{pattern}'",
                is_safe_for_auto_experiment=False,
            )

    control_type = control_info.get("control_type", "unknown")
    if control_type in {"numeric_slider", "boolean", "enum", "color_rgb", "per_key_numeric", "per_key_enum"}:
        return SafetyDecision(
            status=SafetyStatus.SAFE,
            reason=f"Reversible parameter control ({control_type})",
            is_safe_for_auto_experiment=True,
        )

    return SafetyDecision(
        status=SafetyStatus.REVIEW_ONLY,
        reason=f"Unclassified or complex control type: {control_type}",
        is_safe_for_auto_experiment=False,
    )
