"""Global configuration for Peripheral Registry Ingest."""

import os
from pathlib import Path
from pydantic import BaseModel, Field

# Base Directory: root of peripheral-registry-ingest
BASE_DIR = Path(__file__).resolve().parent.parent

# Data Paths
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
EXTRACTED_DIR = BASE_DIR / "extracted"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "registry.sqlite"

# Ensure runtime directories exist
for folder in [DATA_DIR, REPORTS_DIR, ARTIFACTS_DIR, EXTRACTED_DIR, LOGS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    # Crawl limits & Safety
    max_artifact_size_bytes: int = 1024 * 1024 * 1024  # 1 GB global limit
    max_single_artifact_download_bytes: int = 80 * 1024 * 1024  # 80 MB: prioritize drivers/manifests/firmware, defer >80MB suites
    max_extracted_size_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    max_extracted_files: int = 10000
    max_compression_ratio: float = 20.0

    # Network & Caching settings
    request_timeout_seconds: float = 25.0
    max_retries: int = 3
    request_delay_seconds: float = 0.5
    max_concurrency_per_host: int = 2
    artifact_freshness_ttl_hours: float = 24.0  # Fresh cached URLs reused instantly; stale URLs revalidated via conditional 304/ETag
    impersonate_profile: str = "chrome120"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Scanner settings
    max_scan_file_size_bytes: int = 50 * 1024 * 1024  # 50 MB per single static file to scan
    interesting_extensions: list[str] = [
        ".json", ".json5", ".xml", ".inf", ".ini", ".toml", ".yaml", ".yml",
        ".js", ".mjs", ".cjs", ".sqlite", ".db", ".txt", ".manifest", ".cfg", ".hex", ".bin"
    ]
    binary_scan_extensions: list[str] = [".exe", ".dll", ".sys", ".drv"]


settings = Settings()
