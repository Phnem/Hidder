"""Feature-level required-evidence gates for autonomous reversible writes.

A generic bundle row (production_safe / reversible / readback+rollback / bounds
present / firmware gate) is NOT hardware proof. Each operation that requires a
feature-specific hard safety contract must list the evidence it needs, and that
evidence must be physically closed before the operation may be planned as
AUTO_REVERSIBLE.

Precedence (explicit, enforced in automation._classify_op):
    feature-specific OPEN requirement  >  generic reversible metadata  >
    family/global knowledge rank

An unresolved hard requirement always wins. Cross-feature leakage is prevented
by keeping the evidence lists per operation: closing K13/K14/K18/K19 for
light.brightness never closes a requirement of he.rt / keyboard.remap /
he.actuation.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Per-operation required evidence (feature-specific; do NOT reuse across ops).
# Key = operation_id, value = list of evidence items that MUST be physically
# closed before the op may run autonomously.
# ---------------------------------------------------------------------------

REQUIRED_EVIDENCE: dict[str, list[str]] = {
    "light.brightness": [
        "exact FW 0216",
        "K13 baseline",
        "K14 rollback",
        "K18 observable/readback",
        "K19 physical validation",
    ],
    "keyboard.remap": [
        "strong E5 WM_INPUT observable",
    ],
    "he.rt": [
        "rapid_trigger_units_crosscheck",
    ],
    "he.actuation": [
        "physical Probe PASS after 0.5mm-grid fix",
    ],
    "keyboard.polling": [
        "real baseline/readback/rollback PASS",
    ],
    "device.win_lock": [
        "real smoke/readback/rollback PASS",
    ],
    "he.deadzone": [
        "real baseline/write/readback/rollback PASS",
    ],
    "keyboard.profile": [
        "physical profile switch set/readback/rollback",
    ],
}

# ---------------------------------------------------------------------------
# Operations ALWAYS blocked by lighting feature policy (per-operation eligibility).
# Independent of scope: enabling light.brightness never unlocks these. Consulted
# by the executor-level gate too, so a stale plan cannot execute them.
# ---------------------------------------------------------------------------

ALWAYS_BLOCKED: dict[str, str] = {
    "light.rgb_core": "BLOCKED_BY_UNRESOLVED_LIGHTING_REGISTER (global color encoding KNOWN from vendor capture but rollback NOT physically tested)",
    "light.global_color": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (global color rollback NOT physically tested)",
    "light.mode": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (effect selector NOT auto-validated)",
    "light.enable": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (derived from mode selector; NOT auto-validated)",
    "light.effect": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (effect enum unresolved)",
    "light.speed": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (partial; min not captured; rollback not physically tested)",
    "light.direction": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (unsupported by current UI / unresolved protocol applicability)",
    "custom.per_key": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (per-key/custom lighting unresolved)",
    "light.edge_light": "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE (unresolved)",
}

# ---------------------------------------------------------------------------
# Which required-evidence items are PHYSICALLY CLOSED, with the authoritative
# artifact. Nothing may be added here from static mappings, synthetic tests, or
# generic reversible metadata.
# ---------------------------------------------------------------------------

CLOSED_EVIDENCE: dict[str, str] = {
    # light.brightness — closed by K14 run #3 + EXTERNAL_POST_K14_FINAL_GET
    "exact FW 0216": "verified 372E:103E / aula_kb_v3_wired / FW 0216 on physical HERO84",
    "K13 baseline": "lighting_mapping.json v5 — K13_global_lighting_baseline PHYSICALLY_CLOSED",
    "K14 rollback": "lighting_mapping.json v5 — K14_light_brightness_rollback PHYSICALLY_CLOSED (K14 run #3 PASS)",
    "K18 observable/readback": "lighting_mapping.json v5 — K18_light_brightness_observable_readback PHYSICALLY_CLOSED",
    "K19 physical validation": "lighting_mapping.json v5 — K19_light_brightness_hardware_validation PHYSICALLY_CLOSED",
    # polling / win_lock / deadzone / profile — real physical cycles
    "real baseline/readback/rollback PASS": "real HERO84 polling cycle PASS (baseline/readback/rollback)",
    "real smoke/readback/rollback PASS": "real HERO84 win_lock cycle PASS (smoke/readback/rollback)",
    "real baseline/write/readback/rollback PASS": "PHYSICAL_VALIDATION_PASS C — deadzone FAMILY_VALIDATED (set/readback/rollback/readback)",
    "physical profile switch set/readback/rollback": "PHYSICAL_VALIDATION_PASS G — profile switching FAMILY_VALIDATED",
    # he.actuation — closed by the dedicated post-fix physical revalidation.
    # Historical pre-fix failure preserved: A=1.63, B=0.6 (off-grid), readback 0.0,
    # rollback 1.63 PASS. Post-fix revalidation: A=1.63(raw 163), B=1.0, readback
    # 1.0, immutable restore 1.63, fresh final GET 1.63 == A, STATUS RESTORED.
    "physical Probe PASS after 0.5mm-grid fix": (
        "real HERO84 he.actuation revalidation PASS (actuation_revalidation_checkpoint, 2026): "
        "baseline 1.63 (raw u16 163, native 0.01mm), temporary 1.0 (on-grid), fresh readback 1.0, "
        "immutable restore 1.63, fresh final GET 1.63 == original A, restored=true"
    ),
}

# Open required-evidence items that remain hard blockers for HERO84/FW0216.
# These are the canonical blocker names so plans/tests stay greppable.
OPEN_EVIDENCE: dict[str, str] = {
    "strong E5 WM_INPUT observable": "BLOCKED_BY_MISSING_STRONG_E5",
    "rapid_trigger_units_crosscheck": "BLOCKED_BY_KNOWLEDGE_HOLE",
    "physical Probe PASS after 0.5mm-grid fix": "BLOCKED_PENDING_PHYSICAL_REVALIDATION",
}

# Optional per-gate human reason (why it is still open).
GATE_REASONS: dict[str, str] = {
    "strong E5 WM_INPUT observable": (
        "strong independent E5 / WM_INPUT hDevice observable not physically closed; "
        "readback/rollback metadata is NOT sufficient for remap"
    ),
    "rapid_trigger_units_crosscheck": (
        "rapid_trigger_units_crosscheck is authoritative OPEN (hero84_a_preview.json); "
        "no real Probe RT PASS; generic reversible metadata does not close it"
    ),
    "physical Probe PASS after 0.5mm-grid fix": (
        "prior real Probe run FAILED (baseline 1.63, temp 0.6, readback 0.0, rollback 1.63 PASS); "
        "temporary-value fix to the 0.5mm grid [0.5,1.0,1.5,2.0] not yet revalidated by a real "
        "Probe PASS; protocol/safety implementation READY is not physical revalidation"
    ),
}

SCOPE = ("0x372E", "0x103E", "aula_kb_v3_wired", "0216")


def gate_scope_matches(vid: str, pid: str, family: str, fw: str) -> bool:
    """Evidence gates are scoped to the physical unit they were audited on."""
    return (str(vid) == SCOPE[0] and str(pid) == SCOPE[1]
            and str(family) == SCOPE[2] and str(fw) == SCOPE[3])


def missing_evidence(op_id: str, vid: str = "", pid: str = "", family: str = "",
                     fw: str = "", closed: dict[str, str] | None = None) -> list[str]:
    """Required-but-not-closed evidence for op on this scope ([] == gate satisfied).

    Precedence: feature blocker > generic metadata > family knowledge. An op with
    any OPEN requirement must stay BLOCKED regardless of its bundle row.
    """
    if not gate_scope_matches(vid, pid, family, fw):
        return []  # gate is scoped to the audited unit; other scopes classify elsewhere
    req = REQUIRED_EVIDENCE.get(op_id)
    if not req:
        return []
    closed_set = closed if closed is not None else CLOSED_EVIDENCE
    return [e for e in req if e not in closed_set]


def blocker_for(op_id: str, vid: str = "", pid: str = "", family: str = "",
                fw: str = "", closed: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Return (blocker_name, reason) if a hard requirement is open, else None."""
    # Unconditional lighting policy blockers win over everything.
    if op_id in ALWAYS_BLOCKED:
        return "BLOCKED_BY_UNRESOLVED_LIGHTING_FEATURE", f"{ALWAYS_BLOCKED[op_id]}"
    missing = missing_evidence(op_id, vid, pid, family, fw, closed=closed)
    if not missing:
        return None
    first = missing[0]
    name = OPEN_EVIDENCE.get(first, "BLOCKED_BY_OPEN_EVIDENCE")
    reason = GATE_REASONS.get(first, f"required evidence OPEN: {missing}")
    return name, f"{name} ({reason}; open required evidence: {missing})"


def closure_note(op_id: str) -> str:
    """Evidence closure note for an AUTO_REVERSIBLE op (for truthful plans)."""
    req = REQUIRED_EVIDENCE.get(op_id)
    if not req:
        return "no feature-specific evidence gate defined (generic reversible metadata only)"
    missing = [e for e in req if e not in CLOSED_EVIDENCE]
    if missing:
        return f"OPEN required evidence: {missing}"
    return "; ".join(f"{e} -> {CLOSED_EVIDENCE[e]}" for e in req)
