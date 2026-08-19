from miner.schemas.models import ConfidenceClass, Observation
from miner.synthesize.candidate import synthesize


def _builder(identifier: str, report_id: int) -> Observation:
    return Observation(identifier, "a" * 64, "test", "1", "protocol.buffer_builder", {
        "function": "setActuation", "semantic_candidate": "he.actuation.write", "report_id": report_id,
        "field_writes": [{"offset": 0, "expression": "0x13"}],
    }, "fixture.js", ConfidenceClass.VERIFIED_SOURCE_CODE)


def test_conflicting_semantic_layouts_are_reported_not_silently_selected() -> None:
    candidate, conflicts, plan, status = synthesize([_builder("obs-1", 9), _builder("obs-2", 10)])
    assert status == "CONTRADICTED"
    assert candidate.contradictions == conflicts
    assert conflicts[0]["field"] == "command_layout.he.actuation.write"
    assert plan
