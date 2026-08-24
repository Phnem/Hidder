"""Hypothesis objects for protocol inference.

A prediction is never evidence.  Every guess the engine makes is wrapped in one
of these, carries its own supporting and contradicting observations, and may
legitimately be UNKNOWN.  Scoring penalises a confident wrong answer far more
than an honest abstention, so the engine is expected to use UNKNOWN freely.
"""
from __future__ import annotations

import dataclasses
from typing import Any

# Ordered weakest -> strongest.  The engine may only ever emit the first three;
# the last three require an external verification step it does not perform.
STATUSES = (
    "UNKNOWN",
    "PREDICTED",
    "SUPPORTED",
    "STRONGLY_SUPPORTED",
    "VERIFIED_STATIC",
    "VERIFIED_LIVE",
    "VERIFIED_PHYSICAL",
)

#: A prediction at or above this confidence that turns out wrong is counted as
#: HIGH_CONFIDENCE_WRONG, the metric the benchmark punishes hardest.
HIGH_CONFIDENCE = 0.80


@dataclasses.dataclass
class Hypothesis:
    target: str
    prediction: Any = None
    confidence: float = 0.0
    status: str = "UNKNOWN"
    evidence_count: int = 0
    supporting: list = dataclasses.field(default_factory=list)
    contradicting: list = dataclasses.field(default_factory=list)
    alternatives: list = dataclasses.field(default_factory=list)
    next_best_experiment: str | None = None
    notes: str | None = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"bad status {self.status!r}")

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE and self.prediction is not None

    def to_dict(self):
        return dataclasses.asdict(self)


def unknown(target, reason, experiment=None, alternatives=None):
    """Explicit abstention.  Preferred over a low-quality guess."""
    return Hypothesis(
        target=target,
        prediction=None,
        confidence=0.0,
        status="UNKNOWN",
        notes=reason,
        alternatives=alternatives or [],
        next_best_experiment=experiment,
    )


def grade(n_support, n_contra, unique_model):
    """Map evidence counts onto (confidence, status).

    Deliberately conservative: a model that fits everything but has a rival
    that fits equally well never gets past PREDICTED, because the benchmark
    would rather see two alternatives than one confident coin flip.
    """
    if n_contra:
        return 0.0, "UNKNOWN"
    if not unique_model:
        return min(0.45, 0.15 + 0.03 * n_support), "PREDICTED"
    if n_support >= 20:
        return 0.95, "STRONGLY_SUPPORTED"
    if n_support >= 5:
        return 0.80, "SUPPORTED"
    if n_support >= 2:
        return 0.60, "PREDICTED"
    return 0.30, "PREDICTED"
