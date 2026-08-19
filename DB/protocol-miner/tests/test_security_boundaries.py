"""Strict negative security and safety boundary tests."""

import inspect
from pathlib import Path
import pytest

import miner
from miner.dynamic.safety_filter import SafetyStatus, classify_control_safety
from miner.dynamic.input_learning import PrivacyScrubber
from miner.schemas.models import ConfidenceClass, ProtocolCandidate
from miner.synthesize.candidate import synthesize


def test_no_raw_hid_write_api_in_codebase() -> None:
    miner_root = Path(miner.__file__).parent
    forbidden_tokens = [
        "send_raw_hid",
        "hid_write(",
        "hid_send_feature_report(",
        "hid_write_report(",
        "raw_hid_send",
    ]

    for py_file in miner_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in text, f"Forbidden raw HID write API '{token}' found in {py_file.name}"


def test_dynamic_vendor_trace_never_promotes_to_hardware_verified() -> None:
    from miner.dynamic.webhid_trace import load as load_trace
    from miner.schemas.models import Observation

    tmp_trace = Path("temp_test_trace.jsonl")
    tmp_trace.write_text('{"method":"sendReport","report_id":9,"bytes_hex":"09130064"}\n', encoding="utf-8")
    try:
        observations = load_trace(tmp_trace, "0" * 64)
        assert len(observations) == 1
        assert observations[0].confidence == ConfidenceClass.VERIFIED_DYNAMIC_VENDOR_SOFTWARE
        assert observations[0].confidence != ConfidenceClass.HARDWARE_VERIFIED_EXCHANGE

        candidate, _, _, status = synthesize(observations)
        for cmd_name, cmd in candidate.commands.items():
            assert not cmd.get("safe_for_production", True)
    finally:
        if tmp_trace.exists():
            tmp_trace.unlink()


def test_dangerous_actions_always_forbidden_and_not_auto_experimented() -> None:
    dangerous_keywords = [
        "firmware update",
        "flash firmware",
        "enter dfu",
        "bootloader mode",
        "factory reset",
        "clear eeprom",
        "device recovery",
    ]
    for kw in dangerous_keywords:
        decision = classify_control_safety({"label": kw, "control_type": "button_action"})
        assert decision.status == SafetyStatus.FORBIDDEN
        assert not decision.is_safe_for_auto_experiment


def test_privacy_scrub_removes_personal_paths_and_serials() -> None:
    raw_text = r"Error log at C:\Users\AliceSmith\Desktop\report.txt with serialNumber: AULA-SN-12345"
    scrubbed = PrivacyScrubber.scrub_text(raw_text)
    assert "AliceSmith" not in scrubbed
    assert "<SCRUBBED_USER>" in scrubbed
    assert "AULA-SN-12345" not in scrubbed
    assert "<SCRUBBED_SERIAL>" in scrubbed
