import json

from ingest.mass_model_discovery import ModelInventoryPass, split_model_variant
from ingest.normalize.evidence import DeviceIdentifierFact, EvidenceLevel
from ingest.storage.database import RegistryDatabase


def test_model_normalization_preserves_significant_variant_suffixes():
    default = split_model_variant("AULA HERO 84 HE", "AULA")
    assert default.model_name == "HERO 84"
    assert default.variant_label == "HE"

    se = split_model_variant("VXE R1 SE", "VXE")
    se_plus = split_model_variant("VXE R1 SE+", "VXE")
    assert se.model_key == se_plus.model_key == "r1"
    assert se.variant_key != se_plus.variant_key


def test_inventory_uses_variant_scoped_identity_and_software_edges(tmp_path):
    db_path = tmp_path / "registry.sqlite"
    db = RegistryDatabase(db_path)
    aula = db.get_brand_with_details("aula")
    vendor = db.get_or_create_vendor("aula", "AULA")
    f75, _ = db.upsert_product(vendor, "AULA F75", "F75", "keyboard", "f75", product_url="https://www.aulastar.com/product/f75/")
    f75_pro, _ = db.upsert_product(vendor, "AULA F75 Pro", "F75 Pro", "keyboard", "f75pro", product_url="https://www.aulastar.com/product/f75-pro/")
    hero, _ = db.upsert_product(vendor, "AULA HERO 84 HE", "HERO 84 HE", "keyboard", "hero84he", product_url="https://www.aulastar.com/product/hero-84-he/")
    db.upsert_device_identifier(DeviceIdentifierFact(
        product_id=f75, vid=0x372E, pid=0x103D, vid_hex="0x372E", pid_hex="0x103D",
        connection_type="wired", evidence_level=EvidenceLevel.LEVEL_2_DEVICE_IDENTITY,
    ))
    inbox = tmp_path / "inbox" / "AULA" / "keyboard"
    inbox.mkdir(parents=True)
    (inbox / "AULA F75 Driver.exe").write_bytes(b"not executed")
    reports = tmp_path / "reports"

    summary = ModelInventoryPass(db_path, reports, tmp_path / "inbox").run()
    assert summary["models"]["canonical_commercial_models"] >= 2  # F75 and HERO 84

    with db.connection() as conn:
        f75_model = conn.execute("SELECT id FROM commercial_models WHERE brand_id=? AND normalized_name='f75'", (aula["id"],)).fetchone()
        assert f75_model is not None
        variants = conn.execute("SELECT canonical_name FROM model_variants WHERE commercial_model_id=? ORDER BY canonical_name", (f75_model["id"],)).fetchall()
        assert [x["canonical_name"] for x in variants] == ["F75", "F75 Pro"]

        hero_variant = conn.execute("SELECT v.id FROM model_variants v JOIN commercial_models m ON m.id=v.commercial_model_id WHERE m.brand_id=? AND v.normalized_name='hero84he'", (aula["id"],)).fetchone()
        assert hero_variant is not None
        hero_software = conn.execute("SELECT COUNT(*) FROM software_model_compatibilities WHERE model_variant_id=?", (hero_variant["id"],)).fetchone()[0]
        assert hero_software == 0, "A brand-level software target must not be inferred to support HERO84"

        bindings = conn.execute("SELECT binding_role,binding_confidence FROM model_identity_bindings").fetchall()
        assert ("WIRED", "EXACT_OFFICIAL") in [(x["binding_role"], x["binding_confidence"]) for x in bindings]

    assert (reports / "model_inventory_summary.json").exists()
    assert (reports / "emulation_model_candidates.json").exists()
    assert json.loads((reports / "model_inventory_summary.json").read_text(encoding="utf-8"))["brands"]["total"] == 100
