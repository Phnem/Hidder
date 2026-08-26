"""Deterministic differential analysis of a lighting WebHID trace.

Idle filtering, user-action correlation, byte-diff extraction, full-state-write
detection, and field inference (only after multiple controlled samples).
No field is claimed from a single frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def filter_idle(events: list[dict[str, Any]], idle_sig: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """Drop frames whose (method, direction, hex) is in the idle signature set."""
    return [e for e in events if (e.get("method", ""), e.get("direction", ""), e.get("hex", "")) not in idle_sig]


def idle_signatures(events: list[dict[str, Any]]) -> set[tuple[str, str, int]]:
    """Build a signature set from idle background frames (method, direction, hex)."""
    return {(e.get("method", ""), e.get("direction", ""), e.get("hex", "")) for e in events}


def correlate(events: list[dict[str, Any]], actions: list[dict[str, Any]],
              window_s: float = 5.0) -> dict[str, list[dict[str, Any]]]:
    """Associate OUT/IN frames to the nearest preceding USER_ACTION within a window."""
    result: dict[str, list[dict[str, Any]]] = {}
    active: str | None = None
    active_ts = 0.0
    for e in events:
        if e.get("type") == "USER_ACTION":
            active = e.get("action")
            active_ts = e.get("timestamp", 0.0)
            result.setdefault(active, [])
            continue
        if active is None:
            continue
        if e.get("timestamp", 0.0) - active_ts <= window_s:
            result.setdefault(active, []).append(e)
    return result


def byte_diff(hex_a: str, hex_b: str) -> list[dict[str, Any]]:
    """Offset-by-offset changed bytes between two hex frames (same length)."""
    a = bytes.fromhex(hex_a)
    b = bytes.fromhex(hex_b)
    if len(a) != len(b):
        return [{"offset": -1, "a": hex_a, "b": hex_b, "note": "length differs"}]
    return [{"offset": i, "a": f"{x:02x}", "b": f"{y:02x}"} for i, (x, y) in enumerate(zip(a, b)) if x != y]


def same_method_length(events: list[dict[str, Any]], method: str | None = None) -> bool:
    frames = [e for e in events if e.get("direction") == "OUT" and (method is None or e.get("method") == method)]
    if not frames:
        return False
    lengths = {len(e.get("hex", "")) // 2 for e in frames}
    return len(lengths) == 1


def full_state_write_detected(correlated: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """If many different UI actions produce OUT frames of the SAME method+length with
    small per-offset diffs and mostly-invariant bytes, likely a full-state block write."""
    samples = [e for frames in correlated.values() for e in frames if e.get("direction") == "OUT"]
    if len(samples) < 3:
        return {"detected": False, "reason": "not enough outbound samples"}
    methods = {e.get("method") for e in samples}
    if len(methods) != 1:
        return {"detected": False, "reason": f"multiple methods {methods}"}
    if not same_method_length(samples, method=next(iter(methods))):
        return {"detected": False, "reason": "varying frame lengths -> changed-field writes"}
    # invariant ratio = fraction of byte positions unchanged across all samples
    total = len(samples)
    width = min(len(e.get("hex", "")) // 2 for e in samples) if samples else 0
    if width == 0:
        return {"detected": False, "reason": "empty frames"}
    changed_positions = set()
    for i in range(width):
        vals = {e.get("hex", "")[i * 2:i * 2 + 2] for e in samples if len(e.get("hex", "")) // 2 >= width}
        if len(vals) > 1:
            changed_positions.add(i)
    ratio = 1.0 - (len(changed_positions) / width)
    return {
        "detected": ratio >= 0.7 and len(changed_positions) > 0,
        "method": next(iter(methods)),
        "width": width,
        "invariant_ratio": round(ratio, 3),
        "changed_offsets": sorted(changed_positions),
    }


@dataclass
class FieldInference:
    status: str = "UNKNOWN"  # KNOWN / PARTIAL / UNKNOWN
    offset: int | None = None
    length: int = 1
    values: dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0


def infer_field(frames: list[dict[str, Any]], offset: int, length: int = 1,
                min_samples: int = 2) -> FieldInference:
    """Infer a field from distinct UI-value -> hex-value samples.
    A field is only KNOWN/PARTIAL after >=2 distinct controlled samples at a stable offset."""
    values: dict[str, str] = {}
    for f in frames:
        ui = f.get("annotation") or f.get("action")
        hx = f.get("hex", "")
        if not ui or not hx:
            continue
        if offset * 2 + length * 2 > len(hx):
            continue
        values[ui] = hx[offset * 2:offset * 2 + length * 2]
    fi = FieldInference(offset=offset, length=length, values=values, evidence_count=len(values))
    if len(values) >= min_samples:
        distinct = set(values.values())
        fi.status = "KNOWN" if len(distinct) >= 2 else "UNKNOWN"
    return fi


def infer_enum(frames: list[dict[str, Any]], offset: int, length: int = 1,
               min_samples: int = 2) -> FieldInference:
    return infer_field(frames, offset, length, min_samples)


def correlate_window(events: list[dict[str, Any]], begin_ts: float, end_ts: float,
                     tail_s: float = 1.0) -> list[dict[str, Any]]:
    """Frames attributable to one user action: timestamps in [begin_ts, end_ts + tail_s].

    ACTION_BEGIN must be written BEFORE the user acts; ACTION_END after. This fixes the
    old ordering where the marker was written after the action (so frame timestamps could
    precede the marker)."""
    lo = begin_ts
    hi = end_ts + tail_s
    return [e for e in events if lo <= e.get("timestamp", 0) <= hi]


def verify_checksum(payload_hex: str, report_id: int = 9, checksum_offset: int = 62) -> bool:
    """Verify 63-byte family checksum: checksum = 0xFF - ((report_id + sum(payload[0:62])) & 0xFF).

    Proven against two real vendor lighting frames (offset 62: 0x83/0x8D)."""
    b = bytes.fromhex(payload_hex)
    if len(b) != checksum_offset + 1:
        return False
    calc = (0xFF - ((report_id + sum(b[:checksum_offset])) & 0xFF)) & 0xFF
    return calc == b[checksum_offset]


def classify_offsets(changed_offsets: list[int], checksum_offsets: set[int]) -> dict[str, list[int]]:
    """Split changed offsets into semantic vs checksum. Checksum offsets are NEVER semantic
    candidates (so a brightness change at byte 11 + checksum move at byte 62 yields semantic=[11])."""
    semantic = [o for o in changed_offsets if o not in checksum_offsets]
    chk = [o for o in changed_offsets if o in checksum_offsets]
    return {"semantic": semantic, "checksum": chk}
