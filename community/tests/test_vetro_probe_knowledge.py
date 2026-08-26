"""Brand/family-aware research router + knowledge-rank planner tests (deterministic, no hardware)."""

import pytest

from community.vetro_probe.brand_router import resolve
from community.vetro_probe.knowledge_rank import (
    derive_protocol_rank, derive_hardware_rank, research_value_score,
    knowledge_gaps, value_band, load_registry,
)
from community.vetro_probe.knowledge_planner import knowledge_plan_entry, knowledge_plan, value_heading
from community.vetro_probe.knowledge_delta import build_knowledge_delta


def test_aula_reference_regression():
    r = resolve(brand="AULA", family="aula_kb_v3_wired", vid="0x372E", pid="0x103E", model="HERO 84 HE", firmware="0216")
    assert r.strategy == "REFERENCE_REGRESSION"
    assert r.protocol_rank == "A"  # production-validated model (K19 FULL, K20 PART keeps it off S)
    assert r.hardware_rank == "HIGH"
    assert r.family_confidence == "HIGH"
    assert r.k_matrix["K19"] == "FULL"
    assert "K20" in r.target_gaps  # K20 PART -> gap
    assert "K9" not in r.target_gaps


def test_mchose_hardware_ground_truth_closure():
    r = resolve(brand="MCHOSE", family="by_wired")
    assert r.strategy == "HARDWARE_GROUND_TRUTH_CLOSURE"
    assert r.hardware_rank == "NONE"
    assert r.k_matrix["K19"] == "NONE"
    # deep protocol knowledge must NOT be lowered just because K19 is missing
    assert r.protocol_rank == "A Preview"
    assert r.value_score >= 40  # major hardware-validation gap


def test_attack_shark_forensic_high():
    r = resolve(brand="Attack Shark", family="*")
    assert r.strategy == "FORENSIC_HARDWARE_CLOSURE_HIGH"
    assert r.hardware_rank == "NONE"
    assert r.k_matrix["K19"] == "NONE"
    assert "K11" in r.target_gaps and "K14" in r.target_gaps


def test_royal_kludge_forensic_partial():
    r = resolve(brand="Royal Kludge", family="*")
    assert r.strategy == "FORENSIC_HARDWARE_CLOSURE_PARTIAL"
    assert r.protocol_rank == "C"
    assert r.k_matrix["K7"] == "CAND"
    assert r.k_matrix["K19"] == "NONE"


def test_keychron_known_via_conformance():
    r = resolve(brand="Keychron", family="via", model="Q1")
    assert r.strategy == "CONFORMANCE_VALIDATION"
    assert r.protocol_rank == "B+"


def test_keychron_ambiguous_must_not_assume_via():
    r = resolve(brand="Keychron", family="", model="Q1")
    assert r.ambiguous is True
    assert r.strategy == "UNKNOWN_SAFE_DISCOVERY"
    assert "family gate" in r.reason.lower() or "via" in r.reason.lower()


def test_edra_passive_bootstrap():
    r = resolve(brand="E-DRA")
    assert r.strategy == "PASSIVE_BOOTSTRAP"
    assert r.protocol_rank == "D"
    assert r.k_matrix["K3"] == "CAND"


def test_unknown_vidpid_safe_discovery():
    r = resolve(brand="", vid="0x1234", pid="0x5678")
    assert r.strategy == "UNKNOWN_SAFE_DISCOVERY"
    assert r.ambiguous is False


def test_ambiguous_family_zero_writes():
    r = resolve(brand="AULA", family="nonsense_family", families_hint=["aula_kb_v3_wired", "other"])
    assert r.ambiguous is True
    assert r.strategy == "UNKNOWN_SAFE_DISCOVERY"


def test_gap_planner_targets_missing_k_not_redundant():
    # Known K0-K9 FULL, K13/K14/K19 gaps -> planner targets those, not rediscovery
    matrix = {f"K{i}": "FULL" for i in range(10)}
    matrix.update({"K10": "PART", "K11": "PART", "K12": "PART", "K13": "PART", "K14": "CAND",
                   "K15": "PART", "K16": "PART", "K17": "FULL", "K18": "CAND", "K19": "NONE", "K20": "PART"})
    gaps = knowledge_gaps(matrix)
    assert "K19" in gaps and "K14" in gaps and "K10" in gaps
    assert "K0" not in gaps and "K6" not in gaps and "K9" not in gaps


def test_knowledge_plan_entry_gain():
    matrix = {f"K{i}": "FULL" for i in range(10)}
    matrix.update({"K10": "PART", "K11": "CAND", "K13": "PART", "K14": "NONE", "K19": "NONE"})
    res = resolve(brand="AULA", family="aula_kb_v3_wired")
    res.k_matrix = matrix
    res.value_score = research_value_score(matrix)
    entry = knowledge_plan_entry(res, "he.actuation", planned=True, classification="AUTO_REVERSIBLE")
    assert entry["target_K"] == ["K10", "K11", "K13", "K14"]
    assert entry["current_K_state"]["K14"] == "NONE"
    assert entry["expected_information_gain"] == "high"
    assert entry["why_not_selected"] == ""


def test_value_score_band():
    # fully validated device -> redundant
    full = {k: "FULL" for k in [f"K{i}" for i in range(21)]}
    assert research_value_score(full) <= 20
    assert value_band(10) == "mostly redundant validation"
    # high-value forensic: transport known, hardware truth missing
    f = {f"K{i}": "FULL" for i in (0,1,3,4,5,6,7,8,9)}
    f.update({"K2": "PART", "K10": "PART", "K11": "PART", "K12": "PART", "K13": "PART",
              "K14": "CAND", "K15": "PART", "K16": "PART", "K17": "PART", "K18": "CAND",
              "K19": "NONE", "K20": "PART"})
    score = research_value_score(f)
    assert score >= 40
    assert value_band(85) == "high-value unknown/forensics target"


def test_knowledge_delta_export_requires_acceptance():
    res = resolve(brand="Royal Kludge", family="*")
    delta = build_knowledge_delta(res, observations=[{"operation": "he.actuation", "status": "PASS"}],
                                  proposed_after={"K11": "FULL", "K13": "FULL", "K19": "PART"})
    assert delta["schema"] == "vetro.knowledge-delta.v1"
    assert delta["requires_miner_acceptance"] is True
    assert delta["before"]["K19"] == "NONE"
    assert delta["proposed_after"]["K19"] == "PART"


def test_registry_group_count_and_strategies():
    reg = load_registry()
    groups = reg["groups"]
    strategies = {g["research_strategy"] for g in groups}
    assert "REFERENCE_REGRESSION" in strategies
    assert "HARDWARE_GROUND_TRUTH_CLOSURE" in strategies
    assert "CONFORMANCE_VALIDATION" in strategies
    assert "FORENSIC_HARDWARE_CLOSURE_HIGH" in strategies
    assert "FORENSIC_HARDWARE_CLOSURE_PARTIAL" in strategies
    assert "PASSIVE_BOOTSTRAP" in strategies
