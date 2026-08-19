"""Automated test suite for Peripheral Community Research Probe."""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from community.probe.hid_discovery import enumerate_hid_devices, DiscoveredHidCandidate
from community.probe.observer import PassiveTransportObserver
from community.probe.privacy import PrivacyScrubber
from community.probe.schema import (
    CaptureMetadata,
    CommunityObservationBundle,
    DeviceIdentity,
    GuidedAction,
    QualityScore,
    VendorSoftwareInfo,
    SCHEMA_VERSION,
)
from community.probe.wizard import CommunityResearchWizard


def test_privacy_scrubber_removes_sensitive_data():
    scrubber = PrivacyScrubber()
    
    # Path sanitization
    raw_path = r"C:\Users\JohnDoe\AppData\Local\Programs\AULA\AULA.exe"
    sanitized_path = scrubber.sanitize_path(raw_path)
    assert sanitized_path == "AULA.exe"
    assert "JohnDoe" not in sanitized_path
    
    # Email and IP scrubbing
    text_with_ip_email = "Connected from 192.168.1.55 by john.doe@example.com"
    scrubbed = scrubber.scrub_text(text_with_ip_email)
    assert "192.168.1.55" not in scrubbed
    assert "john.doe@example.com" not in scrubbed
    assert "[REDACTED_IP]" in scrubbed or "[REDACTED_EMAIL]" in scrubbed


def test_privacy_scrubber_dict_omits_serials():
    scrubber = PrivacyScrubber()
    data = {
        "device": {
            "user_reported_model": "AULA F75",
            "serial": "1234567890ABCDEF",
            "serial_number": "SN-998877",
            "vid": "0x3151",
            "pid": "0x5025",
        },
        "user": "Alice",
        "username": "alice_admin",
        "process_basename": r"C:\Users\Alice\Downloads\AULA.exe",
    }
    
    sanitized = scrubber.sanitize_dict(data)
    assert "serial" not in sanitized.get("device", {})
    assert "serial_number" not in sanitized.get("device", {})
    assert "user" not in sanitized
    assert "username" not in sanitized
    assert sanitized.get("process_basename") == "AULA.exe"


def test_schema_version_and_sha256_canonical():
    bundle = CommunityObservationBundle(
        submission_id="comm-test-123",
        device=DeviceIdentity(category="keyboard", user_reported_model="ATK Yogo75", vid="0x373B", pid="0x119B"),
        software=VendorSoftwareInfo(process_basename="ATKHub.exe"),
    )
    
    data = bundle.to_dict()
    assert data["schema"] == SCHEMA_VERSION
    assert data["device"]["model_name"] == "ATK Yogo75"
    assert data["device"]["vid"] == "0x373B"
    
    sha = bundle.compute_sha256()
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_device_identity_separation():
    dev = DeviceIdentity(
        category="keyboard",
        user_reported_model="AULA F75",
        detected_product_string="HERO 84 HE",
        detected_manufacturer_string="AULA",
        resolved_model="AULA HERO 84 HE",
        resolved_model_confidence="registry_verified",
        vid="0x372E",
        pid="0x103E",
    )
    d = dev.to_dict()
    assert d["user_reported_model"] == "AULA F75"
    assert d["detected_product_string"] == "HERO 84 HE"
    assert d["resolved_model"] == "AULA HERO 84 HE"
    assert d["resolved_model_confidence"] == "registry_verified"


def test_observer_idle_deduplication():
    obs = PassiveTransportObserver()
    obs.start_idle_baseline()
    
    # Record 100 identical idle polling events
    for _ in range(100):
        obs.record_event(
            api="HidD_GetFeature",
            direction="feature_in",
            report_id=0,
            bytes_hex="000000000000",
            process_basename="VendorApp.exe"
        )
    obs.stop_idle_baseline()
    
    # Must be deduplicated to 1 item with repeat_count=100
    assert len(obs.observations) == 1
    assert obs.observations[0].repeat_count == 100
    assert len(obs.idle_baseline_events) >= 1


def test_zero_traffic_caps_score_at_20():
    wizard = CommunityResearchWizard(is_demo=False)
    # Add dummy completed action with ZERO traffic
    wizard.guided_actions.append(GuidedAction(
        action_id="test_act",
        category="vendor_experiment",
        instruction="Do something",
        expected_semantic={},
        started_at=100.0,
        finished_at=110.0,
        duration_seconds=10.0,
        status="completed",
    ))
    quality = wizard._calculate_quality()
    assert quality.traffic_observed is False
    assert quality.score <= 20
    assert "no protocol traffic" in quality.rating


def test_observer_correlation_changed_offsets():
    obs = PassiveTransportObserver()
    obs.start_idle_baseline()
    obs.record_event("HidD_GetFeature", "feature_in", 0, "000000000000", "VendorApp.exe")
    obs.stop_idle_baseline()
    
    # Record change action
    action = GuidedAction(
        action_id="he_actuation_change",
        category="vendor_experiment",
        instruction="Change actuation",
        expected_semantic={"setting": "he.actuation"},
        started_at=1000.0,
        finished_at=1002.0,
        duration_seconds=2.0,
    )
    
    obs.set_active_action("he_actuation_change")
    # Changed byte at index 2 (from 00 to 0A)
    obs.record_event("HidD_SetFeature", "feature_out", 8, "08130a000000", "VendorApp.exe")
    obs.set_active_action(None)
    
    candidates = obs.correlate_actions([action])
    assert len(candidates) == 1
    assert candidates[0].semantic == "he.actuation"
    assert candidates[0].candidate_reports == [8]
    assert candidates[0].changed_offsets == [0, 1, 2]
    assert candidates[0].confidence == "CommunityGuidedObservation"


def test_wizard_demo_run_produces_valid_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        wizard = CommunityResearchWizard(is_demo=True, output_dir=tmp_path)
        out_file = wizard.run()
        
        assert out_file.is_file()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        
        assert data["schema"] == SCHEMA_VERSION
        assert data["is_demo"] is True
        assert data["completed"] is True
        assert len(data["guided_actions"]) > 0
        assert data["quality"]["score"] >= 80
        assert "capture" in data
        assert data["capture"]["mechanism"] == "win32_user_mode_api_hook"
        
        # Verify no serials or usernames
        assert "serial" not in data["device"]
        assert "username" not in data


def test_critical_security_no_raw_hid_writes_in_probe():
    """Verify that community probe codebase contains no functions that issue raw HID writes."""
    probe_dir = Path(__file__).resolve().parent.parent / "probe"
    
    forbidden_write_apis = [
        "hid_write(",
        "hid_send_feature_report(",
        "win32file.WriteFile(",
        "ctypes.windll.hid.HidD_SetFeature(",
        "ctypes.windll.hid.HidD_SetOutputReport(",
    ]
    
    for p in probe_dir.rglob("*.py"):
        code = p.read_text(encoding="utf-8")
        for api in forbidden_write_apis:
            assert api not in code, f"Forbidden write API '{api}' found in community probe file {p.name}"
