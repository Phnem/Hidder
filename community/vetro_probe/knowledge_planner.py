"""Knowledge-gap planner: selects research targets from the K-matrix + strategy.

Only gaps matter; FULL knowledge is deliberately skipped (why_not_selected).
Each planned operation carries target_K / current_K_state / expected_information_gain.
"""

from __future__ import annotations

from typing import Any

from .brand_router import Resolution
from .knowledge_rank import K_LEVELS, research_value_score, value_band

# operation -> primary knowledge index it advances
OP_KI: dict[str, list[str]] = {
    "he.actuation": ["K10", "K11", "K13", "K14"],
    "he.rt": ["K10", "K11"],
    "he.deadzone": ["K10", "K11"],
    "keyboard.remap": ["K12", "K13", "K14", "K18"],
    "keyboard.profile": ["K12", "K13", "K14"],
    "keyboard.polling": ["K13", "K14", "K15"],
    "device.win_lock": ["K13", "K14"],
    "light.rgb_core": ["K13", "K14", "K18"],
    "input.he.analog_w": ["K18"],
}

_GAIN = {"NONE": "high", "CAND": "high", "PART": "medium", "FULL": "none"}


def _k_level(matrix: dict[str, str], ki: str) -> str:
    return matrix.get(ki, "NONE") if matrix.get(ki, "NONE") in K_LEVELS else "NONE"


def knowledge_plan_entry(res: Resolution, operation: str, planned: bool, classification: str,
                         why_selected: str = "") -> dict[str, Any]:
    kis = OP_KI.get(operation, ["K13", "K14"])
    cur_states = {ki: _k_level(res.k_matrix, ki) for ki in kis}
    # expected gain = highest severity among targeted Ki not yet FULL
    gains = [_GAIN[s] for s in cur_states.values() if s != "FULL"]
    gain = sorted(gains, key=lambda g: {"none": 0, "high": 3, "medium": 2}.get(g, 1), reverse=True)[0] if gains else "none"
    fully_known = all(s == "FULL" for s in cur_states.values())
    entry = {
        "operation": operation,
        "classification": classification,
        "why_selected": why_selected,
        "target_K": kis,
        "current_K_state": cur_states,
        "expected_information_gain": gain,
        "safety_basis": "reversible typed op with baseline/readback/rollback" if classification == "AUTO_REVERSIBLE" else classification,
        "baseline_method": "fresh GET before write",
        "rollback_method": "restore_value",
        "observable": operation in ("keyboard.remap", "input.he.analog_w"),
        "reconnect_required": operation == "keyboard.polling",
    }
    if fully_known and planned:
        entry["why_not_selected"] = f"knowledge already FULL for {kis}"
    else:
        entry["why_not_selected"] = ""
    return entry


def knowledge_plan(res: Resolution, planned: list[Any]) -> list[dict[str, Any]]:
    """Enrich an existing planned-op list with knowledge fields."""
    out = []
    for p in planned:
        op_id = p.operation_id
        mandatory = getattr(p, "mandatory", False)
        entry = knowledge_plan_entry(res, op_id, planned=True,
                                     classification="AUTO_REVERSIBLE" if mandatory else "optional",
                                     why_selected=getattr(p, "reason", ""))
        out.append(entry)
    return out


def value_heading(res: Resolution) -> dict[str, Any]:
    return {
        "value_score": res.value_score,
        "value_band": res.value_band,
        "why_valuable": [f"{ki} missing ({res.k_matrix.get(ki, 'NONE')})" for ki in ("K10", "K11", "K13", "K14", "K15", "K18", "K19", "K20") if res.k_matrix.get(ki, "NONE") != "FULL"],
        "will_not_waste_time_on": res.avoid_redundant,
    }
