import hashlib
import tempfile
from pathlib import Path
import pytest
from ingest.artifacts.cache import ArtifactCache, compute_sha256_of_file, compute_sha256_of_bytes


def test_sha256_calculation(tmp_path):
    data = b"Sample firmware payload 12345"
    expected = hashlib.sha256(data).hexdigest()

    test_file = tmp_path / "firmware.bin"
    test_file.write_bytes(data)

    assert compute_sha256_of_bytes(data) == expected
    assert compute_sha256_of_file(test_file) == expected


def test_cas_storage(tmp_path):
    cache_dir = tmp_path / "artifacts"
    cache = ArtifactCache(base_dir=cache_dir)

    data = b"Driver installer test"
    expected_sha = hashlib.sha256(data).hexdigest()

    temp_file = tmp_path / "temp_dl.bin"
    temp_file.write_bytes(data)

    assert cache.has_artifact(expected_sha) is False

    actual_sha, stored_path = cache.store_file(temp_file)
    assert actual_sha == expected_sha
    assert stored_path.exists()
    assert cache.has_artifact(expected_sha) is True

    # Check path format: artifacts/prefix/sha256
    assert stored_path.parent.name == expected_sha[:2].lower()
    assert stored_path.name == expected_sha.lower()
