from pathlib import Path

from miner.dynamic.webhid_trace import load


def test_fake_webhid_trace_is_dynamic_vendor_evidence_not_hardware_evidence(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"method":"sendReport","report_id":9,"bytes_hex":"130064","ui_action":"set actuation"}\n')
    item = load(trace, "a" * 64)[0]
    assert item.confidence.value == "VerifiedDynamicVendorSoftware"
    assert item.value["length"] == 3
