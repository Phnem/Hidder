"""Automatic Validation Planner.

Builds minimal test set from B Preview capability graph + mandatory Rank A surface.
Per spec chapter 6: maximize evidence with minimal operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bundle import Bundle


@dataclass(frozen=True)
class PlannedOp:
    operation_id: str
    mandatory: bool
    reason: str


# Mandatory core surfaces per device class (spec table)
MOUSE_MANDATORY = [
    "identity",
    "buttons/addressing",
    "remap",
    "mouse.dpi",
    "mouse.polling",
    "profiles_if_present",
    "lod_if_exposed",
    "motion_sync_if_exposed",
    "angle_snap_if_exposed",
    "debounce_if_exposed",
    "light.effect_if_exposed",
    "battery_if_wireless",
    "final_restore",
]

MECH_MANDATORY = [
    "identity",
    "layout/layers",
    "keyboard.remap",
    "macros_if_core",
    "profiles",
    "keyboard.polling",
    "debounce",
    "os_mode_if_exposed",
    "win_lock_if_exposed",
    "light.effect_if_exposed",
    "battery_if_wireless",
    "final_restore",
]

HE_MANDATORY = [
    *MECH_MANDATORY,
    "he.actuation",
    "he.rt.enabled",
    "he.rt.press",
    "he.rt.release",
    "he.deadzone",
    "he.per_key_addressing",
    "he.persistence_if_relevant",
    "analog_if_present",
]


def _device_family_to_kind(bundle: Bundle) -> str:
    caps = bundle.capabilities
    if caps.get("actuation") or "he." in "".join(bundle.operations.keys()):
        return "he"
    # mouse vs mech: check for mouse.* ops
    if any(k.startswith("mouse.") for k in bundle.operations):
        return "mouse"
    return "mechanical"


def _mandatory_ops_for_kind(kind: str) -> list[str]:
    if kind == "mouse":
        return MOUSE_MANDATORY
    if kind == "he":
        return HE_MANDATORY
    return MECH_MANDATORY


ALIASES: dict[str, list[str]] = {
    "profiles": ["profiles", "keyboard.profile", "keyboard.profiles"],
    "light.effect": ["light.effect", "light.rgb_core", "light.brightness"],
    "win_lock": ["win_lock", "device.win_lock"],
    "os_mode": ["os_mode", "device.os_mode"],
    "debounce": ["debounce", "keyboard.debounce", "device.debounce"],
    "he.rt.enabled": ["he.rt.enabled", "he.rt", "he.rt.enable"],
    "he.rt.press": ["he.rt.press", "he.rt"],
    "he.rt.release": ["he.rt.release", "he.rt"],
    "he.deadzone": ["he.deadzone", "he.deadzone.up", "he.deadzone.down"],
    "remap": ["remap", "keyboard.remap"],
    "actuation": ["actuation", "he.actuation"],
}

def _resolve_alias(base: str, bundle: Bundle) -> str | None:
    # Direct match first
    if base in bundle.operations:
        return base
    # Alias match
    for key, aliases in ALIASES.items():
        if base == key or base in aliases:
            for alias in aliases:
                if alias in bundle.operations:
                    return alias
    # Also try base as alias key
    if base in ALIASES:
        for alias in ALIASES[base]:
            if alias in bundle.operations:
                return alias
    return None


def plan(bundle: Bundle, kind: str | None = None) -> list[PlannedOp]:
    """Return ordered minimal plan. Fail-closed planner never invents operations not in bundle."""
    kind = kind or _device_family_to_kind(bundle)
    mandatory_surface = _mandatory_ops_for_kind(kind)

    # Map surface entries to operation ids that exist in bundle
    # e.g. "he.actuation" surface -> operation "he.actuation" if present
    plan_ops: list[PlannedOp] = []
    seen: set[str] = set()

    # Priority: reversible ops first, then observable, then calibration is never included
    for surf in mandatory_surface:
        # surf like "light.effect_if_exposed" -> operation "light.effect" or "light.brightness"
        base = surf.replace("_if_exposed", "").replace("_if_present", "").replace("_if_core", "").replace("_if_relevant", "")
        # direct match via alias
        resolved = _resolve_alias(base, bundle)
        candidates: list[str] = []
        if resolved:
            candidates.append(resolved)
        # prefix match: surf "buttons/addressing" has no direct op, skip but keep for coverage reporting
        # also "light.effect" may map to "light.brightness"/"light.effect"
        if "/" in base or base in ("identity", "final_restore", "he.per_key_addressing", "he.persistence_if_relevant", "analog_if_present", "battery_if_wireless"):
            continue
        for c in candidates:
            if c in seen:
                continue
            op = bundle.operations[c]
            # never plan forbidden
            if c.startswith("calibration"):
                continue
            seen.add(c)
            plan_ops.append(PlannedOp(c, True, f"mandatory:{surf}"))

    # Also include any other reversible operation not yet in plan but present and safe
    for op_id, op in bundle.operations.items():
        if op_id in seen:
            continue
        if op_id.startswith("calibration"):
            continue
        if op.kind == "observable":
            # observable ops are not writes; they are separate step but can be included if needed for evidence
            continue
        # only include if it has bounds (safe to write)
        if op.kind in ("set", "toggle", "transaction") and op_id not in bundle.bounds:
            continue
        seen.add(op_id)
        plan_ops.append(PlannedOp(op_id, False, "optional capability"))

    return plan_ops


def coverage_report(bundle: Bundle, tests: list[Any], kind: str | None = None) -> dict[str, Any]:
    kind = kind or _device_family_to_kind(bundle)
    mandatory = _mandatory_ops_for_kind(kind)
    # Count how many mandatory ops have PASS (alias-aware)
    passed_ops = {t.operation for t in tests if getattr(t, "status", "") == "PASS"}
    # Expand passed set with aliases so light.rgb_core counts for light.effect etc
    expanded = set(passed_ops)
    for op in list(passed_ops):
        for base, aliases in ALIASES.items():
            if op in aliases:
                expanded.add(base)
                expanded.update(aliases)
    total_mappable = 0
    passed_mappable = 0
    by_cap: dict[str, bool] = {}
    for surf in mandatory:
        base = surf.replace("_if_exposed", "").replace("_if_present", "").replace("_if_core", "").replace("_if_relevant", "")
        if base in ("identity", "final_restore", "buttons/addressing", "he.per_key_addressing", "he.persistence_if_relevant", "analog_if_present", "battery_if_wireless", "layout/layers", "profiles_if_present"):
            # synthetic checks handled elsewhere; but profiles (without suffix) is real
            continue
        # Skip if no mappable op exists at all (e.g., no bundle op for this surface)
        # Check if this surface has any alias in bundle
        resolved = _resolve_alias(base, bundle)
        if resolved is None:
            # Check if any alias of base exists as mappable but we still count it as not covered?
            # If bundle doesn't have this capability at all, don't count it toward mandatory_core
            continue
        total_mappable += 1
        # Consider alias-expanded passed set
        op_pass = base in expanded or resolved in expanded
        by_cap[base] = op_pass
        if op_pass:
            passed_mappable += 1
    mandatory_core = (passed_mappable / total_mappable) if total_mappable else 1.0
    return {"mandatory_core": round(mandatory_core, 4), "by_capability": by_cap, "kind": kind}
