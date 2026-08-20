"""Unit and integration tests for OpenRGB bulk-ingestion pipeline and byte-level protocol extraction."""

import json
import pytest
from pathlib import Path
from ingest.collectors.openrgb import (
    OpenRGBDetectorParser, OpenRGBByteProtocolExtractor, OpenRGBCollector,
    OpenRGBDeviceMetadata
)
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import EvidenceLevel


@pytest.fixture
def mock_openrgb_tree(tmp_path: Path) -> Path:
    """Create a temporary mock OpenRGB tree with Controllers, detectors, and packet methods."""
    controllers_dir = tmp_path / "Controllers"
    controllers_dir.mkdir(parents=True)

    # 1. Redragon Controller
    red_dir = controllers_dir / "RedragonController"
    red_dir.mkdir()

    (red_dir / "RedragonControllerDetect.h").write_text("""#pragma once
#define REDRAGON_MOUSE_VID 0x04D9
#define REDRAGON_MOUSE_USAGE_PAGE 0xFFA0
#define REDRAGON_M711_PID 0xFC30
#define REDRAGON_M602_PID 0xFC38
#define REDRAGON_MOUSE_REPORT_ID 0x02
""", encoding="utf-8")

    (red_dir / "RedragonControllerDetect.cpp").write_text("""#include "RedragonControllerDetect.h"
#include "DetectionManager.h"

REGISTER_HID_DETECTOR_IP("Redragon M711 Cobra", DetectRedragonMice, REDRAGON_MOUSE_VID, REDRAGON_M711_PID, 2, REDRAGON_MOUSE_USAGE_PAGE);
REGISTER_HID_DETECTOR_IP("Redragon M602 Griffin", DetectRedragonMice, REDRAGON_MOUSE_VID, REDRAGON_M602_PID, 2, REDRAGON_MOUSE_USAGE_PAGE);
""", encoding="utf-8")

    (red_dir / "RedragonMouseController.cpp").write_text("""#include "RedragonControllerDetect.h"

void RedragonMouseController::SendMouseApply()
{
    unsigned char usb_buf[16];
    usb_buf[0] = REDRAGON_MOUSE_REPORT_ID;
    usb_buf[1] = 0xF1;
    usb_buf[2] = 0x02;
    usb_buf[3] = 0x04;
    hid_send_feature_report(dev, usb_buf, 16);
}

void RedragonMouseController::SendWritePacket(unsigned short address, unsigned char data_size, unsigned char* data)
{
    unsigned char usb_buf[16];
    usb_buf[0] = REDRAGON_MOUSE_REPORT_ID;
    usb_buf[1] = 0xF3;
    usb_buf[2] = address & 0xFF;
    usb_buf[3] = address >> 8;
    usb_buf[4] = data_size;
    memcpy(&usb_buf[8], data, data_size);
    hid_send_feature_report(dev, usb_buf, 16);
}
""", encoding="utf-8")

    (red_dir / "RGBController_RedragonMouse.cpp").write_text("""#include "RGBController_RedragonMouse.h"

/**------------------------------------------------------------------*\\
    @name Redragon Mice
    @category Mouse
    @type USB
    @save :robot:
    @direct :x:
    @effects :white_check_mark:
    @detectors DetectRedragonMice
    @comment Redragon optical mice
\\*-------------------------------------------------------------------*/

void RGBController_RedragonMouse::Setup()
{
    mode Static;
    Static.name = "Static";
    modes.push_back(Static);

    mode Breathing;
    Breathing.name = "Breathing";
    modes.push_back(Breathing);
}
""", encoding="utf-8")

    # 2. ASRock Polychrome Controller (with procedural buffer)
    asrock_dir = controllers_dir / "ASRockPolychromeUSBController"
    asrock_dir.mkdir()

    (asrock_dir / "ASRockPolychromeUSBController.h").write_text("""#pragma once
#define POLYCHROME_USB_SET_ZONE 0x10
#define POLYCHROME_USB_WRITE_HEADER 0x15
""", encoding="utf-8")

    (asrock_dir / "ASRockPolychromeUSBController.cpp").write_text("""#include "ASRockPolychromeUSBController.h"

void PolychromeUSBController::WriteZone(unsigned char zone, unsigned char mode, unsigned char speed, RGBColor rgb)
{
    unsigned char usb_buf[65];
    usb_buf[0x01] = POLYCHROME_USB_SET_ZONE;
    usb_buf[0x03] = zone;
    usb_buf[0x04] = mode;
    usb_buf[0x05] = RGBGetRValue(rgb);
    usb_buf[0x06] = RGBGetGValue(rgb);
    usb_buf[0x07] = RGBGetBValue(rgb);
    usb_buf[0x08] = speed;
    hid_write(dev, usb_buf, 65);
}
""", encoding="utf-8")

    # 3. Razer Controller (with packed struct)
    razer_dir = controllers_dir / "RazerController"
    razer_dir.mkdir()

    (razer_dir / "RazerController.h").write_text("""#pragma once
#define PACK( __Declaration__ ) __Declaration__ __attribute__((__packed__))

PACK(struct razer_report
{
    unsigned char report_id;
    unsigned char status;
    unsigned char transaction_id;
    unsigned short remaining_packets;
    unsigned char protocol_type;
    unsigned char data_size;
    unsigned char command_class;
    unsigned char command_id;
    unsigned char arguments[80];
    unsigned char crc;
    unsigned char reserved;
});
""", encoding="utf-8")

    # 4. Areson Controller (with IPU)
    areson_dir = controllers_dir / "AresonController"
    areson_dir.mkdir()

    (areson_dir / "AresonControllerDetect.cpp").write_text("""#include "DetectionManager.h"

#define ARESON_VID 0x25A7
#define REDRAGON_M914_PID 0xFA7B

REGISTER_HID_DETECTOR_IPU("Redragon M914 NIX", DetectAresonControllers, ARESON_VID, REDRAGON_M914_PID, 1, 0xFF02, 2);
""", encoding="utf-8")

    # 5. ASUS Aura GPU Controller (with I2C PCI)
    asus_dir = controllers_dir / "AsusAuraGPUController"
    asus_dir.mkdir()

    (asus_dir / "AsusAuraGPUControllerDetect.cpp").write_text("""#include "DetectionManager.h"

REGISTER_I2C_PCI_DETECTOR("ASUS ROG STRIX GeForce GTX 1050 Gaming OC", DetectAsusAuraGPUControllers, 0x10DE, 0x1C81, 0x1043, 0x85D0, 0x29);
""", encoding="utf-8")

    return tmp_path


