import datetime
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional, NamedTuple

from ingest.artifacts.cache import ArtifactCache
from ingest.config import settings
from ingest.logging_setup import log_artifact, get_logger
from ingest.network.fetcher import TieredFetcher
from ingest.network.url_norm import normalize_artifact_url
from ingest.normalize.evidence import RawArtifact
from ingest.storage.database import RegistryDatabase

logger = get_logger()


def is_cache_entry_fresh(last_seen_iso: Optional[str], ttl_hours: float = 24.0) -> bool:
    """Check if cache record is within freshness TTL to bypass conditional network requests."""
    if not last_seen_iso:
        return False
    try:
        seen_dt = datetime.datetime.fromisoformat(last_seen_iso)
        if seen_dt.tzinfo is None:
            seen_dt = seen_dt.replace(tzinfo=datetime.timezone.utc)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        age_hours = (now_dt - seen_dt).total_seconds() / 3600.0
        return age_hours < ttl_hours
    except Exception:
        return False


class DownloadResult(NamedTuple):
    sha256: str
    cas_path: Path
    is_new: bool
    is_cache_hit: bool
    is_hash_changed: bool


class ArtifactDownloader:
    def __init__(self, fetcher: TieredFetcher, cache: ArtifactCache, db: RegistryDatabase):
        self.fetcher = fetcher
        self.cache = cache
        self.db = db
        # In-run deduplication cache (URL -> DownloadResult)
        self.seen_urls_in_run: dict[str, DownloadResult] = {}
        # Runtime metrics
        self.metrics = {
            "artifacts_discovered": 0,
            "artifacts_downloaded": 0,
            "artifacts_cache_hit_without_download": 0,
            "conditional_304": 0,
            "bytes_downloaded": 0,
            "bytes_avoided_by_cache": 0,
            "duplicate_urls_skipped": 0,
            "large_artifacts_deferred": 0,
            "artifact_download_failures": 0,
        }

    def process_artifact_url(
        self,
        url: str,
        vendor: str,
        vendor_id: int,
        software_version: Optional[str] = None,
        product_id: Optional[int] = None,
        run_id: Optional[str] = None,
        custom_filename: Optional[str] = None
    ) -> Optional[DownloadResult]:
        """
        Download and register an artifact file with freshness and revalidation:
        1. In-run URL deduplication (same URL never fetched twice in one run).
        2. Database normalized URL lookup:
           - Fresh cached URL (within TTL) -> instant reuse.
           - Stale cached URL -> conditional ETag / Last-Modified request.
           - HTTP 304 -> reuse existing CAS.
           - Changed / 200 -> download new artifact and track changes.
        """
        if not url:
            return None

        self.metrics["artifacts_discovered"] += 1
        norm_url = normalize_artifact_url(url)

        # 1. In-run deduplication
        if norm_url in self.seen_urls_in_run:
            self.metrics["duplicate_urls_skipped"] += 1
            cached_res = self.seen_urls_in_run[norm_url]
            if product_id and cached_res.sha256:
                self.db.link_product_artifact(product_id, cached_res.sha256, relation_type="driver")
            log_artifact(f"In-run duplicate URL skipped: {norm_url}")
            return cached_res

        # Determine filename
        if custom_filename:
            filename = custom_filename
        else:
            parsed = urllib.parse.urlparse(url)
            filename = Path(parsed.path).name or "artifact.bin"

        if not software_version:
            m = re.search(r'(?:[vV]|setup[_\-\s]*|driver[_\-\s]*|_)([0-9]+(?:\.[0-9]+)+)', filename)
            if m:
                software_version = m.group(1)

        # 2. Database Pre-Download Cache Lookup & Freshness Policy
        cached_info = self.db.get_artifact_by_url(norm_url)
        if cached_info and cached_info.get("sha256"):
            sha256 = cached_info["sha256"]
            cas_path = self.cache.get_artifact_path(sha256)
            
            if cas_path.exists():
                etag = cached_info.get("etag")
                last_mod = cached_info.get("last_modified")
                cached_size = cached_info.get("size", 0) or 0
                last_seen = cached_info.get("last_seen")

                # Fresh cached URL: if within freshness TTL, reuse instantly with 0 HTTP calls
                is_fresh = is_cache_entry_fresh(last_seen, ttl_hours=settings.artifact_freshness_ttl_hours)
                
                if is_fresh:
                    self.metrics["artifacts_cache_hit_without_download"] += 1
                    self.metrics["bytes_avoided_by_cache"] += cached_size
                    log_artifact(
                        f"URL Cache HIT (Fresh) — Reusing CAS {sha256[:12]}... for {filename} "
                        f"({cached_size / 1024 / 1024:.2f} MB avoided)"
                    )
                    self.db.update_artifact_url_last_seen(norm_url)
                    if product_id:
                        self.db.link_product_artifact(product_id, sha256, relation_type="driver")

                    res = DownloadResult(
                        sha256=sha256,
                        cas_path=cas_path,
                        is_new=False,
                        is_cache_hit=True,
                        is_hash_changed=False
                    )
                    self.seen_urls_in_run[norm_url] = res
                    return res

                # Stale cached URL: revalidate with conditional ETag / Last-Modified request
                is_valid_cache = True
                if etag or last_mod:
                    status_code, _ = self.fetcher.check_artifact_conditional(url, etag=etag, last_modified=last_mod)
                    if status_code == 304:
                        self.metrics["conditional_304"] += 1
                        is_valid_cache = True
                    elif status_code == 200:
                        # Server indicates remote artifact changed!
                        is_valid_cache = False
                    else:
                        # Network error / timeout / 405 -> fallback safely to existing CAS
                        is_valid_cache = True

                if is_valid_cache:
                    self.metrics["artifacts_cache_hit_without_download"] += 1
                    self.metrics["bytes_avoided_by_cache"] += cached_size
                    log_artifact(
                        f"URL Cache HIT (Revalidated 304) — Reusing CAS {sha256[:12]}... for {filename} "
                        f"({cached_size / 1024 / 1024:.2f} MB avoided)"
                    )
                    self.db.update_artifact_url_last_seen(norm_url)
                    if product_id:
                        self.db.link_product_artifact(product_id, sha256, relation_type="driver")

                    res = DownloadResult(
                        sha256=sha256,
                        cas_path=cas_path,
                        is_new=False,
                        is_cache_hit=True,
                        is_hash_changed=False
                    )
                    self.seen_urls_in_run[norm_url] = res
                    return res

        log_artifact(f"Processing artifact URL (Network download required): {url} ({filename})")

        # 3. Stream download
        temp_dir = Path(tempfile.gettempdir()) / "peripheral_ingest_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / filename

        try:
            size_bytes, sha256, final_url, content_type, etag, last_mod = self.fetcher.download_file_stream(
                url, temp_file, max_bytes=settings.max_single_artifact_download_bytes
            )
            self.metrics["artifacts_downloaded"] += 1
            self.metrics["bytes_downloaded"] += size_bytes

            # Store in CAS
            is_cache_hit = self.cache.has_artifact(sha256)
            if is_cache_hit:
                log_artifact(f"Cache HIT — CAS already contains SHA256: {sha256[:12]}...")
            else:
                log_artifact(f"Cache MISS — Storing {size_bytes / 1024 / 1024:.2f} MB in CAS (SHA256: {sha256[:12]}...)")

            actual_sha, cas_path = self.cache.store_file(temp_file, expected_sha256=sha256)

            # Record in DB artifacts and artifact_urls
            raw_art = RawArtifact(
                original_url=url,
                final_url=final_url,
                filename=filename,
                content_type=content_type,
                size=size_bytes,
                sha256=actual_sha,
                etag=etag,
                last_modified=last_mod,
                normalized_url=norm_url,
                vendor=vendor,
                software_version=software_version
            )
            _, is_new_in_db, is_hash_changed = self.db.upsert_artifact(raw_art, vendor_id, run_id=run_id)
            self.db.record_artifact_url(
                normalized_url=norm_url,
                original_url=url,
                final_url=final_url,
                etag=etag,
                last_modified=last_mod,
                sha256=actual_sha,
                vendor_id=vendor_id,
                size=size_bytes,
                status="downloaded"
            )

            if product_id:
                self.db.link_product_artifact(product_id, actual_sha, relation_type="driver")

            res = DownloadResult(
                sha256=actual_sha,
                cas_path=cas_path,
                is_new=is_new_in_db,
                is_cache_hit=is_cache_hit,
                is_hash_changed=is_hash_changed
            )
            self.seen_urls_in_run[norm_url] = res
            return res

        except ValueError as e:
            logger.warning(f"[artifact] Large artifact deferred from {url}: {e}")
            self.metrics["large_artifacts_deferred"] += 1
            self.db.record_artifact_url(
                normalized_url=norm_url,
                original_url=url,
                final_url=None,
                etag=None,
                last_modified=None,
                sha256=None,
                vendor_id=vendor_id,
                status="deferred"
            )
            return None
        except Exception as e:
            self.metrics["artifact_download_failures"] += 1
            logger.error(f"[artifact] Failed to download artifact from {url}: {e}")
            return None
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    def process_artifact_content(
        self,
        content: bytes,
        filename: str,
        source_url: str,
        vendor: str,
        vendor_id: int,
        software_version: Optional[str] = None,
        product_id: Optional[int] = None,
        run_id: Optional[str] = None
    ) -> Optional[DownloadResult]:
        """Store direct content payload into CAS and database."""
        norm_url = normalize_artifact_url(source_url)
        if norm_url in self.seen_urls_in_run:
            cached_res = self.seen_urls_in_run[norm_url]
            if product_id and cached_res.sha256:
                self.db.link_product_artifact(product_id, cached_res.sha256, relation_type="configurator_bundle")
            return cached_res

        temp_dir = Path(tempfile.gettempdir()) / "peripheral_ingest_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / filename

        try:
            with open(temp_file, "wb") as f:
                f.write(content)

            size_bytes = len(content)
            actual_sha, cas_path = self.cache.store_file(temp_file)
            is_cache_hit = self.cache.has_artifact(actual_sha)

            raw_art = RawArtifact(
                original_url=source_url,
                filename=filename,
                size=size_bytes,
                sha256=actual_sha,
                normalized_url=norm_url,
                vendor=vendor,
                software_version=software_version
            )
            _, is_new_in_db, is_hash_changed = self.db.upsert_artifact(raw_art, vendor_id, run_id=run_id)
            self.db.record_artifact_url(
                normalized_url=norm_url,
                original_url=source_url,
                final_url=source_url,
                etag=None,
                last_modified=None,
                sha256=actual_sha,
                vendor_id=vendor_id,
                size=size_bytes,
                status="downloaded"
            )

            if product_id:
                self.db.link_product_artifact(product_id, actual_sha, relation_type="configurator_bundle")

            res = DownloadResult(
                sha256=actual_sha,
                cas_path=cas_path,
                is_new=is_new_in_db,
                is_cache_hit=is_cache_hit,
                is_hash_changed=is_hash_changed
            )
            self.seen_urls_in_run[norm_url] = res
            return res
        except Exception as e:
            self.metrics["artifact_download_failures"] += 1
            logger.error(f"[artifact] Error storing artifact content from {source_url}: {e}", exc_info=True)
            return None
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
