"""Adaptive experiment planner and research-need generator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from miner.schemas.models import Observation, ProtocolCandidate


@dataclass
class RecommendedExperiment:
    parameter: str
    target_capability: str
    estimated_info_gain: str  # "high", "medium", "low"
    risk_level: str  # "SAFE", "REVIEW_ONLY"
    reversible: bool
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchPlan:
    schema: str = "peripheral.research-plan/1"
    known: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    recommended_experiments: list[RecommendedExperiment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["recommended_experiments"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in self.recommended_experiments]
        return result


_CORE_CAPABILITIES = [
    ("identity", "Device VID/PID and USB product name"),
    ("topology", "HID interface collections and report descriptors"),
    ("actuation", "Key actuation point / distance adjustment"),
    ("rapid_trigger", "Rapid trigger press/release threshold"),
    ("polling_rate", "Report rate (1000Hz - 8000Hz)"),
    ("rgb_lighting", "Backlight mode, brightness, and colors"),
    ("debounce", "Key debounce latency / algorithm"),
    ("profile_persistence", "On-board profile flash save/restore semantics"),
]


def plan_adaptive_research(
    candidate: ProtocolCandidate,
    observations: list[Observation] | None = None,
) -> ResearchPlan:
    """Build machine-readable research plan with known facts, gaps, and next experiments."""
    known: list[str] = []
    missing: list[str] = []

    # Check knowns vs missing
    if candidate.identity:
        known.append("identity")
    else:
        missing.append("identity")

    if candidate.topology:
        known.append("topology")
    else:
        missing.append("topology")

    caps = candidate.capabilities or {}
    for cap_key, _ in _CORE_CAPABILITIES[2:]:
        if any(cap_key in k.lower() for k in caps.keys()):
            known.append(cap_key)
        else:
            missing.append(cap_key)

    recommended: list[RecommendedExperiment] = []

    # If actuation is known but rapid_trigger is missing, recommend RT experiment
    if "actuation" in known and "rapid_trigger" in missing:
        recommended.append(
            RecommendedExperiment(
                parameter="rapid_trigger",
                target_capability="rapid_trigger",
                estimated_info_gain="high",
                risk_level="SAFE",
                reversible=True,
                suggested_action="Exercise rapid trigger sensitivity controls between 0.1mm and 2.0mm in fake sandbox",
            )
        )

    # If RGB is missing, recommend RGB color and brightness probe
    if "rgb_lighting" in missing:
        recommended.append(
            RecommendedExperiment(
                parameter="rgb_lighting",
                target_capability="rgb_lighting",
                estimated_info_gain="medium",
                risk_level="SAFE",
                reversible=True,
                suggested_action="Exercise RGB toggle and primary color selection in fake sandbox",
            )
        )

    # If polling rate is missing, recommend polling enum probe
    if "polling_rate" in missing:
        recommended.append(
            RecommendedExperiment(
                parameter="polling_rate",
                target_capability="polling_rate",
                estimated_info_gain="medium",
                risk_level="SAFE",
                reversible=True,
                suggested_action="Cycle polling rate options (1000Hz, 2000Hz, 4000Hz, 8000Hz) in fake sandbox",
            )
        )

    # If profile persistence is missing, suggest read-back check (REVIEW_ONLY)
    if "profile_persistence" in missing:
        recommended.append(
            RecommendedExperiment(
                parameter="profile_persistence",
                target_capability="profile_persistence",
                estimated_info_gain="high",
                risk_level="REVIEW_ONLY",
                reversible=True,
                suggested_action="Observe EEPROM / flash write sequences during profile switch without arbitrary writes",
            )
        )

    return ResearchPlan(
        known=known,
        missing=missing,
        contradictions=candidate.contradictions,
        recommended_experiments=recommended,
    )
