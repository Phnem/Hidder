"""Unit and integration tests for QMK bulk-ingestion pipeline."""

import json
import pytest
from pathlib import Path
from ingest.collectors.qmk import QmkMetadataResolver, QmkCollector, QmkTargetMetadata
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import EvidenceLevel


@pytest.fixture
def mock_qmk_tree(tmp_path: Path) -> Path:
    """Create a temporary mock QMK repository tree with inheritance."""
    kb_root = tmp_path / "keyboards"
    kb_root.mkdir(parents=True)

    # 1. Vendor level: keyboards/acme/info.json
    acme_dir = kb_root / "acme"
    acme_dir.mkdir()
    (acme_dir / "info.json").write_text(json.dumps({
        "manufacturer": "ACME Keyboards",
        "maintainer": "acme_team",
        "url": "https://acme-keyboards.com",
        "processor": "atmega32u4",
        "bootloader": "atmel-dfu",
        "usb": {
            "vid": "0x1234",
            "device_version": "1.0.0"
        },
        "features": {
            "bootmagic": True,
            "extrakey": True,
            "nkro": True
        }
    }), encoding="utf-8")

    # 2. Model level: keyboards/acme/model_a/info.json
    model_a_dir = acme_dir / "model_a"
    model_a_dir.mkdir()
    (model_a_dir / "info.json").write_text(json.dumps({
        "keyboard_name": "ACME Model A",
        "matrix_size": {
            "rows": 5,
            "cols": 15
        },
        "usb": {
            "pid": "0x5678"
        },
        "features": {
            "encoder": True,
            "rgb_matrix": True
        },
        "layouts": {
            "LAYOUT_ansi": {},
            "LAYOUT_iso": {}
        }
    }), encoding="utf-8")

    # 3. Leaf target: keyboards/acme/model_a/rev1/keyboard.json
    rev1_dir = model_a_dir / "rev1"
    rev1_dir.mkdir()
    (rev1_dir / "keyboard.json").write_text(json.dumps({
        "keyboard_name": "ACME Model A Rev 1",
        "processor": "RP2040",  # Overrides parent MCU
        "bootloader": "rp2040", # Overrides parent bootloader
        "usb": {
            "pid": "0x5679",    # Overrides model PID
            "device_version": "1.0.1"
        },
        "encoder": {
            "rotary": [
                {"pin_a": "GP0", "pin_b": "GP1", "resolution": 4}
            ]
        },
        "rgb_matrix": {
            "driver": "ws2812"
        }
    }), encoding="utf-8")

    # 4. Target with rules.mk fallback: keyboards/acme/model_b/keyboard.json + rules.mk
    model_b_dir = acme_dir / "model_b"
    model_b_dir.mkdir()
    (model_b_dir / "keyboard.json").write_text(json.dumps({
        "keyboard_name": "ACME Model B"
    }), encoding="utf-8")
    (model_b_dir / "rules.mk").write_text(
        "MCU = STM32F401\nBOOTLOADER = tinyuf2\nAUDIO_ENABLE = yes\nOLED_ENABLE = yes\n",
        encoding="utf-8"
    )
    (model_b_dir / "config.h").write_text(
        '#define VENDOR_ID 0x1234\n#define PRODUCT_ID 0x9999\n#define MATRIX_ROWS 4\n#define MATRIX_COLS 12\n',
        encoding="utf-8"
    )

    return tmp_path


def test_qmk_inheritance_resolution(mock_qmk_tree: Path):
    resolver = QmkMetadataResolver(mock_qmk_tree)
    targets = resolver.list_targets()
    assert "acme/model_a/rev1" in targets
    assert "acme/model_b" in targets

    # Test target 1: acme/model_a/rev1
    meta = resolver.resolve_target("acme/model_a/rev1")
    assert meta.manufacturer == "ACME Keyboards"
    assert meta.keyboard_name == "ACME Model A Rev 1"
    assert meta.vid_hex == "0x1234"
    assert meta.pid_hex == "0x5679"  # Leaf override
    assert meta.processor == "RP2040"  # Leaf override
    assert meta.bootloader == "rp2040"  # Leaf override
    assert meta.device_version == "1.0.1"
    assert meta.matrix_rows == 5
    assert meta.matrix_cols == 15
    assert meta.features.get("bootmagic") is True
    assert meta.features.get("nkro") is True
    assert meta.features.get("encoder") is True
    assert meta.features.get("rgb_matrix") is True
    assert "LAYOUT_ansi" in meta.layouts
    assert "LAYOUT_iso" in meta.layouts
    assert meta.hardware_facts.get("encoder_count") == 1
    assert meta.hardware_facts.get("encoder_resolution") == 4
    assert meta.hardware_facts.get("rgb_matrix_driver") == "ws2812"


