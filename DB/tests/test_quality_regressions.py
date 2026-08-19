"""Regression tests for product categorization, scoped hint correlation, and fact idempotency."""

import pytest
from pathlib import Path
import tempfile
import json

from ingest.normalize.models import (
    detect_category, evaluate_category, is_hardware_device,
    normalize_product_name, generate_identity_key
)
from ingest.normalize.evidence import (
    RawProduct, RawArtifact, RawSource, GenericFact, ProtocolHintFact, DeviceIdentifierFact,
    SourceType, EvidenceLevel
)
from ingest.scanners.json_scanner import JsonScanner
from ingest.storage.database import RegistryDatabase
from ingest.collectors.atk_vxe import ATK_V_HUB_DEVICES_JSON
from ingest.collectors.keychron import KEYCHRON_LAUNCHER_DEVICES_JSON


def test_category_classification_regressions():
    # 1. Mice must classify as mouse
    assert detect_category("NEX Pro mouse") == "mouse"
    assert detect_category("VXE MAD R Wireless Mouse PAW3950 Sensor", extra_text="https://vxe.com/products/mad-r-wireless-mouse") == "mouse"
    assert detect_category("VXE Dragonfly R1 Pro PAW3395") == "mouse"
    assert detect_category("NEX Lite wireless mouse") == "mouse"
    assert is_hardware_device("mouse") is True

    # 2. Audio / Headset must classify as headset
    assert detect_category("GX1 Headset Gaming") == "headset"
    assert detect_category("EPOMAKER Shadow-X IEM Earphones") == "headset"
    assert is_hardware_device("headset") is True

    # 3. Accessories & Components MUST NOT classify as keyboard
    assert detect_category("Whisper Linear Switch Set") == "switch"
    assert detect_category("Kailh Box Switch Set") == "switch"
    assert detect_category("Cherry MX Switch Pack") == "switch"
    assert is_hardware_device("switch") is False

    assert detect_category("JellyPaw Keycap Set") == "keycap"
    assert detect_category("Blue Planet PBT Keycaps") == "keycap"
    assert detect_category("Double Shot PBT OSA Full Set Keycap Set") == "keycap"
    assert is_hardware_device("keycap") is False

    assert detect_category("Coiled Aviator Cable") == "cable"
    assert detect_category("RT100 Rainbow Glowing Cable") == "cable"
    assert is_hardware_device("cable") is False

    assert detect_category("Keychron Palm Rest") == "accessory"
    assert detect_category("Krytox GPL 205g0 Lubricant") == "accessory"
    assert detect_category("Rotary Encoder Knob") == "accessory"
    assert is_hardware_device("accessory") is False

    # 4. Six Confirmed Remaining Negative Accessory Regressions
    assert detect_category("60 TKL Full size 104 Solid Wood Wrist Rests") == "accessory"
    assert detect_category("96 Beech Wood Wrist") == "accessory"
    assert detect_category("ZOWIE SKATEZ") == "accessory"
    assert detect_category("Black Walnut Stand") == "accessory"
    assert detect_category("Headphone stand") == "accessory"
    assert detect_category("x MAMBASNAKE 62-Key Cover") == "accessory"

    # 5. Global Semantic Families Audit
    assert detect_category("G-Wolves Hati-S Plus 4K Grip Tape") == "accessory"
    assert detect_category("Tiger ICE Skates for Superlight") == "accessory"
    assert detect_category("KBD67 Mark II v3 soldered PCB") == "accessory"
    assert detect_category("Tiger80 Aluminum Switch Plate") == "accessory"
    assert detect_category("Durock V2 Screw-in Stabilizers") == "accessory"
    assert detect_category("Cherry MX Switch Puller and Opener") == "accessory"
    assert detect_category("Keyboard Carrying Case Sleeve") == "accessory"
    assert detect_category("TOFU60 Redux Replacement Case") == "accessory"

    # 6. Complete Keyboard Kits must NOT be turned into accessories
    assert detect_category("Tiger80 Lite Keyboard Kit") == "keyboard"
    assert detect_category("QK75 Barebone Kit") == "keyboard"
    assert detect_category("KBD67 Lite DIY Kit") == "keyboard"


