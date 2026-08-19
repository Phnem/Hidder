import pytest
from ingest.artifacts.cache import ArtifactCache
from ingest.artifacts.extractor import SafeExtractor
from ingest.collectors.base import BaseCollector
from ingest.normalize.evidence import RawProduct
from ingest.network.fetcher import TieredFetcher, FetchResponse
from ingest.scanners import ScannerDispatcher
from ingest.storage.database import RegistryDatabase


class FaultyCollector(BaseCollector):
    @property
    def vendor_name(self) -> str:
        return "faulty_test"

    @property
    def display_name(self) -> str:
        return "Faulty Vendor"

    def collect(self, metadata_only: bool = False, no_download: bool = False):
        # 1. Product that raises parsing error in driver URL
        prod_bad = RawProduct(
            vendor=self.vendor_name,
            raw_name="Broken Device",
            driver_url="http://invalid.broken.domain/notfound.zip"
        )
        self.process_product(prod_bad)

        # 2. Valid product should still succeed
        prod_good = RawProduct(
            vendor=self.vendor_name,
            raw_name="Good Device",
            category="keyboard"
        )
        self.process_product(prod_good)


def test_collector_resilience_to_errors(tmp_path):
    db = RegistryDatabase(tmp_path / "test_resilience.sqlite")
    cache = ArtifactCache(tmp_path / "artifacts")
    extractor = SafeExtractor(tmp_path / "extracted")
    scanners = ScannerDispatcher()
    fetcher = TieredFetcher()

    collector = FaultyCollector(
        fetcher=fetcher,
        cache=cache,
        extractor=extractor,
        scanners=scanners,
        db=db,
        run_id="test_run"
    )

    # Must complete without crashing
    collector.collect(no_download=False)

    assert collector.stats["products_scanned"] == 2
    assert collector.stats["new_products"] == 2
    # Good product must be recorded in DB
    details = db.get_product_with_details("Good Device")
    assert len(details) == 1
    assert details[0]["canonical_name"] == "Good Device"


def test_artifact_404_resilience_and_metrics(tmp_path, monkeypatch):
    """Verify that an artifact returning 404 does not crash crawl, records artifact_download_failures==1, fatal_errors==0, and run finishes as completed."""
    from ingest.artifacts.downloader import ArtifactDownloader
    from ingest.logging_setup import setup_logging, get_log_metrics

    setup_logging(verbose=False, log_to_file=False)
    db = RegistryDatabase(tmp_path / "test_404.sqlite")
    db.init_db()
    cache = ArtifactCache(tmp_path / "artifacts")
    extractor = SafeExtractor(tmp_path / "extracted")
    scanners = ScannerDispatcher()
    fetcher = TieredFetcher()

    # Mock download_file_stream to simulate HTTP 404 failure
    def mock_download_404(*args, **kwargs):
        raise RuntimeError("HTTP 404 Not Found from upstream server")

    monkeypatch.setattr(fetcher, "download_file_stream", mock_download_404)

    run_id = "test_404_run"
    db.start_crawl_run(run_id)

    downloader = ArtifactDownloader(fetcher=fetcher, cache=cache, db=db)
    collector = FaultyCollector(
        fetcher=fetcher,
        cache=cache,
        extractor=extractor,
        scanners=scanners,
        db=db,
        run_id=run_id,
        downloader=downloader
    )

    total_stats = {
        "products_scanned": 0,
        "new_products": 0,
        "new_artifacts": 0,
        "changed_artifacts": 0,
        "new_vid_pids": 0,
        "new_hints": 0,
        "fatal_errors": 0,
        "collector_errors": 0,
        "artifact_download_failures": 0,
        "parse_failures": 0,
        "warnings": 0,
        "errors_count": 0,
    }

    try:
        collector.collect(no_download=False)
        for k, val in collector.stats.items():
            if k in total_stats:
                total_stats[k] += val
    except Exception:
        total_stats["fatal_errors"] += 1

    for k, val in downloader.metrics.items():
        if k in total_stats:
            total_stats[k] += val
        else:
            total_stats[k] = val

    log_metrics = get_log_metrics()
    for k in ["fatal_errors", "collector_errors", "artifact_download_failures", "parse_failures", "warnings"]:
        total_stats[k] = max(total_stats.get(k, 0), log_metrics.get(k, 0))
    total_stats["errors_count"] = total_stats["fatal_errors"]

    db.finish_crawl_run(run_id, total_stats, status="completed")

    # Reconciled metrics assertions
    assert total_stats["fatal_errors"] == 0
    assert total_stats["artifact_download_failures"] == 1
    assert total_stats["errors_count"] == 0

    with db.connection() as conn:
        run_row = conn.execute("SELECT status, errors_count FROM crawl_runs WHERE id = ?", (run_id,)).fetchone()
        assert run_row["status"] == "completed"
        assert run_row["errors_count"] == 0
