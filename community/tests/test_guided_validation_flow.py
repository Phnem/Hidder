"""Comprehensive test suite for Guided Hardware Validation Flow.

Verifies:
- Guided capability validators (Lighting, Remap, Hall Effect, Mechanical Digital, Read-Only Inventory)
- Observable listener taxonomy (Human physical observable vs OS device-correlated vs simulated)
- Safe key constraints (NEVER remap critical/system keys)
- Mechanical keyboards NEVER receive partial-depth or analog actuation tests
- Hall Effect keyboards receive actuation threshold validation
- Immediate rollback on visual or input observable failure
- Durable DeviceValidationCertificate generation, storage, and fingerprint/firmware matching
- Certificate invalidation on identity/firmware change
- GitHub issue URL generation and template handoff
- Rollback-first safety and fail-closed state management
"""

import json
import os
import tempfile
import urllib.parse
from pathlib import Path

import pytest

from community.vetro_probe.bundle import production_bundle_for_hero84
from community.vetro_probe.device_certificate import (
    DeviceValidationCertificate, CertificateStore, SCHEMA_DEVICE_CERTIFICATE,
)
from community.vetro_probe.guided_validator import (
    GuidedValidationEngine, GuidedValidationContext,
    LightingCapabilityValidator, RemapCapabilityValidator,
    HallEffectCapabilityValidator, MechanicalKeyboardValidator,
    ReadOnlyInventoryValidator,
    FORBIDDEN_REMAP_KEYS,
    STATE_VALIDATED, STATE_VALIDATION_FAILED,
)
from community.vetro_probe.gui_rpc import make_github_issue_url
from community.vetro_probe.observable import (
    ObservableRequest, ObservableResult,
    HumanConfirmationListener, FakeObservableListener,
)
from community.vetro_probe.transport import FakeTransport


def _mock_hero84_transport(initial_state: dict | None = None) -> FakeTransport:
    init = initial_state or {
        "light.brightness": 50,
        "light.global_color": {"r": 255, "g": 0, "b": 0},
        "keyboard.remap": {"source": "Insert", "target": "Insert"},
        "he.actuation": 18,
        "he.deadzone": 2,
        "keyboard.polling": 1000,
        "device.win_lock": False,
        "keyboard.profile": 1,
    }
    return FakeTransport(initial_state=init)


