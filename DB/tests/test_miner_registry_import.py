import json
from pathlib import Path

from ingest.miner_import import import_miner_evidence
from ingest.repair_pass2 import RepairPass2
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import DeviceIdentifierFact, EvidenceLevel


def test_complete_hero84_miner_operation_round_trips_to_registry(tmp_path: Path):
    db = RegistryDatabase(tmp_path / "registry.sqlite")
    vendor = db.get_or_create_vendor("aula", "AULA")
    product, _ = db.upsert_product(vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he")
    db.upsert_device_identifier(DeviceIdentifierFact(product_id=product, vid=0x372E, pid=0x103E, vid_hex="0x372E", pid_hex="0x103E", evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY))
    evidence = {"observations": [
        {"kind": "identity.vid_pid", "value": {"vid": 0x372E, "pid": 0x103E}},
        {"kind": "protocol.operation", "value": {"protocol_family": "AulaHub", "semantic": "keyboard.actuation", "transport": "hid", "api_semantics": "WebHID feature", "report_id": "0x01", "api_length": 65, "wire_length": 64, "direction": "host_to_device", "request_encoding": [1, 2], "response_encoding": {"state": "not_applicable"}, "checksum": {"state": "not_applicable"}, "sequencing": {"timing_state": "not_applicable"}, "initialization": {"state": "not_applicable"}, "dynamic_fields": {"state": "known", "fields": []}, "capability_mapping": {"actuation": True}}},
    ]}
    path = tmp_path / "hero84.evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    result = import_miner_evidence(db, path)
    assert result["product_id"] == product and result["operations"] == 1
    assert RepairPass2(db.db_path).derive_risk_and_reconstructibility()["IMPLEMENTATION_READY"] == 1