def test_negative_product_filters_and_bundles():
    """Verify that preorder cards, bundles, replacement displays, and docs are not hardware devices."""
    assert detect_category("1 Reservation Card for EPOMAKER HE60") == "other"
    assert is_hardware_device(detect_category("1 Reservation Card for EPOMAKER HE60")) is False

    assert detect_category("RT100 Smart mini Display") == "accessory"
    assert is_hardware_device(detect_category("RT100 Smart mini Display")) is False

    assert detect_category("TH108 V2 PRO + CarbonX Bundle") == "bundle"
    assert is_hardware_device(detect_category("TH108 V2 PRO + CarbonX Bundle")) is False

    assert detect_category("B1 Pro and BM24 Combo") == "bundle"
    assert is_hardware_device(detect_category("B1 Pro and BM24 Combo")) is False

    assert detect_category("B6 Pro and BM24 Combo") == "bundle"
    assert is_hardware_device(detect_category("B6 Pro and BM24 Combo")) is False

    assert detect_category("K2 HE and M3 Elite Bundle") == "bundle"
    assert is_hardware_device(detect_category("K2 HE and M3 Elite Bundle")) is False

    assert detect_category("Industry Design Open Source Project - Q Pro Series") == "other"
    assert is_hardware_device(detect_category("Industry Design Open Source Project - Q Pro Series")) is False


def test_keyboard_model_families_classification():
    """Verify that canonical keyboard models are classified as keyboard."""
    keyboard_models = [
        "AULA F75",
        "AULA F87 Pro",
        "AULA F99",
        "EPOMAKER Galaxy70",
        "EPOMAKER HE60",
        "EPOMAKER HE65 V2 TMR",
        "EPOMAKER HE75 V2 TMR",
        "EPOMAKER HE75 V2",
        "EPOMAKER TH108 V2 Pro",
        "EPOMAKER HE108",
        "EPOMAKER RT100 Pro",
        "EPOMAKER TH80 V2 Pro",
        "EPOMAKER RT98",
        "EPOMAKER TH99 Pro",
        "EPOMAKER TH87",
        "EPOMAKER QK108",
        "EPOMAKER TH108 Pro",
        "EPOMAKER RT82",
    ]
    for model in keyboard_models:
        cat = detect_category(model)
        assert cat == "keyboard", f"Failed for model '{model}': got '{cat}', expected 'keyboard'"


def test_identity_key_and_pristine_display_names():
    """Verify that display names preserve pristine spelling while identity_keys unify duplicates."""
    assert normalize_product_name("AULA", "AULA F75 Gasket Mechanical Keyboard") == "F75"
    assert normalize_product_name("AULA", "AULA F87 Pro Mechanical Keyboard") == "F87 Pro"
    assert normalize_product_name("AULA", "AULA F99 Wireless Mechanical Keyboard") == "F99"
    assert normalize_product_name("EPOMAKER", "EPOMAKER Galaxy70 Custom Mechanical Keyboard") == "Galaxy70"

    assert generate_identity_key("AULA", "AULA F75 Gasket Mechanical Keyboard") == "f75"
    assert generate_identity_key("AULA", "AULA F 75") == "f75"
    assert generate_identity_key("AULA", "F75") == "f75"

    assert generate_identity_key("AULA", "AULA F87 Pro") == "f87pro"
    assert generate_identity_key("AULA", "AULA F 87 Pro") == "f87pro"

    assert generate_identity_key("EPOMAKER", "Galaxy70") == "galaxy70"
    assert generate_identity_key("EPOMAKER", "Epomaker Galaxy 70") == "galaxy70"


