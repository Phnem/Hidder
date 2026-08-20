"""Unit and integration tests for all bulk sources ingestion collectors."""

import json
import pytest
from pathlib import Path
from ingest.collectors.signalrgb import SignalRGBPluginParser, SignalRGBCollector
from ingest.collectors.openrazer import OpenRazerDriverParser, OpenRazerCollector
from ingest.collectors.solaar import SolaarDescriptorParser, SolaarCollector
from ingest.collectors.rivalcfg import RivalcfgProfileParser, RivalcfgCollector
from ingest.collectors.wooting import WootingCollector
from ingest.collectors.corsair_ckb import CorsairCkbParser, CorsairCkbCollector
from ingest.collectors.logitech_docs import LogitechDocsCollector
from ingest.collectors.artemis_rgbnet import ArtemisRGBNetParser, ArtemisRGBNetCollector
from ingest.storage.database import RegistryDatabase
from ingest.normalize.evidence import EvidenceLevel


@pytest.fixture
def mock_sources_tree(tmp_path: Path) -> Path:
    """Create mock source trees for testing collectors."""
    # 1. SignalRGB
    srgb_dir = tmp_path / "signalrgb-official-plugins" / "Plugins" / "Asus"
    srgb_dir.mkdir(parents=True)
    (srgb_dir / "Asus_Chakram_Mouse.js").write_text("""
export function Name() { return "ASUS ROG Chakram"; }
export function VendorId() { return 0x0B05; }
export function ProductId() { return 0x1901; }
export function Publisher() { return "WhirlwindFX"; }
export function Documentation() { return "https://docs.signalrgb.com"; }
export function Size() { return [10, 10]; }

export function Validate(endpoint) {
    return endpoint.interface === 2 && endpoint.usage_page === 0xFF00;
}

export function Initialize() {
    device.write([0x00, 0x51, 0x28, 0x01], 65);
}
""", encoding="utf-8")

    # 2. OpenRazer
    razer_dir = tmp_path / "openrazer" / "driver"
    razer_dir.mkdir(parents=True)
    (razer_dir / "razercommon.h").write_text("""
#define USB_VENDOR_ID_RAZER 0x1532
#define USB_DEVICE_ID_RAZER_DEATHADDER_V2 0x0084
#define RAZER_CMD_SET_DPI 0x04
union transaction_id_union { unsigned char id; };
union command_id_union { unsigned char id; };
struct razer_report {
    u8 status;
    union transaction_id_union transaction_id;
    __be16 remaining_packets;
    u8 protocol_type;
    u8 data_size;
    u8 command_class;
    union command_id_union command_id;
    u8 arguments[80];
    u8 crc;
    u8 reserved;
};
static_assert(sizeof(struct razer_report) == 90);
""", encoding="utf-8")
    (razer_dir / "razermouse_driver.c").write_text("""
#include "razercommon.h"
static const struct hid_device_id razer_mouse_devices[] = {
    { USB_DEVICE(USB_VENDOR_ID_RAZER, USB_DEVICE_ID_RAZER_DEATHADDER_V2) },
    { }
};
""", encoding="utf-8")

    # 3. Solaar
    solaar_dir = tmp_path / "solaar" / "lib" / "logitech_receiver"
    solaar_dir.mkdir(parents=True)
    (solaar_dir / "hidpp20_constants.py").write_text("""
from enum import IntEnum
class SupportedFeature(IntEnum):
    ROOT = 0x0000
    FEATURE_SET = 0x0001
    BATTERY_STATUS = 0x1000
    ADJUSTABLE_DPI = 0x2201
    DFUCONTROL = 0x00C3
""", encoding="utf-8")
    (solaar_dir / "descriptors.py").write_text("""
from .hidpp10_constants import Registers as Reg

def _D(name, codename=None, kind=None, wpid=None, protocol=None, usbid=None, interface=None, btid=None):
    pass

_D("MX Master 3", codename="MX Master 3", kind="mouse", wpid="4082", protocol=4.5, usbid="c52b")
_D("G Pro Wireless", codename="G Pro Wireless", kind="mouse", wpid="4079", protocol=4.2, usbid="c088")
""", encoding="utf-8")

    # 4. Rivalcfg
    rival_dir = tmp_path / "rivalcfg" / "rivalcfg" / "devices"
    rival_dir.mkdir(parents=True)
    (rival_dir / "rival3.py").write_text("""
profile = {
    "name": "SteelSeries Rival 3",
    "models": [
        {
            "name": "SteelSeries Rival 3",
            "vendor_id": 0x1038,
            "product_id": 0x1824,
            "endpoint": 0,
        },
    ],
    "settings": {
        "sensitivity": {
            "label": "Sensitivity",
            "command": [0x03, 0x00, 0x01],
        },
    },
}
""", encoding="utf-8")

    # 5. Wooting
    woot_dir = tmp_path / "wootswitch" / "docs"
    woot_dir.mkdir(parents=True)
    (woot_dir / "hid-protocol.md").write_text("# Wooting HID Protocol", encoding="utf-8")

    # 6. Corsair ckb-next
    ckb_dir = tmp_path / "ckb-next" / "src" / "daemon"
    ckb_dir.mkdir(parents=True)
    (ckb_dir / "devices.c").write_text("""
#define P_K70_RGB_PRO 0x1BA4
#define P_SABRE_RGB_PRO 0x1BA5
""", encoding="utf-8")

    # 7. Artemis
    artemis_dir = tmp_path / "artemis-plugins" / "Asus"
    artemis_dir.mkdir(parents=True)
    (artemis_dir / "AsusAuraDevice.cs").write_text("""
public class AsusAuraDevice {
    public static DeviceIdentifier Device = new DeviceIdentifier(0x0B05, 0x1867, "ASUS ROG Claymore");
}
""", encoding="utf-8")

    return tmp_path