def test_qmk_rules_and_config_fallback(mock_qmk_tree: Path):
    resolver = QmkMetadataResolver(mock_qmk_tree)
    meta = resolver.resolve_target("acme/model_b")

    assert meta.manufacturer == "ACME Keyboards"
    assert meta.keyboard_name == "ACME Model B"
    assert meta.vid_hex == "0x1234"
    assert meta.pid_hex == "0x9999"
    assert meta.processor == "STM32F401"
    assert meta.bootloader == "tinyuf2"
    assert meta.matrix_rows == 4
    assert meta.matrix_cols == 12
    assert meta.features.get("audio") is True
    assert meta.features.get("oled") is True


def test_qmk_collector_persistence(tmp_path: Path, mock_qmk_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = QmkCollector(db=db, repo_path=mock_qmk_tree, run_id="test_run_1")
    collector.commit_sha = "abc1234567890def"

    # Dry-run first
    dry_stats = collector.collect(dry_run=True)
    assert dry_stats["targets_discovered"] == 2
    assert dry_stats["records_created"] == 2
    counts_dry = db.get_summary_counts()
    assert counts_dry["total_products"] == 0

    # Real import
    stats = collector.collect(dry_run=False)
    assert stats["targets_discovered"] == 2
    assert stats["records_created"] == 2
    assert stats["with_vid_pid"] == 2

    counts = db.get_summary_counts()
    assert counts["total_products"] == 2
    assert counts["total_vid_pids"] == 2

    # Verify provenance and trust confidence
    with db.connection() as conn:
        # Check source
        src = conn.execute("SELECT * FROM sources WHERE content_hash = 'abc1234567890def'").fetchone()
        assert src is not None
        assert "github.com/qmk/qmk_firmware" in src["source_url"]

        # Check product
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'acme/model_a/rev1'").fetchone()
        assert prod is not None
        assert prod["category"] == "keyboard"
        assert prod["metadata_confidence"] == 0.75  # QmkDeclared

        # Check device identifier
        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x1234"
        assert ident["pid_hex"] == "0x5679"
        assert ident["confidence"] == 0.75  # UpstreamDeclared
        assert ident["evidence_level"] == int(EvidenceLevel.LEVEL_2_DEVICE_IDENTITY)

        # Check protocol hints
        hints = conn.execute("SELECT hint_key, hint_value FROM protocol_hints WHERE product_id = ?", (prod["id"],)).fetchall()
        hint_dict = {h["hint_key"]: h["hint_value"] for h in hints}
        assert hint_dict.get("firmware_family") == "qmk"
        assert hint_dict.get("mcu") == "RP2040"
        assert hint_dict.get("bootloader") == "rp2040"

        # Check facts
        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert fact_dict.get("matrix_dimensions") == "5x15"
        assert fact_dict.get("feature:rgb_matrix") == "true"
        assert fact_dict.get("feature:encoder") == "true"
        assert fact_dict.get("hw:encoder_count") == "1"


def test_qmk_idempotency_and_update(tmp_path: Path, mock_qmk_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = QmkCollector(db=db, repo_path=mock_qmk_tree, run_id="run_1")
    collector.collect(dry_run=False)

    counts1 = db.get_summary_counts()

    # Re-run same ingestion -> should update, not create duplicates
    collector2 = QmkCollector(db=db, repo_path=mock_qmk_tree, run_id="run_2")
    stats2 = collector2.collect(dry_run=False)

    assert stats2["records_created"] == 0
    assert stats2["records_updated"] == 2

    counts2 = db.get_summary_counts()
    assert counts1["total_products"] == counts2["total_products"]
    assert counts1["total_vid_pids"] == counts2["total_vid_pids"]
