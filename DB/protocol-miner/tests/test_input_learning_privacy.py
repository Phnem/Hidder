from pathlib import Path

from miner.dynamic.desktop_hooks import generate_frida_script, load_desktop_trace, normalize_desktop_trace_event
from miner.dynamic.input_learning import (
    PrivacyScrubber,
    get_guided_prompts,
    record_isolated_input,
)


def test_frida_script_and_trace_normalization(tmp_path: Path) -> None:
    script = generate_frida_script()
    assert "HidD_SetFeature" in script
    assert "WriteFile" in script

    trace_file = tmp_path / "frida_trace.jsonl"
    trace_file.write_text(
        '{"api":"HidD_SetFeature","timestamp":"2026-08-19T10:00:00Z","buffer_hex":"09130064","length":4,"process":1234}\n'
    )

    obs = load_desktop_trace(trace_file, "b" * 64)
    assert len(obs) == 1
    assert obs[0].confidence.value == "VerifiedDynamicVendorSoftware"
    assert obs[0].value["method"] == "HidD_SetFeature"
    assert obs[0].value["report_id"] == 9
    assert obs[0].value["length"] == 4


def test_privacy_scrubber_removes_sensitive_data() -> None:
    sensitive_dict = {
        "user_path": r"C:\Users\JohnDoe\AppData\Local\Vendor\app.exe",
        "linux_path": "/home/alice/.config/vendor/app.json",
        "serialNumber": "AULA-SN-9988776655",
        "clipboard": "My Secret Password 123!",
        "typing_history": ["H", "e", "l", "l", "o"],
        "ip_addr": "192.168.1.105",
        "mac_addr": "00:1A:2B:3C:4D:5E",
        "clean_field": "Actuation Point 1.0mm",
    }

    scrubbed = PrivacyScrubber.scrub_structure(sensitive_dict)

    assert "JohnDoe" not in scrubbed["user_path"]
    assert "<SCRUBBED_USER>" in scrubbed["user_path"]
    assert "alice" not in scrubbed["linux_path"]
    assert "<SCRUBBED_USER>" in scrubbed["linux_path"]
    assert "9988776655" not in str(scrubbed)
    assert "My Secret Password" not in str(scrubbed)
    assert "typing_history" in scrubbed and scrubbed["typing_history"] == "<SCRUBBED_PRIVACY>"
    assert scrubbed["clean_field"] == "Actuation Point 1.0mm"


def test_guided_input_learning_isolation() -> None:
    prompts = get_guided_prompts("keyboard")
    assert len(prompts) >= 5
    first_prompt = prompts[0]
    assert first_prompt.target_action == "PRESS_KEY_A"

    # Short isolated window (< 3000ms)
    rec = record_isolated_input(first_prompt, bytes.fromhex("0100000400000000"), duration_ms=250.0)
    assert rec.is_valid_isolated_action
    assert rec.report_id == 1
    assert rec.observed_report_hex == "0100000400000000"

    # Long streaming window (> 3000ms) is rejected as non-isolated
    bad_rec = record_isolated_input(first_prompt, bytes.fromhex("0100000400000000"), duration_ms=5000.0)
    assert not bad_rec.is_valid_isolated_action
