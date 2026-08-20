"""Automated test suite for Peripheral Community Research Probe."""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from community.probe.hid_discovery import (
    DiscoveredHidCandidate,
    enumerate_hid_devices,
    is_generic_driver_string,
)
from community.probe.observer import PassiveTransportObserver
from community.probe.privacy import PrivacyScrubber
from community.probe.schema import (
    CaptureMetadata,
    CommunityObservationBundle,
    DeviceIdentity,
    GuidedAction,
    QualityScore,
    TransitionDelta,
    VendorSoftwareInfo,
    SCHEMA_VERSION,
)
from community.probe.webhid_observer import WEBHID_INJECTION_SCRIPT, WebHidObserver
from community.probe.wizard import CommunityResearchWizard


def test_privacy_scrubber_removes_sensitive_data():
    scrubber = PrivacyScrubber()
    raw_path = r"C:\Users\JohnDoe\AppData\Local\Programs\AULA\AULA.exe"
    sanitized_path = scrubber.sanitize_path(raw_path)
    assert sanitized_path == "AULA.exe"
    assert "JohnDoe" not in sanitized_path
    
    text_with_ip_email = "Connected from 192.168.1.55 by john.doe@example.com"
    scrubbed = scrubber.scrub_text(text_with_ip_email)
    assert "192.168.1.55" not in scrubbed
    assert "john.doe@example.com" not in scrubbed


def test_generic_driver_strings_cannot_become_resolved_model():
    generic_samples = [
        "HID-совместимый системный контроллер",
        "HID-compliant system controller",
        "USB-устройство ввода",
        "USB Input Device",
        "Клавиатура HID",
        "HID Keyboard Device",
        "HID-совместимое устройство, определенное поставщиком",
    ]
    for s in generic_samples:
        assert is_generic_driver_string(s) is True, f"Failed for {s}"

    valid_samples = [
        "AULA HERO 84 HE",
        "Lamzu Atlantis",
        "ATK F1 Ultimate",
        "DrunkDeer A75",
    ]
    for s in valid_samples:
        assert is_generic_driver_string(s) is False, f"Failed for {s}"


def test_failed_attach_reports_empty_hooks_installed():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    obs.attach_native(999999, "FakeApp.exe")
    assert obs.capture_metadata.observer_attached is False
    assert obs.capture_metadata.hooks_installed == []
    assert len(obs.capture_metadata.observer_errors) > 0


def test_webhid_script_contains_transparent_wrappers_and_inputreport():
    assert "origSendReport.apply(this, arguments)" in WEBHID_INJECTION_SCRIPT
    assert "origSendFeature.apply(this, arguments)" in WEBHID_INJECTION_SCRIPT
    assert "origReceiveFeature.apply(this, arguments)" in WEBHID_INJECTION_SCRIPT
    assert "device.addEventListener(\"inputreport\"" in WEBHID_INJECTION_SCRIPT
    assert "window.__peripheral_webhid_event__" in WEBHID_INJECTION_SCRIPT
    assert "window.__peripheral_webhid_injected__" in WEBHID_INJECTION_SCRIPT


def test_repeated_report_deduplication_compacts_traffic():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    obs.start_idle_baseline()
    
    for _ in range(100):
        obs.record_event(
            api="sendFeatureReport",
            direction="feature_out",
            report_id=0,
            bytes_hex="000000000000",
            process_basename="browser.exe"
        )
    obs.stop_idle_baseline()
    
    assert len(obs.observations) == 1
    assert obs.observations[0].repeat_count == 100
    assert obs.observations[0].first_seen is not None
    assert obs.observations[0].last_seen is not None
    assert len(obs.idle_baseline_events) >= 1


def test_device_id_binding_in_transport_events():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    obs.record_event(
        api="sendReport",
        direction="out",
        report_id=9,
        bytes_hex="0913001e004b0074",
        process_basename="browser.exe"
    )
    assert len(obs.observations) == 1
    assert obs.observations[0].device_id == "372E:103E"


def test_boot_keyboard_report_privacy_filtering_outside_guided_input():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    
    # 1. Background typing outside guided input action should be dropped
    obs.set_active_action(None)
    obs._handle_raw_event({
        "api": "inputreport",
        "direction": "in",
        "report_id": 1,
        "bytes_hex": "0000040000000000",  # Standard boot keyboard report (Key 'A')
        "timestamp": time.time(),
    })
    assert len(obs.observations) == 0

    # 2. Input report inside guided analog action should be captured
    obs.set_active_action("he_w_light")
    obs._handle_raw_event({
        "api": "inputreport",
        "direction": "in",
        "report_id": 1,
        "bytes_hex": "0000040000000000",
        "timestamp": time.time(),
    })
    assert len(obs.observations) == 1
    assert obs.observations[0].capture_source == "webhid_inputreport"