def test_openrgb_detector_parser(mock_openrgb_tree: Path):
    parser = OpenRGBDetectorParser(mock_openrgb_tree)
    devices = parser.parse_all_devices()
    assert len(devices) == 4

    dev_dict = {d.name: d for d in devices}

    # Verify Redragon M711 Cobra (HID IP)
    assert "Redragon M711 Cobra" in dev_dict
    m711 = dev_dict["Redragon M711 Cobra"]
    assert m711.vid_hex == "0x04D9"
    assert m711.pid_hex == "0xFC30"
    assert m711.interface == 2
    assert m711.usage_page_hex == "0xFFA0"
    assert m711.category == "mouse"
    assert m711.vendor_slug == "redragon"
    assert m711.save_mode == ":robot:"
    assert m711.direct_mode == ":x:"
    assert m711.effects_mode == ":white_check_mark:"

    # Verify Redragon M914 NIX (HID IPU)
    assert "Redragon M914 NIX" in dev_dict
    m914 = dev_dict["Redragon M914 NIX"]
    assert m914.vid_hex == "0x25A7"
    assert m914.pid_hex == "0xFA7B"
    assert m914.interface == 1
    assert m914.usage_page_hex == "0xFF02"
    assert m914.usage == 2

    # Verify ASUS ROG GPU (I2C PCI)
    assert "ASUS ROG STRIX GeForce GTX 1050 Gaming OC" in dev_dict
    gpu = dev_dict["ASUS ROG STRIX GeForce GTX 1050 Gaming OC"]
    assert gpu.vid_hex == "0x10DE"
    assert gpu.pid_hex == "0x1C81"
    assert gpu.svid_hex == "0x1043"
    assert gpu.spid_hex == "0x85D0"
    assert gpu.i2c_addr_hex == "0x29"
    assert gpu.category == "gpu"
    assert gpu.vendor_slug in ["asus", "asus_rog"]


