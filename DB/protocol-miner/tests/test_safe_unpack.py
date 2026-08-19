import zipfile
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
