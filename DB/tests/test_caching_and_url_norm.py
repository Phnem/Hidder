"""Unit tests for URL normalization, pre-download caching, and in-run deduplication."""

import tempfile
from pathlib import Path
import pytest

from ingest.artifacts.cache import ArtifactCache
from ingest.artifacts.downloader import ArtifactDownloader
from ingest.network.url_norm import normalize_artifact_url
from ingest.network.fetcher import TieredFetcher
from ingest.storage.database import RegistryDatabase


def test_url_normalization_collapses_tracking_and_formatting():
    u1 = "https://DAREU.COM/files//driver.zip?utm_source=google&v=1.2.3&utm_medium=cpc#dl"
    u2 = "https://dareu.com/files/driver.zip?v=1.2.3"
    assert normalize_artifact_url(u1) == normalize_artifact_url(u2)
    assert normalize_artifact_url("http://test.com:80/file.bin") == "http://test.com/file.bin"
    assert normalize_artifact_url("https://test.com:443/file.bin") == "https://test.com/file.bin"


def test_meaningful_url_parameters_are_preserved():
    # Functional parameters like ref, id, token, key, v must NOT be stripped
    url = "https://example.com/download.zip?ref=support_page&id=456&token=secret&key=123&utm_source=ad"
    norm = normalize_artifact_url(url)
    assert "ref=support_page" in norm
    assert "id=456" in norm
    assert "token=secret" in norm
    assert "key=123" in norm
    assert "utm_source" not in norm


def test_contextual_xaml_qml_filtering():
    from ingest.scanners import should_skip_scan
    # UI/skin/view xaml -> should be skipped
    assert should_skip_scan(Path("D:/extracted/Skin/Dialog_Info.xaml")) is True
    assert should_skip_scan(Path("D:/extracted/UI/ButtonTheme.xaml")) is True
    assert should_skip_scan(Path("D:/extracted/Resources/StyleDictionary.xaml")) is True
    assert should_skip_scan(Path("D:/extracted/images/banner.png")) is True

    # Technical / device / config xaml -> should NOT be skipped
    assert should_skip_scan(Path("D:/extracted/Config/DeviceMatrix.xaml")) is False
    assert should_skip_scan(Path("D:/extracted/Protocol/HardwareConfig.xaml")) is False
    assert should_skip_scan(Path("D:/extracted/Firmware/VidPidSettings.xaml")) is False


def test_pre_download_cache_hit_and_in_run_deduplication(tmp_path):
    db_path = tmp_path / "test_cache.sqlite"
    cas_dir = tmp_path / "cas"
    db = RegistryDatabase(db_path)
    db.init_db()
    cache = ArtifactCache(cas_dir)
    fetcher = TieredFetcher()
    downloader = ArtifactDownloader(fetcher=fetcher, cache=cache, db=db)

    vendor_id = db.get_or_create_vendor("dareu", "Dareu")
    test_content = b"TEST_PAYLOAD_FOR_CACHING_VERIFICATION_12345"
    test_url = "https://dareu.com/driver/test_driver.zip"

    # 1. Process initial artifact content into CAS & DB
    res1 = downloader.process_artifact_content(
        content=test_content,
        filename="test_driver.zip",
        source_url=test_url,
        vendor="dareu",
        vendor_id=vendor_id
    )
    assert res1 is not None
    assert res1.is_new is True
    assert cache.has_artifact(res1.sha256)

    # 2. In-run deduplication: calling process_artifact_url on the same URL in the same run
    res2 = downloader.process_artifact_url(
        url=test_url,
        vendor="dareu",
        vendor_id=vendor_id
    )
    assert res2 is not None
    assert res2.sha256 == res1.sha256
    assert downloader.metrics["duplicate_urls_skipped"] == 1

    # 3. New downloader instance in subsequent run: pre-download DB cache lookup (Fresh hit within TTL)
    downloader_run2 = ArtifactDownloader(fetcher=fetcher, cache=cache, db=db)
    res3 = downloader_run2.process_artifact_url(
        url=test_url + "?utm_source=twitter",  # tracking param should collapse
        vendor="dareu",
        vendor_id=vendor_id
    )
    assert res3 is not None
    assert res3.sha256 == res1.sha256
    assert res3.is_new is False
    assert downloader_run2.metrics["artifacts_cache_hit_without_download"] == 1
    assert downloader_run2.metrics["artifacts_downloaded"] == 0
    assert downloader_run2.metrics["bytes_avoided_by_cache"] == len(test_content)
