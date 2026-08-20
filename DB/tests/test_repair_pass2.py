from pathlib import Path

from ingest.repair_pass2 import RepairPass2
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import DeviceIdentifierFact, EvidenceLevel, GenericFact, ProtocolHintFact, RawSource, SourceType


def test_untyped_text_never_becomes_risky_or_ready(tmp_path: Path):
    db = RegistryDatabase(tmp_path / "registry.sqlite")
    vendor = db.get_or_create_vendor("aula", "AULA")
    product, _ = db.upsert_product(vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he")
    db.upsert_device_identifier(DeviceIdentifierFact(product_id=product, vid=0x372E, pid=0x103E, vid_hex="0x372E", pid_hex="0x103E", evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY))
    db.upsert_protocol_hint(ProtocolHintFact(product_id=product, hint_key="protocol_family", hint_value="WebHID"))
    db.upsert_generic_fact(GenericFact(product_id=product, key="marketing_copy", value="NY FLASH DEAL"))
    db.upsert_generic_fact(GenericFact(product_id=product, key="openrgb_packet_layouts", value="{}"))

    result = RepairPass2(db.db_path).derive_risk_and_reconstructibility()
    assert result["IDENTITY_AND_CAPABILITIES"] == 1
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM command_risks").fetchone()[0] == 0


def test_complete_typed_hero_operation_can_be_ready(tmp_path: Path):
    db = RegistryDatabase(tmp_path / "registry.sqlite")
    vendor = db.get_or_create_vendor("aula", "AULA")
    product, _ = db.upsert_product(vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he")
    db.upsert_device_identifier(DeviceIdentifierFact(product_id=product, vid=0x372E, pid=0x103E, vid_hex="0x372E", pid_hex="0x103E", evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY))
    with db.connection() as conn:
        conn.execute("""INSERT INTO protocol_operations(operation_key,scope_type,scope_key,product_id,protocol_family,semantic,transport,api_semantics,report_id,api_length,wire_length,direction,request_encoding_json,response_encoding_json,checksum_json,sequencing_json,initialization_json,capability_mapping_json,confidence,source_trust,operation_status)
        VALUES('test:hero-actuation', 'device', ?, ?, 'AulaHub', 'keyboard.actuation', 'hid', 'WebHID feature report', '0x01', 65, 64, 'host_to_device', '[1,2]', '{"state":"not_applicable"}', '{"state":"not_applicable"}', '{"timing_state":"not_applicable"}', '{"state":"not_applicable"}', '{"actuation":true}', .9, 'VerifiedVendorArtifact', 'observed')""", (f"product:{product}", product))
        op_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE protocol_operations SET dynamic_fields_json=? WHERE id=?", ('{"state":"known","fields":[]}', op_id))
        conn.execute("INSERT INTO operation_evidence(operation_id,extraction_method,trust_class,lineage_group,confidence) VALUES(?,'test','VerifiedVendorArtifact','test-independent',1.0)", (op_id,))
    result = RepairPass2(db.db_path).derive_risk_and_reconstructibility()
    assert result["IMPLEMENTATION_READY"] == 1


def test_same_device_fact_is_one_node_with_two_source_evidence(tmp_path: Path):
    db = RegistryDatabase(tmp_path / "registry.sqlite")
    vendor = db.get_or_create_vendor("aula", "AULA")
    product, _ = db.upsert_product(vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he")
    source_a = db.record_source(RawSource(url="https://example.test/a", source_type=SourceType.OTHER, vendor="aula", content_hash="a"))
    source_b = db.record_source(RawSource(url="https://example.test/b", source_type=SourceType.OTHER, vendor="aula", content_hash="b"))
    db.upsert_generic_fact(GenericFact(product_id=product, key="protocol.family", value="AulaHub", source_id=source_a))
    db.upsert_generic_fact(GenericFact(product_id=product, key="protocol.family", value="AulaHub", source_id=source_b))
    with db.connection() as conn:
        node = conn.execute("SELECT id FROM normalized_facts WHERE product_id=? AND canonical_key='protocol.family'", (product,)).fetchall()
        evidence = conn.execute("SELECT source_id FROM fact_evidence WHERE normalized_fact_id=? AND collector_name='registry_database'", (node[0]["id"],)).fetchall()
    assert len(node) == 1
    assert {row["source_id"] for row in evidence} == {source_a, source_b}