def test_signalrgb_parser_and_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = SignalRGBCollector(db=db, sources_root=mock_sources_tree, run_id="srgb_test")
    stats = collector.collect(dry_run=False)

    assert stats["plugins_discovered"] == 1
    assert stats["records_created"] == 1

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE canonical_name = 'ASUS ROG Chakram'").fetchone()
        assert prod is not None
        assert prod["category"] == "mouse"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x0B05"
        assert ident["pid_hex"] == "0x1901"

        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert "signalrgb_packet_writes" in fact_dict
        assert "signalrgb_validation_rules" in fact_dict


def test_openrazer_parser_and_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = OpenRazerCollector(db=db, repo_path=mock_sources_tree / "openrazer", run_id="razer_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] >= 1
    assert stats["records_created"] >= 1

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name LIKE '%Deathadder V2%'").fetchone()
        assert prod is not None
        assert prod["category"] == "mouse"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x1532"
        assert ident["pid_hex"] == "0x0084"

        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert "openrazer_packet_structs" in fact_dict
        layout = json.loads(fact_dict["openrazer_packet_structs"])[0]
        assert layout["total_size"] == layout["upstream_size"] == 90
        assert layout["fields"][0]["name"] == "status"


def test_solaar_parser_and_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = SolaarCollector(db=db, repo_path=mock_sources_tree / "solaar", run_id="solaar_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] == 2
    assert stats["features_recorded"] == 5

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'MX Master 3'").fetchone()
        assert prod is not None
        assert prod["category"] == "mouse"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x046D"
        assert ident["pid_hex"] == "0xC52B"

        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert "hidpp20_feature_registry" in fact_dict
        hints = conn.execute("SELECT hint_key, hint_value FROM protocol_hints WHERE product_id = ?", (prod["id"],)).fetchall()
        assert ("solaar_device_protocol_field", "4.5") in {(h["hint_key"], h["hint_value"]) for h in hints}


def test_rivalcfg_parser_and_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = RivalcfgCollector(db=db, repo_path=mock_sources_tree / "rivalcfg", run_id="rival_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] == 1

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'SteelSeries Rival 3'").fetchone()
        assert prod is not None

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x1038"
        assert ident["pid_hex"] == "0x1824"

        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert "rivalcfg_command_packets" in fact_dict


def test_wooting_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = WootingCollector(db=db, sources_root=mock_sources_tree, run_id="woot_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] >= 5

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'Wooting 60HE+'").fetchone()
        assert prod is not None
        assert prod["category"] == "keyboard"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x31E3"
        assert ident["pid_hex"] == "0x1320"
        assert ident["usage_page"] == 0xFF55

        facts = conn.execute("SELECT key, value FROM facts WHERE product_id = ?", (prod["id"],)).fetchall()
        fact_dict = {f["key"]: f["value"] for f in facts}
        assert fact_dict.get("supports_rapid_trigger") == "true"
        assert fact_dict.get("actuation_range_mm") == "0.1-4.0"
        assert "wooting_hid_command_specs" in fact_dict


def test_corsair_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = CorsairCkbCollector(db=db, sources_root=mock_sources_tree, run_id="corsair_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] == 2

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name LIKE '%K70 Rgb Pro%'").fetchone()
        assert prod is not None
        assert prod["category"] == "keyboard"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x1B1C"
        assert ident["pid_hex"] == "0x1BA4"


def test_logitech_docs_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = LogitechDocsCollector(db=db, sources_root=mock_sources_tree, run_id="g933_test")
    stats = collector.collect(dry_run=False)

    assert stats["headsets_recorded"] == 3

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name LIKE '%G933%'").fetchone()
        assert prod is not None
        assert prod["category"] == "headset"

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x046D"
        assert ident["pid_hex"] == "0x0A5B"


def test_artemis_rgbnet_collector(tmp_path: Path, mock_sources_tree: Path):
    db_file = tmp_path / "test_registry.sqlite"
    db = RegistryDatabase(db_file)
    db.init_db()

    collector = ArtemisRGBNetCollector(db=db, sources_root=mock_sources_tree, run_id="artemis_test")
    stats = collector.collect(dry_run=False)

    assert stats["devices_discovered"] == 1

    with db.connection() as conn:
        prod = conn.execute("SELECT * FROM products WHERE raw_name = 'ASUS ROG Claymore'").fetchone()
        assert prod is not None

        ident = conn.execute("SELECT * FROM device_identifiers WHERE product_id = ?", (prod["id"],)).fetchone()
        assert ident is not None
        assert ident["vid_hex"] == "0x0B05"
        assert ident["pid_hex"] == "0x1867"
