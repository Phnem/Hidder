import zipfile
import json
from cabarchive import CabArchive, CabFile
from pathlib import Path

from miner.config import default_settings
from miner.storage.cas import sha256_file
from miner.unpack.safe import SafeUnpacker


def test_zip_extracts_regular_files_without_execution(tmp_path: Path) -> None:
    archive = tmp_path / "utility.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("app/devices.json", "{}")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(archive, sha256_file(archive))
    assert result.status == "success"
    assert result.file_count == 1
    assert (result.output_dir / "app" / "devices.json").read_text() == "{}"


def test_zip_path_traversal_is_rejected_before_writes(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../escape.txt", "no")
        bundle.writestr("safe.txt", "yes")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(archive, sha256_file(archive))
    assert result.status == "safety_violation"
    assert not (tmp_path / "escape.txt").exists()
    assert not (settings.workspace_dir / "unpacked" / sha256_file(archive)).exists()


def test_seven_zip_extracts_when_optional_adapter_is_available(tmp_path: Path) -> None:
    import py7zr

    source = tmp_path / "device.json"
    source.write_text("{}")
    archive = tmp_path / "utility.7z"
    with py7zr.SevenZipFile(archive, "w") as bundle:
        bundle.write(source, "app/device.json")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(archive, sha256_file(archive))
    assert result.status == "success"
    assert (result.output_dir / "app" / "device.json").read_text() == "{}"


def test_nested_archives_are_bounded_and_record_parent_relation(tmp_path: Path) -> None:
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as bundle:
        bundle.writestr("device.json", "{}")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as bundle:
        bundle.write(inner, "inner.zip")
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    unpacker = SafeUnpacker(settings)
    outer_sha256 = sha256_file(outer)
    children = unpacker.unpack_nested(unpacker.unpack(outer, outer_sha256), outer_sha256)
    assert children[0]["parent_artifact"] == outer_sha256
    assert children[0]["status"] == "success"


def test_asar_extracts_indexed_files_without_running_electron(tmp_path: Path) -> None:
    content = b"device.sendReport(9, new Uint8Array([1]));"
    header_json = json.dumps({"files": {"app.js": {"size": len(content), "offset": "0"}}}, separators=(",", ":")).encode()
    header_pickle = len(header_json).to_bytes(4, "little") + header_json
    header_pickle += b"\0" * ((4 - len(header_pickle) % 4) % 4)
    archive = tmp_path / "app.asar"
    archive.write_bytes((4).to_bytes(4, "little") + len(header_pickle).to_bytes(4, "little") + header_pickle + content)
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(archive, sha256_file(archive), "app.asar")
    assert result.status == "success"
    assert (result.output_dir / "app.js").read_bytes() == content


def test_cab_extracts_with_pure_python_adapter(tmp_path: Path) -> None:
    cabinet = CabArchive()
    cabinet["app/device.json"] = CabFile(b"{}")
    archive = tmp_path / "driver.cab"
    archive.write_bytes(cabinet.save())
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(archive, sha256_file(archive), "driver.cab")
    assert result.status == "success"
    assert (result.output_dir / "app" / "device.json").read_bytes() == b"{}"


def test_self_extracting_zip_is_unpacked_without_executing_exe(tmp_path: Path) -> None:
    ordinary_zip = tmp_path / "payload.zip"
    with zipfile.ZipFile(ordinary_zip, "w") as archive:
        archive.writestr("devices.json", "{}")
    sfx = tmp_path / "setup.exe"
    sfx.write_bytes(b"MZfake-sfx-stub" + ordinary_zip.read_bytes())
    settings = default_settings(root=tmp_path / "miner", cas_root=tmp_path / "cas")
    result = SafeUnpacker(settings).unpack(sfx, sha256_file(sfx), "setup.exe")
    assert result.status == "success"
    assert (result.output_dir / "devices.json").read_text() == "{}"
