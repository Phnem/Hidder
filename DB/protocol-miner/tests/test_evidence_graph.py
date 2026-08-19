from miner.schemas.models import ConfidenceClass, Observation, ProtocolCandidate
from miner.synthesize.graph import build


def test_every_synthesized_candidate_edge_is_traceable_to_artifact() -> None:
    observation = Observation("obs-fixture", "a" * 64, "test", "1", "identity.vid_pid", {"vid": 1, "pid": 2}, "fixture", ConfidenceClass.VERIFIED_SOURCE_CODE)
    graph = build("a" * 64, [observation], ProtocolCandidate(evidence_refs=["obs-fixture"]))
    assert any(edge == {"from": "artifact:" + "a" * 64, "to": "obs-fixture", "type": "produced"} for edge in graph["edges"])
    assert any(edge["from"] == "obs-fixture" and edge["type"] == "synthesizes" for edge in graph["edges"])