def test_metadata_evidence_precedence_and_aliases():
    """Verify that a stronger source preserves primary name/URL while weaker source is stored as alias."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        v_id = db.get_or_create_vendor("aula", "AULA")

        # 1. Technical official manifest (Level 2 / metadata_confidence = 1.0)
        p_id, is_new = db.upsert_product(
            vendor_id=v_id,
            raw_name="AULA HERO 84 HE Mechanical Keyboard",
            canonical_name="HERO 84 HE",
            category="keyboard",
            identity_key="hero84he",
            product_url="https://www.aulastar.com/product/hero-84-he/",
            category_confidence=1.0,
            metadata_confidence=1.0,
            evidence_level=2
        )
        assert is_new is True

        # 2. Later weak Shopify crawl (Level 1 / metadata_confidence = 0.6)
        p_id2, is_new2 = db.upsert_product(
            vendor_id=v_id,
            raw_name="AULA HERO84 HE",
            canonical_name="HERO 84 HE",
            category="keyboard",
            identity_key="hero84he",
            product_url="https://aulagear.com/products/aula-hero84-he",
            category_confidence=0.75,
            metadata_confidence=0.6,
            evidence_level=1
        )
        assert p_id2 == p_id
        assert is_new2 is False

        # 3. Primary metadata must remain from strong source
        details = db.get_product_with_details("HERO 84 HE")
        assert len(details) == 1
        prod = details[0]
        assert prod["raw_name"] == "AULA HERO 84 HE Mechanical Keyboard"
        assert prod["product_url"] == "https://www.aulastar.com/product/hero-84-he/"
        assert prod["category"] == "keyboard"

        # 4. Weaker observation must be in aliases
        assert len(prod["aliases"]) == 1
        assert prod["aliases"][0]["alias_name"] == "AULA HERO84 HE"
        assert prod["aliases"][0]["alias_url"] == "https://aulagear.com/products/aula-hero84-he"
    finally:
        if db_file.exists():
            db_file.unlink()


def test_atk68_must_not_have_mouse_sensors():
    scanner = JsonScanner()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(ATK_V_HUB_DEVICES_JSON, f)
        temp_path = Path(f.name)

    try:
        res = scanner.scan_file(temp_path, artifact_sha256="dummy_sha")
        atk68_records = [r for r in res.device_records if r.model == "ATK68" or r.pid == 0x0068]
        assert len(atk68_records) == 1
        atk68 = atk68_records[0]

        assert atk68.hints.get("sdkModuleName") == "vgn_atk_hub"
        assert "sensor" not in atk68.hints

        f1_records = [r for r in res.device_records if r.model == "Blazing Sky F1" or r.pid == 0xF101]
        assert len(f1_records) == 1
        f1 = f1_records[0]
        assert f1.hints.get("sensor") == "PAW3950"
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_q1_max_must_not_have_mouse_engine():
    scanner = JsonScanner()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(KEYCHRON_LAUNCHER_DEVICES_JSON, f)
        temp_path = Path(f.name)

    try:
        res = scanner.scan_file(temp_path, artifact_sha256="dummy_sha")
        q1_records = [r for r in res.device_records if r.model == "Q1 Max" or r.pid == 0x0101]
        assert len(q1_records) == 1
        q1 = q1_records[0]

        assert q1.hints.get("sdkModuleName") == "qmk_via"
        assert q1.hints.get("sdkModuleName") != "keychron_mouse_engine"

        m3_records = [r for r in res.device_records if r.model == "M3" or r.pid == 0x0801]
        assert len(m3_records) == 1
        m3 = m3_records[0]
        assert m3.hints.get("sdkModuleName") == "keychron_mouse_engine"
        assert m3.hints.get("sensor") == "PAW3395"
    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_facts_and_sources_idempotency():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        v_id = db.get_or_create_vendor("keychron", "Keychron")

        src1 = RawSource(url="https://keychron.com/products.json", source_type=SourceType.VENDOR_WEB, vendor="keychron")
        id1 = db.record_source(src1)

        src2 = RawSource(url="https://keychron.com/products.json", source_type=SourceType.VENDOR_WEB, vendor="keychron")
        id2 = db.record_source(src2)
        assert id1 == id2

        p_id, is_new = db.upsert_product(v_id, "Keychron Q1 Max", "Q1 Max", "keyboard", identity_key="q1max")
        assert is_new is True

        fact1 = GenericFact(product_id=p_id, key="handle", value="keychron-q1-max", source_id=id1, evidence_level=EvidenceLevel.LEVEL_1_METADATA)
        assert db.upsert_generic_fact(fact1) is True

        fact2 = GenericFact(product_id=p_id, key="handle", value="keychron-q1-max", source_id=id2, evidence_level=EvidenceLevel.LEVEL_1_METADATA)
        assert db.upsert_generic_fact(fact2) is False

        with db.connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM facts WHERE product_id = ?", (p_id,)).fetchone()[0]
            assert cnt == 1
            src_cnt = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            assert src_cnt == 1
    finally:
        if db_file.exists():
            db_file.unlink()


def test_aula_a700_headphones_category_regression():
    """Verify that explicit vendor product_type='Headphones' correctly classifies as headset."""
    eval_res = evaluate_category(
        name="AULA A700",
        product_url="https://aulagear.com/products/aula-a700",
        product_type="Headphones"
    )
    assert eval_res.category == "headset"
    assert eval_res.confidence >= 0.85


def test_source_role_precedence_and_alias_provenance():
    """Verify that software/download articles cannot overwrite primary product metadata and aliases retain correct provenance."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        v_id = db.get_or_create_vendor("aula", "AULA")

        # 1. Product page source (SourceType.VENDOR_WEB, metadata_confidence = 0.85)
        src_catalog = RawSource(url="https://aulagear.com/products.json", source_type=SourceType.VENDOR_WEB, vendor="aula")
        src_catalog_id = db.record_source(src_catalog)

        p_id, is_new = db.upsert_product(
            vendor_id=v_id,
            raw_name="A700",
            canonical_name="A700",
            category="headset",
            identity_key="a700",
            product_url="https://aulagear.com/products/aula-a700",
            category_confidence=0.85,
            metadata_confidence=0.85,
            source_id=src_catalog_id,
            evidence_level=1
        )
        assert is_new is True

        # 2. Software blog article source (SourceType.VENDOR_DOWNLOAD, metadata_confidence = 0.40)
        src_blog = RawSource(url="https://aulagear.com/blogs/software/aula-a700-driver", source_type=SourceType.VENDOR_DOWNLOAD, vendor="aula")
        src_blog_id = db.record_source(src_blog)

        p_id2, is_new2 = db.upsert_product(
            vendor_id=v_id,
            raw_name="AULA A700 Driver",
            canonical_name="A700",
            category="headset",
            identity_key="a700",
            product_url="https://aulagear.com/blogs/software/aula-a700-driver",
            category_confidence=0.85,
            metadata_confidence=0.40,
            source_id=src_blog_id,
            evidence_level=1
        )
        assert p_id2 == p_id
        assert is_new2 is False

        # Verify primary metadata in products table is preserved
        with db.connection() as conn:
            p_row = conn.execute("SELECT raw_name, canonical_name, product_url, metadata_confidence FROM products WHERE id = ?", (p_id,)).fetchone()
            assert p_row["raw_name"] == "A700"
            assert p_row["canonical_name"] == "A700"
            assert p_row["product_url"] == "https://aulagear.com/products/aula-a700"
            assert p_row["metadata_confidence"] == 0.85

            # Verify alias provenance points to the blog source
            aliases = conn.execute("SELECT alias_name, alias_url, source_id FROM product_aliases WHERE product_id = ?", (p_id,)).fetchall()
            assert len(aliases) == 1
            assert aliases[0]["alias_name"] == "AULA A700 Driver"
            assert aliases[0]["source_id"] == src_blog_id
    finally:
        if db_file.exists():
            db_file.unlink()


