"""K0-K20 knowledge model, derived ranks, value score, registry loading.

Knowledge is per brand/family/device. Derived ranks are separate axes:
- Protocol Rank (how well the wire protocol is understood) — D..S
- Hardware Validation Rank (how much real hardware truth exists) — independent
- Family Confidence
- Feature Coverage

Brand != family: brand decides RESEARCH STRATEGY, family decides HOW to talk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

K_DEFS: dict[str, str] = {
    "K0": "Identity (VID/PID, usage page/usage, descriptors)",
    "K1": "Model mapping (VID/PID -> commercial model)",
    "K2": "Firmware (GET, parser, branch/version)",
    "K3": "Interfaces (HID collections, config, DFU, receiver, wired/wireless)",
    "K4": "Transport (sendReport / FeatureReport / interrupt)",
    "K5": "Envelope/framing (report ID, header, length, checksum, sequence)",
    "K6": "Command map (GET/SET/opcodes/command IDs)",
    "K7": "Request serializer (semantic request -> bytes)",
    "K8": "Response parser (bytes -> normalized response)",
    "K9": "Semantic mapping (command <-> user-facing function)",
    "K10": "Value encoding (scale, units, Hz ladder, enums, bitfields)",
    "K11": "Bounds (min/max/step/grid/valid values)",
    "K12": "Layout/stride (keys/profiles/zones/record arrays)",
    "K13": "Baseline/readback (safe current-state GET before WRITE)",
    "K14": "Rollback/inverse (proven restoration method)",
    "K15": "Reconnect lifecycle (re-enumeration / fresh handles / reacquire)",
    "K16": "Safety/destructive (reset/calibration/flash/erase classification)",
    "K17": "Intent provenance (semantic intent survives to serializer/transport)",
    "K18": "Observable evidence (independent physical effect outside config readback)",
    "K19": "Hardware validation (actual real-device validation)",
    "K20": "Cross-device validation (multiple devices/models/FW within family)",
}

K_LEVELS = ("FULL", "PART", "CAND", "NONE")

PROTOCOL_RANKS = ("D", "C", "B", "B+", "A Preview", "A", "A+", "S")

# Hardware-answerable gaps are weighted higher in the value score.
HARDWARE_WEIGHTED_KI = ("K10", "K11", "K13", "K14", "K15", "K18", "K19", "K20")

STRATEGIES = (
    "REFERENCE_REGRESSION",
    "HARDWARE_GROUND_TRUTH_CLOSURE",
    "CONFORMANCE_VALIDATION",
    "FORENSIC_HARDWARE_CLOSURE_HIGH",
    "FORENSIC_HARDWARE_CLOSURE_PARTIAL",
    "PASSIVE_BOOTSTRAP",
    "UNKNOWN_SAFE_DISCOVERY",
)


def registry_path() -> Path:
    return Path(__file__).resolve().parent / "knowledge" / "brand_strategy_registry.json"


def load_registry() -> dict[str, Any]:
    return json.loads(registry_path().read_text(encoding="utf-8"))


def _level_index(k: str, matrix: dict[str, str]) -> int:
    lv = matrix.get(k, "NONE")
    return K_LEVELS.index(lv) if lv in K_LEVELS else K_LEVELS.index("NONE")


def derive_protocol_rank(matrix: dict[str, str]) -> str:
    """Deterministic protocol-rank heuristic from the K-matrix.

    D: mostly identity/catalog. C: protocol located / framing partially known.
    B: structurally understood. B+: hardware-shaped, bounds/readback starting.
    A Preview: selected ops safe+usable. A/A+/S: production-validated model/family/mature.
    """
    has = {k: _level_index(k, matrix) for k in K_DEFS}
    FULL = K_LEVELS.index("FULL")
    PART = K_LEVELS.index("PART")
    full_core = all(has[k] <= FULL for k in ("K5", "K6", "K7", "K8", "K9"))
    struct = all(has[k] <= PART for k in ("K5", "K6", "K7", "K8", "K9"))
    located = has["K5"] <= PART or has["K6"] <= PART
    CAND = K_LEVELS.index("CAND")
    if full_core and has["K19"] <= FULL and has["K20"] <= FULL:
        return "S"
    if full_core and has["K19"] <= FULL and has["K11"] <= FULL:
        return "A"
    # A Preview: core protocol FULL and readback/rollback mechanism understood
    # (K13 <= PART, K14 <= CAND) — even when hardware truth (K19) is missing.
    if full_core and has["K13"] <= PART and has["K14"] <= CAND:
        return "A Preview"
    if full_core and (has["K11"] <= PART or has["K13"] <= PART):
        return "B+"
    if struct:
        return "B"
    if located:
        return "C"
    return "D"


def derive_hardware_rank(matrix: dict[str, str]) -> str:
    """Hardware validation is independent of protocol rank."""
    if matrix.get("K19") == "FULL" and matrix.get("K20") == "FULL":
        return "HIGH"
    if matrix.get("K19") == "FULL":
        return "HIGH"
    if matrix.get("K19") == "PART":
        return "MEDIUM"
    if matrix.get("K19") == "CAND":
        return "LOW"
    return "NONE"


def knowledge_gaps(matrix: dict[str, str]) -> list[str]:
    return [k for k in K_DEFS if matrix.get(k, "NONE") != "FULL"]


def research_value_score(matrix: dict[str, str], strategy: str | None = None) -> int:
    """0..100. Weight hardware-answerable gaps higher, especially when transport/serializer
    (K4-K9) are already known (we know HOW to talk, we don't know what hardware does)."""
    has = {k: _level_index(k, matrix) for k in K_DEFS}
    PART = K_LEVELS.index("PART")
    core_known = sum(1 for k in ("K4", "K5", "K6", "K7", "K8", "K9") if has[k] <= PART)
    hw_gap_weight = 0
    for k in HARDWARE_WEIGHTED_KI:
        idx = has[k]
        weight = 9 if idx == K_LEVELS.index("NONE") else (6 if idx == K_LEVELS.index("CAND") else (3 if idx <= PART else 0))
        hw_gap_weight += weight
    # Weigh even higher when we already know how to talk (ideal forensic target)
    multiplier = 1.0 + 0.25 * (core_known / 6)
    score = min(100, int(round(hw_gap_weight * multiplier)))
    # Fully validated device -> low score (redundant)
    if matrix.get("K19") == "FULL" and matrix.get("K20") == "FULL":
        score = min(score, 15)
    elif matrix.get("K19") == "FULL" and matrix.get("K20") == "PART":
        score = min(score, 40)
    # Value = how much this Probe run can REALLY extract, not raw gap count.
    # If we cannot yet talk to the device (transport/serializer unknown), the run can only
    # do bootstrap -> do not hand a huge score for sheer NONE volume.
    if core_known == 0:
        score = min(score, 40)
    elif core_known <= 3:
        score = min(score, 65)
    return max(0, min(100, score))


def value_band(score: int) -> str:
    if score <= 20:
        return "mostly redundant validation"
    if score <= 40:
        return "useful model/FW conformance"
    if score <= 60:
        return "several unresolved hardware questions"
    if score <= 80:
        return "major hardware-validation gap"
    return "high-value unknown/forensics target"


def gap_actionability(matrix: dict[str, str], ki: str) -> tuple[str, str]:
    """Is this knowledge gap something a Probe run on THIS device can actually close?

    Returns (category, prerequisite):
      software_only                          -> static/passive, no hardware needed
      hardware_answerable_now                -> a run on this device can close it safely now
      hardware_answerable_after_prerequisite -> needs earlier Ki (e.g. K7/K8 serializer) first
      needs_observable                       -> needs an independent OS/HID observable
      needs_other_device                     -> requires a different device/model/FW (K20)
      blocked                                -> no safe path known
    """
    level = matrix.get(ki, "NONE")
    if level == "FULL":
        return "software_only", "already FULL"
    k4_9 = all(matrix.get(k, "NONE") in ("FULL", "PART") for k in ("K4", "K5", "K6", "K7", "K8", "K9"))
    parser = matrix.get("K8", "NONE") in ("FULL", "PART")
    serializer = matrix.get("K7", "NONE") in ("FULL", "PART")

    if ki == "K20":
        return "needs_other_device", "another device/model/FW"
    if ki in ("K18",):
        return "needs_observable", "independent OS/HID observable (WM_INPUT)" if not k4_9 else "readback + independent observable"
    if ki in ("K19", "K13", "K14", "K15"):
        return ("hardware_answerable_now", "") if k4_9 else ("hardware_answerable_after_prerequisite", "K4-K9 transport/serializer/parser")
    if ki in ("K10", "K11"):
        return ("hardware_answerable_now", "") if parser else ("hardware_answerable_after_prerequisite", "K8 response parser")
    if ki == "K12":
        return ("hardware_answerable_now", "") if (k4_9 and serializer) else ("hardware_answerable_after_prerequisite", "K7 serializer")
    if ki == "K2":
        return ("hardware_answerable_now", "") if k4_9 else ("software_only", "passive GET if a proven route exists")
    if ki in ("K16", "K17"):
        return "software_only", "classification / intent provenance from known artifacts"
    if ki in ("K0", "K1", "K3", "K4", "K5", "K6", "K7", "K8", "K9"):
        return "software_only", "static/passive enumeration"
    return "blocked", ""
