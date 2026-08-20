import json
from pathlib import Path

import dpkt
import pytest

from ingest.full_reingest import FullTypedReprocessor
from ingest.miner_import import MinerImportError, import_miner_evidence
from ingest.normalize.evidence import DeviceIdentifierFact, EvidenceLevel
from ingest.repair_pass2 import RepairPass2
from ingest.storage.database import RegistryDatabase


def _db(tmp_path: Path) -> RegistryDatabase:
    (tmp_path / "data").mkdir(exist_ok=True)
    return RegistryDatabase(tmp_path / "data" / "registry.sqlite")


def _fixture(tmp_path: Path, root: str, files: dict[str, str | bytes]) -> RegistryDatabase:
    db = _db(tmp_path)
    directory = tmp_path / "sources" / root
    directory.mkdir(parents=True)
    for name, value in files.items():
        path = directory / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
    FullTypedReprocessor(db.db_path, tmp_path).run()
    return db


def _insert_operation(db: RegistryDatabase, key: str, semantic: str, direction: str,
                      persistence: str | None = None, risk_state: str | None = None) -> int:
    with db.connection() as conn:
        conn.execute("""INSERT INTO protocol_operations(
            operation_key,scope_type,scope_key,product_id,protocol_family,semantic,transport,
            api_semantics,report_id,api_length,direction,request_encoding_json,
            response_encoding_json,checksum_json,sequencing_json,initialization_json,
            capability_mapping_json,dynamic_fields_json,persistence,risk_state)
            VALUES(?, 'device', 'product:1', 1, 'fixture', ?, 'hid', 'hidapi', '0x01', 65, ?,
                   '[1]', '{"state":"not_applicable"}', '{"state":"not_applicable"}',
                   '{"timing_state":"not_applicable"}', '{"state":"not_applicable"}',
                   '{"fixture":true}', '{"state":"known","fields":[]}', ?, ?)""",
            (key, semantic, direction, persistence, risk_state))
        operation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO operation_evidence(operation_id,extraction_method,trust_class,lineage_group,confidence) VALUES(?,'test','OfficialSpecification','fixture',1)", (operation_id,))
    return operation_id


def test_family_scope_separation(tmp_path: Path):
    db = _fixture(tmp_path, "signalrgb-one", {"a.js": "function SetRgb(v){device.write([v],65);}"})
    second = tmp_path / "sources" / "signalrgb-two"; second.mkdir()
    (second / "a.js").write_text("// independently formatted\nfunction SetRgb(v) { device.write([v], 65); }")
    FullTypedReprocessor(db.db_path, tmp_path).run()
    with db.connection() as conn:
        assert conn.execute("SELECT count(DISTINCT scope_key) FROM protocol_operations").fetchone()[0] == 2


def test_lineage_exact_duplicate_is_not_independent(tmp_path: Path):
    db = _fixture(tmp_path, "signalrgb-one", {"a.js": "function SetRgb(v){device.write([v],65);}"})
    second = tmp_path / "sources" / "signalrgb-two"; second.mkdir()
    (second / "copy.js").write_text("function SetRgb(v){device.write([v],65);}")
    FullTypedReprocessor(db.db_path, tmp_path).run()
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM source_files WHERE parse_status='duplicate_or_derived'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM source_lineage WHERE relationship='copied'").fetchone()[0] == 1


def test_module_scope_prevents_cross_device_operation_merge(tmp_path: Path):
    db = _fixture(tmp_path, "signalrgb-official-plugins", {
        "Plugins/Nzxt/Mouse.js": "function SaveDPIToFlash(v){device.write([v],65);}",
        "Plugins/Steelseries/Mouse.js": "function SetDpi(v){device.write([v],65);}",
    })
    RepairPass2(db.db_path).derive_risk_and_reconstructibility()
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM protocol_operations").fetchone()[0] == 2
        assert conn.execute("SELECT count(DISTINCT scope_key) FROM protocol_operations").fetchone()[0] == 2
        assert dict(conn.execute("SELECT risk_class,count(*) FROM command_risks GROUP BY risk_class").fetchall()) == {
            "destructive": 1, "volatile_write": 1,
        }


def test_flash_deal_report_npi_enum_and_struct_are_not_operations(tmp_path: Path):
    db = _fixture(tmp_path, "noise", {"noise.c": """
        enum Report_NPI { Report_NPI_NON = 0, FLASH_DEAL = 1 };
        struct command_report { uint8_t opcode; uint8_t value; };
        const char *marketing = "NY FLASH DEAL";
    """})
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM protocol_operations").fetchone()[0] == 0
    assert RepairPass2(db.db_path).derive_risk_and_reconstructibility().get("destructive", 0) == 0


