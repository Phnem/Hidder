from miner.schemas.models import ProtocolCandidate
from miner.synthesize.planner import plan_adaptive_research


def test_adaptive_planner_identifies_known_and_missing_gaps() -> None:
    candidate = ProtocolCandidate(
        identity=[{"vid": "0x3434", "pid": "0x0121"}],
        topology=[{"interface": 2, "usagePage": 0xFF00}],
        capabilities={"actuation": {"confidence": "VerifiedVendorArtifact"}},
        contradictions=[],
    )

    plan = plan_adaptive_research(candidate)
    assert plan.schema == "peripheral.research-plan/1"
    assert "identity" in plan.known
    assert "topology" in plan.known
    assert "actuation" in plan.known
    assert "rapid_trigger" in plan.missing
    assert "rgb_lighting" in plan.missing

    # Should recommend RT experiment with high info gain
    rt_recs = [r for r in plan.recommended_experiments if r.parameter == "rapid_trigger"]
    assert len(rt_recs) == 1
    assert rt_recs[0].estimated_info_gain == "high"
    assert rt_recs[0].risk_level == "SAFE"

    # Profile persistence should be flagged as REVIEW_ONLY
    profile_recs = [r for r in plan.recommended_experiments if r.parameter == "profile_persistence"]
    assert len(profile_recs) == 1
    assert profile_recs[0].risk_level == "REVIEW_ONLY"