class TestGuidedValidationFlow:
    """Guided Hardware Validation Unit & Flow Tests."""

    def test_lighting_validation_success_with_human_observable(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport({"light.brightness": 40})
        human_listener = HumanConfirmationListener(auto_response="yes")

        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=human_listener,
            device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_kb_v3_wired"},
        )

        validator = LightingCapabilityValidator()
        assert validator.is_applicable(ctx) is True

        res = validator.validate(ctx)
        assert res.passed is True
        assert res.rollback_verified is True
        assert res.evidence.status == "PASS"
        assert res.evidence.observable_pass is True
        assert res.evidence.observable_source == "human_physical_observable"
        # Verify state was restored to baseline (40)
        val, _ = transport.get("light.brightness")
        assert val == 40

    def test_lighting_validation_fails_when_user_says_no_and_rolls_back(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport({"light.brightness": 60})
        # User says lighting did not change
        human_listener = HumanConfirmationListener(auto_response="no")

        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=human_listener,
            device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_kb_v3_wired"},
        )

        validator = LightingCapabilityValidator()
        res = validator.validate(ctx)

        assert res.passed is False
        assert "Visual check failed" in res.error
        assert res.rollback_verified is True
        # Verify rollback restored original baseline value (60)
        val, _ = transport.get("light.brightness")
        assert val == 60

    def test_successful_remap_validation_with_safe_secondary_key(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport()
        # Fake OS listener that expects 'A' on step 1, then 'Insert' on step 2
        fake_os = FakeObservableListener(expectations={
            "press_key:A": {"vk": 0x41, "target": "A"},
            "press_key:Insert": {"vk": 0x2D, "target": "Insert"},
        })

        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=fake_os,
            device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_kb_v3_wired"},
        )

        # On AULA physical hardware, remap is blocked by missing strong E5 observable
        validator = RemapCapabilityValidator()
        assert validator.is_applicable(ctx) is False

        # Directly testing validator execution on mock transport
        res = validator.validate(ctx)
        assert res.passed is True
        assert res.rollback_verified is True
        assert res.evidence.status == "PASS"

    def test_remap_fails_on_wrong_key_and_rolls_back(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport()
        # Fake OS listener where step 1 fails (wrong key or timeout)
        fake_os = FakeObservableListener(expectations={
            "press_key:A": {"fail": True, "error": "timeout - user pressed wrong key"},
        })

        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=fake_os,
            device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_kb_v3_wired"},
        )

        validator = RemapCapabilityValidator()
        res = validator.validate(ctx)

        assert res.passed is False
        assert "Remap input observable failed" in res.error
        assert res.rollback_verified is True

    def test_critical_keys_strictly_forbidden_from_remap(self):
        for bad_key in ["Esc", "Escape", "Enter", "Power", "Sleep", "Reset", "Win"]:
            assert bad_key in FORBIDDEN_REMAP_KEYS

    def test_hall_effect_validation_on_he_keyboard(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport({"he.actuation": 18})
        fake_os = FakeObservableListener(expectations={
            "he_press:W": {"travel": 2.5, "target": "W"},
        })

        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=fake_os,
            device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_kb_v3_wired"},
        )

        validator = HallEffectCapabilityValidator()
        # On AULA physical hardware, he.actuation is blocked pending independent threshold observable
        assert validator.is_applicable(ctx) is False

        # Directly testing validator execution on mock transport
        res = validator.validate(ctx)
        assert res.passed is True
        assert res.rollback_verified is True
        assert res.evidence.status == "PASS"
        # Verify baseline restored
        val, _ = transport.get("he.actuation")
        assert val == 18

    def test_mechanical_keyboard_never_receives_partial_depth_instructions(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport()
        fake_os = FakeObservableListener()

        # Device is a standard mechanical keyboard (NOT Hall Effect)
        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=fake_os,
            device_identity={"vendor": "Standard", "name": "Mechanical Keyboard Pro", "vid": "0x1234", "pid": "0x5678", "firmware": "1.0", "family": "standard_mechanical_kb"},
        )

        he_validator = HallEffectCapabilityValidator()
        mech_validator = MechanicalKeyboardValidator()

        # HE validator MUST REFUSE to run on mechanical keyboard
        assert he_validator.is_applicable(ctx) is False

        # Mechanical validator is applicable and only checks digital capabilities
        assert mech_validator.is_applicable(ctx) is True
        res = mech_validator.validate(ctx)
        assert res.passed is True
        assert res.capability == "keyboard.digital"

    def test_guided_validation_engine_complete_pass_flow_and_certificate(self):
        with tempfile.TemporaryDirectory() as td:
            cert_store = CertificateStore(base_dir=Path(td))
            engine = GuidedValidationEngine(cert_store=cert_store)

            bundle = production_bundle_for_hero84()
            transport = _mock_hero84_transport()
            fake_os = FakeObservableListener(auto_pass=True)

            ctx = GuidedValidationContext(
                bundle=bundle,
                transport=transport,
                observable_listener=fake_os,
                device_identity={
                    "vendor": "AULA",
                    "name": "HERO 84 HE",
                    "vid": "0x372E",
                    "pid": "0x103E",
                    "descriptor_hash": "desc-hero84-test",
                    "firmware": "0216",
                    "family": "aula_kb_v3_wired",
                    "connection": "wired",
                },
                build_commit="78b4510",
                app_version="0.3.1",
            )

            result = engine.run_validation(ctx)

            assert result["state"] == STATE_VALIDATED
            assert result["validated_groups"] == ["lighting.brightness"]
            assert "he.actuation" not in result["validated_groups"]
            assert "inventory" not in result["validated_groups"]
            assert "keyboard.remap" not in result["validated_groups"]

            cert = result["certificate"]
            assert cert["schema"] == SCHEMA_DEVICE_CERTIFICATE
            assert cert["terminal_verdict"] == "COMPLETE_PASS"
            assert cert["identity"]["vid"] == "0x372E"
            assert cert["identity"]["pid"] == "0x103E"
            assert cert["identity"]["firmware_branch"] == "0216"

            # Check that certificate was persistently saved and can be found
            found = cert_store.find_matching(
                vid="0x372E",
                pid="0x103E",
                firmware_branch="0216",
                descriptor_hash="desc-hero84-test",
            )
            assert found is not None
            assert found.terminal_verdict == "COMPLETE_PASS"

    def test_certificate_invalidation_on_firmware_or_descriptor_change(self):
        with tempfile.TemporaryDirectory() as td:
            cert_store = CertificateStore(base_dir=Path(td))
            cert = DeviceValidationCertificate(
                vendor="AULA",
                model="HERO 84 HE",
                vid="0x372E",
                pid="0x103E",
                descriptor_hash="desc-hero84-orig",
                firmware_branch="0216",
                final_state_verified=True,
                terminal_verdict="COMPLETE_PASS",
            )
            cert_store.save(cert)

            # Same firmware -> matches
            assert cert_store.find_matching(vid="0x372E", pid="0x103E", firmware_branch="0216", descriptor_hash="desc-hero84-orig") is not None

            # Different firmware -> MUST NOT match
            assert cert_store.find_matching(vid="0x372E", pid="0x103E", firmware_branch="0999", descriptor_hash="desc-hero84-orig") is None

            # Different descriptor hash -> MUST NOT match
            assert cert_store.find_matching(vid="0x372E", pid="0x103E", firmware_branch="0216", descriptor_hash="desc-different-hw") is None

    def test_engine_stops_mutations_immediately_on_first_failure(self):
        with tempfile.TemporaryDirectory() as td:
            cert_store = CertificateStore(base_dir=Path(td))
            engine = GuidedValidationEngine(cert_store=cert_store)

            bundle = production_bundle_for_hero84()
            transport = _mock_hero84_transport({"light.brightness": 50})
            # Visual check fails
            human_listener = HumanConfirmationListener(auto_response="no")

            ctx = GuidedValidationContext(
                bundle=bundle,
                transport=transport,
                observable_listener=human_listener,
                device_identity={"vendor": "AULA", "name": "HERO 84 HE", "vid": "0x372E", "pid": "0x103E", "firmware": "0216", "family": "aula_he_v3"},
            )

            result = engine.run_validation(ctx)

            assert result["state"] == STATE_VALIDATION_FAILED
            assert result["verdict"] == "FAILED"
            assert result["failed_capability"] == "lighting.brightness"
            assert result["rollback_verified"] is True
            # Verified baseline was preserved
            val, _ = transport.get("light.brightness")
            assert val == 50

    def test_github_issue_url_generation(self):
        url = make_github_issue_url(
            brand="EPOMAKER",
            model="TH99",
            connection="Wired USB",
            firmware="1.0.4",
            vid="0x3151",
            pid="0x4015",
            app_version="0.3.1",
            build_commit="78b4510",
            run_id="run_12345",
            failure_category="Lighting validation failed",
            observed="Lighting stayed white instead of green.",
        )

        assert url.startswith("https://github.com/Phnem/Hidder/issues/new?")
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        assert "[Compatibility] EPOMAKER TH99" in params["title"][0]
        assert "compatibility,device-report" in params["labels"][0]
        assert "0x3151:0x4015" in params["body"][0]
        assert "run_12345" in params["body"][0]
        assert "Lighting stayed white" in params["body"][0]
        # Verify no local Windows paths leaked in URL
        assert "C:\\" not in url
        assert "D:\\" not in url

    def test_brightness_certificate_cannot_expose_global_color_or_effect(self):
        cert = DeviceValidationCertificate(
            vendor="AULA",
            model="HERO 84 HE",
            vid="0x372E",
            pid="0x103E",
            firmware_branch="0216",
            validated_capability_groups=["lighting.brightness"],
            final_state_verified=True,
            terminal_verdict="COMPLETE_PASS",
        )

        # Brightness is validated
        assert cert.authorizes_operation("light.brightness") is True

        # Global color is NOT validated -> MUST NOT be authorized
        assert cert.authorizes_operation("light.global_color") is False

        # Lighting effect is NOT validated -> MUST NOT be authorized
        assert cert.authorizes_operation("light.effect") is False

        # Per-key custom RGB is NOT validated -> MUST NOT be authorized
        assert cert.authorizes_operation("custom.per_key") is False

    def test_actuation_and_deadzone_certificates_cannot_expose_rapid_trigger(self):
        cert_act = DeviceValidationCertificate(
            vendor="AULA",
            model="HERO 84 HE",
            vid="0x372E",
            pid="0x103E",
            firmware_branch="0216",
            validated_capability_groups=["he.actuation"],
            final_state_verified=True,
            terminal_verdict="COMPLETE_PASS",
        )

        # Actuation is authorized
        assert cert_act.authorizes_operation("he.actuation") is True
        # Deadzone is NOT authorized by actuation alone
        assert cert_act.authorizes_operation("he.deadzone") is False
        # Rapid trigger is NOT authorized
        assert cert_act.authorizes_operation("he.rt") is False

        cert_dead = DeviceValidationCertificate(
            vendor="AULA",
            model="HERO 84 HE",
            vid="0x372E",
            pid="0x103E",
            firmware_branch="0216",
            validated_capability_groups=["he.deadzone"],
            final_state_verified=True,
            terminal_verdict="COMPLETE_PASS",
        )
        assert cert_dead.authorizes_operation("he.deadzone") is True
        assert cert_dead.authorizes_operation("he.actuation") is False
        assert cert_dead.authorizes_operation("he.rt") is False

    def test_inventory_pass_alone_cannot_unlock_any_mutation(self):
        cert_inv = DeviceValidationCertificate(
            vendor="AULA",
            model="HERO 84 HE",
            vid="0x372E",
            pid="0x103E",
            firmware_branch="0216",
            validated_capability_groups=[],
            inventory_evidence={"descriptor": "raw_hid_report"},
            final_state_verified=True,
            terminal_verdict="COMPLETE_PASS",
        )

        # Inventory evidence must NEVER unlock any mutation
        assert cert_inv.authorizes_operation("light.brightness") is False
        assert cert_inv.authorizes_operation("he.actuation") is False
        assert cert_inv.authorizes_operation("keyboard.remap") is False
        assert cert_inv.authorizes_operation("keyboard.polling") is False

    def test_unknown_switch_technology_never_receives_analog_plan(self):
        bundle = production_bundle_for_hero84()
        transport = _mock_hero84_transport()
        fake_os = FakeObservableListener()

        # Device with unknown / unspecified switch technology
        ctx = GuidedValidationContext(
            bundle=bundle,
            transport=transport,
            observable_listener=fake_os,
            device_identity={"vendor": "UnknownVendor", "name": "Generic KB", "vid": "0x9999", "pid": "0x8888", "firmware": "1.0", "family": "unknown_family"},
        )

        he_validator = HallEffectCapabilityValidator()
        assert he_validator.is_applicable(ctx) is False

    def test_rebuild_does_not_invalidate_hardware_proof_if_scope_unchanged(self):
        cert = DeviceValidationCertificate(
            vendor="AULA",
            model="HERO 84 HE",
            vid="0x372E",
            pid="0x103E",
            descriptor_hash="desc-orig",
            firmware_branch="0216",
            build_commit="commit_aaa",
            final_state_verified=True,
            terminal_verdict="COMPLETE_PASS",
        )

        # Same hardware scope with new application rebuild -> still valid
        assert cert.is_valid_for(
            vid="0x372E",
            pid="0x103E",
            descriptor_hash="desc-orig",
            firmware_branch="0216",
            connection_mode="wired",
        ) is True

    def test_run_guided_validation_real_branch_with_injected_transport(self, monkeypatch):
        from community.vetro_probe.cli import run_guided_validation
        from community.vetro_probe.identity import PhysicalInstance

        mock_instance = PhysicalInstance(
            vid="0x372E",
            pid="0x103E",
            descriptor_hash="desc-test-hero84",
            firmware_version="0216",
            connection_mode="wired",
            interfaces=[0, 1, 2],
            report_ids=[1, 2, 4],
            product_string="AULA HERO84 HE",
            manufacturer="AULA",
        )
        mock_transport = _mock_hero84_transport({"light.brightness": 10})

        write_calls = []
        orig_set = mock_transport.set
        def tracking_set(op, val):
            write_calls.append((op, val))
            return orig_set(op, val)
        mock_transport.set = tracking_set

        monkeypatch.setattr("community.vetro_probe.cli._create_real_transport", lambda b: (mock_transport, mock_instance))

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "run_out"
            rc = run_guided_validation(run_dir=out_dir, use_real=True, auto_confirm=True)
            assert rc == 0

            # Verified certificate produced with lighting.brightness
            summary = json.loads((out_dir / "guided_validation_summary.json").read_text(encoding="utf-8"))
            assert summary["verdict"] == "COMPLETE_PASS"
            assert summary["validated_groups"] == ["lighting.brightness"]
            assert summary["rollback_verified"] is True

            # Exactly 2 writes: 1 test mutation (brightness 5) and 1 rollback (brightness 10)
            assert len(write_calls) == 2
            assert write_calls[0][0] == "light.brightness"
            assert write_calls[1][0] == "light.brightness"

    def test_run_guided_validation_real_branch_fails_closed_on_identity_mismatch(self, monkeypatch):
        from community.vetro_probe.cli import run_guided_validation
        from community.vetro_probe.identity import PhysicalInstance

        # Wrong VID / PID (e.g. 0x9999:0x8888)
        bad_instance = PhysicalInstance(
            vid="0x9999",
            pid="0x8888",
            descriptor_hash="desc-test-bad",
            firmware_version="0216",
            connection_mode="wired",
            interfaces=[0],
            report_ids=[1],
        )
        mock_transport = _mock_hero84_transport({"light.brightness": 10})
        write_calls = []
        mock_transport.set = lambda op, val: write_calls.append((op, val))

        monkeypatch.setattr("community.vetro_probe.cli._create_real_transport", lambda b: (mock_transport, bad_instance))

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "run_out"
            rc = run_guided_validation(run_dir=out_dir, use_real=True, auto_confirm=True)
            assert rc == 1
            # STRICT REQUIREMENT: ZERO physical writes occurred
            assert len(write_calls) == 0

    def test_run_guided_validation_real_branch_fails_closed_on_firmware_mismatch(self, monkeypatch):
        from community.vetro_probe.cli import run_guided_validation
        from community.vetro_probe.identity import PhysicalInstance

        # Unsupported firmware branch
        bad_fw_instance = PhysicalInstance(
            vid="0x372E",
            pid="0x103E",
            descriptor_hash="desc-test-badfw",
            firmware_version="9999",
            connection_mode="wired",
            interfaces=[0],
            report_ids=[1],
        )
        mock_transport = _mock_hero84_transport({"light.brightness": 10})
        write_calls = []
        mock_transport.set = lambda op, val: write_calls.append((op, val))

        monkeypatch.setattr("community.vetro_probe.cli._create_real_transport", lambda b: (mock_transport, bad_fw_instance))

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "run_out"
            rc = run_guided_validation(run_dir=out_dir, use_real=True, auto_confirm=True)
            assert rc == 1
            # STRICT REQUIREMENT: ZERO physical writes occurred
            assert len(write_calls) == 0

    def test_run_guided_validation_real_branch_fails_closed_on_connection_error(self, monkeypatch):
        from community.vetro_probe.cli import run_guided_validation

        def failing_connect(bundle):
            raise RuntimeError("Device node not found / busy")

        monkeypatch.setattr("community.vetro_probe.cli._create_real_transport", failing_connect)

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "run_out"
            rc = run_guided_validation(run_dir=out_dir, use_real=True, auto_confirm=True)
            assert rc == 1

    def test_real_hardware_validation_milestone_certificate_fixture(self):
        fixture_path = Path(__file__).parent.parent / "vetro_probe" / "fixtures" / "real_runs" / "aula_hero84_fw0216_guided_cert.json"
        assert fixture_path.is_file()

        cert_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        cert = DeviceValidationCertificate.from_dict(cert_data)

        # Exact physical provenance
        assert cert.schema == SCHEMA_DEVICE_CERTIFICATE
        assert cert.certificate_id == "cert-efd256a44797"
        assert cert.terminal_verdict == "COMPLETE_PASS"
        assert cert.vid == "0x372E"
        assert cert.pid == "0x103E"
        assert cert.firmware_branch == "0216"
        assert cert.connection_mode == "wired"
        assert cert.protocol_family == "aula_kb_v3_wired"
        assert cert.final_state_verified is True

        # Validated capability groups strictly limited to lighting.brightness
        assert cert.validated_capability_groups == ["lighting.brightness"]
        assert cert.authorizes_operation("light.brightness") is True

        # Other capabilities strictly remain unauthorized
        assert cert.authorizes_operation("light.global_color") is False
        assert cert.authorizes_operation("light.effect") is False
        assert cert.authorizes_operation("he.actuation") is False
        assert cert.authorizes_operation("he.deadzone") is False
        assert cert.authorizes_operation("he.rt") is False
        assert cert.authorizes_operation("keyboard.remap") is False

        # Human physical observable evidence is recorded
        assert len(cert.observables) == 1
        obs = cert.observables[0]
        assert obs["capability"] == "lighting.brightness"
        assert obs["source"] == "human_physical_observable"
        assert obs["ok"] is True
