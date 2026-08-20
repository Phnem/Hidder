"""Unit and integration tests for libratbag bulk-ingestion pipeline."""

import json
import pytest
from pathlib import Path
from ingest.collectors.libratbag import (
    LibratbagDeviceParser, LibratbagProtocolExtractor, LibratbagCollector,
    LibratbagDeviceMetadata, LibratbagCommand, LibratbagPacketStruct
)
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import EvidenceLevel


@pytest.fixture
def mock_libratbag_tree(tmp_path: Path) -> Path:
    """Create a temporary mock libratbag tree with .device files and C sources."""
    # 1. data/devices
    dev_dir = tmp_path / "data" / "devices"
    dev_dir.mkdir(parents=True)

    # Logitech G502 Hero device file
    (dev_dir / "logitech-g502-hero.device").write_text("""[Device]
Name=Logitech G502 Hero
DeviceMatch=usb:046d:c08b
Driver=hidpp20
DeviceType=mouse

[Driver/hidpp20]
Quirk=INDEX_OFFSET;G502X_PLUS
Buttons=11
Leds=2
DpiRange=100:25600@50
Profiles=5
""", encoding="utf-8")

    # SinoWealth generic mouse with sub-devices
    (dev_dir / "sinowealth-0027.device").write_text("""[Device]
DeviceMatch=usb:258a:0027
DeviceType=mouse
Driver=sinowealth
Name=SinoWealth Generic Mouse (0027)

[Driver/sinowealth/devices/3106]
Buttons=8
DeviceName=DreamMachines DM5 Blink
LedType=RGB
SensorType=PMW3389
Profiles=1

[Driver/sinowealth/devices/V161]
Buttons=6
DeviceName=G-Wolves Hati HT-M Wired
LedType=None
SensorType=PMW3360
""", encoding="utf-8")

    # 2. src
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)

    # Header with defines and structs
    (src_dir / "test_driver.h").write_text("""#pragma once
#include <stdint.h>

#define TEST_REPORT_ID 0x04
#define TEST_CMD_GET_DPI 0x11
#define TEST_CMD_SET_DPI 0x12
#define TEST_CMD_GET_POLL_RATE 0x14
#define TEST_CMD_GET_BATTERY 0x20
#define TEST_CMD_DFU_FLASH 0x75

enum test_led_mode {
    TEST_LED_OFF = 0x00,
    TEST_LED_RGB_BREATHING = 0x01,
    TEST_LED_STATIC = 0x02
};

struct test_dpi_packet {
    uint8_t report_id;
    uint8_t cmd;
    uint16_t dpi_x;
    uint16_t dpi_y;
    uint8_t flags;
} __attribute__((packed));
""", encoding="utf-8")

    return tmp_path


def test_libratbag_device_parser(mock_libratbag_tree: Path):
    parser = LibratbagDeviceParser(mock_libratbag_tree)
    files = parser.list_device_files()
    assert len(files) == 2

    # Test G502 Hero
    g502_file = [f for f in files if "g502" in f.name][0]
    meta_g502 = parser.parse_device_file(g502_file)
    assert meta_g502 is not None
    assert meta_g502.name == "Logitech G502 Hero"
    assert meta_g502.vid_hex == "0x046D"
    assert meta_g502.pid_hex == "0xC08B"
    assert meta_g502.driver == "hidpp20"
    assert meta_g502.vendor_slug in ["logitech", "logitech_g"]
    assert meta_g502.buttons == 11
    assert meta_g502.leds == 2
    assert meta_g502.profiles == 5
    assert meta_g502.dpi_range == "100:25600@50"
    assert "INDEX_OFFSET" in meta_g502.quirks
    assert "G502X_PLUS" in meta_g502.quirks

    # Test SinoWealth with sub-devices
    sino_file = [f for f in files if "sinowealth" in f.name][0]
    meta_sino = parser.parse_device_file(sino_file)
    assert meta_sino is not None
    assert meta_sino.vid_hex == "0x258A"
    assert meta_sino.pid_hex == "0x0027"
    assert meta_sino.driver == "sinowealth"
    assert len(meta_sino.sub_devices) == 2
    assert meta_sino.sub_devices[0]["name"] == "DreamMachines DM5 Blink"
    assert meta_sino.sub_devices[0]["buttons"] == 8
    assert meta_sino.sub_devices[1]["name"] == "G-Wolves Hati HT-M Wired"