@pytest.mark.parametrize(("semantic", "direction", "persistence", "risk_state", "expected"), [
    ("battery.get", "device_to_host", None, None, "read_only"),
    ("lighting.set_rgb", "host_to_device", None, None, "volatile_write"),
    ("profile.save", "host_to_device", "persistent", None, "persistent_write"),
    ("firmware.dfu", "host_to_device", None, "destructive", "destructive"),
])
def test_typed_risk_classes(tmp_path: Path, semantic: str, direction: str,
                            persistence: str | None, risk_state: str | None, expected: str):
    db = _db(tmp_path)
    vendor = db.get_or_create_vendor("fixture", "Fixture")
    product, _ = db.upsert_product(vendor, "Fixture Mouse", "Fixture Mouse", "mouse", "fixture")
    assert product == 1
    operation_id = _insert_operation(db, expected, semantic, direction, persistence, risk_state)
    RepairPass2(db.db_path).derive_risk_and_reconstructibility()
    with db.connection() as conn:
        assert conn.execute("SELECT risk_class FROM command_risks WHERE operation_id=?", (operation_id,)).fetchone()[0] == expected


def test_webhid_and_hidapi_report_id_semantics(tmp_path: Path):
    db = _fixture(tmp_path, "signalrgb-protocol", {"reports.js": """
        function SetRgb(value) { device.sendReport(0x07, new Uint8Array([value])); }
        function SetDpi(value) { hid_send_feature_report(dev, buffer, 65); }
    """})
    with db.connection() as conn:
        rows = conn.execute("SELECT api_semantics,report_id_in_buffer FROM protocol_operations ORDER BY semantic").fetchall()
    assert {tuple(row) for row in rows} == {("WebHID sendReport", 0), ("hidapi feature report", 1)}


def test_packed_and_nested_struct_validation(tmp_path: Path):
    db = _fixture(tmp_path, "structs", {"packet.h": """
        struct inner_report { uint8_t a; uint8_t b; };
        static_assert(sizeof(inner_report) == 2);
        struct command_packet { struct inner_report inner; uint8_t opcode; };
        static_assert(sizeof(command_packet) == 3);
    """})
    with db.connection() as conn:
        row = conn.execute("SELECT struct_size,validation_status FROM packet_layouts WHERE layout_name='command_packet'").fetchone()
        validation = conn.execute("SELECT calculated_size,upstream_size,status FROM struct_validations WHERE struct_name='command_packet'").fetchone()
    assert tuple(row) == (3, "validated_static_assert")
    assert tuple(validation) == (3, 3, "validated")


@pytest.mark.parametrize(("name", "code"), [
    ("builder.js", "function SetRgb(value){device.write([0x01, value << 1],65);}"),
    ("builder.cpp", "void set_rgb(int value){hid_write(dev, buffer, 65);}"),
    ("builder.cs", "public void SetRgb(int value) { hidDevice.Write(buffer); }"),
    ("builder.py", "def set_dpi(value):\n    hid_device.write([0x01, value])\n"),
])
def test_multilanguage_procedural_builders(tmp_path: Path, name: str, code: str):
    db = _fixture(tmp_path, "builders", {name: code})
    with db.connection() as conn:
        row = conn.execute("SELECT semantic,dynamic_fields_json FROM protocol_operations").fetchone()
    assert row is not None
    assert json.loads(row[1])["state"] == "known"


def test_capture_creates_observations_not_operations(tmp_path: Path):
    capture = tmp_path / "capture.pcap"
    with capture.open("wb") as stream:
        writer = dpkt.pcap.Writer(stream)
        writer.writepkt(b"\x01\x02\x03", ts=1.0); writer.writepkt(b"\x04\x05", ts=2.0)
        writer.close()
    data = capture.read_bytes(); capture.unlink()
    db = _fixture(tmp_path, "g933-utils", {"notes/set_light_solid.pcap": data})
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM capture_transactions").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM runtime_observations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM protocol_operations").fetchone()[0] == 0


def test_protocol_miner_refuses_untyped_trace(tmp_path: Path):
    db = _db(tmp_path)
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({"observations": [{"kind": "trace.raw", "value": {"bytes": "aabb"}}]}))
    with pytest.raises(MinerImportError, match="identity"):
        import_miner_evidence(db, path)


def test_production_safety_never_promoted(tmp_path: Path):
    db = _fixture(tmp_path, "signalrgb-safe", {"a.js": "function SetRgb(v){device.write([v],65);}"})
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM protocol_operations WHERE production_safe != 0").fetchone()[0] == 0


def test_unknown_contract_fields_prevent_ready(tmp_path: Path):
    db = _db(tmp_path)
    vendor = db.get_or_create_vendor("fixture", "Fixture")
    product, _ = db.upsert_product(vendor, "Fixture", "Fixture", "mouse", "fixture")
    db.upsert_device_identifier(DeviceIdentifierFact(product_id=product, vid=1, pid=2, vid_hex="0x0001", pid_hex="0x0002", evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY))
    _insert_operation(db, "unknown", "mouse.dpi", "host_to_device")
    with db.connection() as conn:
        conn.execute("UPDATE protocol_operations SET response_encoding_json='{\"state\":\"unknown\"}'")
    result = RepairPass2(db.db_path).derive_risk_and_reconstructibility()
    assert result.get("IMPLEMENTATION_READY", 0) == 0
