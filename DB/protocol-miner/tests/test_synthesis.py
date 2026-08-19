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


def test_fake_webhid_trace_can_confirm_static_candidate_but_not_promote_it() -> None:
    dynamic = Observation("obs-trace", "a" * 64, "trace", "1", "dynamic.webhid_call", {"method": "sendReport", "report_id": 9, "bytes_hex": "1300"}, "trace", ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE)
    candidate, _, _, _ = synthesize([_builder("obs-static", 9), dynamic])
    command = next(value for value in candidate.commands.values() if value["report_id"] == 9)
    assert command["dynamic_evidence_refs"] == ["obs-trace"]
    assert command["safe_for_production"] is False
