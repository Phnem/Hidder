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
    # Brand == Keychron must NOT imply VIA. Strategy is known (conformance), but
    # the family (via/qmk/vial) is unproven -> writes gated until family resolution.
    r = resolve(brand="Keychron", family="", model="Q1")
    assert r.strategy == "CONFORMANCE_VALIDATION"
    assert r.family_required is True
    assert r.family == ""
    assert r.ambiguous is False
    # no automatic write without a proven family
    assert r.family_required is True


def test_brand_never_implies_family():
    # AULA unknown family must NOT auto-assume aula_kb_v3_wired for writes
    a = resolve(brand="AULA", family="")
    assert a.family == "" and a.family_required is True and a.ambiguous is False
    # MCHOSE unresolved must NOT auto-assume BY/CZ
    m = resolve(brand="MCHOSE", family="")
    assert m.family == "" and m.family_required is True
    # ATK/VXE/VGN unknown identity must NOT auto-assume one serializer
    for b in ("ATK", "VXE", "VGN"):
        r = resolve(brand=b, family="")
        assert r.family_required is True and r.ambiguous is False
    # VID-only is insufficient for a write decision
    vid_only = resolve(brand="", vid="0x1234", pid="0x5678")
    assert vid_only.strategy == "UNKNOWN_SAFE_DISCOVERY"
    assert vid_only.family == ""


def test_aliases_do_not_create_new_family():
    # Each alias group must resolve to the SAME strategy group (no accidental new family)
    assert resolve(brand="Logitech", family="").group == resolve(brand="Logitech G", family="").group == "conformance_open_spec"
    assert resolve(brand="Gigabyte", family="").group == resolve(brand="AORUS", family="").group == "forensic_high"
    assert resolve(brand="ATK", family="").group == resolve(brand="VXE", family="").group == resolve(brand="VGN", family="").group == "forensic_high"
    assert resolve(brand="Meletrix", family="").group == resolve(brand="Wuque", family="").group == "conformance_qmk"
    assert resolve(brand="ASUS ROG", family="").group == resolve(brand="ASUS", family="").group == "partial_mature"
    # FL·ESPORTS (interpunct) and IO alias resolve via token-normalized matching
    assert resolve(brand="FL·ESPORTS", family="").group == "forensic_partial"
    assert resolve(brand="Red Square", family="").group == resolve(brand="IO", family="").group == "forensic_partial"


def test_turtle_beach_ambiguous_zero_writes():
    # Turtle Beach is listed under both forensic-high and controller-tier3 -> AMBIGUOUS, zero writes
    r = resolve(brand="Turtle Beach", family="")
    assert r.ambiguous is True
    assert r.strategy == "UNKNOWN_SAFE_DISCOVERY"


def test_gap_actionability():
    from community.vetro_probe.knowledge_rank import gap_actionability
    # Transport/serializer known + K19 NONE -> hardware_answerable_now
    known = {k: "FULL" for k in ("K4", "K5", "K6", "K7", "K8", "K9")}
    cat, prereq = gap_actionability(known, "K19")
    assert cat == "hardware_answerable_now" and prereq == ""
    # Serializer unknown -> K14 not yet answerable
    weak = {"K4": "FULL", "K5": "FULL", "K6": "NONE", "K7": "NONE", "K8": "NONE", "K9": "NONE"}
    cat, prereq = gap_actionability(weak, "K14")
    assert cat == "hardware_answerable_after_prerequisite" and "K4-K9" in prereq
    # K20 needs another device, not a re-test of the same unit
    cat, prereq = gap_actionability(known, "K20")
    assert cat == "needs_other_device"
    # K18 needs an independent observable
    cat, _ = gap_actionability(known, "K18")
    assert cat == "needs_observable"
    # K16/K17 are software-only classification
    cat, _ = gap_actionability(known, "K16")
    assert cat == "software_only"


def test_value_score_ordering():
    from community.vetro_probe.knowledge_rank import research_value_score
    # AULA known (K19 FULL, K20 PART) -> LOW/MID
    aula = {"K0": "FULL", "K4": "FULL", "K5": "FULL", "K6": "FULL", "K7": "FULL", "K8": "FULL",
            "K9": "FULL", "K10": "FULL", "K11": "FULL", "K13": "FULL", "K14": "FULL", "K19": "FULL", "K20": "PART"}
    assert research_value_score(aula) <= 40
    # forensic-high: transport known, hardware truth missing -> HIGH
    fh = {"K0": "FULL", "K1": "FULL", "K4": "FULL", "K5": "FULL", "K6": "FULL", "K7": "FULL", "K8": "FULL", "K9": "FULL",
          "K10": "PART", "K11": "PART", "K12": "PART", "K13": "PART", "K14": "CAND", "K15": "PART",
          "K16": "PART", "K17": "PART", "K18": "CAND", "K19": "NONE", "K20": "PART"}
    assert research_value_score(fh) >= 40
    # catalog-only with nothing known must NOT get huge score just for NONE volume
    cat = {"K0": "PART", "K1": "PART", "K3": "CAND", "K2": "NONE", "K4": "NONE", "K5": "NONE",
           "K6": "NONE", "K7": "NONE", "K8": "NONE", "K9": "NONE", "K10": "NONE", "K11": "NONE",
           "K12": "NONE", "K13": "NONE", "K14": "NONE", "K15": "NONE", "K16": "NONE", "K17": "NONE",
           "K18": "NONE", "K19": "NONE", "K20": "NONE"}
    assert research_value_score(cat) <= 40


def test_router_coverage_full():
    from community.vetro_probe.router_coverage import generate_coverage, AUDIT_ROWS
    r = generate_coverage()
    assert r["audit_rows"] == len(AUDIT_ROWS) == 110
    assert r["unresolved"] == 0
    assert r["ambiguous"] == 1 and r["ambiguous_brands"] == ["Turtle Beach"]
    assert r["routable"] == 109
    total = sum(r["strategy_counts"].values())
    assert total == len(AUDIT_ROWS) == 110


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
