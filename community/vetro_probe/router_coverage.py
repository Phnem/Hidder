"""Brand/Family router coverage audit (deterministic, no hardware).

Every brand/group row from the authoritative K0-K20 audit must resolve.
The audit names are expanded to constituent brand names (slash-groups count as
one audit row = 99; expanded brand names = 111). Each name must map to a strategy
or be correctly flagged AMBIGUOUS (zero writes).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .brand_router import resolve

# Authoritative audit rows (expanded constituent brand names).
# Grouped as in the K0-K20 audit; slash-groups expanded so every brand resolves.
AUDIT_ROWS: list[str] = [
    # AULA
    "AULA",
    # MCHOSE / BY / CZ
    "MCHOSE", "BY", "CZ",
    # Open-source / official-spec
    "Logitech", "Logitech G", "Razer", "Corsair", "SteelSeries", "Wooting",
    # QMK / VIA / VIAL
    "Keychron", "MonsGeek", "KBDfans", "Glorious", "NuPhy", "MelGeek", "Akko",
    "YMDK", "Meletrix", "Wuque", "Qwertykeys", "Mode Designs", "Drop",
    # Partial mature / multi-plugin
    "ASUS ROG", "ASUS", "HyperX",
    # Forensic high
    "ATK", "VXE", "VGN", "Attack Shark", "Chosfox", "Lamzu", "Darmoshark",
    "Machenike", "EPOMAKER", "Ajazz", "A4Tech", "Bloody", "Pulsar Gaming",
    "Dark Project", "ARDOR GAMING", "CHERRY", "Cougar Gaming", "MSI", "NZXT",
    "Gigabyte", "AORUS", "Alienware", "Turtle Beach", "Cooler Master",
    # Forensic partial
    "Royal Kludge", "Feker", "Skyloong", "Yunzii", "Womier", "Xinmeng", "Zaopin",
    "WLMOUSE", "WOBKEY", "Scyrox", "Sikakeyb", "Phylina", "Rawm", "Waizowl",
    "Dareu", "DrunkDeer", "Endgame Gear", "Finalmouse", "Fnatic Gear", "Vaxee",
    "Ducky", "Filco", "HHKB", "Leopold", "Realforce", "Zowie", "Red Square", "IO",
    "Kzzi", "Gamakay", "X-Bows", "E-Yooso", "Kemove", "Fantech", "Thunderobot",
    "IQUNIX", "Kysona", "Leobog", "Ninjutso", "Rapoo", "Delux", "FL ESPORTS",
    "G-Wolves", "Incott", "IROK", "Madlions", "Varmilo", "Weikav", "Chilkey",
    "Cidoo", "Tecware", "Redragon",
    # Controller-style tier 3
    "Roccat", "Turtle Beach", "EVGA", "Lian Li", "ThermalTake", "Phanteks", "Deepcool",
    # Catalog-only
    "E-DRA", "DarkFlash",
]

# Keep one row per brand name (Turtle Beach is listed under both forensic-high and tier3
# in the audit — deduplicated here and flagged AMBIGUOUS instead).
AUDIT_ROWS = list(dict.fromkeys(AUDIT_ROWS))

# Brand names that legitimately map to multiple strategy groups in the audit
# (e.g. Turtle Beach is both controller-tier3 and forensic-high) -> AMBIGUOUS, zero writes.
INTENTIONAL_AMBIGUOUS = {"Turtle Beach"}


def _family_resolution_mode(res) -> str:
    if res.ambiguous:
        return "AMBIGUOUS (zero writes until device class/family resolved)"
    if res.family_required:
        return "family_must_be_proven (strategy known, writes gated)"
    if res.families == ["*"] or not res.families:
        return "brand_fallback (family must be proven per device)"
    return "brand->candidate_family"


def _write_policy(res) -> str:
    if res.ambiguous:
        return "zero_writes"
    if res.family_required:
        return "zero_writes_until_family_proven"
    if res.strategy == "UNKNOWN_SAFE_DISCOVERY":
        return "zero_writes_until_identity"
    return "reversible_typed_after_safety_gate"


def _aliases_for(brand: str) -> list[str]:
    from .knowledge_rank import load_registry

    b = brand.strip().lower()
    for g in load_registry()["groups"]:
        names = list(g.get("brands", [])) + list(g.get("aliases", []))
        if any(n.strip().lower() == b for n in names):
            return [n for n in names if n.strip().lower() != b][:6]
    return []


def generate_coverage() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    strategy_counts: dict[str, int] = {}
    unresolved: list[str] = []
    ambiguous: list[str] = []
    routable = 0
    for audit_name in AUDIT_ROWS:
        res = resolve(brand=audit_name)
        entries.append({
            "audit_name": audit_name,
            "canonical_brand": res.brand or audit_name,
            "aliases": _aliases_for(audit_name),
            "registry_match": res.group,
            "strategy": res.strategy,
            "protocol_rank": res.protocol_rank,
            "hardware_rank": res.hardware_rank,
            "family_confidence": res.family_confidence,
            "value_score": res.value_score,
            "family_resolution_mode": _family_resolution_mode(res),
            "write_policy": _write_policy(res),
            "automatic_write_allowed_without_family": False,
        })
        strategy_counts[res.strategy] = strategy_counts.get(res.strategy, 0) + 1
        if res.ambiguous:
            ambiguous.append(audit_name)
        else:
            routable += 1
            if res.strategy == "UNKNOWN_SAFE_DISCOVERY":
                unresolved.append(audit_name)
    report = {
        "schema": "vetro.brand-router-coverage.v1",
        "audit_rows": len(AUDIT_ROWS),
        "audit_rows_grouped": 99,  # slash-groups counted as one audit row (audit's own count)
        "routable": routable,
        "unresolved": len(unresolved),
        "ambiguous": len(ambiguous),
        "ambiguous_brands": ambiguous,
        "strategy_counts": {s: strategy_counts.get(s, 0) for s in (
            "REFERENCE_REGRESSION", "HARDWARE_GROUND_TRUTH_CLOSURE", "CONFORMANCE_VALIDATION",
            "FORENSIC_HARDWARE_CLOSURE_HIGH", "FORENSIC_HARDWARE_CLOSURE_PARTIAL",
            "PASSIVE_BOOTSTRAP", "UNKNOWN_SAFE_DISCOVERY")},
        "entries": entries,
    }
    return report


def write_coverage(path: Path | None = None) -> Path:
    report = generate_coverage()
    out = path or Path(__file__).resolve().parent / "knowledge" / "brand_router_coverage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    p = write_coverage()
    r = json.loads(p.read_text(encoding="utf-8"))
    print(f"audit_rows={r['audit_rows']} routable={r['routable']} unresolved={r['unresolved']} ambiguous={r['ambiguous']}")
    print("strategy_counts:", r["strategy_counts"])
