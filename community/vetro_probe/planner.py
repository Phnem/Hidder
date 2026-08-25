"""Automatic Validation Planner.

Builds minimal test set from B Preview capability graph + mandatory Rank A surface.
Per spec chapter 6: maximize evidence with minimal operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import json
import sys
from pathlib import Path

from .bundle import Bundle

# Ensure DB on path for registry import (for knowledge completeness)
_DB = Path(__file__).resolve().parents[2] / "DB"
if str(_DB) not in sys.path:
    sys.path.insert(0, str(_DB))


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


def _knowledge_completeness(bundle: Bundle) -> dict[str, Any]:
    """How complete is the bundle vs registry's production knowledge for this product.
    Returns {ratio, bundle_ops, registry_caps, missing}
    """
    try:
        import aula_kb_v3.registry as reg  # type: ignore

        uuid = None
        try:
            uuid = int(bundle.product.uuid) if bundle.product.uuid else None
        except Exception:
            uuid = None
        if uuid is not None:
            try:
                product = reg.resolve_by_uuid(uuid)
                # All capabilities that are production_safe and supported
                total_caps = [c for c in product.capabilities if c.supported]
                # How many of those have a bundle operation covering them?
                # Map cap name to whether bundle has op for it via alias
                covered = 0
                missing: list[str] = []
                for cap in total_caps:
                    # Find if any bundle op maps to this cap via OP_MAP cap field
                    # We check if any bundle op's cap == cap.name
                    # For simplicity, check if any op's alias maps
                    found = False
                    for op_id, op in bundle.operations.items():
                        # Use ALIASES to see if op covers cap
                        # Quick: if cap.name in bundle.capabilities and op cap mapping
                        # We have no direct op->cap map here, so check bundle.capabilities
                        pass
                    # Simpler: check bundle.capabilities dict
                    if cap.name in bundle.capabilities and bundle.capabilities[cap.name]:
                        # Check if at least one op exists for this cap via OP_MAP
                        # For now, if cap is in bundle.capabilities, consider covered if any op with that cap exists
                        # We approximate by checking if any op's id contains cap substring
                        has_op = any(cap.name in op_id or op_id in cap.name or cap.name.replace("_", ".") in op_id for op_id in bundle.operations)
                        # Fallback: if cap is actuation and bundle has he.actuation, consider covered
                        if cap.name == "actuation" and "he.actuation" in bundle.operations:
                            has_op = True
                        if cap.name == "profiles" and "keyboard.profile" in bundle.operations:
                            has_op = True
                        if cap.name == "rgb_core" and "light.rgb_core" in bundle.operations:
                            has_op = True
                        if cap.name == "device_settings" and "device.win_lock" in bundle.operations:
                            has_op = True
                        if cap.name == "polling" and "keyboard.polling" in bundle.operations:
                            has_op = True
                        if cap.name == "remap" and "keyboard.remap" in bundle.operations:
                            has_op = True
                        if cap.name in ("rapid_trigger", "deadzone") and any(x in bundle.operations for x in ("he.rt", "he.deadzone")):
                            has_op = True
                        if has_op:
                            covered += 1
                        else:
                            missing.append(cap.name)
                    else:
                        missing.append(cap.name)
                # Also count bundle ops that are not in registry (should not happen)
                total = len(total_caps)
                ratio = (covered / total) if total else 1.0
                return {"ratio": round(ratio, 4), "covered": covered, "total": total, "missing": missing}
            except Exception:
                pass
    except Exception:
        pass
    # Fallback if registry unavailable: use bundle ops vs bundle capabilities
    total = len(bundle.capabilities)
    covered = len([op for op in bundle.operations if bundle.operations[op].kind != "observable"])
    ratio = (covered / total) if total else 1.0
    return {"ratio": round(ratio, 4), "covered": covered, "total": total, "missing": []}


def _load_authoritative_holes(bundle: Bundle) -> dict[str, Any] | None:
    """Load machine-readable rank artifact for hardware-shaped holes.

    For HERO84, authoritative is community/vetro_probe/knowledge/hero84_a_preview.json
    state=A_PREVIEW holes=[rapid_trigger_units_crosscheck]. This is the single source;
    diff is diagnostics only and must not be named authoritative.
    """
    try:
        # Only for HERO84 currently has authoritative artifact
        uuid = int(bundle.product.uuid) if bundle.product.uuid else 0
        if uuid == 18691697672197:
            p = Path(__file__).resolve().parent / "knowledge" / "hero84_a_preview.json"
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                return data
    except Exception:
        pass
    return None


def _hardware_shaped_holes(bundle: Bundle, tests: list[Any]) -> dict[str, Any]:
    """Authoritative hardware-shaped holes from rank artifact; diff only diagnostics.

    For HERO84, authoritative is knowledge/hero84_a_preview.json.
    For other products or if artifact missing, fallback to diagnostics diff but mark as such.
    """
    authoritative = _load_authoritative_holes(bundle)
    if authoritative is not None:
        holes = authoritative.get("hardware_shaped_holes", [])
        return {
            "authoritative": holes,
            "authoritative_source": str(Path(__file__).resolve().parent / "knowledge" / "hero84_a_preview.json"),
            "diagnostics": _hardware_shaped_holes_diagnostics(bundle, tests),
            "count": len(holes),
            "note": "authoritative holes from rank artifact; diagnostics diff is not authoritative",
        }
    # Fallback diagnostics only
    diag = _hardware_shaped_holes_diagnostics(bundle, tests)
    diag["note"] = "no authoritative artifact found — diagnostics only, not authoritative"
    return diag


def _hardware_shaped_holes_diagnostics(bundle: Bundle, tests: list[Any]) -> dict[str, Any]:
    """Diagnostics diff: registry vs bundle, bundle caps without PASS. Not authoritative."""
    passed_ops = {t.operation for t in tests if getattr(t, "status", "") == "PASS"}
    expanded = set(passed_ops)
    for op in list(passed_ops):
        for base, aliases in ALIASES.items():
            if op in aliases:
                expanded.update(aliases)
                expanded.add(base)
    holes: list[str] = []
    for cap_name, supported in bundle.capabilities.items():
        if not supported:
            continue
        cap_to_ops = {
            "actuation": ["he.actuation"],
            "rapid_trigger": ["he.rt", "he.rt.enabled"],
            "deadzone": ["he.deadzone"],
            "remap": ["keyboard.remap", "remap"],
            "profiles": ["keyboard.profile", "profiles"],
            "rgb_core": ["light.rgb_core", "light.effect"],
            "device_settings": ["device.win_lock", "win_lock"],
            "polling": ["keyboard.polling"],
        }
        ops_for_cap = cap_to_ops.get(cap_name, [cap_name])
        has_pass = any(op in expanded for op in ops_for_cap)
        if not has_pass:
            holes.append(cap_name)
    registry_missing: list[str] = []
    try:
        import aula_kb_v3.registry as reg  # type: ignore

        uuid = int(bundle.product.uuid) if bundle.product.uuid else None
        if uuid is not None:
            product = reg.resolve_by_uuid(uuid)
            for cap in product.capabilities:
                if cap.supported and cap.name not in bundle.capabilities:
                    registry_missing.append(cap.name)
    except Exception:
        pass
    return {"unvalidated_bundle_caps": holes, "registry_caps_not_in_bundle": registry_missing, "count": len(holes), "diagnostics_only": True}


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
    # Additional split coverage per requirement: validation plan vs knowledge vs hardware holes
    knowledge = _knowledge_completeness(bundle)
    holes = _hardware_shaped_holes(bundle, tests)
    # Build distinct sections
    return {
        "mandatory_core": round(mandatory_core, 4),
        "by_capability": by_cap,
        "kind": kind,
        "validation_plan": {
            "planned": total_mappable,
            "passed": passed_mappable,
            "ratio": round(mandatory_core, 4),
            "note": "validation plan coverage (planned ops that passed)",
        },
        "knowledge_completeness": knowledge,
        "hardware_shaped_holes": holes,
        "a_preview_note": "mandatory_core=1.0 alone does not imply A Preview/A; A requires knowledge_completeness=1.0 and hardware_shaped_holes=0 and quorum",
    }
