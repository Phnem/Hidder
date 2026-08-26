"""Knowledge delta export: Probe proposes K promotions, miner/DB accepts.

Probe never rewrites canonical truth; it emits knowledge_delta.json with
requires_miner_acceptance=true.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .brand_router import Resolution


def build_knowledge_delta(
    res: Resolution,
    observations: list[dict[str, Any]],
    proposed_after: dict[str, str] | None = None,
) -> dict[str, Any]:
    before = dict(res.k_matrix)
    after = proposed_after or {}
    # observations that should raise a specific Ki
    delta = {
        "schema": "vetro.knowledge-delta.v1",
        "brand": res.brand,
        "family": res.family,
        "model": res.model,
        "firmware": res.firmware,
        "before": {k: before.get(k, "NONE") for k in before},
        "observations": observations,
        "proposed_after": {**before, **after},
        "confidence": {
            "source": "probe_real_hardware" if res.hardware_rank != "NONE" else "probe_simulated",
            "hardware_validated": res.hardware_rank == "HIGH",
        },
        "requires_miner_acceptance": True,
        "timestamp": time.time(),
    }
    return delta


def write_knowledge_delta(package_dir: Path, delta: dict[str, Any]) -> Path:
    path = Path(package_dir) / "knowledge_delta.json"
    path.write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
