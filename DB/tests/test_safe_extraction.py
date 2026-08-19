import zipfile
import pytest
from pathlib import Path
from ingest.artifacts.extractor import SafeExtractor


def test_safe_zip_extraction(tmp_path):
    zip_path = tmp_path / "test_driver.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("devices.json", '{"vendorId": "0x372E", "productId": "0x103E"}')
        zf.writestr("subfolder/driver.inf", "[Strings]\nDeviceDesc=AULA Keyboard")

    extractor = SafeExtractor(output_base=tmp_path / "extracted")
    res = extractor.extract(zip_path, "mockhash123")

    assert res.status == "success"
    assert res.file_count == 2
    assert (res.extracted_path / "devices.json").exists()
    assert (res.extracted_path / "subfolder" / "driver.inf").exists()


def test_path_traversal_rejection(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Suspicious relative path
        zf.writestr("../../../evil.txt", "malicious payload")
        zf.writestr("normal.txt", "safe")

    extractor = SafeExtractor(output_base=tmp_path / "extracted")
    res = extractor.extract(zip_path, "evilhack")

    assert res.status == "success"
    # Evil file must not escape or be written outside sandbox
    assert not (tmp_path / "evil.txt").exists()
    assert (res.extracted_path / "normal.txt").exists()
