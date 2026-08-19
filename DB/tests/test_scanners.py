import pytest
from pathlib import Path
from ingest.scanners.inf_scanner import InfScanner
from ingest.scanners.json_scanner import JsonScanner
from ingest.scanners.js_scanner import JsScanner
from ingest.scanners.binary_strings import BinaryStringsScanner


def test_inf_scanner(tmp_path):
    inf_content = """
    [Version]
    Signature="$Windows NT$"
    DriverVer=05/20/2024,1.0.8

    [Manufacturer]
    %MfgName%=Standard,NTamd64

    [Standard.NTamd64]
    %DeviceDesc%=DriverInstall, USB\\VID_372E&PID_103E&MI_00
    %MouseDesc%=MouseInstall, HID\\VID_046D&PID_C539

    [Strings]
    MfgName="AULA"
    DeviceDesc="AULA Hero 84 HE Gaming Keyboard"
    """
    inf_file = tmp_path / "aula_driver.inf"
    inf_file.write_text(inf_content, encoding="utf-8")

    scanner = InfScanner()
    res = scanner.scan_file(inf_file, "fakehash")

    assert len(res.identifiers) == 2
    vids = {i.vid_hex for i in res.identifiers}
    assert "0x372E" in vids
    assert "0x046D" in vids


def test_json_scanner(tmp_path):
    json_content = """
    {
      "name": "Keychron Q1 Max",
      "vendorProductId": "0x34340120",
      "sdkModuleName": "qmk_via",
      "matrix": {"rows": 6, "cols": 16}
    }
    """
    json_file = tmp_path / "config.json"
    json_file.write_text(json_content, encoding="utf-8")

    scanner = JsonScanner()
    res = scanner.scan_file(json_file, "fakehash")

    assert len(res.identifiers) == 1
    assert res.identifiers[0].vid_hex == "0x3434"
    assert res.identifiers[0].pid_hex == "0x0120"
    assert any(h.hint_value == "qmk_via" for h in res.hints)


def test_js_scanner(tmp_path):
    js_content = """
    const HID_CONFIG = {
        filters: [{ vendorId: 0x372E, productId: 0x103E }],
        usagePage: 0xFF60,
        usage: 0x0061
    };
    function setPollingRate(hz) { return [0x93, hz]; }
    const sdkModuleName = "bytech";
    """
    js_file = tmp_path / "bundle.js"
    js_file.write_text(js_content, encoding="utf-8")

    scanner = JsScanner()
    res = scanner.scan_file(js_file, "fakehash")

    assert len(res.identifiers) >= 1
    assert res.identifiers[0].vid_hex == "0x372E"
    assert res.identifiers[0].pid_hex == "0x103E"
    assert any(h.hint_key == "sdkModuleName" and h.hint_value == "bytech" for h in res.hints)
    assert any(h.hint_key == "command_signature" and h.hint_value == "setPollingRate" for h in res.hints)


def test_binary_strings_scanner(tmp_path):
    # Simulated binary with embedded USB strings
    bin_file = tmp_path / "driver.bin"
    raw_data = b"SomePEHeader\x00\x00USB\\VID_372E&PID_103E\x00ExtraDataBytechDriver\x00"
    bin_file.write_bytes(raw_data)

    scanner = BinaryStringsScanner()
    res = scanner.scan_file(bin_file, "fakehash")

    assert len(res.identifiers) == 1
    assert res.identifiers[0].vid_hex == "0x372E"
    assert res.identifiers[0].pid_hex == "0x103E"
