"""Automated test suite for Protocol Miner community observation importer."""

import json
import tempfile
from pathlib import Path

import pytest

from miner.config import Settings
from miner.schemas.models import ConfidenceClass
from miner.storage.community_import import import_community_observation, CommunityImportError


def test_community_importer_valid_bundle():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(root=Path(tmpdir), cas_root=Path(tmpdir) / "cas")
        settings.ensure_directories()
        
        bundle_file = Path(tmpdir) / "PeripheralResearch-keyboard-test.json"
        bundle_data = {
            "schema": "peripheral.community-observation/1",
            "tool_version": "0.2.0",
            "submission_id": "comm-test-abc",
            "started_at": "2026-08-19T20:00:00Z",
            "finished_at": "2026-08-19T20:05:00Z",
            "completed": True,
            "is_demo": False,
            "device": {
                "category": "keyboard",
                "model_name": "AULA F75",
                "keyboard_type": "mechanical",
                "vid": "0x3151",
                "pid": "0x5025",
                "product_string": "AULA Gaming Keyboard",
                "manufacturer_string": "AULA",
            },
            "software": {
                "process_basename": "AULAHUB.exe"
            },
            "guided_actions": [],
            "transport_observations": [
                {
                    "timestamp": 100.0,
                    "process_basename": "AULAHUB.exe",
                    "api": "HidD_SetFeature",
                    "direction": "feature_out",
                    "report_id": 5,
                    "bytes_hex": "05220164",
                    "action_id": "light_effect_change",
                    "repeat_count": 1
                }
            ],
            "correlations": [
                {
                    "semantic": "light.effect",
                    "action_id": "light_effect_change",
                    "candidate_reports": [5],
                    "changed_offsets": [0, 1, 2],
                    "before_values": ["00000000"],
                    "after_values": ["05220164"],
                    "confidence": "CommunityGuidedObservation"
                }
            ],
            "quality": {"score": 90, "rating": "Excellent capture"},
            "privacy_scrubbed": True,
            "payload_sha256": "0" * 64
        }
        bundle_file.write_text(json.dumps(bundle_data), encoding="utf-8")
        
        res = import_community_observation(bundle_file, settings)
        assert res["observations_count"] >= 3
        assert res["correlations_count"] == 1
        assert res["model_name"] == "AULA F75"
        
        # Verify evidence.json contents and confidence
        run_dir = settings.workspace_dir / "runs" / res["run_id"]
        ev_data = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        for obs in ev_data["observations"]:
            assert obs["confidence"] != "HardwareVerifiedExchange"
            assert obs["confidence"] != "ProductionSafe"


def test_community_importer_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(root=Path(tmpdir), cas_root=Path(tmpdir) / "cas")
        settings.ensure_directories()
        
        bundle_file = Path(tmpdir) / "evil.json"
        bundle_data = {
            "schema": "peripheral.community-observation/1",
            "software": {
                "process_basename": "../../windows/system32/evil.exe"
            }
        }
        bundle_file.write_text(json.dumps(bundle_data), encoding="utf-8")
        
        with pytest.raises(CommunityImportError, match="Invalid process path"):
            import_community_observation(bundle_file, settings)


def test_community_importer_rejects_invalid_hex():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(root=Path(tmpdir), cas_root=Path(tmpdir) / "cas")
        settings.ensure_directories()
        
        bundle_file = Path(tmpdir) / "invalid_hex.json"
        bundle_data = {
            "schema": "peripheral.community-observation/1",
            "software": {"process_basename": "app.exe"},
            "device": {"category": "mouse", "model_name": "Test Mouse"},
            "transport_observations": [
                {
                    "api": "WriteFile",
                    "direction": "out",
                    "report_id": 0,
                    "bytes_hex": "NOT_A_VALID_HEX_STRING!@#$",
                }
            ]
        }
        bundle_file.write_text(json.dumps(bundle_data), encoding="utf-8")
        
        with pytest.raises(CommunityImportError, match="Invalid hex bytes"):
            import_community_observation(bundle_file, settings)