def test_openrgb_byte_protocol_extractor(mock_openrgb_tree: Path):
    extractor = OpenRGBByteProtocolExtractor(mock_openrgb_tree)
    controllers = extractor.extract_controller_info()
    assert len(controllers) == 5

    ctrl_dict = {c.family_name: c for c in controllers}

    # Verify Redragon procedural packet builders
    assert "RedragonController" in ctrl_dict
    red = ctrl_dict["RedragonController"]
    assert len(red.packet_layouts) >= 2
    apply_pkt = next(p for p in red.packet_layouts if "SendMouseApply" in p["method"])
    assert apply_pkt["packet_length"] == 16
    assert apply_pkt["sink_function"] == "hid_send_feature_report"
    assert apply_pkt["fields"]["0"]["resolved_val"] == "0x02"
    assert apply_pkt["fields"]["1"]["raw_expr"] == "0xF1"

    # Verify ASRock procedural packet builders
    assert "ASRockPolychromeUSBController" in ctrl_dict
    asrock = ctrl_dict["ASRockPolychromeUSBController"]
    assert len(asrock.packet_layouts) >= 1
    write_zone = next(p for p in asrock.packet_layouts if "WriteZone" in p["method"])
    assert write_zone["packet_length"] == 65
    assert write_zone["sink_function"] == "hid_write"
    assert write_zone["fields"]["1"]["resolved_val"] == "0x10"
    assert write_zone["fields"]["5"]["semantic_tag"] == "color_red"

    # Verify Razer packed struct
    assert "RazerController" in ctrl_dict
    razer = ctrl_dict["RazerController"]
    assert len(razer.packed_structs) >= 1
    report_struct = razer.packed_structs[0]
    assert report_struct["struct_name"] == "razer_report"
    assert report_struct["total_size"] == 91
    field_names = [f["name"] for f in report_struct["fields"]]
    assert "report_id" in field_names
    assert "command_class" in field_names
    assert "arguments" in field_names


def test_openrgb_collector_persistence(tmp_path: Path, mock_openrgb_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = OpenRGBCollector(db=db, repo_path=mock_openrgb_tree, run_id="openrgb_run_1")
    collector.commit_sha = "openrgb_test_sha_123"

    # Dry run
    dry_stats = collector.collect(dry_run=True)
    assert dry_stats["devices_discovered"] == 4
    assert dry_stats["devices_recognized"] == 4
    counts_dry = db.get_summary_counts()
    assert counts_dry["total_products"] == 0

    # Real import
    stats = collector.collect(dry_run=False)
    assert stats["devices_discovered"] == 4
    assert stats["devices_recognized"] == 4
    assert stats["with_vid_pid"] == 4
    assert stats["with_ipu"] == 3

    counts = db.get_summary_counts()
    assert counts["total_products"] >= 4
    assert counts["total_vid_pids"] >= 3

    # Check database content
    with db.connection() as conn:
        # Check source
        src = conn.execute("SELECT * FROM sources WHERE content_hash = 'openrgb_test_sha_123'").fetchone()
        assert src is not None
        assert "CalcProgrammer1/OpenRGB" in src["source_url"]

        # Check Redragon M711 Cobra
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'Redragon M711 Cobra'").fetchone()
        assert prod is not None
        assert prod["category"] == "mouse"
        assert prod["metadata_confidence"] == 0.85

        # Check device identifier with interface and usage_page
        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x04D9"
        assert ident["pid_hex"] == "0xFC30"
        assert ident["usage_page"] == 0xFFA0
        assert ident["confidence"] == 0.85
        assert ident["evidence_level"] == int(EvidenceLevel.LEVEL_2_DEVICE_IDENTITY)

        # Check facts
        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert fact_dict.get("hid_interface") == "2"
        assert fact_dict.get("hid_usage_page") == "0xFFA0"
        assert fact_dict.get("lighting_save_mode") == ":robot:"
        assert fact_dict.get("lighting_direct_mode") == ":x:"
        assert fact_dict.get("lighting_effects_mode") == ":white_check_mark:"
        assert "openrgb_packet_layouts" in fact_dict


def test_openrgb_idempotency(tmp_path: Path, mock_openrgb_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector1 = OpenRGBCollector(db=db, repo_path=mock_openrgb_tree, run_id="run_1")
    collector1.collect(dry_run=False)

    counts1 = db.get_summary_counts()

    # Re-run ingestion
    collector2 = OpenRGBCollector(db=db, repo_path=mock_openrgb_tree, run_id="run_2")
    stats2 = collector2.collect(dry_run=False)

    assert stats2["records_created"] == 0
    assert stats2["records_updated"] >= 4

    counts2 = db.get_summary_counts()
    assert counts1["total_products"] == counts2["total_products"]
    assert counts1["total_vid_pids"] == counts2["total_vid_pids"]
