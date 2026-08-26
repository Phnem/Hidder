"""Brand/Family-aware research router.

Resolution order:
  exact device identity -> exact family identity rule -> exact model mapping
  -> high-confidence family match -> brand-level fallback -> UNKNOWN.

Brand != family. Never infer family from VID alone. AMBIGUOUS -> zero writes,
UNKNOWN_SAFE_DISCOVERY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .knowledge_rank import (
    load_registry, derive_protocol_rank, derive_hardware_rank,
    research_value_score, knowledge_gaps,
)


@dataclass
class Resolution:
    brand: str = "UNKNOWN"
    group: str = "unknown"
    families: list[str] = field(default_factory=list)
    family: str = ""
    model: str = ""
    firmware: str = ""
    vid: str = ""
    pid: str = ""
    strategy: str = "UNKNOWN_SAFE_DISCOVERY"
    k_matrix: dict[str, str] = field(default_factory=dict)
    protocol_rank: str = "D"
    hardware_rank: str = "NONE"
    family_confidence: str = "NONE"
    value_score: int = 0
    value_band: str = ""
    target_gaps: list[str] = field(default_factory=list)
    research_targets: list[str] = field(default_factory=list)
    avoid_redundant: list[str] = field(default_factory=list)
    destructive: list[str] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand, "group": self.group, "families": self.families,
            "family": self.family, "model": self.model, "firmware": self.firmware,
            "vid": self.vid, "pid": self.pid, "strategy": self.strategy,
            "k_matrix": self.k_matrix, "protocol_rank": self.protocol_rank,
            "hardware_rank": self.hardware_rank, "family_confidence": self.family_confidence,
            "value_score": self.value_score, "value_band": self.value_band,
            "target_gaps": self.target_gaps, "research_targets": self.research_targets,
            "avoid_redundant_targets": self.avoid_redundant,
            "known_destructive_classes": self.destructive,
            "ambiguous": self.ambiguous, "reason": self.reason,
        }


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _group_for_brand(brand: str, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    b = _normalize(brand)
    for g in groups:
        names = [g.get("group", "")] + list(g.get("brands", [])) + list(g.get("aliases", []))
        if any(_normalize(n) == b or _normalize(n) in b or b in _normalize(n) for n in names if n):
            return g
    return None


def resolve(
    *,
    brand: str = "",
    vid: str = "",
    pid: str = "",
    family: str = "",
    model: str = "",
    firmware: str = "",
    families_hint: list[str] | None = None,
) -> Resolution:
    registry = load_registry()
    groups = registry["groups"]
    res = Resolution(brand=brand, vid=vid, pid=pid, family=family, model=model, firmware=firmware)

    # Never infer family from VID alone; VID maps only to candidate brands.
    if not brand and not family:
        res.reason = "unknown VID/PID — no brand/family candidate"
        res.strategy = "UNKNOWN_SAFE_DISCOVERY"
        return res

    group = _group_for_brand(brand, groups)
    if group is None:
        # brand unmatched -> unknown safe discovery
        res.reason = f"brand {brand!r} not in registry"
        res.strategy = "UNKNOWN_SAFE_DISCOVERY"
        return res

    res.group = group["group"]
    res.families = list(group.get("families", []))
    res.k_matrix = dict(group.get("k_matrix", {}))
    res.strategy = group.get("research_strategy", "UNKNOWN_SAFE_DISCOVERY")
    res.protocol_rank = derive_protocol_rank(res.k_matrix)
    res.hardware_rank = derive_hardware_rank(res.k_matrix)
    res.family_confidence = group.get("family_confidence", "LOW")
    res.value_score = research_value_score(res.k_matrix, res.strategy)
    from .knowledge_rank import value_band
    res.value_band = value_band(res.value_score)
    res.target_gaps = knowledge_gaps(res.k_matrix)
    res.research_targets = list(group.get("research_targets", []))
    res.avoid_redundant = list(group.get("avoid_redundant_targets", []))
    res.destructive = list(group.get("known_destructive_classes", []))

    # family resolution: exact family hint -> group family -> brand fallback
    if family:
        res.family = family
        if families_hint and family in families_hint:
            pass
    elif group.get("families") and group["families"] != ["*"]:
        # high-confidence family match from group when a concrete family is known
        res.family = group["families"][0] if group["families"] else ""
    else:
        res.family = ""

    # AMBIGUOUS: if a family gate is present (e.g. QMK must be proven) and family unresolved
    if group.get("family_gate") and not family:
        res.ambiguous = True
        res.reason = f"family gate: {group['family_gate']} — family unresolved"
        res.strategy = "UNKNOWN_SAFE_DISCOVERY"
        return res
    if family and families_hint and family not in families_hint and len(families_hint) > 1:
        res.ambiguous = True
        res.reason = f"family {family!r} matches multiple incompatible profiles ({families_hint})"
        res.strategy = "UNKNOWN_SAFE_DISCOVERY"
        return res

    res.reason = f"resolved via {group['group']} (strategy {res.strategy})"
    return res