def test_change_and_restore_merge_with_checksum_detection_and_no_fake_baseline():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    
    # Real baseline packet with matching structural key (Report 9, Opcode 0x13, Key 0x001E, Travel 0x33, Checksum 0x8C)
    obs.start_idle_baseline()
    obs.record_event("sendFeatureReport", "feature_out", 9, "0913001e0033008c", "browser.exe")
    obs.stop_idle_baseline()
    
    # 1. Action Change (B): Travel 0x4B (75), Checksum 0x74
    action_change = GuidedAction(
        action_id="he_actuation_change",
        category="vendor_experiment",
        instruction="Change actuation to 0.75mm",
        expected_semantic={"setting": "he.actuation"},
        started_at=1000.0,
        finished_at=1002.0,
        duration_seconds=2.0,
    )
    obs.set_active_action("he_actuation_change")
    obs.record_event("sendFeatureReport", "feature_out", 9, "0913001e004b0074", "browser.exe")
    obs.set_active_action(None)

    # 2. Action Restore (A'): Travel 0x33 (51), Checksum 0x8C
    action_restore = GuidedAction(
        action_id="he_actuation_restore",
        category="vendor_restore",
        instruction="Restore actuation to 0.51mm",
        expected_semantic={"setting": "he.actuation", "restore": True},
        started_at=1003.0,
        finished_at=1005.0,
        duration_seconds=2.0,
        restore_attempted=True,
    )
    obs.set_active_action("he_actuation_restore")
    obs.record_event("sendFeatureReport", "feature_out", 9, "0913001e0033008c", "browser.exe")
    obs.set_active_action(None)
    
    # Correlate pairwise
    candidates = obs.correlate_actions([action_change, action_restore])
    assert len(candidates) == 1
    c = candidates[0]
    
    assert c.change_action_id == "he_actuation_change"
    assert c.restore_action_id == "he_actuation_restore"
    assert c.semantic == "he.actuation"
    assert c.candidate_reports == [9]
    
    # Offsets 5 (travel) and 7 (checksum)
    assert c.changed_offsets == [5, 7]
    assert c.semantic_offsets == [5]
    assert c.checksum_offsets == [7]
    
    assert c.baseline_available is True
    assert c.baseline_reports == ["0913001e0033008c"]
    assert c.change_reports == ["0913001e004b0074"]
    assert c.restore_reports == ["0913001e0033008c"]
    assert c.restore_matches_original is True
    
    assert len(c.transitions) == 2
    t_travel = next(t for t in c.transitions if t.offset == 5)
    assert t_travel.before == "33"
    assert t_travel.changed == "4b"
    assert t_travel.restored == "33"
    assert t_travel.field_role == "semantic_field"
    
    t_checksum = next(t for t in c.transitions if t.offset == 7)
    assert t_checksum.before == "8c"
    assert t_checksum.changed == "74"
    assert t_checksum.restored == "8c"
    assert t_checksum.field_role == "checksum_candidate"


def test_fake_zero_baseline_is_forbidden_when_no_real_baseline_exists():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    # NO idle baseline recorded
    
    action_change = GuidedAction(
        action_id="light_effect_change",
        category="vendor_experiment",
        instruction="Change RGB",
        expected_semantic={"setting": "light.effect"},
        started_at=10.0,
        finished_at=12.0,
    )
    obs.set_active_action("light_effect_change")
    obs.record_event("sendFeatureReport", "feature_out", 9, "0904010200000007", "browser.exe")
    obs.set_active_action(None)

    action_restore = GuidedAction(
        action_id="light_effect_restore",
        category="vendor_restore",
        instruction="Restore RGB",
        expected_semantic={"setting": "light.effect", "restore": True},
        started_at=13.0,
        finished_at=15.0,
        restore_attempted=True,
    )
    obs.set_active_action("light_effect_restore")
    obs.record_event("sendFeatureReport", "feature_out", 9, "0904010100000006", "browser.exe")
    obs.set_active_action(None)

    candidates = obs.correlate_actions([action_change, action_restore])
    assert len(candidates) == 1
    c = candidates[0]
    # Baseline must be empty, not synthetic zero string
    assert c.baseline_available is False
    assert c.baseline_reports == []
    assert c.restore_matches_original is True


def test_rt_press_release_ambiguity_does_not_over_promote_confidence():
    obs = PassiveTransportObserver(target_vid="372E", target_pid="103E")
    rt_table_b = "0919" + "0202020202020202" * 7 + "000000000000"
    rt_table_a = "0919" + "0101010101010101" * 7 + "000000000000"

    act_change = GuidedAction(
        action_id="he_rt_press_change",
        category="vendor_experiment",
        instruction="Change RT press",
        expected_semantic={"setting": "he.rt.press"},
        started_at=10.0,
        finished_at=12.0,
    )
    obs.set_active_action("he_rt_press_change")
    obs.record_event("sendFeatureReport", "feature_out", 9, rt_table_b, "browser.exe")
    obs.set_active_action(None)

    act_restore = GuidedAction(
        action_id="he_rt_press_restore",
        category="vendor_restore",
        instruction="Restore RT press",
        expected_semantic={"setting": "he.rt.press", "restore": True},
        started_at=13.0,
        finished_at=15.0,
        restore_attempted=True,
    )
    obs.set_active_action("he_rt_press_restore")
    obs.record_event("sendFeatureReport", "feature_out", 9, rt_table_a, "browser.exe")
    obs.set_active_action(None)

    candidates = obs.correlate_actions([act_change, act_restore])
    assert len(candidates) == 1
    c = candidates[0]
    assert "rt_table" in c.semantic
    assert c.notes is not None
    assert "Composite" in c.notes
    assert c.confidence == "CommunityGuidedObservation"


def test_quality_score_requires_real_evidence_and_zero_traffic_cap():
    wizard = CommunityResearchWizard(is_demo=False)
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


def test_wizard_demo_run_produces_valid_json_with_compact_structure():
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
        assert "browser_webhid_api_observer" in data["capture"]["mechanism"]
        assert len(data["correlations"]) >= 1
        
        # Verify correlations structure
        c0 = data["correlations"][0]
        assert "change_action_id" in c0
        assert "transitions" in c0
        assert "restore_matches_original" in c0


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
