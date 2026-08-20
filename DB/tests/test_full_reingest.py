from pathlib import Path

from ingest.full_reingest import FullTypedReprocessor
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import DeviceIdentifierFact, EvidenceLevel


def test_full_reingest_is_typed_scoped_and_idempotent(tmp_path: Path):
    (tmp_path / "data").mkdir()
    db = RegistryDatabase(tmp_path / "data" / "registry.sqlite")
    vendor = db.get_or_create_vendor("test", "Test")
    product, _ = db.upsert_product(vendor, "Test Mouse", "Test Mouse", "mouse", "testmouse")
    db.upsert_device_identifier(DeviceIdentifierFact(product_id=product,vid=0x1234,pid=0x5678,vid_hex="0x1234",pid_hex="0x5678",evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY))
    root = tmp_path / "sources" / "signalrgb-fixture"
    root.mkdir(parents=True)
    (root / "mouse.js").write_text("""
export function VendorId() { return 0x1234; }
export function ProductId() { return 0x5678; }
const CMD_SET_DPI = 0x13;
export function SetDpi(value) { device.write([0x01, CMD_SET_DPI, value], 65); }
""", encoding="utf-8")
    processor = FullTypedReprocessor(db.db_path, tmp_path)
    first = processor.run()
    with db.connection() as conn:
        counts1 = tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("typed_facts","typed_fact_evidence","protocol_operations","source_files"))
        operation = conn.execute("SELECT semantic,api_semantics,report_id_in_buffer,production_safe FROM protocol_operations").fetchone()
    second = FullTypedReprocessor(db.db_path, tmp_path).run()
    with db.connection() as conn:
        counts2 = tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("typed_facts","typed_fact_evidence","protocol_operations","source_files"))
    assert first["relevant_files"] == second["relevant_files"] == 1
    assert counts1 == counts2
    assert tuple(operation) == ("mouse.dpi", "vendor API write", None, 0)
