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


def test_via_definition_is_detected_only_with_structured_identity(tmp_path: Path) -> None:
    definition = tmp_path / "keyboard.json"
    definition.write_text(json.dumps({"vendorId": "0xFEED", "productId": "0x6060", "layouts": {"keymap": []}}))
    observations = scan_file("c" * 64, "unpacked/keyboard.json", definition)
    assert any(item.kind == "ecosystem.via_qmk" for item in observations)


def test_vial_and_qmk_metadata_are_distinguished(tmp_path: Path) -> None:
    definition = tmp_path / "keyboard.json"
    definition.write_text(json.dumps({"keyboard_name": "Fixture", "matrix_pins": {"rows": ["B1"]}, "vial_protocol": 6, "uid": "abc", "matrix": {"rows": 1}}))
    kinds = {item.kind for item in scan_file("f" * 64, "unpacked/keyboard.json", definition)}
    assert {"ecosystem.vial", "ecosystem.qmk"} <= kinds


def test_simple_function_buffer_flow_preserves_field_encoding_evidence(tmp_path: Path) -> None:
    source = tmp_path / "protocol.js"
    source.write_text("function setActuation(value) { const packet = new Uint8Array(3); packet[0] = 0x13; packet[1] = value * 100; device.sendReport(9, packet); }")
    observations = scan_file("d" * 64, "unpacked/protocol.js", source)
    builder = next(item for item in observations if item.kind == "protocol.buffer_builder")
    assert builder.value["semantic_candidate"] == "he.actuation.write"
    assert builder.value["field_writes"][1] == {"offset": 1, "expression": "value * 100"}


def test_dangerous_word_is_not_a_command_without_a_proven_buffer_builder(tmp_path: Path) -> None:
    source = tmp_path / "copy.js"
    source.write_text("const copy = 'firmware update available';")
    observations = scan_file("e" * 64, "unpacked/copy.js", source)
    assert {item.kind for item in observations} == {"protocol.dangerous_keyword"}
