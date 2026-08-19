import json
from pathlib import Path

from miner.static.extract import scan_file


def test_sourcemap_sources_content_is_stronger_than_bundle_text(tmp_path: Path) -> None:
    source_map = tmp_path / "bundle.js.map"
    source_map.write_text(json.dumps({"sources": ["src/device.ts"], "sourcesContent": ["device.sendReport(9, new Uint8Array([0x13]));"]}))
    observations = scan_file("a" * 64, "unpacked/bundle.js.map", source_map)
    packet = next(item for item in observations if item.kind == "protocol.direct_packet_literal")
    assert "sourcesContent/src/device.ts" in packet.source_path


def test_pe_transport_hints_are_not_promoted_to_packet_commands(tmp_path: Path) -> None:
    binary = tmp_path / "driver.dll"
    binary.write_bytes(b"MZ...HidD_SetFeature...WriteFile...")
    observations = scan_file("b" * 64, "unpacked/driver.dll", binary)
    assert {item.kind for item in observations} == {"native.transport_hint"}