def test_libratbag_protocol_extractor(mock_libratbag_tree: Path):
    extractor = LibratbagProtocolExtractor(mock_libratbag_tree)
    commands, structs = extractor.extract_all()

    # Verify commands and semantic mappings
    cmd_dict = {c.name: c for c in commands}
    assert "TEST_REPORT_ID" in cmd_dict
    assert cmd_dict["TEST_REPORT_ID"].opcode == 0x04
    assert cmd_dict["TEST_REPORT_ID"].report_id == 0x04

    assert "TEST_CMD_GET_DPI" in cmd_dict
    assert cmd_dict["TEST_CMD_GET_DPI"].semantic == "mouse.dpi"

    assert "TEST_CMD_GET_POLL_RATE" in cmd_dict
    assert cmd_dict["TEST_CMD_GET_POLL_RATE"].semantic == "mouse.polling_rate"

    assert "TEST_CMD_GET_BATTERY" in cmd_dict
    assert cmd_dict["TEST_CMD_GET_BATTERY"].semantic == "battery.status"

    assert "TEST_CMD_DFU_FLASH" in cmd_dict
    assert cmd_dict["TEST_CMD_DFU_FLASH"].destructive_or_firmware_command is True
    assert cmd_dict["TEST_CMD_DFU_FLASH"].semantic == "device.dfu"

    assert "TEST_LED_RGB_BREATHING" in cmd_dict
    assert cmd_dict["TEST_LED_RGB_BREATHING"].semantic == "lighting.mode"

    # Verify struct extraction and byte offsets
    struct_dict = {s.struct_name: s for s in structs}
    assert "test_dpi_packet" in struct_dict
    st = struct_dict["test_dpi_packet"]
    assert st.total_size == 7
    assert len(st.fields) == 5
    assert st.fields[0].name == "report_id" and st.fields[0].offset == 0 and st.fields[0].size == 1
    assert st.fields[1].name == "cmd" and st.fields[1].offset == 1 and st.fields[1].size == 1
    assert st.fields[2].name == "dpi_x" and st.fields[2].offset == 2 and st.fields[2].size == 2
    assert st.fields[3].name == "dpi_y" and st.fields[3].offset == 4 and st.fields[3].size == 2
    assert st.fields[4].name == "flags" and st.fields[4].offset == 6 and st.fields[4].size == 1


def test_libratbag_collector_persistence(tmp_path: Path, mock_libratbag_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = LibratbagCollector(db=db, repo_path=mock_libratbag_tree, run_id="libratbag_run_1")
    collector.commit_sha = "deadbeef12345678"

    # Dry-run
    dry_stats = collector.collect(dry_run=True)
    assert dry_stats["device_files_discovered"] == 2
    assert dry_stats["devices_recognized"] == 4  # 1 G502 + 1 generic sino + 2 subdevices
    counts_dry = db.get_summary_counts()
    assert counts_dry["total_products"] == 0

    # Real import
    stats = collector.collect(dry_run=False)
    assert stats["device_files_discovered"] == 2
    assert stats["devices_recognized"] == 4

    counts = db.get_summary_counts()
    # 4 device products + 1 protocol family reference product
    assert counts["total_products"] >= 4
    assert counts["total_vid_pids"] == 2

    # Check database records
    with db.connection() as conn:
        # Check source
        src = conn.execute("SELECT * FROM sources WHERE content_hash = 'deadbeef12345678'").fetchone()
        assert src is not None
        assert "libratbag/libratbag" in src["source_url"]

        # Check G502 Hero product
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'Logitech G502 Hero'").fetchone()
        assert prod is not None
        assert prod["category"] == "mouse"
        assert prod["metadata_confidence"] == 0.85  # UpstreamImplementationEvidence

        # Check device identifier
        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x046D"
        assert ident["pid_hex"] == "0xC08B"
        assert ident["confidence"] == 0.85
        assert ident["evidence_level"] == int(EvidenceLevel.LEVEL_2_DEVICE_IDENTITY)

        # Check protocol hints
        hints = conn.execute("SELECT hint_key, hint_value FROM protocol_hints WHERE product_id = ?", (prod["id"],)).fetchall()
        hint_dict = {h["hint_key"]: h["hint_value"] for h in hints}
        assert hint_dict.get("driver") == "hidpp20"
        assert hint_dict.get("protocol_family") == "hidpp20"

        # Check facts
        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert fact_dict.get("buttons_count") == "11"
        assert fact_dict.get("leds_count") == "2"
        assert fact_dict.get("profiles_count") == "5"
        assert fact_dict.get("dpi_range") == "100:25600@50"
        assert fact_dict.get("quirk:INDEX_OFFSET") == "true"


def test_libratbag_idempotency(tmp_path: Path, mock_libratbag_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector1 = LibratbagCollector(db=db, repo_path=mock_libratbag_tree, run_id="run_1")
    collector1.collect(dry_run=False)

    counts1 = db.get_summary_counts()

    # Re-run same ingestion
    collector2 = LibratbagCollector(db=db, repo_path=mock_libratbag_tree, run_id="run_2")
    stats2 = collector2.collect(dry_run=False)

    assert stats2["records_created"] == 0
    assert stats2["records_updated"] >= 4

    counts2 = db.get_summary_counts()
    assert counts1["total_products"] == counts2["total_products"]
    assert counts1["total_vid_pids"] == counts2["total_vid_pids"]
