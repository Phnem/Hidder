from pathlib import Path

from miner.detect.file_type import detect


def test_magic_bytes_override_extension() -> None:
    path = Path(__file__).parent / "_magic_fixture.bin"
    try:
        path.write_bytes(b"PK\x03\x04not-a-real-archive")
        assert detect(path) == "zip"
    finally:
        path.unlink(missing_ok=True)