def test_artifact_metadata_persistence():
    """Verify that final_url, content_type, and software_version are persisted in database."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        v_id = db.get_or_create_vendor("aula", "AULA")

        art = RawArtifact(
            original_url="https://aulagear.com/downloads/AULA_A700_Setup_1.1.0.3.zip",
            final_url="https://cdn.shopify.com/files/AULA_A700_Setup_1.1.0.3.zip",
            filename="AULA_A700_Setup_1.1.0.3.zip",
            content_type="application/zip",
            size=9000000,
            sha256="abc123def4567890123456789012345678901234567890123456789012345678",
            vendor="aula",
            software_version="1.1.0.3"
        )
        sha, is_new, _ = db.upsert_artifact(art, v_id)
        assert is_new is True

        details = db.get_artifact_with_details(sha)
        assert len(details) == 1
        assert details[0]["final_url"] == "https://cdn.shopify.com/files/AULA_A700_Setup_1.1.0.3.zip"
        assert details[0]["content_type"] == "application/zip"
        assert details[0]["software_version"] == "1.1.0.3"
    finally:
        if db_file.exists():
            db_file.unlink()


def test_negative_accessory_and_non_peripheral_semantics():
    """Verify that accessories and non-peripheral items are correctly classified."""
    from ingest.normalize.models import evaluate_category, is_software_filename

    assert evaluate_category("MICROPHONE BOOM ARM AC902").category == "accessory"
    assert evaluate_category("Tiger Arc 1 Mouse Skates for Logitech G Pro X").category == "accessory"
    assert evaluate_category("ARIA XD7 PRO GRIP TAPE").category == "accessory"
    assert evaluate_category("136 Ice Cream Keycaps for Mechanical Keyboard").category == "keycap"
    assert evaluate_category("GMMK 3 Top Case White 75").category == "accessory"
    assert evaluate_category("Rubber Feet Thickness 1.8mm Anti-slip Protective Pad").category == "accessory"
    assert evaluate_category("Daughterboard and JST Cable for Custom Keyboard").category in {"cable", "accessory"}
    assert evaluate_category("Gamakay Switch Puller Pro").category == "accessory"
    assert evaluate_category("Rawm Car Battery Charger 12V").category == "other"
    assert evaluate_category("Electric Tricycle Cargo Heavy Duty").category == "other"
    assert evaluate_category("Bathroom Hardware Fittings Sink Faucet").category == "other"


def test_software_filename_rejection():
    """Verify that software archive filenames are identified and rejected from becoming products."""
    from ingest.normalize.models import is_software_filename

    assert is_software_filename("DAREU_Driver_Installer_v1.5.8.20_20260721.zip") is True
    assert is_software_filename("8517ed_d2f651f02ba44182914b2fee755ef967.zip") is True
    assert is_software_filename("Setup_v1.2.exe") is True
    assert is_software_filename("AULA F75 Mechanical Keyboard") is False
    assert is_software_filename("GMMK 3 HE 75%") is False


def test_provenance_source_id_never_null():
    """Verify that technical evidence facts never have source_id = NULL in the database."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        v_id = db.get_or_create_vendor("ajazz", "Ajazz")

        p_id, _ = db.upsert_product(
            vendor_id=v_id,
            raw_name="Ajazz AJ159 Apex",
            canonical_name="AJ159 Apex",
            category="mouse",
            identity_key="aj159apex",
            product_url="https://ajazz.com/products/aj159-apex"
        )

        # Upsert device identifier with source_id = None
        ident = DeviceIdentifierFact(
            product_id=p_id,
            vid=0x0c45,
            pid=0x7040,
            vid_hex="0c45",
            pid_hex="7040",
            source_id=None
        )
        db.upsert_device_identifier(ident)

        with db.connection() as conn:
            row = conn.execute("SELECT source_id FROM device_identifiers WHERE product_id = ?", (p_id,)).fetchone()
            assert row is not None
            assert row["source_id"] is not None, "source_id must not be NULL"
    finally:
        if db_file.exists():
            db_file.unlink()


def test_interrupted_run_handling():
    """Verify that starting a new crawl run marks prior running runs as interrupted."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_file = Path(f.name)

    try:
        db = RegistryDatabase(db_file)
        db.start_crawl_run("run-1")

        with db.connection() as conn:
            row = conn.execute("SELECT status FROM crawl_runs WHERE id = 'run-1'").fetchone()
            assert row["status"] == "running"

        db.start_crawl_run("run-2")

        with db.connection() as conn:
            row1 = conn.execute("SELECT status FROM crawl_runs WHERE id = 'run-1'").fetchone()
            row2 = conn.execute("SELECT status FROM crawl_runs WHERE id = 'run-2'").fetchone()
            assert row1["status"] == "interrupted"
            assert row2["status"] == "running"
    finally:
        if db_file.exists():
            db_file.unlink()


